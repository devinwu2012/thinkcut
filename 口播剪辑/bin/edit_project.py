#!/usr/bin/env python3
"""Per-video editing steps. The Codex agent supplies literal semantic selections."""
import argparse
import hashlib
import json
import os
import shutil
import signal
import socket
import time
import subprocess
import sys
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/'engine'
sys.path.insert(0,str(ENGINE/'src'))
from probe_media import probe
from editing_preflight import alignment_runtime
from source_guard import verify_source,single_audio,replace_source,SourceChanged,standard_project_root

@contextmanager
def delivery_lock(run):
    """Hold the project's delivery lock; retry briefly because the page and clean probe it for an instant."""
    from prepare_mvp_delivery import exclusive_lock
    for attempt in range(10):
        lock=exclusive_lock(run/'.delivering.lock','这个项目正在保存到达芬奇，请等它完成')
        try:lock.__enter__()
        except RuntimeError:
            if attempt==9:raise
            time.sleep(0.3);continue
        try:yield
        finally:lock.__exit__(None,None,None)
        return


# 本机达芬奇写入锁：和 B-roll／花字（engine/broll/live/workspace.py 的 write_lock）、thinkcut-edit、视频项目管理是同一个文件，
# 谁在写达芬奇谁拿着；剪口播交付也拿这一把，才不会和它们同时写同一个工程。测试用环境变量 THINKCUT_RESOLVE_LOCK 换成临时文件。
RESOLVE_WRITE_LOCK=Path.home()/'.cache/ai-talking-head-edit/resolve-write.lock'


@contextmanager
def resolve_write_lock(run):
    """拿不到（另一个命令正在写达芬奇）就停下，不等；锁文件不删（删了会出现两把锁），进程退出系统自动放锁。"""
    import fcntl
    from prepare_mvp_delivery import lock_busy
    path=Path(os.environ.get('THINKCUT_RESOLVE_LOCK') or RESOLVE_WRITE_LOCK);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+',encoding='utf-8') as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as error:
            if not lock_busy(error):raise
            # 锁文件里是持锁方自己写的：JSON（thinkcut-edit、本命令）或只有进程号（B-roll／花字）
            try:
                handle.seek(0);text=handle.read().strip()
                who=json.loads(text) if text.startswith('{') else {'进程号':int(text),'命令':'口播剪辑（B-roll/花字写入）'}
            except ValueError:who={}
            if not isinstance(who,dict):who={}
            raise SystemExit(f"达芬奇正被另一个命令占用，这次没有动达芬奇：{who.get('命令') or '不知道哪个命令'}"
                             f"（片子 {who.get('片子目录') or '不知道'}，进程 {who.get('进程号','不知道')}，{who.get('开始时间') or '开始时间不知道'} 开始）。"
                             f"等它做完再保存一次。锁文件：{path}") from None
        try:
            handle.seek(0);handle.truncate()
            handle.write(json.dumps({'片子目录':str(run.parent),'命令':'口播剪辑 deliver','进程号':os.getpid(),'开始时间':time.strftime('%Y-%m-%d %H:%M:%S')},ensure_ascii=False))
            handle.flush()
            yield
        finally:fcntl.flock(handle,fcntl.LOCK_UN)


def deliver_preflight(run):
    from run_mvp import resolve_mcp_runtime,node_runtime
    root,python,_=resolve_mcp_runtime()
    node_runtime(root)
    verify_source(json.loads((run/'project_runtime.json').read_text()),run,sampled=True,audio=True)
    return python

def prepare_resolve(run):
    """deliver 动达芬奇之前，标准项目先把达芬奇备到“开着这条片子的工程”（2026-11-23 初稿模式实测：达芬奇开着时新片子的库登记不了、
    还要 AI 自己写脚本建工程）。交给 视频项目管理 的 project.py resolve-open：没开就开、库不在列表就借用当前开着的库、没有工程就新建同名工程、
    切走用户的工程前先保存并记一笔。全程不问。用户在 project_runtime.json 另指了库（resolve_library）或工程名不是项目文件夹名时不管，照旧由后面的检查把关。
    返回 resolve-open 打印的结果（dict）；不适用返回 None。"""
    try:config=json.loads((run/'project_runtime.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):return None
    root=standard_project_root(run,config.get('source'))
    if not root or config.get('resolve_library') or config.get('existing_project_only') is not True or config.get('project')!=root.name:return None
    tool=Path(os.environ.get('THINKCUT_PM') or ROOT.parent/'视频项目管理'/'bin').expanduser()/'project.py'
    if not tool.is_file():
        print(f'提醒：找不到 {tool}（开发时用环境变量 THINKCUT_PM 指到 视频项目管理/bin），这次没有自动备好达芬奇工程。',file=sys.stderr);return None
    done=subprocess.run([sys.executable,str(tool),'resolve-open',str(root)],capture_output=True,text=True,env={**os.environ,'PYTHONUTF8':'1'})
    if done.returncode:
        raise SystemExit('没能自动备好达芬奇工程，这次没有交付：'+(done.stderr.strip() or done.stdout.strip() or f'resolve-open 退出码 {done.returncode}'))
    try:result=json.loads(done.stdout)
    except ValueError:result={'结果':done.stdout.strip()}
    for key in ('库列表','借用','切走了','结果'):
        if result.get(key):print(f'达芬奇·{key}：{result[key]}')
    return result


class StepFailed(Exception):
    def __init__(self,details):super().__init__(details);self.details=details


def execute(script,env,python=sys.executable,*extra):
    # Progress stays on screen; error output is kept so it can be explained in Chinese.
    process=subprocess.run([str(python),str(ENGINE/'src'/script),*map(str,extra)],env=env,stderr=subprocess.PIPE,text=True)
    if process.stderr and not process.returncode:sys.stderr.write(process.stderr)
    if process.returncode:raise StepFailed(process.stderr or f'{script} 退出码 {process.returncode}')


def run_self(command):
    # The child already printed its own Chinese explanation.
    code=subprocess.run(command).returncode
    if code:raise SystemExit(code)

def service_info(run):
    """The running review service of this project on this Mac, or None."""
    try:info=json.loads((run/'审核网址.txt').read_text())
    except (OSError,ValueError):return None
    if info.get('host')!=socket.gethostname():return {**info,'other_host':True}
    try:os.kill(info['pid'],0)
    except (OSError,KeyError,TypeError):return None
    command=subprocess.run(['ps','-p',str(info['pid']),'-o','command='],capture_output=True,text=True).stdout
    return info if 'review_v2_server.py' in command and str(run) in command else None


def free_port(start=18443):
    for port in range(start,start+200):
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1',port)):return port
    raise SystemExit('找不到空闲端口（18443–18642 都被占用）')


def thinkcut_skill():
    """thinkcut-edit（审片台在它里面）：环境变量 THINKCUT_SKILL 优先（开发时指到 worktree），否则是本仓库的兄弟目录 口播自动剪辑/skill/thinkcut-edit。"""
    return Path(os.environ.get('THINKCUT_SKILL') or ROOT.parent/'口播自动剪辑'/'skill'/'thinkcut-edit').expanduser()


def serve_backend(run,env,args):
    """起（或复用）剪口播后台：审片台“口播剪辑”栏的文字剪辑器用它。一个项目一个进程，不开浏览器。"""
    info=service_info(run)
    if info and info.get('other_host'):
        raise SystemExit(f"这个项目的剪口播后台正在另一台 Mac（{info['host']}）上运行；请在那台 Mac 上用审片台，或先在那台 stop。")
    if not info:
        env['AI_EDIT_PORT']=str(args.port or free_port())
        (run/'审核网址.txt').unlink(missing_ok=True)
        log=(run/'review_service.log').open('a')
        # The project path on the command line lets stop/status recognise this project's page process.
        process=subprocess.Popen([sys.executable,str(ENGINE/'src'/'review_v2_server.py'),str(run)],env=env,stdout=log,stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL,start_new_session=True)
        deadline=time.time()+120
        while not (info:=service_info(run)) or info.get('pid')!=process.pid:
            if process.poll() is not None or time.time()>deadline:
                tail=(run/'review_service.log').read_text(errors='replace').strip().splitlines()[-3:]
                raise SystemExit('剪口播后台没有启动成功：'+(tail[-1] if tail else '原因未知')+f"\n完整日志：{run/'review_service.log'}")
            time.sleep(0.5)
    return info


def serve(run,env,args):
    """2026-11-22 用户定：剪口播的审核网页并进审片台“口播剪辑”栏。serve 先起后台，再打开这条片子的审片台、停在“口播剪辑”栏
    （还没建自动剪辑的片子目录时，审片台只有“口播剪辑”栏能用）。--backend 只起后台（审片台自己按需调用）。"""
    info=serve_backend(run,env,args)
    if args.backend:
        print(f"剪口播后台：{info['url']}（程序版本 {info.get('version')}）");return
    entry=thinkcut_skill()/'scripts'/'thinkcut_edit.py'
    if not entry.is_file():
        raise SystemExit(f'找不到审片台（{entry}）。剪口播的网页已并进审片台；thinkcut-edit 应在本仓库旁边的 口播自动剪辑/skill/thinkcut-edit，'
                         '不在那里就设环境变量 THINKCUT_SKILL 指到它。')
    child={k:v for k,v in os.environ.items() if k not in ('AI_EDIT_RUN_DIR','AI_EDIT_PORT')}
    if args.no_open:child['THINKCUT_NO_OPEN']='1'
    command=[sys.executable,str(entry),'workbench',str(run.parent),'--column','文案']
    code=subprocess.run(command,env=child,stdin=subprocess.DEVNULL).returncode
    if code:raise SystemExit(f'审片台没有打开（退出码 {code}）；剪口播后台已在 {info["url"]} 运行。')


def stop(run,force=False):
    info=service_info(run)
    if not info:
        (run/'审核网址.txt').unlink(missing_ok=True)   # 进程已经不在：把残留记录清掉（2026-11-13 审查）
        print('这个项目没有正在运行的剪口播后台。');return
    if info.get('other_host'):
        if not force:raise SystemExit(f"剪口播后台在另一台 Mac（{info['host']}）上运行，需在那台停止。那台已经关机、崩溃或找不到了：先跟用户确认没人在那台用这个片子的审片台口播剪辑栏，再运行 stop --force 清掉这条残留记录。")
        (run/'审核网址.txt').unlink(missing_ok=True)
        print(f"已清掉另一台 Mac（{info['host']}）留下的剪口播后台记录。");return
    os.kill(info['pid'],signal.SIGTERM)
    for _ in range(40):
        if not service_info(run):break
        time.sleep(0.25)
    (run/'审核网址.txt').unlink(missing_ok=True)
    print('剪口播后台已停止（审片台还开着的话，口播剪辑栏下次操作时会自动再起）。')


def main_line_name(folder):
    """主线名前缀用短版本号 v1、v2…（2026-10-23 用户：名字短一点、带版本，好找也好看）。
    同一个 02_制作 下每多一个已建过的剪辑目录（有 审核/project_runtime.json）就加 1；达芬奇只认英文名，所以不用片名。"""
    folder=Path(folder).resolve()
    done=[p for p in folder.parent.iterdir() if p.is_dir() and p.resolve()!=folder and (p/'审核/project_runtime.json').is_file()] if folder.parent.is_dir() else []
    return f'v{len(done)+1}'


def self_command(action,folder,args,*extra):
    command=[sys.executable,str(Path(__file__).resolve()),action,'--project-dir',str(folder),*map(str,extra)]
    if args.align_python and action in ('init','align'):command+=['--align-python',str(args.align_python)]
    if action=='init' and getattr(args,'retry_uncertain',False):command+=['--retry-uncertain']
    return command


def transcribe_command(video,folder,args):
    command=[sys.executable,str(ROOT/'bin/transcribe_video.py'),'--video',str(video),'--work-dir',str(folder/'转写')]
    if args.audio_stream is not None:command+=['--audio-stream',str(args.audio_stream)]
    # 上次识别请求没有完整结果、用户核对过豆包用量并同意重试时才加（2026-11-13 审查）
    if getattr(args,'retry_uncertain',False):command+=['--retry-uncertain']
    sidecar=machine_voice(video)      # 机器配音占位（bin/doubao_tts.py 做的口播源）：字和时间合成时就有了，不调豆包识别
    if sidecar:command+=['--preset',str(sidecar)]
    return command


def machine_voice(video):
    """口播源旁边有 <片名>.机器配音.json（标着占位、带逐字结果）＝这条口播是机器配音占位；返回记录文件路径，否则 None。"""
    path=Path(video).with_name(Path(video).stem+'.机器配音.json')
    try:record=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError):return None
    return path if isinstance(record,dict) and record.get('占位') and isinstance(record.get('asr'),dict) else None


INIT_FILES=('sentence_units.json','sentence_boundaries.json','project_runtime.json')


def initialized(run):
    """三个初始化文件都在才算初始化完；project_runtime.json 最后写，是“已初始化”的标志。"""
    return all((run/name).is_file() for name in INIT_FILES)


def atomic_text(path,text):
    temporary=path.with_name(f'.{path.name}.{os.getpid()}.partial')
    try:
        with temporary.open('w',encoding='utf-8') as stream:
            stream.write(text);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def write_init_files(run,config,units):
    """每个文件都先写临时文件再改名；标志文件 project_runtime.json 最后写：中途断了，下次 init/prepare 会整套重写。"""
    data={'sentence_units.json':units,'sentence_boundaries.json':{str(u['id']):{'ms':u['start_ms'],'safe':True} for u in units},'project_runtime.json':config}
    atomic_text(run/'glossary.txt','保留原始识别；产品名和专业词须对照原声。\n')
    for filename in INIT_FILES:atomic_text(run/filename,json.dumps(data[filename],ensure_ascii=False,indent=2))


def resolve_project(folder,video,name):
    """达芬奇工程名。标准项目：就是项目文件夹名，只打开已有的同名工程（工程名要先和用户确认再建，程序不替人新建）；
    其他项目：程序自建的 _mcp_ 工程。"""
    root=standard_project_root(folder,video)
    if root:return {'project':root.name,'existing_project_only':True}
    return {'project':'_mcp_AI口播_'+name+'_'+datetime.now().strftime('%Y%m%d')+'_'+hashlib.sha256(str(folder).encode()).hexdigest()[:6]}


def prepare(folder,run,args):
    """Transcribe, then align in the background while the agent chooses retakes."""
    if not initialized(run):
        if not args.video:raise SystemExit('这个项目上次初始化没有做完；请带上 --video <原片> 再运行一次 prepare（已有的识别结果会复用，不重复计费）。')
        run_self(self_command('init',folder,args,'--video',args.video.expanduser().resolve(strict=True)))
    if alignment_running(run):
        print('声学对齐已在后台进行；继续写 review_decisions.json，写好后运行 finish。');return
    if (run/'character_alignment.json').exists():
        print('识别和对齐都已完成；请通读 审核/sentence_units.json 写 review_decisions.json，然后运行 finish。');return
    with (run/'align.log').open('a') as log:
        process=subprocess.Popen(self_command('align',folder,args),stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,start_new_session=True)
    (run/'对齐进程.txt').write_text(json.dumps({'pid':process.pid,'host':socket.gethostname()}))
    print('识别完成，声学对齐已在后台进行（日志 审核/align.log）。现在通读 审核/sentence_units.json 写 review_decisions.json，写好后运行 finish。')


def alignment_running(run):
    """True while this project's background `align` (started by prepare on this Mac) is still running."""
    try:info=json.loads((run/'对齐进程.txt').read_text())
    except (OSError,ValueError):return False
    if not isinstance(info,dict):return False
    if (run/'character_alignment.json').exists():return False   # 对齐结果已经在了：在哪台 Mac 上跑的都算完成（2026-11-13 审查）
    if info.get('host')!=socket.gethostname():
        raise SystemExit(f"这个项目的声学对齐是在另一台 Mac（{info.get('host')}）上启动的；请在那台 Mac 上继续，或等它完成。")
    status=subprocess.run(['ps','-p',str(info.get('pid')),'-o','stat=,command='],capture_output=True,text=True).stdout.strip()
    return bool(status) and not status.startswith('Z') and 'edit_project.py' in status and ' align ' in status+' ' and str(run.parent) in status


# 等后台对齐的总时限。正常很快：12.5 分钟的原片实测 64 秒～2 分钟（CHANGELOG 待修清单第 7 项），
# 识别上限 2 小时的原片按比例约 20 分钟；2 小时是它的好几倍，到了还没完基本就是卡住了。
ALIGN_WAIT_SECONDS=2*60*60


def wait_for_alignment(run):
    deadline=time.monotonic()+ALIGN_WAIT_SECONDS
    while alignment_running(run):
        if time.monotonic()>=deadline:
            raise SystemExit(f'等后台声学对齐超过 {ALIGN_WAIT_SECONDS//60} 分钟还没完成，先停下（没有结束后台的对齐进程）。'
                             f"\n日志：{run/'align.log'}；日志还在往下走就再运行一次 finish 接着等；不动了就结束那个对齐进程，重新运行 align。")
        print('等待后台对齐完成…',flush=True);time.sleep(10)
    (run/'对齐进程.txt').unlink(missing_ok=True)
    if not (run/'character_alignment.json').exists():
        tail=(run/'align.log').read_text(errors='replace').strip().splitlines()[-3:] if (run/'align.log').exists() else []
        raise SystemExit('声学对齐没有完成：'+(tail[-1] if tail else '没有日志')+f"\n日志：{run/'align.log'}；可重新运行 align。")


# 项目文案的分类解析在 视频项目管理（两个仓库是同一个程序目录下的兄弟目录）；按文件路径加载，不改 sys.path
SCRIPT_DOC=ROOT.parent/'视频项目管理'/'bin'/'script_doc.py'


def project_script_doc(folder,run):
    """这条片子的项目文案 03_成片/<项目名>.md（2026-11-22 用户定：定稿文案和成片放一起；还没搬的老项目在 02_制作/<项目名>.md，读那份）。
    项目根＝往上找同时有 项目.json 和 01_原始素材 的那层，同 standard_project_root。"""
    try:source=json.loads((run/'project_runtime.json').read_text()).get('source')
    except (OSError,ValueError,AttributeError):source=None
    root=standard_project_root(folder,source)
    if not root:return None
    new,old=root/'03_成片'/(root.name+'.md'),root/'02_制作'/(root.name+'.md')
    return old if old.is_file() and not new.exists() else new


def default_script(folder,run):
    """没传 --script、审核/文案.txt 也还没有时：取项目文案里的“口播正文”写成 审核/文案.txt 去对稿（2026-11-20 Q131）。
    取不到就停下说清楚，不悄悄跳过对稿；标题、封面文字、### 小标题不拿去对，免得误报“漏念”。"""
    how='在文案里口播开始的地方加「## 口播正文」小标题，或用 --script 指定纯文本文案，再运行'
    doc=project_script_doc(folder,run)
    if doc is None:raise SystemExit('没有对稿文案：这个剪辑目录不在标准项目里（往上找不到 项目.json 和 01_原始素材），找不到项目文案。用 --script 指定纯文本文案再运行。')
    if not doc.is_file():raise SystemExit(f'没有对稿文案：缺项目文案 {doc}。把定稿全文存成这个文件（文件名和项目名一样），{how}。')
    if not SCRIPT_DOC.is_file():raise SystemExit(f'找不到文案解析程序 {SCRIPT_DOC}（视频项目管理）；先恢复它，或用 --script 指定纯文本文案。')
    import importlib.util
    spec=importlib.util.spec_from_file_location('thinkcut_script_doc',SCRIPT_DOC);parser=importlib.util.module_from_spec(spec);spec.loader.exec_module(parser)
    text=parser.read(doc)['口播正文']
    if not text:raise SystemExit(f'没有对稿文案：{doc.name} 里认不出“口播正文”（文案里有别的 ## 小标题，但没有「## 口播正文」）。{how}。')
    atomic_text(run/'文案.txt',text+'\n')
    print(f'对稿文案：取自 {doc.name} 的“口播正文”（{parser.count(text)} 字），存成 审核/文案.txt。')
    return run/'文案.txt'


def finish(folder,run,args):
    """Review + script comparison + open the 审片台 at its 文案 column, then print a Chinese summary."""
    if not (run/'review_decisions.json').exists():raise SystemExit('还没有 审核/review_decisions.json；先完成选择再运行 finish。')
    # 先定对稿文案：取不到就在等对齐之前停下
    script=args.script or ((run/'文案.txt') if (run/'文案.txt').exists() else default_script(folder,run))
    wait_for_alignment(run)
    if not (run/'review_v2_state.json').exists():run_self(self_command('review',folder,args))
    elif (run/'review_v2.json').exists() and (run/'review_decisions.json').stat().st_mtime>(run/'review_v2.json').stat().st_mtime:
        print('注意：review_decisions.json 比已生成的审核新，这次没有按它重新生成（不覆盖已有审核）。要按改好的选择重做：先 reopen 再 finish（2026-11-22 新12）。')
    run_self(self_command('script',folder,args,*(['--script',script] if args.script else [])))
    # Word times run tight: cut points follow the measured audio and pauses keep the chosen rhythm (2026-10-23).
    if json.loads((run/'review_v2_state.json').read_text()).get('natural_pause_mode')=='sentence':run_self(self_command('refine',folder,args))
    run_self(self_command('proofread',folder,args))
    state=json.loads((run/'review_v2_state.json').read_text())
    pending='subtitle_proofread' in state and not state['subtitle_proofread']
    # Open the page for the user only after proofreading, so they never review stale subtitles.
    if not args.no_serve and not pending:run_self(self_command('serve',folder,args,*(['--no-open'] if args.no_open else [])))
    full=json.loads((run/'review_v2_full.json').read_text())
    lines=[f"初剪完成：保留 {full['seconds']:.1f} 秒。"]
    report=run/'文案对照.json'
    if script and report.exists():
        r=json.loads(report.read_text())
        lines.append(f"文案对照：{len(r['candidates'])} 处与文案不同（校对时一并参考）。")
        if r['possibly_missing']:lines.append('文案里可能没念到：'+'；'.join(r['possibly_missing']))
    uncertain=[r for r in state.get('repeat_edits',{}).get('candidates',[]) if r['status']=='review']
    if uncertain:
        lines.append('重复词已保留，需 AI 语义复核：'+'；'.join(r['before_text']+'（'+r['reason']+'）' for r in uncertain))
    if pending:
        lines.append('下一步：按 审核/字幕校对.json 逐句校对字幕，用 corrections --file … --done 写入并标记完成，再运行 serve 打开审片台“口播剪辑”栏给用户（未标记前不能导入达芬奇）。')
    print('\n'.join(lines))


def cleanable(run):
    """Regenerable files a finished project no longer needs: (path, bytes, why)."""
    def size(path):return sum(f.stat().st_size for f in path.rglob('*') if f.is_file()) if path.is_dir() else path.stat().st_size
    items=[]
    if (run/'audio_asr.wav').exists() and (run/'character_alignment.json').exists():
        items.append((run/'audio_asr.wav','对齐用的音频，需要时会从 转写/source_audio.mp3 重新生成'))
    if (run/'preview_parts').is_dir():items.append((run/'preview_parts','网页预览缓存，打开网页时按需重新生成'))
    # Delivery packages are kept: they hold the pre-import project backup and let later saves recognise
    # hand-edited main lines. They are small (about 1–2 MB each).
    newest=lambda paths,keep:sorted(paths,key=lambda p:p.stat().st_mtime)[:-keep] if len(paths)>keep else []
    items+=[(p,'旧的保存日志') for p in newest(list(run.glob('resolve_sync_*.log')),3)]
    items+=[(p,'较早的审核状态备份（保留最近 20 份）') for p in newest(list((run/'review_state_backups').glob('*.json')) if (run/'review_state_backups').is_dir() else [],20)]
    return [(path,size(path),why) for path,why in items]


def clean(run,yes):
    from prepare_mvp_delivery import delivering
    if service_info(run) or delivering(run):raise SystemExit('剪口播后台还开着或正在保存到达芬奇；先 stop 停掉后台、等保存完成再清理。')
    items=cleanable(run)
    if not items:print('没有可以清理的内容。');return
    for path,size,why in items:print(f'{size/1e6:8.1f} MB  {path.relative_to(run.parent)}  —— {why}')
    print(f'合计 {sum(s for _,s,_ in items)/1e6:.1f} MB。')
    if not yes:print('以上内容都能重新生成，不影响剪辑结果。确认删除请加 --yes 重新运行。');return
    for path,_,_ in items:shutil.rmtree(path) if path.is_dir() else path.unlink()
    print('已清理。')


def replace_source_command(run,new,old):
    """同帧数换源：新口播（新颜色/新声音、帧数不变）检查通过后接着用原审核状态（2026-11-12《水电阶段》）。"""
    from prepare_mvp_delivery import delivering
    if not (run/'project_runtime.json').is_file():raise SystemExit('项目尚未初始化，不用换源：直接用新文件 prepare')
    info=service_info(run)
    if info or delivering(run):raise SystemExit('剪口播后台还开着或正在保存到达芬奇；先 stop 停掉后台、等保存完成再换源')
    config=json.loads((run/'project_runtime.json').read_text())
    print('核对新口播：帧数、帧率、尺寸、声音时间'+('、逐帧抽查画面' if old else '')+'…',flush=True)
    try:updated,entry=replace_source(config,run,new,old=old)
    except SourceChanged as error:raise SystemExit('没有换源：'+str(error)) from None
    checks=entry['checks']
    lines=[f"已换源：{Path(updated['source']).name}（{checks['frames']} 帧，和原来一样）。"]
    offsets=[c['offset_ms'] for c in checks.get('audio_checks',[])]
    if offsets:lines.append(f"声音 {len(offsets)} 处和转写时对齐（偏差 {min(offsets)}～{max(offsets)} 毫秒），字时间照用。")
    if checks.get('frame_checks'):lines.append(f"画面 {len(checks['frame_checks'])} 处和旧片同一帧。")
    else:lines.append('没给旧片（--old），画面只核对了帧数；颜色变了没关系，换了别的镜头会对不上，请自己确认是同一条口播。')
    if updated.get('media_pool_item_id') is None and config.get('media_pool_item_id'):
        lines.append('新文件换了路径：下次保存到达芬奇时会把新文件导入媒体池，新主线用新文件；旧主线不动。')
    else:
        lines.append('路径没变：达芬奇里如果画面还是旧的，在媒体池对这个素材点“重新链接”或重开工程。')
    lines.append('审核状态、删句、字幕校对都保留；记录在 转写/换源记录.json。可以 serve 打开网页接着改、deliver 出新主线和 SRT。')
    print('\n'.join(lines))


# 审核回退（2026-11-22 261122 反馈单 新12）：审核（review_v2_state.json）一生成，review 就拒绝重做（“Review already exists”），
# 删错的字（AI 手写的 review_decisions.json 选错位置）没法按改好的选择重新生成，只能另开 _第2次 目录。
# reopen：用户还没在审片台口播剪辑栏动过这一版时，把这一版审核挪进 review_state_backups/回退前_<时间>/（不删），
# 字时间换回对齐原样（alignment_qwen_full.json），之后改好 review_decisions.json 照常 finish。已经存进达芬奇的主线不动（留旧版），再保存是新的一条主线。
# 用户已经在页面上改过的不回退（会丢掉用户的修改）：请用户或 AI 在审片台口播剪辑栏直接恢复删错的字、删该删的字。
REVIEW_FILES=('review_v2.json','review_v2_state.json','review_v2_full.json','trial_keep_segments.json','字幕校对.json','文案对照.json','气口精修.json')


def user_changes(state):
    """用户在审片台口播剪辑栏动过这一版的痕迹（回退会丢掉这些）。AI 写的字幕纠错不算，回退时另存一份。"""
    editor=state.get('editor') or {}
    why=[]
    if editor.get('operations'):why.append(f"文字剪辑器里改过 {len(editor['operations'])} 步")
    if state.get('overrides'):why.append('改过删改卡片的取舍')
    if state.get('notes'):why.append('写过备注')
    if state.get('saved_at'):why.append(f"页面上保存过修改（{state['saved_at'][:19]}）")
    if state.get('confirmed_at'):why.append('确认过')
    return why


def reopen(folder,run):
    from prepare_mvp_delivery import delivering
    state_path=run/'review_v2_state.json'
    if not state_path.exists():raise SystemExit('这个项目还没生成审核，不用回退：改好 审核/review_decisions.json 直接运行 finish。')
    if service_info(run) or delivering(run):raise SystemExit('剪口播后台还开着或正在保存到达芬奇；先 stop 停掉后台、等保存完成再 reopen。')
    if not (run/'alignment_qwen_full.json').is_file():raise SystemExit('找不到对齐原样 审核/alignment_qwen_full.json，没法回退；请在审片台口播剪辑栏直接改。')
    state=json.loads(state_path.read_text())
    why=user_changes(state)
    if why:
        raise SystemExit('没有回退：这一版用户已经在审片台口播剪辑栏动过（'+'；'.join(why)+'），回退会把这些丢掉。'
                         '删错的字直接在审片台口播剪辑栏改：选中删错的字点“恢复”，再选中真正要删的字点“删除”，然后照常保存到达芬奇。')
    keep=run/'review_state_backups'/('回退前_'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    with delivery_lock(run):
        keep.mkdir(parents=True,mode=0o700)
        moved=[name for name in REVIEW_FILES if (run/name).exists()]
        shutil.copy2(run/'character_alignment.json',keep/'character_alignment.json')
        if (run/'review_decisions.json').exists():shutil.copy2(run/'review_decisions.json',keep/'review_decisions.json')
        corrections=state.get('subtitle_token_corrections') or []
        if corrections:atomic_text(keep/'字幕纠错.json',json.dumps(corrections,ensure_ascii=False,indent=2))
        for name in moved:shutil.move(str(run/name),str(keep/name))
        shutil.copy2(run/'alignment_qwen_full.json',run/'character_alignment.json')
    lines=[f'已回退到“还没生成审核”：这一版审核挪到了 {keep.relative_to(folder)}（没删）。',
           '下一步：改好 审核/review_decisions.json，运行 finish 重新生成审核、对稿、精修气口、出字幕校对单，再照常校对字幕、打开审片台。']
    if corrections:lines.append(f'这一版写过 {len(corrections)} 条字幕纠错：finish 后用 corrections --file "{keep/"字幕纠错.json"}" 再写回（字编号没变），核对一遍再 --done。')
    if (run/'latest_delivery.json').exists() or (run/'mvp_deliveries').is_dir():
        lines.append('以前保存到达芬奇的主线不动（留着旧版）；改完再保存会是一条新的主线。')
    print('\n'.join(lines))


def default_project_dir(video):
    """Only choose a location; never move an existing editing project."""
    video = Path(video).expanduser().resolve()
    legacy = video.parent / (video.stem + '_AI剪辑')
    if legacy.is_dir():
        return legacy
    for parent in video.parents:
        # 标准项目（有 项目.json 和 01_原始素材）：放 02_制作，别在原始素材里多出文件（2026-10-24）
        if (parent / '项目.json').is_file() and (parent / '01_原始素材').is_dir() and (parent / '01_原始素材') in video.parents:
            return parent / '02_制作' / (video.stem + '_AI剪辑')
    return legacy


def main():
    parser=argparse.ArgumentParser(description='新片：初始化、声学对齐、生成审核、文案对照、打开审片台口播剪辑栏、MCP 交付；不渲染')
    parser.add_argument('action',choices=['prepare','finish','init','align','review','script','proofread','refine','corrections','serve','stop','status','deliver','clean','replace-source','reopen'])
    parser.add_argument('--project-dir',type=Path,help='项目目录；新项目不填时按素材项目结构选择，其他动作必填')
    parser.add_argument('--video',type=Path,help='prepare/init：原片；replace-source：同帧数的新口播')
    parser.add_argument('--old',type=Path,help='replace-source：换源前的旧口播文件（给了就逐帧抽查画面）')
    parser.add_argument('--audio-stream',type=int)
    parser.add_argument('--retry-uncertain',action='store_true',help='prepare/init：上次识别请求没有完整结果（可能已计费）时，用户核对过豆包用量、同意重试才加')
    parser.add_argument('--align-python',type=Path)
    parser.add_argument('--port',type=int,help='serve：指定端口；不填则自动找空闲端口')
    parser.add_argument('--no-open',action='store_true',help='serve：不自动在浏览器打开审片台')
    parser.add_argument('--backend',action='store_true',help='serve：只起剪口播后台（审片台按需调用，不打开审片台）')
    parser.add_argument('--no-serve',action='store_true',help='finish：不打开审片台')
    parser.add_argument('--yes',action='store_true',help='clean：确认删除')
    parser.add_argument('--done',action='store_true',help='corrections：标记字幕校对完成')
    parser.add_argument('--force',action='store_true',help='refine：已精修过也重算；stop：清掉另一台 Mac 留下的剪口播后台记录')
    parser.add_argument('--script',type=Path,help='finish/script：纯文本文案文件；不写时用 审核/文案.txt，没有就取项目文案 02_制作/<项目名>.md 的“口播正文”')
    parser.add_argument('--file',type=Path,help='corrections：字幕纠错 JSON 列表')
    args=parser.parse_args()
    if args.project_dir is None:
        if args.action not in ('prepare','init') or not args.video:
            parser.error('此动作需要 --project-dir；新项目 prepare/init 可用 --video 自动选择目录')
        args.project_dir=default_project_dir(args.video)
    folder=args.project_dir.expanduser().resolve();run=folder/'审核'
    if folder==ROOT or ROOT in folder.parents:parser.error('项目不能放入共享能力目录')
    env={**os.environ,'AI_EDIT_RUN_DIR':str(run)}
    if args.action=='prepare':
        if not args.video and not (run/'project_runtime.json').exists():parser.error('prepare 需要 --video')
        prepare(folder,run,args)
    elif args.action=='finish':finish(folder,run,args)
    elif args.action=='init':
        if not args.video:parser.error('初始化需要 --video')
        video=args.video.expanduser().resolve(strict=True)
        info=probe(video);track=next(t for t in info['tracks'] if t['handler']=='vide')
        if track.get('fps')!={'num':30000,'den':1001} or not track.get('constant_presentation_duration'):
            parser.error('本版剪切引擎只支持恒定 30000/1001 fps；不强行套用其他帧率')
        # 三个文件都在才算已初始化；上次写到一半就断了（没有审核状态）时放行，下面整套重写补齐
        if initialized(run) or (run/'review_v2_state.json').exists():parser.error('项目已初始化；不要覆盖审核状态')
        single_audio(info,args.audio_stream or 0)
        # Video edit lists normally skip one frame of B-frame delay; an audio start offset (phone AAC priming) is rarer.
        audio=next(t for t in info['tracks'] if t['handler']=='soun')
        edits=audio.get('edits') or []
        offset_ms=round(sum(e['media_time'] for e in edits if e['media_time']>0)/audio.get('timescale',1)*1000,1) if edits else 0
        # An empty edit delays the whole audio track: transcription times start at the first sample,
        # Resolve plays it later, so every cut lands early and swallows words (2026-10-23).
        empty_ms=round(sum(e['movie_duration'] for e in edits if e['media_time']==-1)/info.get('movie_timescale',1)*1000,1)
        if empty_ms>1001/30:
            parser.error(f'原片声音比画面晚 {empty_ms} 毫秒才开始（音轨开头有空白编辑）。直接剪会让识别时间和达芬奇画面错位，刀口全部偏早、吞字。'
                         '请先重新转码，把这段空白补成静音、让声音和画面都从 0 开始（如 ffmpeg 音频加 aresample=async=1:first_pts=0），再用新文件建项目')
        if any(e['media_time']==-1 for e in edits) or offset_ms>1001/30:
            print(f'提示：原片音频带起始偏移（约 {offset_ms} 毫秒或空白开头）。剪完请在达芬奇里听一下口型和声音是否对齐。')
        alignment=alignment_runtime(args.align_python)
        if not alignment['alignment_dependencies_ready']:parser.error(alignment['alignment_error'])
        run.mkdir(parents=True,exist_ok=True,mode=0o700)
        run_self(transcribe_command(video,folder,args))  # transcription prints its own Chinese errors
        manifest=json.loads((folder/'转写/transcription_manifest.json').read_text())
        response=json.loads((folder/'转写/doubao_turbo.json').read_text())
        name=main_line_name(folder)
        config={'name':name,'source':str(video),'source_frames':track['sample_count'],'fps':[30000,1001],'resolution':track['display_size'],'audio_edit_offset_ms':offset_ms,
                'audio_stream':manifest['audio_stream'],'media_pool_item_id':None,
                **resolve_project(folder,video,name),'sections':[{'start':0,'title':'口播全文'}]}
        if machine_voice(video):      # 记下“这是机器配音占位”：后面的分镜不排露脸镜头，审片台标出来；换真口播时是新建一个剪口播工程
            config['placeholder']={'kind':'机器配音','record':str(machine_voice(video)),'note':'没有口播录像，豆包语音合成照定稿文案念的占位配音；用户录了真口播后换掉'}
        units=[{'id':i,'text':x['text'],'context_text':x['text'],'start_ms':x['start_time'],'end_ms':x['end_time'],'project_generic':True} for i,x in enumerate(response['result']['utterances'])]
        write_init_files(run,config,units)
    elif args.action=='align':
        alignment=alignment_runtime(args.align_python)
        if not alignment['alignment_dependencies_ready']:parser.error(alignment['alignment_error'])
        python=Path(alignment['alignment_python'])
        verify_source(json.loads((run/'project_runtime.json').read_text()),run)
        wav=run/'audio_asr.wav'
        ffmpeg=shutil.which('ffmpeg')
        if not wav.exists() and not ffmpeg:raise SystemExit('这台 Mac 找不到 ffmpeg（没装，或不在 PATH 里）：运行 bash bin/setup_mac.sh 装好后再运行 align。')
        if not wav.exists():subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-nostdin','-n','-i',str(folder/'转写/source_audio.mp3'),'-ac','1','-ar','16000','-c:a','pcm_s16le',str(wav)],check=True)
        execute('align_local.py',env,python)
    elif args.action=='review':execute('prepare_new_review.py',env)
    elif args.action=='script':
        if not args.script and not (run/'文案.txt').exists():default_script(folder,run)
        execute('script_check.py',env,sys.executable,*(['--script',args.script.expanduser().resolve(strict=True)] if args.script else []))
    elif args.action=='corrections':
        if not args.file and not args.done:parser.error('corrections 需要 --file，或用 --done 标记校对完成')
        extra=(['--corrections',args.file.expanduser().resolve(strict=True)] if args.file else [])+(['--done'] if args.done else [])
        execute('script_check.py',env,sys.executable,*extra)
    elif args.action=='proofread':execute('script_check.py',env,sys.executable,'--sheet')
    elif args.action=='refine':
        if args.align_python:env['AI_EDIT_ALIGN_PYTHON']=str(args.align_python)   # 语音检测用对齐环境的 Python（2026-11-22）
        execute('pause_refine.py',env,sys.executable,*(['--force'] if args.force else []))
    elif args.action=='serve':serve(run,env,args)
    elif args.action=='stop':stop(run,args.force)
    elif args.action=='clean':clean(run,args.yes)
    elif args.action=='reopen':reopen(folder,run)
    elif args.action=='replace-source':
        if not args.video:parser.error('replace-source 需要 --video <新口播>')
        replace_source_command(run,args.video.expanduser().resolve(strict=True),args.old.expanduser().resolve(strict=True) if args.old else None)
    elif args.action=='status':
        info=service_info(run)
        print('没有运行' if not info else (f"在另一台 Mac（{info['host']}）上运行" if info.get('other_host') else f"运行中：{info['url']}，版本 {info.get('version')}，启动于 {info.get('started')}"))
    else:
        from prepare_mvp_delivery import exclusive_lock
        # Resolve has one active project per local app: serialize all projects.
        # Refuse unproofread subtitles before anything touches Resolve.
        execute('script_check.py',env,sys.executable,'--check')
        with exclusive_lock(Path.home()/'.cache/ai-talking-head-edit/resolve-delivery.lock'),\
             resolve_write_lock(run),delivery_lock(run):
            python=deliver_preflight(run)
            prepare_resolve(run)
            execute('setup_new_resolve.py',env,python)
            execute('run_mvp.py',env)

def explain_failure(details):
    import errors_zh
    try:
        project=Path(sys.argv[sys.argv.index('--project-dir')+1]).expanduser().resolve()/'审核'
        log=project/'错误日志.txt' if project.is_dir() else None
    except (ValueError,IndexError):log=None
    if log:
        with log.open('a',encoding='utf-8') as stream:stream.write(f"\n===== {datetime.now().isoformat(timespec='seconds')} {' '.join(sys.argv[1:2])}\n{details}\n")
    print(errors_zh.report(details,log),file=sys.stderr)
    raise SystemExit(1)


if __name__=='__main__':
    try:main()
    except StepFailed as failure:explain_failure(failure.details)
    except (KeyboardInterrupt,SystemExit):raise
    except Exception:
        import traceback;explain_failure(traceback.format_exc())
