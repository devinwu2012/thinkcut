#!/usr/bin/env python3
"""HyperFrames 出片（网页动画模板，2026-11-20 用户选定 HyperFrames）。一个镜头一个 镜头/<镜头号>.html。用法：
  python3 出片.py --预览 A20                 只生成 index.html（半尺寸），之后可以 hyperframes preview / check 看效果、查问题
  python3 出片.py --样张 31.2,40.5 --shot A20  出这几个时刻（成片秒）的静帧（半尺寸）→ 样张/，自动发到审片台“动画”栏（页面没开时拼成一张图在“预览”里弹出），并打印“请看哪里、确认什么”给用户
  python3 出片.py --shot A20                  整段出片，默认 1080P 审片版（半尺寸）：出成 出片/<镜头>_预览50.mp4、登记 50%，不动 4K 的 <镜头>.mp4。要先有样张确认（用户在审片台点“可以”，或 anim-confirm --kind 样张），没有就拦
  python3 出片.py --shot A20 --4k             只在用户看完 OK、导出前用：出 4K 出片/<镜头>.mp4、登记 100%（2026-11-21 Q159 复发：默认 1080P，不靠记得加参数）
  python3 出片.py --shot A20 --透明           另出一条带透明通道的 ProRes 4444（出片/<镜头>_透明.mov，预览是 <镜头>_预览50_透明.mov），不登记进成片，叠在别的画面上用
渲染前的硬拦和 Blender 工程同一套（anim.gate）：分镜规格.json 每个镜头要有口播要点，讲关系/对比/档位的要有草图确认，整段出片要有对得上当前代码的样张确认。
出样张和整段出片前先用 hyperframes check 查页面脚本有没有报错（卡点不在镜头里、动作排出镜头结尾都会报错），有就停。
每出完一个镜头核对帧数和尺寸，对了才换成正式文件并马上登记这一个镜头进 合成/动画清单.json、合成/渲染尺寸.json（预览尺寸导出前会被拦）；之后 anim-assemble 拼进 B肉。
index.html 是各镜头共用的生成文件：生成它到这个镜头出完/截完，都拿着工程里 index.html.lock 这把锁，同时开两个出片进程会排队，不会互相覆盖。
画幅、帧率、镜头起止、卡点都从片子目录读（job.json、分镜.json），镜头文件里不写死数字。"""
import argparse, contextlib, fcntl, json, os, shutil, signal, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent; FILM = HERE.parent
def find_skill(env=None, fixed=str(Path.home() / '.claude' / 'skills' / 'thinkcut-edit'), volumes='/Volumes'):
    """找剪辑能力（skill/thinkcut-edit）。这个文件会被复制到片子目录里用，不能按自己的位置推：
    先用环境变量 THINKCUT_SKILL，再用这台 Mac 的技能链接 ~/.claude/skills/thinkcut-edit（指着使用版，2026-11-24 起不写死盘符路径）；写死的不在（NAS 换了挂载名之类）就在 /Volumes 下的 work* 共享盘里找（程序文件夹叫什么都行，开发版排后面），都没有就停下说 NAS 没挂上。"""
    env = os.environ.get('THINKCUT_SKILL') if env is None else env
    if env: return Path(env)
    if Path(fixed).is_dir(): return Path(fixed)
    got = sorted((q for q in Path(volumes).glob('work*/*/口播自动剪辑/skill/thinkcut-edit') if q.is_dir()), key=lambda q: (q.parents[2].name.endswith('-开发'), str(q)))   # 使用版排在开发版前面
    if got: return got[0]
    sys.exit(f'停止：找不到剪辑能力（{fixed}，{volumes}/work* 下也没有）：NAS 没挂上？先在访达里连上 NAS 的 work 共享盘再跑；能力放在别处就设环境变量 THINKCUT_SKILL 指到 skill/thinkcut-edit')


SKILL = find_skill()
sys.path.insert(0, str(SKILL / 'scripts'))
from thinkcut.common import Job, rj, die, say, local_tmp, local_rm   # noqa: E402
from thinkcut import anim                        # noqa: E402

TESTED = '0.9.39'      # 选型和模板都是在这个版本上验证的；别的版本先跑一遍样张再信
ENV = dict(os.environ, HYPERFRAMES_NO_TELEMETRY='1')      # 不上报使用数据（只对本次命令生效，不改这台 Mac 的设置）
# 时限（秒，2026-11-21 体检：以前 hyperframes 卡住就一直等）。都是正常耗时的几十倍，正常碰不到；机器慢或镜头特别重时用环境变量 THINKCUT_TIMEOUT_X=2 整体放大。
TIMEOUT_X = float(os.environ.get('THINKCUT_TIMEOUT_X') or 1)
CHECK_LIMIT = 300          # hyperframes check：开一次浏览器跑一遍页面脚本，正常几秒到十几秒
SNAPSHOT_LIMIT = 300       # hyperframes snapshot：开一次浏览器截完一个镜头要的几个时刻（半尺寸），正常两三秒
RENDER_BASE = 600          # hyperframes render：启动、编码收尾留的固定时间
RENDER_PER_FRAME = 5       # 再加每帧这么多秒（半尺寸 1080P；正常一个镜头几秒到几十秒，合每帧零点几秒）；4K 像素是 4 倍，按 4 倍给
PROBE_LIMIT = 300          # ffprobe 数出片的帧数（要把整段解码一遍）：正常几秒到几十秒
RENDER_LIMIT = RENDER_BASE # 当前 index.html 这个镜头整段出片给多久：page() 生成页面时按它的帧数、尺寸算好（render_limit）


def hf():
    exe = shutil.which('hyperframes')
    if not exe: die(f'找不到 hyperframes：npm install -g hyperframes@{TESTED}（装完跑一次 thinkcut_edit.py doctor）')
    return exe


def args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--shot', default='all', help='镜头 id，逗号分隔；默认 分镜规格.json 里 "工程" 写本工程的全部镜头')
    p.add_argument('--4k', dest='full4k', action='store_true', help='只在导出前用：出 4K 定稿（不加就是 1080P 审片版）')
    p.add_argument('--scale', type=int, default=50, choices=[50, 100], help='默认 50＝1080P 审片版；100 和 --4k 一样')
    p.add_argument('--样张', dest='still', help='只出这些时刻（成片秒，逗号分隔）的静帧')
    p.add_argument('--预览', dest='preview', help='只把这个镜头生成成 index.html（半尺寸），不出片')
    p.add_argument('--透明', dest='alpha', action='store_true', help='另出带透明通道的 ProRes 4444，不登记')
    return p.parse_args()


def shot_span(job, B, sid):
    s = next((x for x in B['shots'] if x['id'] == sid), None)
    if s is None: die(f'分镜.json 里没有镜头 {sid}（先在 分镜规格.json 写好并跑 beats）')
    fps = float(job.fps); frames = round(s['b'] * fps) - round(s['a'] * fps)      # 和 anim-assemble 的拼接表同一种算法
    if frames < 1: die(f'镜头 {sid} 不足一帧')
    return s['a'], s['b'], frames


def page(job, B, sid, scale, alpha=False):
    """镜头/<sid>.html → index.html：填画幅、时长，注入 window.THINKCUT。返回 (帧数, 宽, 高)。"""
    src = HERE / '镜头' / f'{sid}.html'
    if not src.exists(): die(f'没有 {src}：复制 镜头/示例.html 改名成 {sid}.html 再写')
    a, b, frames = shot_span(job, B, sid); k = scale / 100; W, H = round(job.W * k), round(job.H * k)
    dur = frames / float(job.fps)
    fps = float(job.fps); covered = [[k0 / fps, k1 / fps] for k0, k1, *_ in anim.cover_list(job).get(sid, [])]   # 盖实拍的那几段（镜头里的秒）：THINKCUT.label 自动藏起标签
    data = dict(shot=sid, a=a, b=b, dur=dur, fps=fps, W=W, H=H, k=2 * k, card=[round(v * k) for v in job.fmt['card']],
                anchors=B.get('anchors', {}), transparent=alpha, covered=covered)
    text = src.read_text(encoding='utf-8')
    for key, val in (('__W__', str(W)), ('__H__', str(H)), ('__DUR__', f'{dur:.6f}')):
        if key not in text: die(f'{src.name} 里没有占位 {key}：照 镜头/示例.html 的写法，不要把画幅、时长写死')
        text = text.replace(key, val)
    if '共用/基础.js' not in text or 'THINKCUT.pad(' not in text or 'window.__timelines["main"]' not in text:
        die(f'{src.name} 要引入 共用/基础.js，并在时间轴最后照 示例.html 写 THINKCUT.pad(tl); window.__timelines["main"] = tl; tl.seek(0);')
    inject = '<script>window.THINKCUT = ' + json.dumps(data, ensure_ascii=False) + ';</script>'
    if '</head>' not in text: die(f'{src.name} 没有 </head>')
    (HERE / 'index.html').write_text(text.replace('</head>', inject + '\n  </head>', 1), encoding='utf-8')
    global RENDER_LIMIT; RENDER_LIMIT = render_limit(frames, scale)
    return frames, W, H


@contextlib.contextmanager
def locked():
    """拿着 index.html 的锁（两个出片进程同时出不同镜头会互相覆盖 index.html）；拿不到就等。"""
    with open(HERE / 'index.html.lock', 'w') as f:
        try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: say('另一个出片进程正在用 index.html，等它出完这个镜头…'); fcntl.flock(f, fcntl.LOCK_EX)
        yield


def render_limit(frames, scale):
    """整段出片给多久：固定时间＋每帧时间（4K 按 4 倍）。"""
    return RENDER_BASE + frames * RENDER_PER_FRAME * (4 if scale >= 100 else 1)


def call(cmd, limit, what):
    """跑一条 hyperframes 命令并限时，返回 (退出码, 标准输出, 错误输出)。超时就把它连同它开的浏览器、ffmpeg 一起结束（它们在同一个进程组），停下说原因；
    被 Ctrl-C 打断或出别的错也先结束它们再抛出（子进程单独一个进程组，终端的 Ctrl-C 到不了它）。"""
    limit = limit * TIMEOUT_X
    p = subprocess.Popen(cmd, cwd=HERE, env=ENV, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        out, err = p.communicate(timeout=limit)
    except BaseException as e:
        with contextlib.suppress(ProcessLookupError, PermissionError): os.killpg(p.pid, signal.SIGKILL)
        with contextlib.suppress(Exception): p.kill()
        with contextlib.suppress(Exception): p.communicate(timeout=10)
        if isinstance(e, subprocess.TimeoutExpired):
            die(f'{what} 超过 {limit:.0f} 秒还没结束（正常远用不了这么久），已结束它和它开的浏览器，这一步没做成。'
                '多半是页面脚本卡住或浏览器没起来：先 hyperframes check 看报错；确实是镜头太重、机器慢，命令前加 THINKCUT_TIMEOUT_X=2 把时限放大一倍再跑')
        raise
    return p.returncode, out, err


def preflight(exe, sid):
    """出样张/出片前查当前 index.html 的页面脚本有没有报错。snapshot 遇到脚本报错照样出图，所以先查，有错就停并把原因显示出来。"""
    _, out, err = call([exe, 'check', '--json', '--no-craft', '--no-contrast', '--no-cache'], CHECK_LIMIT, f'{sid}：hyperframes check')
    try: rt = json.loads(out)['runtime']
    except (ValueError, KeyError): die(f'{sid}：hyperframes check 没给出结果，先别出片：\n' + (out + err).strip()[-1500:])
    bad = [x['message'].split('\n')[0] for x in rt.get('findings', []) if x.get('severity') == 'error']
    if bad: die(f'{sid} 的页面脚本报错，没出：\n- ' + '\n- '.join(bad))


def probe(path):
    limit = PROBE_LIMIT * TIMEOUT_X
    try:
        r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-count_frames', '-show_entries', 'stream=width,height,nb_read_frames', '-of', 'json', str(path)],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=limit)
    except subprocess.TimeoutExpired:      # 2026-11-21：以前 ffprobe 卡住就一直等。超时 subprocess.run 会先结束它
        die(f'核对出片（ffprobe 数 {Path(path).name} 的帧数）超过 {limit:.0f} 秒还没结束（正常远用不了这么久），已结束它；这个镜头已经出完但还没核对、没登记，新出的留在 {path}。'
            '多半是这个文件所在的盘（NAS）卡住了：确认盘连着再重跑；确实是机器慢，命令前加 THINKCUT_TIMEOUT_X=2 把时限放大一倍')
    s = json.loads(r.stdout)['streams'][0]; return int(s['nb_read_frames']), int(s['width']), int(s['height'])


def run(cmd):
    """跑 hyperframes snapshot / render（限时：截静帧用 SNAPSHOT_LIMIT，整段出片用当前页面的 RENDER_LIMIT）。"""
    code, out, err = call(cmd, SNAPSHOT_LIMIT if cmd[1] == 'snapshot' else RENDER_LIMIT, 'hyperframes ' + ' '.join(map(str, cmd[1:2])))
    if code: die('HyperFrames 出错：' + ' '.join(map(str, cmd[1:4])) + '\n' + (out + err).strip()[-1500:])
    return out


def main():
    a = args(); job = Job(FILM); B = rj(FILM / '分镜.json'); exe = hf()
    if a.full4k: a.scale = 100
    if a.preview:
        with locked(): frames, W, H = page(job, B, a.preview, 50)
        say(f'已生成 {HERE / "index.html"}（{a.preview}，{W}×{H}，{frames} 帧）。看效果：cd {HERE} && HYPERFRAMES_NO_TELEMETRY=1 hyperframes preview；查问题：hyperframes check'); return
    ids = anim.project_shots(HERE, FILM) if a.shot == 'all' else a.shot.split(',')
    if not ids: die(f'不知道这个工程出哪些镜头：在 分镜规格.json 的镜头里写 "工程": "{HERE.name}"，或用 --shot 指定')
    if a.still:
        anim.require(job, HERE, ids, False)
        times = [float(t) for t in a.still.split(',')]; out = HERE / '样张'; out.mkdir(exist_ok=True); made = []
        for sid in ids:
            a0, b0, _ = shot_span(job, B, sid); mine = [t for t in times if a0 <= t < b0]
            if not mine: continue
            with locked():
                page(job, B, sid, 50); preflight(exe, sid)
                # 截图先出到本机临时目录再挪进 样张/（用完就删，2026-11-21 用户定；以前在工程的 缓存/样张_<镜头>）。开始先清掉上次崩溃留下的
                tmp = local_tmp(FILM, HERE.name, f'样张_{sid}', need=len(mine) * 64 * 1024 ** 2, what=f'{sid} 的样张截图')
                # 这个镜头的几个时刻开一次浏览器截完（2026-11-22：以前一个时刻开一次，6 张约 6.5 秒，现在约 1.7 秒）；按 --json 报的时刻对文件，不靠文件名排序
                keys = list(dict.fromkeys(f'{t - a0:.3f}' for t in mine))
                rep = run([exe, 'snapshot', '--at', ','.join(keys), '--no-end', '--json', '-o', str(tmp)])
                try: got = {f'{float(f["time"]):.3f}': Path(f['path']) for f in json.loads(rep)['frames']}
                except (ValueError, KeyError, TypeError): die(f'{sid} 的样张没给出结果：\n' + rep.strip()[-1500:])
                lost = [k for k in keys if k not in got or not got[k].exists()]
                if lost: die(f'{sid} 的样张少了镜头里第 {"、".join(lost)} 秒的（要 {len(keys)} 张，出了 {len(got)} 张）：{tmp}')
                for t in dict.fromkeys(mine):
                    dst = out / f'{sid}_{t:.2f}.png'; shutil.copyfile(got[f'{t - a0:.3f}'], dst); made.append((sid, t, dst))
                local_rm(tmp)
        if not made: die('这些时刻不在要出的镜头里：' + a.still)
        say('样张（半尺寸）：\n' + '\n'.join(str(f) for _, _, f in made) + '\n样张会发到审片台“动画”栏，用户在页面上点“可以”就是确认（只在对话里说可以时再 anim-confirm --kind 样张），然后整段出片')
        anim.still_sheet(job, made, out / f"样张拼图_{'_'.join(dict.fromkeys(s for s, _, _ in made))}.jpg", HERE); return
    anim.require(job, HERE, ids, True)
    out = HERE / '出片'; out.mkdir(exist_ok=True); files = {}
    for sid in ids:
        name = sid if a.scale == 100 else f'{sid}_预览{a.scale}'      # 预览另起文件名，不顶替 4K 正式文件
        with locked():
            frames, W, H = page(job, B, sid, a.scale, False); preflight(exe, sid)
            new = out / f'{name}.新出.mp4'; new.unlink(missing_ok=True)
            say(f'出 {sid}（{frames} 帧，{W}×{H}）…')
            run([exe, 'render', '--strict', '--fps', job['fps'], '--quality', 'high', '-o', str(new)])
        n, w, h = probe(new)
        if (n, w, h) != (frames, W, H):
            die(f'{sid} 出片不对：要 {frames} 帧 {W}×{H}，出来是 {n} 帧 {w}×{h}。新出的留在 {new}，正式文件没动、没登记' + ('；前面的 ' + '、'.join(files) + ' 已出好并登记' if files else ''))
        final = out / f'{name}.mp4'; os.replace(new, final); files[sid] = final
        anim.register(job, HERE, 'hyperframes', [sid], a.scale, {sid: final}, None, [W, H])      # 出好一个登记一个，中途失败时登记表和磁盘对得上
        say(f'已登记 {sid} → {final}')
        if a.alpha:
            mov = out / f'{name}_透明.mov'
            with locked():
                page(job, B, sid, a.scale, True); run([exe, 'render', '--strict', '--fps', job['fps'], '--format', 'mov', '-o', str(mov)])
            say(f'透明版 → {mov}（不登记进成片）')
    say('已登记：' + '、'.join(files) + (f'（{a.scale}% 预览＝1080P 审片版；用户看完 OK、导出前加 --4k 重出）' if a.scale != 100 else '') + '\n下一步：thinkcut_edit.py anim-assemble <片子目录>')


if __name__ == '__main__':
    main()
