#!/usr/bin/env bash
# 离线测试：不连达芬奇、不开 Chrome、不碰 NAS 素材。用烟道那条的样例数据核对分镜、字幕（逐像素）、字幕整轨、改字、动画工程拼装和三种 4K 画幅。
set -u
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P) || exit 1
# 用完即删的渲染临时文件平时放本机临时目录下的“ThinkCut临时”（2026-11-21）；测试用自己的临时文件夹，跑完删掉，不碰真的那个
THINKCUT_LOCAL_TMP=$(mktemp -d "${TMPDIR:-/tmp}/thinkcut_test_tmp.XXXXXX") || exit 1
export THINKCUT_LOCAL_TMP; trap 'rm -rf "$THINKCUT_LOCAL_TMP"' EXIT
# 测试用自己的配置档（“测试IP”当默认 IP），不靠哪个真 IP 的配置（bin/make_test_profile.py，2026-11-24）
if [[ -z "${THINKCUT_PROFILES:-}" ]]; then THINKCUT_PROFILES=$(python3 "$ROOT/../bin/make_test_profile.py" "$THINKCUT_LOCAL_TMP/配置档") || exit 1; export THINKCUT_PROFILES; fi
# 模板 出片.py 会被复制到片子里用，默认按写死的使用版路径找程序；测试要测的是这一份自己（开发版、worktree 里也一样），所以指回自己（2026-11-24）
export THINKCUT_SKILL="${THINKCUT_SKILL:-$ROOT/skill/thinkcut-edit}"
# 测试不碰这台 Mac 上真的 AI 命令行（2026-11-26 AI 引擎）：只在这里找，空＝一个都没装；test_engine 自己放假的替身
export THINKCUT_AI_PATH="${THINKCUT_AI_PATH-}"
PYTHONUTF8=1 THINKCUT_NO_OPEN=1 python3 -B - "$ROOT" <<'PY'
import hashlib, json, shutil, subprocess, sys, tempfile
from pathlib import Path
root = Path(sys.argv[1]); SK = root / 'skill/thinkcut-edit'; S = SK / 'scripts/thinkcut_edit.py'; sample = root / 'samples/烟道反味'
sys.path.insert(0, str(SK / 'scripts'))
from thinkcut import formats, beats, subs
from thinkcut.common import Job, rj
from PIL import Image
fails = []
def check(ok, msg): print(('通过 ' if ok else '失败 ') + msg, flush=True); ok or fails.append(msg)
_FULL = __import__('os').environ.get('THINKCUT_TEST_FULL')      # bin/test_all.py 设：没过时把子测试的完整输出打出来（2026-11-26 CI 只看得到尾巴）
def _t(n): return 10**9 if _FULL else n

r = subprocess.run([sys.executable, '-B', str(S), '-h'], capture_output=True, text=True)
check(r.returncode == 0 and 'review' in r.stdout and 'subfix' in r.stdout, '命令行帮助能打开，列出全部命令')

for name in formats.FORMATS:
    f = formats.get(name); W, H = f['W'], f['H']; x, y, w, h = f['card']
    check(max(W, H) == 3840 or (W, H) == (2160, 2880), f'{name} 是 4K：{W}×{H}')
    inside = all(x <= px and px + f['pip']['d'] <= x + w and y <= py and py + f['pip']['d'] <= y + h for px, py in (f['pip']['L'], f['pip']['R']))
    nx, ny, nw, nh = f['nav']; bx, by, bs = f['body']
    check(inside and nx + nw <= W and ny + nh <= H and bx + bs <= W and by + bs <= H, f'{name} 小圆圈在卡片里，导航条、取脸方块在画面里')
    g = formats.get(name, formats.NAV_H_NEW); gx, gy, gw, gh = g['nav']; cx2, cy2, cw2, ch2 = g['card']
    check(gh == 224 and formats.get(name)['nav'] == f['nav'] and formats.get(name)['card'] == f['card'] and g['pip'] == f['pip']
          and (gy + gh <= cy2) and cy2 + ch2 == y + h, f'{name} 新条高 224：条子在卡片/画面区上方，底边和小圆圈位置不变，不给 nav_h 时老版式不变')
check(formats.get('3:4满屏', 224)['card'] == (0, 224, 2160, 2656) and formats.get('9:16', 224)['nav'] == (0, 896, 2160, 224) and formats.get('16:9', 224)['nav'] == (0, 0, 3840, 224),
      '新条高 224：3:4满屏 画面区从 224 铺到底，9:16 条子贴着卡片，16:9 条子在顶部')
def nav_err(name, v):
    try: formats.get(name, v); return ''
    except SystemExit as e: return str(e)
check(all('nav_h' in nav_err(n, v) for n, v in (('3:4', 'abc'), ('3:4', -5), ('3:4', True), ('3:4', 224.5), ('3:4', 361), ('16:9', 241), ('3:4满屏', 341), ('9:16', 381)))
      and not any(nav_err(n, v) for n, v in (('16:9', 224), ('16:9', 240), ('3:4', 360), ('9:16', 380), ('3:4满屏', 340), ('3:4', None), ('3:4', 0))),
      'nav_h 不是正整数、比原来高出 40 以上或会压到画面区时中文报错；16:9 的 224、不写或 0（老版式）照常')

spec, tok, want = rj(sample / '分镜规格.json'), rj(sample / '口播字时间.json')['tokens'], rj(sample / '分镜.json')
got = beats.compile_spec(spec, tok, 190.123)
check(all(got[k] == want[k] for k in ('range', 'anchors', 'shots', 'sfx')), '分镜规格编译结果与烟道成片用的分镜逐项一致')
# 推拉文件：每次推拉自动加一声 ZOOM，带小圆圈的镜头自动缩小；重复的不加（2026-10-24）
pb = [s for s in got['shots'] if s['kind'] == 'B' and s.get('pip')][0]
sp2 = json.loads(json.dumps(spec)); sp2.setdefault('sounds', {})['ZOOM'] = '唰音效刷出标题.mp3'; sp2['push_sound'] = True   # 推拉默认不响（2026-11-12），这里测打开时的老行为
t0 = round(pb['a'] + 0.5, 3)
g2 = beats.compile_spec(sp2, tok, 190.123, pushes=[{'shot': pb['id'], 't0': t0, 't1': t0 + 0.7, 'kind': 'in'}, {'shot': pb['id'], 't0': t0, 't1': t0 + 0.7, 'kind': 'in'}])
zs = [e for e in g2['sfx'] if e['file'] == '唰音效刷出标题.mp3']
win = g2.get('pip_shrink', {}).get('windows', [])
sh = got['shots']; i = sh.index(pb); end = pb['b']
while i + 1 < len(sh) and sh[i + 1].get('pip') == pb.get('pip') and abs(sh[i + 1]['a'] - sh[i]['b']) < 1e-6: i += 1; end = sh[i]['b']
check(len(zs) == 1 and abs(zs[0]['t'] - t0) < 1e-6 and len(win) == 1 and win[0]['a'] == t0 and win[0]['b'] == round(end, 3), '推拉文件自动加一声唰、没拉回时小圆圈缩到这段连续小圆圈结束，重复的不加')
# 推拉跨镜头配对、手写与自动重叠合并（2026-10-24 审计）
runs = [s for s in got['shots'] if s['kind'] == 'B' and s.get('pip')]
k = next((i for i in range(len(got['shots']) - 1) if got['shots'][i].get('pip') and got['shots'][i + 1].get('pip') == got['shots'][i].get('pip') and got['shots'][i]['kind'] == 'B'), None)
if k is not None:
    s1, s2 = got['shots'][k], got['shots'][k + 1]
    sp3 = json.loads(json.dumps(sp2)); sp3.setdefault('pip', {})['shrink'] = [[round(s1['b'] - 0.8, 3), round(s1['b'] + 0.1, 3)]]
    g3 = beats.compile_spec(sp3, tok, 190.123, pushes=[{'shot': s1['id'], 't0': round(s1['b'] - 1, 3), 't1': round(s1['b'] - 0.3, 3), 'kind': 'in'},
                                                       {'shot': s2['id'], 't0': round(s2['a'] + 0.5, 3), 't1': round(s2['a'] + 1.2, 3), 'kind': 'out'}])
    w3 = g3['pip_shrink']['windows']
    check(len(w3) == 1 and w3[0]['a'] == round(s1['b'] - 1, 3) and w3[0]['b'] == round(s2['a'] + 1.2, 3), '推近在前一镜、拉回在后一镜也能配对；和手写时段重叠时合成一段')
else:
    check(True, '样例里没有相邻小圆圈镜头，跳过跨镜头推拉测试')
from thinkcut import resolve as R
rows = [{'start': 0, 'end': 30, 'text': '第一句'}, {'start': 30, 'end': 60, 'text': '上 / 下'}]
cues, miss = R.srt_cues(rows, [(0, 25, 0), (25, 55, 30), (60, 70, 999)])
txt = R.srt_text(cues, 30)
check(len(cues) == 2 and miss == [(60, 70, 999)] and '00:00:00,833 --> 00:00:01,833' in txt and '上\n下' in txt and ' / ' not in txt, 'SRT 时间按审核线片段、文字按源入点对回字幕表，“/”换成换行，对不上的单独报出')
rows2 = [{'start': 0, 'end': 30, 'text': '第一条', 'file': '/x/字幕_aaa.png'}, {'start': 30, 'end': 60, 'text': '第二条', 'file': '/x/字幕_aab.png'}]
cues, miss = R.srt_cues(rows2, [(0, 30, 0, '字幕整轨.mov'), (30, 60, 0, '字幕_aab_a.mov'), (60, 70, 0, '别的.mov')])
check([c[2] for c in cues] == ['第一条', '第二条'] and len(miss) == 1, '改字换进去的片段（源入点从 0 起）按文件名对回那一条，不会写成第 1 条的字')
bad = dict(spec, anchors=dict(spec['anchors'], zz=['口播里没有的词', 0]))
try:
    beats.compile_spec(bad, tok, 190.123); check(False, '找不到的卡点词会报错')
except SystemExit as e:
    check('找不到' in str(e), '找不到的卡点词会报错')

exp = rj(sample / '字幕前12条_期望.json')
with tempfile.TemporaryDirectory() as t:
    t = Path(t)
    def job(fmt, d):
        d.mkdir(); srt = d / 'x.srt'; shutil.copy(sample / '字幕前12条.srt', srt)
        j = dict(rj(sample / 'job_样例.json'), format=fmt, srt=str(srt), subtitle_key_color='#FF6F00', frames=exp['rows'][-1]['end'] + 10, aroll='', source='', project='', main_timeline='')
        (d / 'job.json').write_text(json.dumps(j, ensure_ascii=False), encoding='utf-8'); return Job(d)
    j = job('3:4', t / 'p'); rows = subs.render(j)
    hs = {str(r['n']): hashlib.sha256(open(r['file'], 'rb').read()).hexdigest() for r in rows}
    check(hs == exp['sha256'], '3:4 前 12 条字幕图与烟道成片逐像素一致')
    check([{k: r[k] for k in ('n', 'start', 'end', 'text', 'keys')} for r in rows] == exp['rows'], '字幕帧号、去标点后的文字、橙色重点词一致')
    for fmt in ('9:16', '16:9'):
        jj = job(fmt, t / fmt.replace(':', '_')); rr = subs.render(jj); f = jj.fmt; x, y, w, h = f['card']
        boxes = [Image.open(r['file']).getchannel('A').getbbox() for r in rr]
        ok = all(b and b[0] >= 0 and b[2] <= f['W'] and b[3] <= f['H'] for b in boxes)
        below = all(b[1] >= y + h for b in boxes)
        check(ok and below and Image.open(rr[0]['file']).size == (f['W'], f['H']), f'{fmt} 字幕图是 4K、在画面内、在卡片下方')
    st = subprocess.run([sys.executable, '-B', str(S), 'status', str(t / 'p')], capture_output=True, text=True).stdout
    check('✓ 字幕图' in st and '下一步：字时间' in st, '进度查看能认出已做和下一步')
    nt = subprocess.run([sys.executable, '-B', str(S), 'nav-target', str(t / 'p')], capture_output=True, text=True).stdout
    check('"nav_height": 340' in nt and '"width": 2160' in nt, '导航参数给出 3:4 的 2160×2880、条高 340')
    subs.track(j); tr = t / 'p/合成/字幕/字幕整轨.mov'
    n = int(subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0', '-show_entries', 'stream=nb_read_frames', '-of', 'csv=p=0', str(tr)], capture_output=True, text=True).stdout.strip())
    check(n == j['frames'], f'字幕整轨帧数 {n} 等于成片帧数 {j["frames"]}')
    gap = next(b['start'] - 1 for a, b in zip(rows, rows[1:]) if b['start'] > a['end'])
    png = t / 'gap.png'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(tr), '-vf', f'select=eq(n\\,{gap})', '-frames:v', '1', str(png)], check=True)
    check(Image.open(png).convert('RGBA').getchannel('A').getextrema() == (0, 0), '字幕之间的空档帧完全透明')
    before = {r['n']: hashlib.sha256(open(r['file'], 'rb').read()).hexdigest() for r in rows}
    row, clip = subs.refix(j, 5, '项目', '工地', write_srt=True)
    after = {r['n']: hashlib.sha256(open(r['file'], 'rb').read()).hexdigest() for r in rj(t / 'p/合成/字幕/字幕表.json')}
    srt_new = (t / 'p/x.srt').read_text(encoding='utf-8')
    check([k for k in before if before[k] != after[k]] == [5] and row['text'] == '我们做了这么多工地', '改字只重画第 5 条')
    check('这么多工地' in srt_new and (t / 'p/x.改字前.srt').exists() and srt_new.count('\n') == (sample / '字幕前12条.srt').read_text(encoding='utf-8').count('\n'), '改字同步 SRT 并留了备份，其余行不变')
    ln = int(subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0', '-show_entries', 'stream=nb_read_frames', '-of', 'csv=p=0', str(clip)], capture_output=True, text=True).stdout.strip())
    check(ln == row['end'] - row['start'], f'改字片段 {ln} 帧，等于这条字幕长度')
    try:
        subs.refix(j, 5, '不存在的字', 'x'); check(False, '要改的字不在这一条时会停')
    except SystemExit as e:
        check('里面没有' in str(e), '要改的字不在这一条时会停')
    # 老代码动画模板 2026-11-21 下线；已有老工程的片子还要能重出：用最小的老工程夹具（samples/老代码动画工程_最小，three.js 是占位文件，不开浏览器）
    a = t / 'job'; a.mkdir(); shutil.copytree(root / 'samples/老代码动画工程_最小/动画', a / '动画'); shutil.copy(sample / '分镜.json', a / '分镜.json')
    r = subprocess.run([sys.executable, '-B', str(a / '动画/build.py')], capture_output=True, text=True)
    html = (a / '动画/动画.html').read_text(encoding='utf-8') if (a / '动画/动画.html').exists() else ''
    check(r.returncode == 0 and 'window.BEATS_DATA' in html and 'B_hook' in html, '已有的老代码动画工程（最小夹具）仍能拼成单文件网页，并读到片子目录里的分镜')
# 导出默认按达芬奇平衡，平衡前查手调（2026-10-24）：用假的达芬奇片段核对，不连达芬奇
from thinkcut import resolve
from thinkcut.common import wj
class Clip:
    def __init__(s, start, name, db): s.s, s.n, s.db = start, name, db
    def GetStart(s): return s.s
    def GetName(s): return s.n
    def GetProperty(s, k): return s.db
class TL:
    def GetStartFrame(s): return 108000
class FakeJob:
    fps = 29.97
    def __init__(s, d): s.d = d
    def p(s, *a): return s.d.joinpath(*a)
    def get(s, k, default=None): return default
with tempfile.TemporaryDirectory() as t:
    fj = FakeJob(Path(t)); (Path(t) / '合成').mkdir()
    wj(fj.p('合成', '音效表.json'), [{'file': '/x/唰.mp3', 'record': 300, 'gain_db': -6.0}, {'file': '/x/冲水.wav', 'record': 900, 'gain_db': -12.5}])
    wj(fj.p('合成', '音量.json'), {'voice_db': 8.0})
    voice = [Clip(108000, 'A', 8.0), Clip(108500, 'A', 8.0)]
    sfx = [Clip(108300, '唰.mp3', -6.0), Clip(108950, '冲水.wav', -12.5)]
    check(resolve.hand_tweaked(fj, TL(), voice, sfx) == [], '平衡前查手调：音量和记录一致、挪过的音效按文件名认出，不算手调')
    voice[1].db = 3.0; sfx[0].db = -2.0; extra = Clip(109999, '自己加的.mp3', 0.0)
    tw = resolve.hand_tweaked(fj, TL(), voice, sfx + [extra])
    check(len(tw) == 3 and any('人声' in x for x in tw) and any('唰.mp3' in x for x in tw) and any('表里没有' in x for x in tw), '平衡前查手调：改过的人声、音效和用户自己加的音效都会报出来')
    wj(fj.p('合成', '音量.json'), {'voice_db': 8.0, 'voice_levels': [3.0, 8.0]})
    check(not any('人声' in x for x in resolve.hand_tweaked(fj, TL(), voice, sfx)), '平衡后记下的人声音量都认，不误报')
r = subprocess.run([sys.executable, '-B', str(S), 'export', '-h'], capture_output=True, text=True)
check('--no-balance' in r.stdout and '--balance' in r.stdout, 'export 有 --balance（强制）和 --no-balance（不平衡），默认平衡')
r = subprocess.run([sys.executable, '-B', str(S), 'export', '/nonexistent', '--balance', '--no-balance'], capture_output=True, text=True)
check(r.returncode != 0 and 'not allowed' in r.stderr, '--balance 和 --no-balance 不能一起用')
# 2026-11-12《水电阶段》剪辑工具：卡点顺序找、音效重点与画面事件、小圆圈任意位置、改动标记、花字/额外轨、项目内音效（剪辑工具会话加）
# HyperFrames 模板（2026-11-20）：不启动 HyperFrames，只查建工程、生成页面、指纹、防呆
import importlib.util
from thinkcut import anim
with tempfile.TemporaryDirectory() as hd:
    hd = Path(hd).resolve(); film = hd / '片'; film.mkdir()
    (film / 'job.json').write_text(json.dumps({'fps': '30000/1001', 'format': '3:4满屏', 'nav_h': 224, 'frames': 300}), encoding='utf-8')
    (film / '分镜.json').write_text(json.dumps({'anchors': {'卡点': 3.5}, 'shots': [{'id': 'A01', 'kind': 'B', 'a': 2.0, 'b': 6.0}]}, ensure_ascii=False), encoding='utf-8')
    (film / '分镜规格.json').write_text(json.dumps({'shots': [{'id': 'A01', '工程': 'HF_试', '讲什么': '步骤', '口播要点': ['三步']}]}, ensure_ascii=False), encoding='utf-8')
    hj = Job(film); anim.hf_new(hj, '试'); proj = film / 'HF_试'
    check((proj / '出片.py').exists() and (proj / '共用' / '基础.js').exists() and not (proj / 'index.html').exists() and anim.project_shots(proj, film) == ['A01'], 'hf-new 复制 HyperFrames 模板，认得这个工程负责的镜头')
    shutil.copy(proj / '镜头' / '示例.html', proj / '镜头' / 'A01.html')
    spec = importlib.util.spec_from_file_location('hf_out', proj / '出片.py'); hf_out = importlib.util.module_from_spec(spec); spec.loader.exec_module(hf_out)
    B = rj(film / '分镜.json'); frames, W, H = hf_out.page(hj, B, 'A01', 100); html = (proj / 'index.html').read_text(encoding='utf-8')
    data = json.loads(html.split('window.THINKCUT = ', 1)[1].split(';</script>', 1)[0])
    check((frames, W, H) == (120, 2160, 2880) and 'data-width="2160"' in html and '__W__' not in html and '__DUR__' not in html
          and data['card'] == [0, 224, 2160, 2656] and data['k'] == 2 and data['anchors'] == {'卡点': 3.5} and abs(data['dur'] - 120 * 1001 / 30000) < 1e-6,
          'HyperFrames 镜头页面按本片画幅、镜头帧数、画面区和卡点生成（4K，画面区从 224 开始）')
    _, w2, h2 = hf_out.page(hj, B, 'A01', 50); d2 = json.loads((proj / 'index.html').read_text(encoding='utf-8').split('window.THINKCUT = ', 1)[1].split(';</script>', 1)[0])
    check((w2, h2) == (1080, 1440) and d2['card'] == [0, 112, 1080, 1328] and d2['k'] == 1, 'HyperFrames 半尺寸预览：整帧和画面区都减半，设计稿不用改')
    fp = anim.fingerprint(hj, proj, ['A01']); (proj / '镜头' / '示例.html').write_text('x', encoding='utf-8'); same = anim.fingerprint(hj, proj, ['A01'])
    p1 = proj / '镜头' / 'A01.html'; p1.write_text(p1.read_text(encoding='utf-8') + '<!-- 改 -->', encoding='utf-8')
    check(fp == same and anim.fingerprint(hj, proj, ['A01']) != fp and len(anim.gate(hj, proj, ['A01'], True)) == 1, 'HyperFrames 镜头页面算进样张确认的指纹（示例和生成的 index.html 不算）；没确认整段出片会被拦')
    # 2026-11-20 审查：改一个镜头不作废同工程别的镜头的确认；共用/ 改了都作废
    fa = anim.fingerprint(hj, proj, ['A01']); (proj / '镜头' / 'A02.html').write_text('另一个镜头', encoding='utf-8'); fb = anim.fingerprint(hj, proj, ['A01'])
    fab = anim.fingerprint(hj, proj, ['A01', 'A02']); (proj / '镜头' / 'A02.html').write_text('另一个镜头改了', encoding='utf-8')
    js = proj / '共用' / '基础.js'; js0 = js.read_text(encoding='utf-8'); js.write_text(js0 + '//', encoding='utf-8'); fc = anim.fingerprint(hj, proj, ['A01']); js.write_text(js0, encoding='utf-8')
    check(fa == fb == anim.fingerprint(hj, proj, ['A01']) and fab != anim.fingerprint(hj, proj, ['A01', 'A02']) and fc != fa and [f.name for f in anim._code_files(proj)].count('A02.html') == 1,
          'HyperFrames 每个镜头的指纹只算自己的镜头页面（改 A02 不作废 A01 的确认），共用/ 改了都作废；不给镜头时全算')
    # 2026-11-24 初稿模式第二次实测：这条片子自己的共用画法 共用/本片_*.js 改了，全部镜头的确认作废；没有这个文件时指纹和以前一样
    g0 = anim.fingerprint(hj, proj, ['A01']); mine = proj / '共用' / '本片_画法.js'; mine.write_text('window.X = 1', encoding='utf-8'); g1 = anim.fingerprint(hj, proj, ['A01'])
    mine.write_text('window.X = 2', encoding='utf-8'); g2 = anim.fingerprint(hj, proj, ['A01']); mine.unlink()
    check(g0 != g1 != g2 and anim.fingerprint(hj, proj, ['A01']) == g0, 'HyperFrames 共用/本片_*.js（本片共用画法）算进指纹：改了镜头确认作废，删掉后和以前一样')
    # 出片.py 整段出片（不启动 HyperFrames，替换掉调用）：预览另起文件名、出好一个登记一个、出片时拿着 index.html 的锁
    import fcntl, threading, time
    def held():
        with open(proj / 'index.html.lock', 'w') as f:
            try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); return False
            except BlockingIOError: return True
    seen = []
    def fake_run(cmd):
        seen.append(held()); o = Path(cmd[cmd.index('-o') + 1]); o.write_bytes(b'x'); return ''
    hf_out.hf = lambda: 'hyperframes'; hf_out.run = fake_run; hf_out.preflight = lambda exe, sid: seen.append(held())
    hf_out.probe = lambda p: (120, 1080, 1440) if 'A01' in p.name else (1, 1, 1)
    B2 = json.loads((film / '分镜.json').read_text(encoding='utf-8')); B2['shots'].append({'id': 'A02', 'kind': 'B', 'a': 6.0, 'b': 8.0})
    (film / '分镜.json').write_text(json.dumps(B2, ensure_ascii=False), encoding='utf-8')
    shutil.copy(proj / '镜头' / 'A01.html', proj / '镜头' / 'A02.html'); (proj / '出片').mkdir(exist_ok=True); (proj / '出片' / 'A01.mp4').write_bytes(b'4K'); req0 = anim.require; anim.require = lambda *x: None; argv0 = sys.argv
    try:
        sys.argv = ['出片.py', '--shot', 'A01,A02']      # 2026-11-21 Q159 复发：不带参数就是 1080P 审片版（以前要记得加 --scale 50）
        try: hf_out.main(); err = ''
        except SystemExit as e: err = str(e)
        reg = rj(film / '合成' / '动画清单.json')['shots']; rs = rj(film / '合成' / '渲染尺寸.json')['items']
        check('A02 出片不对' in err and (proj / '出片' / 'A01.mp4').read_bytes() == b'4K' and (proj / '出片' / 'A01_预览50.mp4').exists()
              and list(reg) == ['A01'] and reg['A01']['file'].endswith('A01_预览50.mp4') and reg['A01']['scale'] == 50 and [(i['shot'], i['scale'], i['w']) for i in rs] == [('A01', 50, 1080)]
              and seen and all(seen), 'HyperFrames 出片.py 不带参数：1080P 审片版出成 <镜头>_预览50.mp4、登记 50%、不动 4K 文件；出好一个登记一个，后面的镜头失败时已登记的和磁盘一致；出片时拿着 index.html 的锁')
        probe1 = hf_out.probe; hf_out.probe = lambda p: (120, 2160, 2880)
        try: sys.argv = ['出片.py', '--shot', 'A01', '--4k']; hf_out.main()
        finally: hf_out.probe = probe1
        reg = rj(film / '合成' / '动画清单.json')['shots']; rs = rj(film / '合成' / '渲染尺寸.json')['items']
        check(reg['A01']['file'].endswith('/A01.mp4') and reg['A01']['scale'] == 100 and (proj / '出片' / 'A01.mp4').read_bytes() == b'x' and (proj / '出片' / 'A01_预览50.mp4').exists()
              and [(i['shot'], i['scale'], i['w']) for i in rs] == [('A01', 100, 2160)], 'HyperFrames 出片.py --4k（导出前才用）：出 4K 的 <镜头>.mp4、登记换成 100%，预览文件留着')
        def hold(sec):
            with open(proj / 'index.html.lock', 'w') as f: fcntl.flock(f, fcntl.LOCK_EX); time.sleep(sec)
        th = threading.Thread(target=hold, args=(1.0,)); th.start(); time.sleep(0.2); t0 = time.time()
        sys.argv = ['出片.py', '--预览', 'A01']; hf_out.main(); waited = time.time() - t0; th.join()
        check(waited > 0.6, f'--预览 也走 index.html 的锁：别的进程拿着锁时等它放开（等了 {waited:.1f} 秒）')
    finally: anim.require = req0; sys.argv = argv0
    # preflight：hyperframes check 报页面脚本错就停，并把原因显示出来（用假的 hyperframes 命令）
    fake = hd / 'fake_hf'; out_json = hd / 'chk.json'; fake.write_text(f'#!/bin/sh\ncat "{out_json}"\nexit 1\n'); fake.chmod(0o755)
    spec = importlib.util.spec_from_file_location('hf_out2', proj / '出片.py'); hf2 = importlib.util.module_from_spec(spec); spec.loader.exec_module(hf2)
    out_json.write_text(json.dumps({'ok': False, 'runtime': {'findings': [{'severity': 'error', 'message': '卡点「早」在成片 1 秒，不在镜头 A01 的起止 2–6 秒里\nD.at (x:1)'}]}}, ensure_ascii=False), encoding='utf-8')
    try: hf2.preflight(str(fake), 'A01'); e1 = ''
    except SystemExit as e: e1 = str(e)
    out_json.write_text(json.dumps({'ok': False, 'runtime': {'findings': [{'severity': 'warning', 'message': '小问题'}]}}), encoding='utf-8')
    try: hf2.preflight(str(fake), 'A01'); e2 = ''
    except SystemExit as e: e2 = str(e)
    check('页面脚本报错' in e1 and '卡点「早」' in e1 and 'D.at' not in e1 and e2 == '', '出样张/出片前 hyperframes check 查到页面脚本报错就停并显示原因，只有提醒不拦')
    # 共用/基础.js：卡点不在镜头起止里、时间轴排出镜头结尾都报错（node 跑，不开浏览器）
    node_js = '''
const fs = require("fs"); const el = () => ({ style: {}, classList: { add() {} } });
global.window = { THINKCUT: { shot: "A01", a: 2, b: 6, dur: 4.004, fps: 29.97, W: 1080, H: 1440, k: 1, card: [0, 112, 1080, 1328], anchors: { 早: 1.0, 中: 3.5, 晚: 6.5 } } };
global.document = { getElementById: el }; eval(fs.readFileSync(process.argv[1], "utf8")); const D = window.THINKCUT; const r = {};
const tryit = (k, f) => { try { r[k] = f(); } catch (e) { r[k] = "ERR:" + e.message; } };
tryit("早", () => D.at("早")); tryit("中", () => D.at("中")); tryit("晚", () => D.at("晚")); tryit("中加", () => D.at("中", 3));
const tl = (d) => ({ duration: () => d, set() {} }); tryit("pad好", () => (D.pad(tl(3.9)), "ok")); tryit("pad一帧内", () => (D.pad(tl(4.03)), "ok")); tryit("pad超", () => (D.pad(tl(5)), "ok"));
console.log(JSON.stringify(r));'''
    r = subprocess.run(['node', '-e', node_js, str(proj / '共用' / '基础.js')], capture_output=True, text=True)
    try: jr = json.loads(r.stdout)
    except ValueError: jr = {}
    check(abs((jr.get('中') or 0) - 1.5) < 1e-9 and all(str(jr.get(k, '')).startswith('ERR:') and '2–6' in jr[k] for k in ('早', '晚', '中加')) and '卡点「早」在成片 1 秒' in jr.get('早', '')
          and jr.get('pad好') == 'ok' and jr.get('pad一帧内') == 'ok' and str(jr.get('pad超', '')).startswith('ERR:') and '4.004' in jr['pad超'],
          '共用/基础.js：卡点不在镜头起止里就报错（写清卡点名、时间、镜头起止），时间轴超出镜头 1 帧以上报错' + ('' if jr else '：' + (r.stdout + r.stderr)[-_t(300):]))
    # _frames_at：取回的帧不够、又读不出视频帧数时报错，不静默返回空帧
    pf0 = anim._ffprobe_frames
    try:
        for fake_nf in (lambda p: 0, lambda p: int('x')):
            anim._ffprobe_frames = fake_nf
            try: anim._frames_at(hd / '没有这个.mp4', [0, 1], 4, 4); e3 = ''
            except SystemExit as e: e3 = str(e)
            check('读不出视频共几帧' in e3, '取帧不够又读不出视频帧数（返回 0 / 出错）时报错停下，不静默当成超出结尾')
    finally: anim._ffprobe_frames = pf0
    from thinkcut import status
    film0 = hd / '空片'; film0.mkdir(); (film0 / 'job.json').write_text(json.dumps({'fps': '30000/1001', 'format': '3:4', 'frames': 300}), encoding='utf-8')
    hint = status.anim_state(Job(film0))[2]
    check('hf-new' in hint and 'blender-new' in hint and '代码动画' not in hint, 'status 没有动画工程时提示 hf-new（HyperFrames）和 blender-new，不再提代码动画')
    r = subprocess.run([sys.executable, '-B', str(S), 'anim-new', str(film)], capture_output=True, text=True, cwd=str(hd))
    check(r.returncode != 0 and 'hf-new' in (r.stdout + r.stderr) and 'blender-new' in (r.stdout + r.stderr) and '已下线' in (r.stdout + r.stderr) and not (film / '动画').exists(),
          '老代码动画已下线：anim-new 停下，中文说明新片子用 blender-new、hf-new')
    png = hd / 's.png'; Image.new('RGB', (4, 4)).save(png)
    r = subprocess.run([sys.executable, '-B', str(S), 'anim-confirm', str(film), '--kind', '样张', '--project', 'HF_写错', '--shots', 'A01', '--files', str(png), '--words', '可以'], capture_output=True, text=True, cwd=str(hd))
    check(r.returncode != 0 and '动画工程文件夹不存在' in (r.stdout + r.stderr) and not (film / '合成' / '确认记录.json').exists(), '记确认时工程路径写错会拦下，不记一条对不上的确认')

r = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'thinkcut.test_tools_1112'], cwd=str(SK / 'scripts'), capture_output=True, text=True)
check(r.returncode == 0 and 'OK' in r.stderr.splitlines()[-1], '水电反馈单剪辑工具测试（thinkcut/test_tools_1112.py）' + ('' if r.returncode == 0 else '：' + r.stderr[-_t(600):]))
# 2026-11-21《别墅装灯前》：达芬奇写入锁、审核线零碎、口播降噪修音、音效卡关键词、没看过的样张、给用户看的 MD（剪辑工具助手加）
r = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'thinkcut.test_tools_1121'], cwd=str(SK / 'scripts'), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
check(r.returncode == 0 and 'OK' in r.stderr.splitlines()[-1], '别墅装灯反馈单剪辑工具测试（thinkcut/test_tools_1121.py）' + r.stdout.strip().replace('\n', '') + ('' if r.returncode == 0 else '：' + r.stderr[-_t(800):]))
# 2026-11-27《261114 千万的房子》新流程口播反馈单：剪口播交付后一条命令修音、分段响度补齐、口播精剪栏跟着点名的剪口播工程
r = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'thinkcut.test_newflow_1127'], cwd=str(SK / 'scripts'), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
check(r.returncode == 0 and 'OK' in r.stderr.splitlines()[-1], '261114 新流程口播反馈单测试（thinkcut/test_newflow_1127.py）' + ('' if r.returncode == 0 else '：' + r.stderr[-_t(800):]))
# 2026-11-12《水电阶段》动画与包装系统：包装系统组件、标签排版、布局检查、渲染前硬拦、尺寸拦截、总装（动画与包装系统会话加）
r = subprocess.run([sys.executable, '-B', str(root / 'bin' / 'test_anim_packaging.py')], capture_output=True, text=True, stdin=subprocess.DEVNULL)
check(r.returncode == 0, '水电反馈单动画与包装系统测试（bin/test_anim_packaging.py）' + ('' if r.returncode == 0 else '：' + (r.stdout + r.stderr)[-_t(800):]))
# 2026-11-21《别墅装灯》反馈单 新10/11/19：人挡字同取景、逐字遮挡、单条调位置、签名；花字类型；步骤卡并排
r = subprocess.run([sys.executable, '-B', str(root / 'bin' / 'test_occlusion_step_cards.py')], capture_output=True, text=True, stdin=subprocess.DEVNULL)
check(r.returncode == 0, '别墅装灯反馈单人挡字/步骤卡测试（bin/test_occlusion_step_cards.py）' + ('' if r.returncode == 0 else '：' + (r.stdout + r.stderr)[-_t(800):]))
# 2026-11-21《别墅装灯》新2/3/14/15/16：标签自检提速（新旧结果一致）、预览渲染并行、实拍/图片/盖实拍、标签直上直下
r = subprocess.run([sys.executable, '-B', str(root / 'bin' / 'test_anim_speedup_footage.py')], capture_output=True, text=True, stdin=subprocess.DEVNULL)
check(r.returncode == 0, '别墅装灯反馈单动画提速与实拍测试（bin/test_anim_speedup_footage.py）' + ('' if r.returncode == 0 else '：' + (r.stdout + r.stderr)[-_t(800):]))
# 2026-11-21《261118 装修最后那点细节》新1/8：人挡字出片前预查头顶空间、同一遍渲染的 A肉 不误报旧
r = subprocess.run([sys.executable, '-B', str(root / 'bin' / 'test_occlusion_precheck.py')], capture_output=True, text=True, stdin=subprocess.DEVNULL)
check(r.returncode == 0, '261118 反馈单人挡字预查/渲染批次测试（bin/test_occlusion_precheck.py）' + ('' if r.returncode == 0 else '：' + (r.stdout + r.stderr)[-_t(800):]))
# 2026-11-21《261118》新3：地图类画面硬拦、距离圈组件
r = subprocess.run([sys.executable, '-B', str(root / 'bin' / 'test_map_distance_ring.py')], capture_output=True, text=True, stdin=subprocess.DEVNULL)
check(r.returncode == 0, '261118 反馈单地图硬拦/距离圈测试（bin/test_map_distance_ring.py）' + ('' if r.returncode == 0 else '：' + (r.stdout + r.stderr)[-_t(800):]))
r = subprocess.run([sys.executable, '-B', str(root / 'bin' / 'test_anim_speedup.py')], capture_output=True, text=True, stdin=subprocess.DEVNULL)
check(r.returncode == 0, '动画提速测试：样张一次截完、镜头不同时出（bin/test_anim_speedup.py）' + ('' if r.returncode == 0 else '：' + (r.stdout + r.stderr)[-_t(800):]))
# 2026-11-21 体检“止血和清理”：渲染超时先叫停达芬奇、外部程序时限、写死路径自动找、达芬奇返回空不崩、审片工作台只收本页面请求、vendor/pp 下线
r = subprocess.run([sys.executable, '-B', str(root / 'bin' / 'test_audit_hotfix.py')], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
check(r.returncode == 0, '体检止血测试（bin/test_audit_hotfix.py）' + ('' if r.returncode == 0 else '：' + (r.stdout + r.stderr)[-_t(800):]))
# 2026-11-21 体检第二批：老代码动画模板下线（老片子仍能重出）、小修 a–h、用完即删的渲染临时文件放本机、说明瘦身
r = subprocess.run([sys.executable, '-B', str(root / 'bin' / 'test_audit_batch2.py')], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=900)
check(r.returncode == 0, '体检第二批测试（bin/test_audit_batch2.py）' + ('' if r.returncode == 0 else '：' + (r.stdout + r.stderr)[-_t(800):]))
r = subprocess.run([sys.executable, '-B', str(root / 'bin' / 'test_sandbox_findings.py')], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
check(r.returncode == 0, '试验田查出的问题（bin/test_sandbox_findings.py：修音后音轨开关恢复、修音轨开着）' + ('' if r.returncode == 0 else '：' + (r.stdout + r.stderr)[-_t(800):]))
r = subprocess.run([sys.executable, '-B', str(root / 'bin' / 'test_review_gate.py')], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
check(r.returncode == 0, '交审前硬拦（bin/test_review_gate.py：已定规矩清单、给用户的 MD、几何遮挡、人声响度底噪、黑名单音效、关键词偏差）' + ('' if r.returncode == 0 else '：' + (r.stdout + r.stderr)[-_t(800):]))
# 2026-11-21《261118》新6/7：说明里有“发图片卡片前先把项目文件夹加进会话、点不开用 open 弹出”；动画案例索引查得到 4 个新镜头和代码位置
skill_md = (SK / 'SKILL.md').read_text(encoding='utf-8'); idx = (SK / 'references' / '动画案例索引.md').read_text(encoding='utf-8')
check('项目文件夹已加进本会话' in skill_md and '`open`' in skill_md and '要看哪里、确认什么' in skill_md, 'SKILL.md 写了：发图片、视频卡片前先确认项目文件夹已加进会话，点不开用 open 弹出并说清看哪里、确认什么')
check(all(x in idx for x in ('HF_地图/镜头/B01.html', 'HF_地图/镜头/B03.html', 'HF_远程/镜头/B02.html', 'HF_机票/镜头/B04.html', '261118 装修最后那点细节'))
      and all(f'| {b} |' in idx.split('### 5.')[-1] for b in ('B01', 'B02', 'B03', 'B04')) and idx.count('装修细节 B0') >= 4 and '共用/距离圈.js' in idx,
      '动画案例索引：查得到《装修最后那点细节》B01–B04 四个镜头、各自的代码位置，“按画过的东西找”里有距离圈、手机聊天、登机牌')
# 2026-11-21 新9：审片工作台（达芬奇用替身，起真的本机服务走一遍接口）
r = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'thinkcut.test_workbench'], cwd=str(SK / 'scripts'), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
check(r.returncode == 0 and 'OK' in r.stderr.splitlines()[-1], '审片工作台测试（thinkcut/test_workbench.py）' + ('' if r.returncode == 0 else '：' + r.stderr[-_t(600):]))
# 2026-11-22 审片台：7 个栏目、发布／读结果／回复、页面点“可以”“选这个”“写要求”“改字”、动画确认 anim.gate 认、命令行不能替用户点（要视频项目管理的 script_doc.set_section；开发时 THINKCUT_PM 指到它的 bin）
r = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'thinkcut.test_board'], cwd=str(SK / 'scripts'), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
check(r.returncode == 0 and 'OK' in r.stderr.splitlines()[-1], '审片台测试（thinkcut/test_board.py）' + ('' if r.returncode == 0 else '：' + r.stderr[-_t(900):]))
# 2026-11-22 审片台“口播剪辑”栏：剪口播的文字剪辑器并进来（转发每个接口含 409／保存进度／分段读、没口令不转、按需起后台、口播阶段没有 job.json 也能开、init 后数据接着用）
r = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'thinkcut.test_draft'], cwd=str(SK / 'scripts'), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
check(r.returncode == 0 and 'OK' in r.stderr.splitlines()[-1], '初稿模式测试（thinkcut/test_draft.py：确认类不再拦渲染、导出最终闸只认审片台、机器配音没有人脸、占位图、审片台初稿和占位标记、五项进度）' + ('' if r.returncode == 0 else '：' + r.stderr[-_t(600):]))
r = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'thinkcut.test_xiubu_1123'], cwd=str(SK / 'scripts'), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
check(r.returncode == 0 and 'OK' in r.stderr.splitlines()[-1], '初稿模式实测后的修补（thinkcut/test_xiubu_1123.py：该做动画的提醒、全片假设、达芬奇切换记录和借用库、片名、记反馈用真时间、试听收视频、花字间隔/标蓝/白字压浅色、nav-draft、占位图总览）' + ('' if r.returncode == 0 else '：' + r.stderr[-_t(900):]))
r = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'thinkcut.test_wenan'], cwd=str(SK / 'scripts'), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
check(r.returncode == 0 and 'OK' in r.stderr.splitlines()[-1], '审片台口播剪辑栏测试（thinkcut/test_wenan.py）' + ('' if r.returncode == 0 else '：' + r.stderr[-_t(900):]))
# 2026-11-24 用户定：审片台第一栏“文案”（数据里叫 写稿）——在页面里写稿、和 AI 一起改（改写建议逐处采纳才落盘）、版本、定稿接出初稿
r = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'thinkcut.test_writing'], cwd=str(SK / 'scripts'), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
check(r.returncode == 0 and 'OK' in r.stderr.splitlines()[-1], '审片台文案栏测试（thinkcut/test_writing.py：读写各节、空白和贴进来、意见进 --wait、改写只有采纳才落盘、版本退回、定稿）' + ('' if r.returncode == 0 else '：' + r.stderr[-_t(900):]))
r = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'thinkcut.test_engine'], cwd=str(SK / 'scripts'), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
check(r.returncode == 0 and 'OK' in r.stderr.splitlines()[-1], 'AI 引擎测试（thinkcut/test_engine.py：替身 AI 走通提交→后台处理→采纳、接着会话、换引擎、停止、提问、出错、未装未登录、--wait 老办法、权限参数、钥匙不泄露）' + ('' if r.returncode == 0 else '：' + r.stderr[-_t(900):]))
# 2026-11-27 用户要：AI 对话侧栏（首页和审片台随时跟 AI 说一句）
r = subprocess.run([sys.executable, '-B', '-W', 'ignore', '-m', 'unittest', 'thinkcut.test_chat'], cwd=str(SK / 'scripts'), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
check(r.returncode == 0 and 'OK' in r.stderr.splitlines()[-1], 'AI 对话侧栏测试（thinkcut/test_chat.py：替身 AI 走通首页开项→接到新项目、拖入素材登记、说路径放素材、存档走代办、停止、提问选项、导出发布定稿被拦、和提交 AI 共用会话不同时跑、未登录、记录和清空）' + ('' if r.returncode == 0 else '：' + r.stderr[-_t(900):]))
r = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'thinkcut.test_animclip'], cwd=str(SK / 'scripts'), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
check(r.returncode == 0 and 'OK' in r.stderr.splitlines()[-1], '动画栏带口播播放测试（thinkcut/test_animclip.py）' + ('' if r.returncode == 0 else '：' + r.stderr[-_t(900):]))
# 2026-11-25 发布前 QA 的后台问题：口令只在 HttpOnly cookie、job.json 现读、闲置退出和端口回收、坏文件能恢复、发布卡住能撤回、新建加锁去重、撤销回原 IP、长题目、编号不重用
r = subprocess.run([sys.executable, '-B', '-W', 'ignore', '-m', 'unittest', 'thinkcut.test_qa_1125'], cwd=str(SK / 'scripts'), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
check(r.returncode == 0 and 'OK' in r.stderr.splitlines()[-1], '发布前 QA 回归测试（thinkcut/test_qa_1125.py）' + ('' if r.returncode == 0 else '：' + r.stderr[-_t(900):]))
r = subprocess.run([sys.executable, '-B', '-W', 'ignore', '-m', 'unittest', 'thinkcut.test_home'], cwd=str(SK / 'scripts'), capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)
check(r.returncode == 0 and 'OK' in r.stderr.splitlines()[-1], '首页测试（thinkcut/test_home.py：扫片子和四步阶段、按需起单片审片台并复用、新建片子、右键改名／移到做完的／移到 NAS 回收站再拿回来、封面小图、点开先到 /open 再跳、备料拖进来和登记路径（同名不覆盖、大文件、半截不留）、等所有片子、命令行不能替用户点）' + ('' if r.returncode == 0 else '：' + r.stderr[-_t(900):]))
# 2026-11-21 新19：导航条底（nav_bg）。不连达芬奇，只用 ffmpeg 造假导航条
from thinkcut import overlay
check(overlay.NAV_BG_NEW == 'dim' and set(overlay.NAV_BGS) == {'opaque', 'dim', 'clear'}, '新片子默认导航条底是半透明深色（dim），三种做法 opaque/dim/clear')
with tempfile.TemporaryDirectory() as nd:
    nd = Path(nd).resolve(); old_job = nd / 'old'; new_job = nd / 'new'
    for d_, extra in ((old_job, {}), (new_job, {'nav_h': 224, 'nav_bg': 'dim'})):
        d_.mkdir(); (d_ / 'job.json').write_text(json.dumps({'fps': '30', 'format': '3:4满屏', 'frames': 6, **extra}), encoding='utf-8')
    jo, jn = Job(old_job), Job(new_job)
    check(overlay.nav_bg(jo) == 'opaque' and overlay.nav_style_hint(jo) == {} and overlay.nav_style_hint(jn) == {'background_mode': 'dim'}, '老片子 job.json 没有 nav_bg＝不透明黑底、不要求写 style；新片子要求章节导航 style 写 background_mode: dim')
    mp4, mov = nd / 'bar.mp4', nd / 'bar.mov'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=black:s=2160x340:r=30:d=0.2', '-pix_fmt', 'yuv420p', str(mp4)], check=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=black@0.45:s=2160x224:r=30:d=0.2,format=rgba', '-c:v', 'prores_ks', '-profile:v', '4444', '-pix_fmt', 'yuva444p10le', str(mov)], check=True)
    r = subprocess.run([sys.executable, '-B', str(S), 'nav', str(new_job), str(mp4)], capture_output=True, text=True)
    check(r.returncode != 0 and 'nav_bg' in (r.stdout + r.stderr) and 'background_mode' in (r.stdout + r.stderr) and not (new_job / '合成/导航/导航整帧.mov').exists(), '半透明底的片子给了不透明 mp4：拦下，说明怎么重出')
    r = subprocess.run([sys.executable, '-B', str(S), 'nav', str(old_job), str(mp4)], capture_output=True, text=True)
    check(r.returncode == 0 and (old_job / '合成/导航/导航整帧.mov').exists(), '老片子（没有 nav_bg）给不透明 mp4：照旧转成整帧片')
    r = subprocess.run([sys.executable, '-B', str(S), 'nav', str(new_job), str(mov)], capture_output=True, text=True)
    px = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(new_job / '合成/导航/导航整帧.mov'), '-frames:v', '1', '-vf', 'crop=1:1:5:5,format=rgba,pad=1:1', '-f', 'rawvideo', '-'], capture_output=True).stdout
    low = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(new_job / '合成/导航/导航整帧.mov'), '-frames:v', '1', '-vf', 'crop=1:1:5:2000,format=rgba', '-f', 'rawvideo', '-'], capture_output=True).stdout
    check(r.returncode == 0 and len(px) == 4 and abs(px[3] - 115) <= 3 and low[3] == 0, '半透明底的整帧片：条内保留 45% 透明度，条以外（画面其余部分）全透明')
print('全部测试通过' if not fails else f'有 {len(fails)} 项失败')
sys.exit(1 if fails else 0)
PY
