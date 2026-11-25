# 环境检查：缺什么、怎么补，一项一行
import os, shutil, subprocess, sys
from pathlib import Path
from .common import ASSETS, THINKCUT_DIR, DEFAULTS, say

HOME = Path.home()


def find_blender():
    """Blender 在哪（和 assets/blender_template/出片.py 找的地方一样，再加 PATH）；找不到返回 None。"""
    return next((b for b in (os.environ.get('BLENDER'), '/Applications/Blender.app/Contents/MacOS/Blender', '/opt/homebrew/bin/blender', shutil.which('blender'))
                 if b and Path(b).exists()), None)


def chrome_candidates(home=None):
    """无头截图用的浏览器可能在哪：和 scripts/tools/chrome.mjs 的 chromeCandidates 同一份清单、同一个顺序（改一处要改两处，离线测试会比对）。"""
    home = Path(home) if home else HOME
    apps = ('Google Chrome.app/Contents/MacOS/Google Chrome', 'Chromium.app/Contents/MacOS/Chromium', 'Microsoft Edge.app/Contents/MacOS/Microsoft Edge')
    return [str(base / a) for a in apps for base in (Path('/Applications'), home / 'Applications')]


def find_chrome(cands=None, exists=None):
    """找得到的第一个浏览器，都没有返回 None（和 chrome.mjs 的 findChrome 一样）。"""
    exists = exists or (lambda q: Path(q).exists())
    return next((q for q in (cands if cands is not None else chrome_candidates()) if exists(q)), None)


def find_pingfang():
    """花字用的苹方字体找不找得到（和 assets/包装系统/huazi.py 用同一个找法）。返回字体文件路径，找不到返回 None。"""
    try:
        if str(ASSETS / '包装系统') not in sys.path: sys.path.insert(0, str(ASSETS / '包装系统'))
        import huazi
        got = huazi.find_pingfang()
        return got[0] if got else None
    except Exception:
        return None


def env_check(script=None, timeout=60):
    """比对 NAS 环境原件和 Mac 运行副本（<程序目录>/bin/sync_env.sh --check，只比对、不拷不删）。返回 (退出码, 输出)：0 一致、4 不一致、1 读不到 NAS；
    脚本不在或没跑成返回 (None, 原因)。THINKCUT_SYNC_ENV 可以换成别的脚本（离线测试用）。"""
    script = Path(script or os.environ.get('THINKCUT_SYNC_ENV') or THINKCUT_DIR / 'bin' / 'sync_env.sh')
    if not script.is_file(): return None, f'没有 {script}'
    try: r = subprocess.run(['bash', str(script), '--check'], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e: return None, f'{script} --check 没跑成：{type(e).__name__}'
    return r.returncode, (r.stdout + r.stderr).strip()


def run(job=None):
    cfg = dict(DEFAULTS); cfg.update(job.cfg if job else {})
    rows = []
    def chk(name, ok, fix='', need=True): rows.append((name, ok, fix, need))
    try:
        import numpy, PIL
        chk('Python 包 numpy / Pillow', True)
    except ImportError:
        chk('Python 包 numpy / Pillow', False, 'python3 -m pip install --user numpy pillow')
    ff = shutil.which('ffmpeg')
    enc = subprocess.run([ff, '-hide_banner', '-encoders'], capture_output=True, text=True).stdout if ff else ''
    chk('ffmpeg（含 prores_ks、libx264）', bool(ff) and 'prores_ks' in enc and 'libx264' in enc, 'brew install ffmpeg')
    node = shutil.which('node'); v = subprocess.run([node, '--version'], capture_output=True, text=True).stdout.strip() if node else ''
    chk(f'Node.js ≥ 22（出动画）{v}', bool(v) and int(v.lstrip('v').split('.')[0]) >= 22, 'brew install node')
    chk('Google Chrome（无头截图、出老片子的代码动画）', bool(find_chrome()), '在 /Applications 里安装 Google Chrome（Chromium、Microsoft Edge 也可以）')
    # Blender 不是每条片子都用：找不到只提示，不算没就绪（2026-11-13 C13）
    chk('Blender（出 Blender 动画才要）', bool(find_blender()), '这条片子有 Blender 镜头才需要：安装 Blender（放 /Applications），或设环境变量 BLENDER 指到它', need=False)
    # HyperFrames（2026-11-20 选定的网页动画工具）：和 Blender 一样，有这类镜头才需要
    hfx = shutil.which('hyperframes'); hv = ''
    if hfx:
        try: hv = subprocess.run([hfx, '--version'], capture_output=True, text=True, env=dict(os.environ, HYPERFRAMES_NO_TELEMETRY='1'), timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError): hv = ''
    chk(f'HyperFrames（出网页动画才要；模板在 0.9.39 上验证过）{hv}', bool(hv), '有 HF_ 工程的片子才需要：npm install -g hyperframes@0.9.39', need=False)
    if hv and hv != '0.9.39': say(f'   提醒：这台装的是 HyperFrames {hv}，模板是在 0.9.39 上验证的；出片前先出一张样张看对不对')
    chk('字幕字体 冬青黑体 Hiragino Sans GB', Path(cfg['subtitle_font']).exists(), 'macOS 自带；缺了在“字体册”里下载')
    chk('花字字体 苹方 PingFang SC', bool(find_pingfang()), 'macOS 自带；缺了打开“字体册”，搜“苹方”点下载')
    chk('达芬奇脚本模块', Path('/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules/DaVinciResolveScript.py').exists(), '安装 DaVinci Resolve Studio')
    chk('音效收藏表（程序目录/core）', Path(cfg['sfx_favorites']).exists(), '挂上放程序的盘（NAS）')
    chk('音效素材库（程序目录/素材库）', Path(cfg['sfx_library']).exists(), '挂上放程序的盘（NAS）')
    # 2026-10-23 起剪口播、章节导航是剪辑（thinkcut-edit）下的步骤，不再单独链接为技能，只查说明文件在不在。
    chk('剪口播（剪辑第 1 步）', (THINKCUT_DIR / 'core' / 'skills' / 'a-roll' / 'SKILL.md').exists(), '挂上放程序的盘（NAS）')
    chk('章节导航（剪辑第 5 步）', (THINKCUT_DIR / 'chapters' / 'SKILL.md').exists(), '挂上放程序的盘（NAS）')
    ent = [d / 'thinkcut-edit' / 'SKILL.md' for d in (HOME / '.claude/skills', HOME / '.codex/skills')]
    chk('剪辑入口 thinkcut-edit', all(x.exists() for x in ent), f'在 {THINKCUT_DIR} 运行 bash bin/setup_mac.sh')
    if job:
        chk(f"a-roll 工程 {job['aroll']}", (Path(job['aroll']) / '审核' / 'latest_delivery.json').exists(), '路径不对或盘没挂载')
        chk(f"口播源视频", Path(job['source']).exists(), '盘没挂载？')
        chk(f"字幕 SRT", Path(job['srt']).exists(), '盘没挂载？')
    # 程序环境（NAS 原件和 Mac 运行副本）不一致只提醒，不算没就绪（2026-11-21 体检）
    code, out = env_check()
    chk('程序环境：NAS 原件和 Mac 运行副本一致' if code == 0 else '程序环境（NAS 原件和 Mac 运行副本）' + ('不一致，提醒' if code == 4 else '没比对成，提醒'),
        code == 0, out or '没有输出', need=False)
    for name, ok, fix, need in rows:
        say(('✓ ' if ok else '✗ ' if need else '· ') + name + ('' if ok else f'  → {fix}'))
    bad = [r for r in rows if not r[1] and r[3]]
    say('全部就绪' if not bad else f'{len(bad)} 项没就绪')
    return not bad
