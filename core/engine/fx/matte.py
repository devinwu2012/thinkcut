"""Automatic RVM foregrounds. 成品、缓存结果和日志都留在片子工作目录的 抠像/ 下；用完即删的原始帧放本机临时目录。"""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import math
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from . import ledger
from .snapshot import write_json

WEIGHTS_SHA256 = '3c7c1d92033f7c38d6577c481d13a195d7d80a159b960f4f3119ac7b534cf4f8'
CODE_COMMIT = '53d74c6'
MODEL_VERSION = 'rvm-mobilenetv3-v1.0.0-' + WEIGHTS_SHA256
FORMAT_VERSION = 1
TRACK = '人物抠像'
# 抠像 worker 的时限。抠像本来就慢：约 30 秒处理 1 秒画面（skills/fx/SKILL.md），也就是每帧约 1 秒；
# 按每帧 6 秒给（约 6 倍），再短的片段也至少给 30 分钟（装载模型、从 NAS 读原片）。到了还没完基本就是卡住了。
MATTE_SECONDS_PER_FRAME = 6
MATTE_MIN_SECONDS = 30*60
# 原始帧临时文件很大（4K 约 116MB 一帧，3 秒约 10GB），写 NAS 很慢：放本机系统临时目录，每次一个这个前缀的专用文件夹，用完就删（2026-11-21 用户定）。
FRAMES_PREFIX = 'thinkcut-rvm-frames-'
FRAMES_UNLOCKED_GRACE_SECONDS = 60   # 刚建好、还没来得及上锁的文件夹：先不当残留


def environment_root():
    sys.path.append(str(Path(__file__).resolve().parents[3]/'bin')); import thinkcut_config      # 本机文件夹名（含旧名兼容）
    return thinkcut_config.app_dir()/'环境'/thinkcut_config.env_name('matting')   # 2026-10-29：NAS 原件的 Mac 运行副本（macOS 27.2 不准运行 NAS 上的程序），bin/sync_env.sh 恢复


def environment_check(root=None):
    root = Path(root or environment_root())
    weights = root/'weights/rvm_mobilenetv3.pth'
    try:
        if not weights.is_file() or hashlib.sha256(weights.read_bytes()).hexdigest() != WEIGHTS_SHA256:
            return False, 'RVM 权重缺失或SHA-256不符'
        commit = subprocess.check_output(['git','-C',str(root/'RobustVideoMatting'),'rev-parse','HEAD'], text=True, timeout=10).strip()
        if not commit.startswith(CODE_COMMIT):
            return False, 'RVM代码版本不符'
        p = subprocess.run([str(root/'venv/bin/python'), '-c', 'import torch, torchvision, numpy, PIL'], capture_output=True, timeout=30)
        return (True, None) if p.returncode == 0 else (False, '独立抠像环境依赖不可用')
    except (OSError, subprocess.SubprocessError):
        return False, '独立抠像环境缺失或检查超时'


def frames_root():
    return Path(tempfile.gettempdir())


def clear_stale_frames(root=None):
    """清掉上次崩溃或超时留下的原始帧文件夹；别的抠像正在用的（它还拿着里面 .lock 的文件锁）不动。返回清掉的路径。"""
    removed = []
    for path in sorted(Path(root or frames_root()).glob(FRAMES_PREFIX+'*')):
        try:
            if path.is_symlink() or not path.is_dir(): continue
            lock = path/'.lock'
            if not lock.is_file():
                if time.time()-path.stat().st_mtime < FRAMES_UNLOCKED_GRACE_SECONDS: continue
                shutil.rmtree(path); removed.append(str(path)); continue
            with lock.open('a') as handle:
                try: fcntl.flock(handle, fcntl.LOCK_EX|fcntl.LOCK_NB)
                except OSError: continue      # 另一个抠像正在用
                shutil.rmtree(path); removed.append(str(path))
        except OSError:
            continue
    return removed


@contextmanager
def frame_directory(root=None):
    """本机临时目录下的一个专用原始帧文件夹：开始前先清残留，用的时候拿着里面的文件锁（进程死了系统自动放锁），用完删掉。"""
    root = Path(root or frames_root())
    clear_stale_frames(root)
    for attempt in range(3):
        path = Path(tempfile.mkdtemp(prefix=FRAMES_PREFIX, dir=root))
        handle = (path/'.lock').open('w')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            # 极少见：刚建好就被另一个抠像当成残留抢先上锁清掉了，换一个文件夹再来
            handle.close(); shutil.rmtree(path, ignore_errors=True)
            if attempt == 2: raise
            continue
        break
    try:
        yield path
    finally:
        handle.close()
        shutil.rmtree(path, ignore_errors=True)


def cache_key(path, start, end, width, height, retime=None):
    path = Path(path).resolve(); stat = path.stat()
    return ledger.digest({'path':str(path),'size':stat.st_size,'mtime_ns':stat.st_mtime_ns,
                         'start':start,'end':end,'model':MODEL_VERSION,'format':FORMAT_VERSION,
                         'width':width,'height':height,'fps':'30000/1001',**({'retime':retime} if retime else {})})


def cached(directory, key):
    base = Path(directory)/key
    try:
        meta = json.loads(base.with_suffix('.json').read_text())
        mov, masks = base.with_suffix('.mov'), base.with_suffix('.npz')
        if meta['cache_key'] == key and mov.stat().st_size == meta['bytes'] and masks.stat().st_size == meta['mask_bytes']:
            return {**meta,'path':str(mov),'masks':str(masks),'cache_hit':True}
    except (OSError, KeyError, ValueError):
        pass
    return None


def generate(path, start, end, width, height, work_dir, retime=None):
    """retime：V1 是在达芬奇里设了速度的原片（2026-11-27 起口播 1.2 倍速这样放）时 {'seconds': 原片起点秒, 'speed': 倍速}，
    按原片时间取帧、加速成 29.97 帧再抠；没有时按 29.97 帧原片的帧号取。"""
    directory = Path(work_dir)/'抠像'; directory.mkdir(parents=True,exist_ok=True)
    key = cache_key(path,start,end,width,height,retime)
    result = cached(directory,key)
    if result: return result
    ready, reason = environment_check()
    if not ready: raise RuntimeError(reason+'；运行 bash bin/setup_mac.sh 自动准备')
    request = {'source':str(path),'start':start,'end':end,'width':width,'height':height,
               'directory':str(directory),'cache_key':key,**({'retime':retime} if retime else {})}
    # Passing JSON on stdin avoids exposing paths in shell interpolation.
    limit = max(MATTE_MIN_SECONDS,(end-start)*MATTE_SECONDS_PER_FRAME)
    try:
        p = subprocess.run([str(environment_root()/'venv/bin/python'),'-m','engine.fx.matte','--worker'],
                           input=json.dumps(request),text=True,capture_output=True,
                           cwd=str(Path(__file__).resolve().parents[2]),timeout=limit)
    except subprocess.TimeoutExpired as exc:
        said = [v.decode('utf-8','replace') if isinstance(v,bytes) else v or '' for v in (exc.stdout,exc.stderr)]
        (directory/(key+'.log')).write_text(''.join(said)+f'\n超过 {limit} 秒没有完成，已结束抠像进程\n')
        clear_stale_frames()      # 被结束的进程已经放了锁：把它没做完的原始帧清掉，别的抠像正在用的不动
        raise RuntimeError(f'自动抠像超过 {limit//60} 分钟还没完成（{end-start} 帧），已停止这次抠像，见 '+str(directory/(key+'.log'))
                           +f'；下一步：确认 NAS 和原片能正常读后重试。没做完的临时文件放在这台 Mac 的临时目录 {frames_root()} 里 {FRAMES_PREFIX} 开头的文件夹，'
                           '已自动清理；还有残留的话下次开始抠像时会自动清掉（别的抠像正在用的不会动），不用手动删') from None
    (directory/(key+'.log')).write_text(p.stdout+p.stderr)
    if p.returncode: raise RuntimeError('自动抠像失败，见 '+str(directory/(key+'.log')))
    result = cached(directory,key)
    if not result: raise RuntimeError('抠像输出没有通过缓存完整性检查')
    return {**result,'cache_hit':False}


def worker(request):
    import numpy as np
    import torch
    from PIL import Image
    torch.set_num_threads(4)
    root = environment_root(); sys.path.insert(0,str(root/'RobustVideoMatting'))
    from model import MattingNetwork
    device = 'mps' if torch.backends.mps.is_available() else 'cpu'
    model = MattingNetwork('mobilenetv3').eval().to(device)
    model.load_state_dict(torch.load(root/'weights/rvm_mobilenetv3.pth',map_location=device,weights_only=True))
    w,h = request['width'],request['height']; count = request['end']-request['start']
    base = Path(request['directory'])/request['cache_key']; tmp = base.with_suffix('.partial.mov')
    # Decode original RGB48 with Full range; ignore the model's reconstructed RGB.
    retime = request.get('retime')
    seek = retime['seconds'] if retime else request['start']*1001/30000
    speedup = f'setpts=(PTS-STARTPTS)/{retime["speed"]:g},fps=30000/1001,' if retime else ''
    decode = ['ffmpeg','-v','error','-threads','4','-filter_threads','4','-ss',f'{seek:.9f}','-i',request['source'],
              '-map','0:v:0','-frames:v',str(count),'-vf',f'{speedup}scale={w}:{h}:in_range=full:out_range=full:in_color_matrix=bt709:flags=accurate_rnd+full_chroma_int',
              '-f','rawvideo','-pix_fmt','rgb48le','-threads','4','-']
    encode = ['ffmpeg','-v','error','-y','-threads','4','-filter_threads','4','-f','rawvideo','-pix_fmt','rgba64le','-s',f'{w}x{h}',
              '-framerate','30000/1001','-i','-','-frames:v',str(count),'-vf',
              'scale=in_range=full:out_range=full:out_color_matrix=bt709:flags=accurate_rnd+full_chroma_int',
              '-c:v','prores_ks','-profile:v','4','-pix_fmt','yuva444p10le','-alpha_bits','16','-qscale:v','5',
              '-vendor','apl0','-color_range','pc','-colorspace','bt709','-color_primaries','bt709','-color_trc','bt709','-threads','4',str(tmp)]
    started = time.perf_counter(); masks=[]; rec=[None]*4
    # One FFmpeg process at a time; inference also runs after decoding finishes.
    # Temporary raw frames are private generated data, never imported into Resolve.
    # 它们写在本机临时目录（不写片子目录所在的 NAS）；成品、缓存结果、日志照旧写在 request['directory']（片子的 抠像/）。
    with frame_directory() as temporary:
        rgb_path=Path(temporary)/'rgb.raw';rgba_path=Path(temporary)/'rgba.raw'
        decode[-1]=str(rgb_path)
        encode[encode.index('-i')+1]=str(rgba_path)
        with base.with_suffix('.decode.log').open('wb') as dl:
            subprocess.run(decode,stderr=dl,check=True)
        with rgb_path.open('rb') as decoded, rgba_path.open('wb') as encoded:
            with torch.inference_mode():
                for n in range(count):
                    data=decoded.read(w*h*6)
                    if len(data)!=w*h*6: raise RuntimeError(f'源帧不足：{n}/{count}')
                    rgb=np.frombuffer(data,dtype='<u2').reshape(h,w,3)
                    src=torch.from_numpy(rgb.astype(np.float32)/65535).permute(2,0,1).unsqueeze(0).to(device)
                    _,alpha,*rec=model(src,*rec,min(1,512/max(w,h)))
                    a=(alpha[0,0].clamp(0,1).cpu().numpy()*65535).round().astype('<u2')
                    rgba=np.empty((h,w,4),dtype='<u2');rgba[:,:,:3]=rgb;rgba[:,:,3]=a
                    encoded.write(rgba.tobytes())
                    masks.append(np.asarray(Image.fromarray((a/257).astype('uint8')).resize((720,round(720*h/w)),Image.Resampling.BOX)))
                    if (n+1)%20==0: print(f'{n+1}/{count}',flush=True)
        rgb_path.unlink()
        with base.with_suffix('.encode.log').open('wb') as el:
            subprocess.run(encode,stderr=el,check=True)
    probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(tmp)],text=True))['streams'][0]
    if (probe['width'],probe['height'],int(probe['nb_frames'])) != (w,h,count) or 'a' not in probe['pix_fmt']:
        raise RuntimeError('透明ProRes分辨率、帧数或Alpha读回不符')
    np.savez_compressed(base.with_suffix('.npz'),alpha=np.stack(masks))
    tmp.replace(base.with_suffix('.mov'))
    meta={**request,'model_version':MODEL_VERSION,'device':device,'elapsed_seconds':time.perf_counter()-started,
          'bytes':base.with_suffix('.mov').stat().st_size,'mask_bytes':base.with_suffix('.npz').stat().st_size,
          'probe':probe,'intermediate_png_bytes':0,'max_ffmpeg_processes':1,
          'serial_stages':['decode','infer','encode'],'temporary_raw_peak_bytes_estimate':w*h*count*14,
          'rgb_policy':'source RGB, RVM alpha only'}
    write_json(base.with_suffix('.json'),meta)


def coverage(mask, bbox):
    """Alpha-weighted area of a normalized bottom-left text rectangle, clipped to screen."""
    import numpy as np
    h,w=mask.shape
    x0,y0,x1,y1=bbox
    left,right=max(0,math.floor(x0*w)),min(w,math.ceil(x1*w))
    top,bottom=max(0,math.floor((1-y1)*h)),min(h,math.ceil((1-y0)*h))
    area=max(1,(right-left)*(bottom-top))
    return float(np.asarray(mask[top:bottom,left:right],dtype=float).sum()/255/area)


def decision(ratio, limit=.2):
    if not 0 <= limit <= 1 or not 0 <= ratio <= 1: raise ValueError('覆盖比例须在0到1之间')
    return 'none' if ratio == 0 else 'matte' if ratio <= limit else 'move'


def geometry(source, timeline):
    media=source.GetMediaPoolItem(); sw,sh=map(int,media.GetClipProperty('Resolution').split('x'))
    tw,th=map(int,(timeline.GetSetting('timelineResolutionWidth'),timeline.GetSetting('timelineResolutionHeight')))
    props=source.GetProperty(); mode=timeline.GetSetting('timelineInputResMismatchBehavior')
    if props.get('Opacity',100)!=100 or props.get('CompositeMode',0)!=0:
        raise ValueError('非正常合成或半透明V1不能重复叠放')
    if props.get('Scaling',0)!=0: raise ValueError('片段单独输入缩放尚未校准')
    if any(props.get(k,0) for k in ('RotationAngle','Pitch','Yaw','Distortion','CropLeft','CropRight','CropTop','CropBottom')) or props.get('DynamicZoomEnabled'):
        raise ValueError('旋转、裁切、动态缩放或透视未校准')
    if any(props.get(k,0) for k in ('AnchorPointX','AnchorPointY')): raise ValueError('非中心锚点尚未校准')
    scale={'scaleToCrop':max(tw/sw,th/sh),'scaleToFit':min(tw/sw,th/sh),'centerCrop':1}.get(mode)
    if scale is None: raise ValueError('未知输入缩放：'+str(mode))
    transform=dict(props);transform.update(ZoomGang=False,ZoomX=props['ZoomX']*sw*scale/tw,ZoomY=props['ZoomY']*sh*scale/th,
                                          Pan=props.get('Pan',0)*sw*scale/tw,Tilt=props.get('Tilt',0)*sh*scale/th)
    return transform


def projected(mask, props, width, height):
    from PIL import Image
    import numpy as np
    mh,mw=mask.shape; zx,zy=props['ZoomX'],props['ZoomY']
    image=Image.fromarray(mask)
    if props.get('FlipX'):image=image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if props.get('FlipY'):image=image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    dx,dy=props.get('Pan',0)*mw/width,-props.get('Tilt',0)*mh/height
    return np.asarray(image.transform((mw,mh),Image.Transform.AFFINE,
                      (1/zx,0,mw/2-(mw/2+dx)/zx,0,1/zy,mh/2-(mh/2+dy)/zy),Image.Resampling.BILINEAR))


def clip_speed(item):
    """片段速度（1.2＝120%）；达芬奇 21.1 以前没有 GetSpeed，读不出返回 None。"""
    try:value=(item.GetSpeed() or {}).get('Percentage')
    except Exception:return None
    try:return float(value)/100 if value else None
    except (TypeError,ValueError):return None


def visible_segments(timeline, start, end):
    """Split at every upper-picture boundary, never matte over B-roll or photos."""
    if not timeline.GetIsTrackEnabled('video',1):return []
    v1=timeline.GetItemListInTrack('video',1) or []; upper=[]
    for track in range(2,timeline.GetTrackCount('video')+1):
        name=timeline.GetTrackName('video',track)
        if not timeline.GetIsTrackEnabled('video',track) or name==TRACK or re.fullmatch(r'花字(?:[0-9]+)?',name):continue
        for item in timeline.GetItemListInTrack('video',track) or []:
            if item.GetClipEnabled() and item.GetStart()<end and start<item.GetEnd():upper.append(item)
    points=sorted({start,end}|{max(start,min(end,v)) for i in v1+upper for v in (int(i.GetStart()),int(i.GetEnd()))})
    rows=[]
    for a,b in zip(points,points[1:]):
        hits=[i for i in v1 if i.GetClipEnabled() and i.GetStart()<=a and i.GetEnd()>=b]
        if len(hits)!=1 or any(i.GetStart()<b and a<i.GetEnd() for i in upper):continue
        i=hits[0];media=i.GetMediaPoolItem()
        if not media or not media.GetClipProperty('File Path'):continue
        fps=float(media.GetClipProperty('FPS'));speed=clip_speed(i)
        if speed is not None and speed!=1 and fps>=29.97-.01:
            # 2026-11-27 起口播原片放进达芬奇设 120%：来源起点（原片帧）换成秒，再按倍速往后走
            seconds=i.GetSourceStartFrame()/fps+(a-i.GetStart())*speed*1001/30000
            rows.append({'start':a,'end':b,'source_start':round(seconds*30000/1001),'retime':{'seconds':round(seconds,6),'speed':speed},'source':i})
            continue
        if abs(fps-29.97)>.01:continue
        # LeftOffset indexes the file; TC-based source starts are not safe file offsets.
        if int(i.GetSourceEndFrame()-i.GetSourceStartFrame())!=int(i.GetDuration()):
            raise ValueError('V1变速片段不能可靠抠像')
        rows.append({'start':a,'end':b,'source_start':int(i.GetLeftOffset()+a-i.GetStart()),'source':i})
    return rows


def prepare(io, plan, before, work_dir, preserve_ids=()):
    from .plan import config
    from .safety import estimate, check_layout
    import numpy as np
    cfg=config('fx_styles.json');limit=cfg.get('matting',{}).get('max_coverage',.2)
    result=deepcopy(plan); decisions=[]; placements=[]
    width,height=map(int,(before['timeline']['width'],before['timeline']['height']))
    for e in result['effects']:
        samples=[];assets=[]; reason=None
        try:parts=visible_segments(io.main,e['start'],e['end'])
        except ValueError as exc:parts=[];reason=str(exc)
        for part in parts:
            source=part['source']
            try:transform=geometry(source,io.main)
            except ValueError as exc:reason=str(exc);break
            asset=generate(source.GetMediaPoolItem().GetClipProperty('File Path'),part['source_start'],part['source_start']+part['end']-part['start'],width,height,work_dir,part.get('retime'))
            with np.load(asset['masks']) as data:
                # Temporal union is conservative: a letter must remain readable through the whole clip.
                mask=projected(data['alpha'].max(axis=0),transform,width,height)
            samples.append(mask);assets.append((part,asset,transform))
        row={'effect_id':e['effect_id'],'threshold':limit,'coverage_basis':'all-frame alpha union / text outer rectangle',
             'original_center':deepcopy(e['anchor']['center'])}
        if not parts or reason:
            decisions.append({**row,'action':'none','coverage':None,'reason':reason or 'V1被上层画面覆盖或没有可用源区间'});continue
        def ratio(effect):
            box=estimate(effect,width,height,cfg)['bbox']
            return max(coverage(mask,box) for mask in samples)
        original=ratio(e);row['original_coverage']=original;r=original;action=decision(r,limit)
        from .layout import ordinary
        beside=ordinary(e)
        if beside and r>0:action='move'
        if action=='move' and e['effect_id'] not in preserve_ids:
            # Search the nearest legal translation away from the alpha centroid; never reduce size.
            union=np.maximum.reduce(samples);ys,xs=np.where(union>127)
            cx,cy=(float(xs.mean()/union.shape[1]),float(1-ys.mean()/union.shape[0])) if len(xs) else (.5,.5)
            x,y=e['anchor']['center'];vectors=[(-1,0),(1,0),(0,1),(0,-1)]
            vectors.sort(key=lambda d: -((x-cx)*d[0]+(y-cy)*d[1]))
            for step in range(1,51):
                found=False
                for dx,dy in vectors:
                    candidate=deepcopy(e);candidate['anchor']['center']=[x+dx*step*.01,y+dy*step*.01]
                    if not all(0<=v<=1 for v in candidate['anchor']['center']):continue
                    safe=check_layout({'timeline':result['timeline'],'effects':[candidate]},before['timeline'],check_person_bbox=False)
                    if safe['violations']:continue
                    cr=ratio(candidate)
                    if cr<=(0 if beside else limit):
                        e.update(candidate);r=cr;action='matte' if cr>0 else 'none';found=True;break
                if found:break
        if action=='move' and beside and e['effect_id'] not in preserve_ids:
            raise ValueError('真实人物遮罩两侧没有安全留白，请指定布局：'+e['effect_id'])
        if action=='move':action='fallback'
        row.update(action=action,coverage=r,center=deepcopy(e['anchor']['center']),
                   moved=e['anchor']['center']!=row['original_center'],cache_hits=[a['cache_hit'] for _,a,_ in assets])
        if action=='fallback':row['reason']='覆盖超阈值且无法安全平移（或保留用户手调），按原方式盖在人上'
        decisions.append(row)
        if action=='matte':
            for part,asset,transform in assets:
                placements.append({'effect_ids':[e['effect_id']],'start':part['start'],'end':part['end'],
                                   'source_id':part['source'].GetUniqueId(),'path':asset['path'],'transform':transform,'asset':asset})
    # Overlapping effects use a single foreground, not duplicate transparent layers.
    merged=[]
    for row in sorted(placements,key=lambda x:(x['start'],x['end'])):
        if merged and row['start']<merged[-1]['end']:
            old=merged[-1]
            if row['source_id']!=old['source_id']:raise ValueError('重叠人物抠像来源不一致')
            end=max(old['end'],row['end']);source=next(i for i in io.main.GetItemListInTrack('video',1) if i.GetUniqueId()==row['source_id'])
            ss=int(source.GetLeftOffset()+old['start']-source.GetStart())
            asset=generate(source.GetMediaPoolItem().GetClipProperty('File Path'),ss,ss+end-old['start'],width,height,work_dir)
            old.update(end=end,path=asset['path'],asset=asset,effect_ids=old['effect_ids']+row['effect_ids'])
        else:merged.append(row)
    merged=exclude_fallback_intervals(merged,result,decisions)
    return result,decisions,merged


def exclude_fallback_intervals(rows, plan, decisions):
    """One shared foreground must never cover a simultaneous fallback title."""
    fallback_ids={r['effect_id'] for r in decisions if r['action']=='fallback'}
    blocked=[(e['start'],e['end']) for e in plan['effects'] if e['effect_id'] in fallback_ids]
    if not blocked:return rows
    result=[]
    for row in rows:
        points=sorted({row['start'],row['end']}|{max(row['start'],min(row['end'],v)) for span in blocked for v in span})
        for a,b in zip(points,points[1:]):
            if any(x<b and a<y for x,y in blocked):continue
            result.append({**row,'start':a,'end':b,'source_offset':row.get('source_offset',0)+a-row['start']})
    for d in decisions:
        if d['action']=='matte' and any(e['effect_id']==d['effect_id'] and any(a<e['end'] and e['start']<b for a,b in blocked) for e in plan['effects']):
            d['reason']='同屏其他花字需要盖在人上，共享人物层在其区间自动停用'
            if not any(d['effect_id'] in row['effect_ids'] for row in result):d['action']='none'
    return result


def state(item):
    return deepcopy({k:item.get(k) for k in ('start','end','track','enabled','media_path','source_start','source_end','props')})


def removal(record, items):
    removed=[];preserved=[]
    for row in record.get('mattes',[]):
        current=items.get(row['item_id'])
        if current is not None and ledger.equal(state(current),row['written_state']):removed.append(row['item_id'])
        else:preserved.append({**row,'manual_fields':['deleted'] if current is None else ledger.changed_fields(row['written_state'],state(current))})
    return removed,preserved


def preflight(before, removed):
    tracks=[t for t in before['timeline']['tracks'] if t['type']=='video' and t['name']==TRACK]
    if len(tracks)>1:raise ValueError('人物抠像轨重名')
    if tracks:
        track=tracks[0]
        if not track['enabled'] or track['locked']:raise ValueError('人物抠像轨停用或锁定')
        top=max(t['index'] for t in before['timeline']['tracks'] if t['type']=='video')
        if track['index']!=top:raise ValueError('人物抠像轨必须位于全部花字轨之上')
        if any(i['type']=='video' and i['track']==track['index'] and i['id'] not in removed for i in before['timeline']['items']):
            raise ValueError('人物抠像轨含未认领片段或用户手调；保留现场，停止替换')


def place(io, rows):
    from .resolve_io import folder_clips
    result=[];tracks=[]
    if not rows:return result,tracks
    track,created=io.ensure_track('video',TRACK)
    if created:tracks.append(('video',track))
    if track!=io.main.GetTrackCount('video'):raise ValueError('人物抠像轨不在最上层')
    mp=io.p.GetMediaPool();old=mp.GetCurrentFolder()
    try:
        folder=io.folder('抠像',io.folder('花字音效'));io.write(mp.SetCurrentFolder,folder)
        for row in rows:
            source=next(i for i in io.main.GetItemListInTrack('video',1) if i.GetUniqueId()==row['source_id'])
            found=[m for m in folder_clips(mp.GetRootFolder()) if m.GetClipProperty('File Path')==row['path']]
            if found:media=found[0]
            else:
                imported=io.write(mp.ImportMedia,[row['path']])
                if not imported:
                    raise RuntimeError('人物抠像文件导入达芬奇失败：'+str(row['path'])+'；下一步：确认这个文件还在、能在达芬奇里手动导入，再按 recover 的提示恢复后重新 build')
                media=imported[0]
            settings={'Alpha mode':'Straight','Data Level':'Full','Input LUT':source.GetMediaPoolItem().GetClipProperty('Input LUT'),
                      'Input Color Space':source.GetMediaPoolItem().GetClipProperty('Input Color Space'),
                      'Input Gamma':source.GetMediaPoolItem().GetClipProperty('Input Gamma')}
            for key,value in settings.items():
                io.write(media.SetClipProperty,key,value)
                if media.GetClipProperty(key)!=value:raise RuntimeError('人物抠像媒体属性读回不符：'+key)
            item=io.append(media,'video',track,row['start'],row.get('source_offset',0),row['end']-row['start'])
            # SDK ZoomGang is a UI switch; set it first, then the two corrected axes.
            io.write(item.SetProperty,'ZoomGang',False)
            for key,value in row['transform'].items():
                io.write(item.SetProperty,key,value)
            if not source.CopyGrades([item]):raise RuntimeError('人物抠像CopyGrades失败')
            if not ledger.equal(item.GetProperty(),row['transform']):raise RuntimeError('人物抠像变换读回不符')
            result.append({k:v for k,v in row.items() if k not in ('asset','transform')}|{'item_id':item.GetUniqueId(),'media_settings':settings,'copygrades':True})
    finally:io.write(mp.SetCurrentFolder,old)
    return result,tracks


if __name__=='__main__':
    if sys.argv[1:]!=['--worker']:raise SystemExit('仅作为独立环境worker调用')
    worker(json.load(sys.stdin))
