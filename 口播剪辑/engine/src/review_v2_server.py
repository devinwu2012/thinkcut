"""剪口播的后台（审片台“口播剪辑”栏的文字剪辑器用它）：逐字删改、字幕纠错、分段、保存到达芬奇、预览片段。
2026-11-22 用户定：原来单独的审核网页并进审片台“口播剪辑”栏，页面在 thinkcut-edit 的 assets/审片工作台/文案剪辑.html，
由审片台转发请求过来；这里不再给页面，直接打开本服务的地址只说一句“已并入审片台”并给出审片台的地址。"""
import json
import hashlib
import os
import re
import tempfile
import threading
import html
import shutil
import subprocess
import math
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from auto_review import ROOT, RUN
from review_build import active_map, build_full
import paragraph_editor
from local_repetitions import refresh as refresh_repetitions

from project_runtime import NAME, CONFIG, TOTAL
from web_delivery import DeliveryJob
from source_guard import verify_source,SourceChanged
from prepare_mvp_delivery import exclusive_lock, delivering
PORT=int(os.environ.get('AI_EDIT_PORT','18442'))
STATE=RUN/'review_v2_state.json'
LOCK=threading.RLock()
DELIVERY=DeliveryJob(RUN,ROOT.parent/'bin/edit_project.py',LOCK)
JOB={'running':False,'error':None}
PART_SECONDS=360/(30000/1001)
PART_LOCKS={}
TIMEOUT_MESSAGE='处理原片超过限定时间没完成（NAS 慢或原片太大），这次没有改动。稍等再试一次；还不行就重开审片台。'
class FfmpegMissing(RuntimeError):
    """这台 Mac 找不到 ffmpeg，预览片段转不出来。"""


def preview_part(index):
    verify_source(CONFIG,RUN)
    if not CONFIG or not 0<=index<math.ceil(TOTAL/360):raise ValueError('Invalid preview segment')
    source=__import__('pathlib').Path(CONFIG['source'])
    before=source.stat();cache=RUN/'preview_parts';cache.mkdir(exist_ok=True)
    identity=hashlib.sha256(json.dumps([str(source),before.st_size,before.st_mtime_ns,before.st_ctime_ns]).encode()).hexdigest()[:12]
    path=cache/f'{identity}_{index}.mp4'
    with LOCK:guard=PART_LOCKS.setdefault(index,threading.Lock())
    with guard:
        if not path.exists():
            temporary=path.with_name(path.stem+'.partial-'+__import__('uuid').uuid4().hex[:8]+'.mp4')
            ffmpeg=shutil.which('ffmpeg')
            if not ffmpeg:raise FfmpegMissing('这台 Mac 找不到 ffmpeg（没装，或不在 PATH 里），预览画面出不来。请让 AI 运行 bash bin/setup_mac.sh 装好后重开审片台。')
            try:
                process=subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-nostdin','-y',
                    '-ss',str(index*PART_SECONDS),'-i',str(source),'-t',str(min(PART_SECONDS,TOTAL/(30000/1001)-index*PART_SECONDS)),
                    '-map','0:v:0','-map',f"0:a:{CONFIG.get('audio_stream',0)}",'-vf','scale=960:-2',
                    '-c:v','h264_videotoolbox','-b:v','2000k','-c:a','aac','-b:a','128k','-movflags','+faststart',str(temporary)],
                    capture_output=True,text=True,timeout=120)
                after=source.stat()
                if process.returncode or (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):
                    raise ValueError('Preview conversion failed or source changed')
                os.replace(temporary,path)
            finally:temporary.unlink(missing_ok=True)
    return path


def prefetch(index):
    """Convert the next part while the current one plays (one part takes ~6 s, plays 12 s)."""
    def work():
        try:preview_part(index)
        except Exception:pass  # the request for that part reports any real error
    if CONFIG and 0<=index<math.ceil(TOTAL/360):threading.Thread(target=work,daemon=True).start()


def manifest(): return json.loads((RUN/'review_v2.json').read_text())
def state(): return json.loads(STATE.read_text())
def full(): return json.loads((RUN/'review_v2_full.json').read_text())


def persist_state(previous,current):
    """Serialize every writer with delivery, then detect external byte changes."""
    import fcntl
    import errno
    import time
    with (STATE.parent/'.delivering.lock').open('a+') as lock:
        deadline = time.monotonic()+3
        while True:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except OSError as error:
                if error.errno not in (errno.EAGAIN,errno.EWOULDBLOCK,errno.EACCES):raise
                if time.monotonic()>=deadline:
                    raise paragraph_editor.StateConflict('正在保存到达芬奇或另一处正在写入，请稍后重试') from None
                time.sleep(0.05)
        try:
            original=STATE.read_bytes()
            if paragraph_editor.state_revision(json.loads(original))!=paragraph_editor.state_revision(previous):
                raise paragraph_editor.StateConflict('审核状态已更新，请刷新后重试。')
            backups=STATE.parent/'review_state_backups'
            backups.mkdir(exist_ok=True,mode=0o700)
            backup=backups/(hashlib.sha256(original).hexdigest()+'.json')
            try:file=backup.open('xb')
            except FileExistsError:
                if backup.read_bytes()!=original:raise OSError('State backup integrity check failed')
            else:
                try:
                    with file:
                        os.chmod(backup,0o600);file.write(original);file.flush();os.fsync(file.fileno())
                except BaseException:
                    backup.unlink(missing_ok=True);raise
            temporary=None
            try:
                with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=STATE.parent,prefix='.review-state-',delete=False) as file:
                    temporary=file.name
                    json.dump(current,file,ensure_ascii=False,indent=2)
                    file.flush();os.fsync(file.fileno())
                if STATE.read_bytes()!=original:raise paragraph_editor.StateConflict('审核状态在写入期间已被修改，请刷新后重试。')
                os.replace(temporary,STATE)
            finally:
                if temporary and os.path.exists(temporary):os.unlink(temporary)
        finally:
            fcntl.flock(lock,fcntl.LOCK_UN)


def rebuild():
    with LOCK:
        if JOB['running']: return
        JOB.update(running=True,error=None)
    def work():
        try:
            # This only writes small frame maps, never renders media. Keep the
            # state snapshot and derived files under the same writer lock.
            with LOCK:
                build_full(manifest(),state())
                JOB['running']=False
        except Exception as e:
            with LOCK: JOB.update(running=False,error=str(e)[:250])
    threading.Thread(target=work,daemon=True).start()


def merged_page():
    """直接打开本服务地址时的那一页：已并入审片台，给出审片台的地址（审片台起来时写在 审核/审片台.json）。"""
    try:url=json.loads((RUN/'审片台.json').read_text()).get('url') or ''
    except (OSError,ValueError):url=''
    link=(f'<p><a href="{html.escape(url)}#%E6%96%87%E6%A1%88">打开审片台的“口播剪辑”栏：{html.escape(url)}</a></p>' if url.startswith('http://127.0.0.1:') else '')
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>已并入审片台</title>'
            '<body style="font:15px/1.7 -apple-system,BlinkMacSystemFont,\'PingFang SC\',sans-serif;background:#f5f5f7;color:#1d1d1f;padding:40px">'
            f'<h1 style="font-size:20px">{html.escape(RUN.parent.name)}：文稿剪辑已并入审片台</h1>'
            '<p>剪口播（改文字做剪辑、保存到达芬奇）现在在审片台的“口播剪辑”栏里，这个单独的网页不再用了。</p>'+link+
            '<p style="color:#6e6e73">审片台没开着就让 AI 运行 edit_project.py serve --project-dir &lt;剪口播工程&gt;，它会打开审片台、停在“口播剪辑”栏。</p></body></html>')


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup();self.connection.settimeout(15)
    def log_message(self,*_): pass
    def reply(self,value,kind='application/json',code=200):
        body=value.encode() if isinstance(value,str) else json.dumps(value,ensure_ascii=False).encode()
        self.send_response(code);self.send_header('Content-Type',kind+'; charset=utf-8')
        self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)
    def valid_host(self): return self.headers.get('Host')==f'127.0.0.1:{PORT}'
    def do_GET(self):
        try:return self.get_response()
        except (BrokenPipeError,ConnectionResetError):pass
        except SourceChanged as e:self.reply({'error':str(e)},code=409)
        except subprocess.TimeoutExpired:self.reply({'error':TIMEOUT_MESSAGE},code=504)
        except FfmpegMissing as e:self.reply({'error':str(e)},code=500)
        except (OSError,ValueError,KeyError,TypeError):self.reply({'error':'审核数据暂时无法读取，请检查项目文件后刷新。'},code=500)
    def get_response(self):
        if not self.valid_host(): self.send_error(403);return
        verify_source(CONFIG,RUN)
        if self.path=='/':return self.reply(merged_page(),'text/html')
        # 审片台给文字剪辑器页面填预览片段的长度（页面在审片台那边）
        if self.path=='/editor-page-info':
            return self.reply({'name':NAME,**({'segment_seconds':PART_SECONDS,'source_duration':TOTAL/(30000/1001)} if CONFIG else {})})
        if self.path=='/resolve-sync.js':return self.reply((ROOT/'resolve_sync.js').read_text(),'application/javascript')
        if self.path=='/preview-source.js':return self.reply((ROOT/'preview_source.js').read_text(),'application/javascript')
        if self.path=='/caption-engine.js':return self.reply((ROOT/'caption_engine.js').read_text(),'application/javascript')
        if self.path=='/caption-text.js':return self.reply((ROOT/'caption_text.js').read_text()+'\n'+(ROOT/'src/caption_ranges.js').read_text(),'application/javascript')
        if self.path=='/editor-api':
            with LOCK:return self.reply(paragraph_editor.payload(manifest(),state()))
        if self.path=='/resolve-status':return self.reply(DELIVERY.status())
        part_match=re.fullmatch(r'/source-part/(\d+)\.mp4',self.path)
        if part_match:
            prefetch(int(part_match[1])+1)
            path=preview_part(int(part_match[1]))
            total=path.stat().st_size; start=0; end=total-1; code=200
            range_value=self.headers.get('Range')
            if range_value:
                match=re.fullmatch(r'bytes=(\d+)-(\d*)',range_value)
                if not match:self.send_error(416);return
                start=int(match[1]);end=min(int(match[2]) if match[2] else end,end);code=206
                if not 0<=start<=end:self.send_error(416);return
            self.send_response(code);self.send_header('Content-Type','video/mp4')
            self.send_header('Accept-Ranges','bytes');self.send_header('Content-Length',str(end-start+1))
            if code==206:self.send_header('Content-Range',f'bytes {start}-{end}/{total}')
            self.end_headers()
            try:
                with path.open('rb') as file:
                    file.seek(start);left=end-start+1
                    while left:
                        chunk=file.read(min(left,262144))
                        if not chunk:break
                        self.wfile.write(chunk);left-=len(chunk)
            except (BrokenPipeError,ConnectionResetError):pass
            return
        self.send_error(404)
    def do_POST(self):
        if not self.valid_host() or self.headers.get('Origin')!=f'http://127.0.0.1:{PORT}' or self.path not in ('/editor-action','/save-to-resolve','/subtitle-corrections','/paragraph-break'):
            self.send_error(403);return
        try:
            verify_source(CONFIG,RUN)
            n=int(self.headers.get('Content-Length','0'))
            if not 0<n<100000: raise ValueError('Invalid size')
            if self.headers.get('Transfer-Encoding'):raise ValueError('Invalid request framing')
            raw=self.rfile.read(n)
            if len(raw)!=n:raise ValueError('Incomplete request')
            body=json.loads(raw)
            if not isinstance(body,dict):raise ValueError('Expected a JSON object')
            with LOCK:
                if self.path=='/save-to-resolve':
                    return self.reply(DELIVERY.start(body.get('state_revision'),paragraph_editor.state_revision(state())))
                if DELIVERY.info['running'] or (self.path!='/save-to-resolve' and delivering(RUN)):
                    return self.reply({'error':'正在保存到达芬奇，完成后可继续修改文字。'},code=409)
            if self.path=='/paragraph-break':
                # 只改网页排版（自然段），不碰剪辑和审核状态
                with LOCK:
                    paragraph_editor.set_break(body.get('id'),body.get('on'))
                    return self.reply(paragraph_editor.payload(manifest(),state()))
            if self.path=='/subtitle-corrections':
                import script_check
                with LOCK:
                    if DELIVERY.info['running'] or delivering(RUN):raise paragraph_editor.StateConflict('正在保存到达芬奇，完成后可继续修改。')
                    previous=state()
                    if 'state_revision' in body and body['state_revision']!=paragraph_editor.state_revision(previous):
                        raise paragraph_editor.StateConflict('审核状态已更新，请刷新后重试。')
                    done=body.get('proofread') is True
                    aligned=paragraph_editor.alignment()
                    kept=[t['id'] for t in script_check.kept_tokens(manifest(),previous,aligned)] if done else None
                    s=script_check.merge(previous,script_check.validate_rows(body.get('rows'),aligned,allow_empty=done),done,kept)
                    s['saved_at']=datetime.now(timezone.utc).isoformat()
                    persist_state(previous,s)
                    return self.reply(paragraph_editor.payload(manifest(),s))
            if self.path=='/editor-action':
                with LOCK:
                    if DELIVERY.info['running'] or delivering(RUN):raise paragraph_editor.StateConflict('正在保存到达芬奇，完成后可继续修改。')
                    previous=state();m=manifest()
                    if body.get('state_revision')!=paragraph_editor.state_revision(previous):
                        raise paragraph_editor.StateConflict('审核状态已更新，请核对最新文字后重试。')
                    s=paragraph_editor.change(previous,body,m)
                    refresh_repetitions(s,m)
                    import pause_refine   # 删句、恢复之后按新的删句重算接缝（2026-11-13，Q060）
                    s=pause_refine.after_edit(RUN,m['cards'],previous,s,paragraph_editor.alignment(),TOTAL,CONFIG.get('source'))
                    s['saved_at']=datetime.now(timezone.utc).isoformat()
                    result=paragraph_editor.payload(m,s)
                    if not result['segments']:raise ValueError('不能删除全部口播文字；此次未修改。')
                    persist_state(previous,s)
                rebuild()
                return self.reply(result)
        except paragraph_editor.StateConflict as e:self.reply({'error':str(e),'reload':True},code=409)
        except (ValueError,TypeError) as e:self.reply({'error':str(e)},code=400)
        except OSError:self.reply({'error':'保存或读取文件失败，请检查磁盘与目录权限后刷新核对。'},code=500)
        except KeyError as e:self.reply({'error':f'审核状态文件缺少内容（{e}），这次没有保存。请刷新网页重试；还不行就把这句话交给 AI 检查项目的“审核”文件夹。'},code=500)
        except subprocess.TimeoutExpired:self.reply({'error':TIMEOUT_MESSAGE},code=504)


URL_FILE=RUN/'审核网址.txt'
VERSION_FILE=ROOT.parent.parent/'VERSION'   # 全家一个版本号：程序目录的 VERSION（2026-11-24 六个仓库并成一个）


def shared_version():
    try:return VERSION_FILE.read_text().strip()
    except OSError:return None


def version_changed(started,current):
    """程序更新了没有：VERSION 的内容变了，或启动时有、现在读不到了（文件被挪走＝目录结构变了；2026-11-24 六个仓库并成一个时，
    老进程盯的各块自己的 VERSION 被删，一直读到 None，就再也不会自己重启，要手动重启）。启动时就读不到的不算（没东西可比）。"""
    return bool(started) and current!=started


def restart_when_updated(server,started_version,flag):
    """Reload this service after the shared program is updated, but never during a save to Resolve."""
    while True:
        threading.Event().wait(20)
        current=shared_version()
        if version_changed(started_version,current) and not DELIVERY.status().get('running'):
            # Take the writer lock and keep it: no edit is half-way and none can start before the process is replaced.
            LOCK.acquire()
            if DELIVERY.info.get('running'):LOCK.release();continue
            flag['restart']=True;server.shutdown();return


def main():
    import socket,sys
    with exclusive_lock(RUN/'.review-server.lock', '该项目已有审核服务运行；请使用现有网页，不要再启动第二个服务。'):
        verify_source(CONFIG,RUN,sampled=True,audio=True)
        if full()['cut_frames']!=active_map(manifest()['cards'],state())['cut_frames']:rebuild()
        version=shared_version();flag={'restart':False}
        with ThreadingHTTPServer(('127.0.0.1',PORT),Handler) as server:
            url=f'http://127.0.0.1:{PORT}/'
            URL_FILE.write_text(json.dumps({'url':url,'pid':os.getpid(),'host':socket.gethostname(),'version':version,
                                            'started':datetime.now().isoformat(timespec='seconds')},ensure_ascii=False))
            print(f'Result review: {url}',flush=True)
            threading.Thread(target=restart_when_updated,args=(server,version,flag),daemon=True).start()
            server.serve_forever()
    if flag['restart']:
        print('程序已更新，自动重启审核服务',flush=True)
        os.execv(sys.executable,[sys.executable,__file__,*sys.argv[1:]])

if __name__=='__main__':main()
