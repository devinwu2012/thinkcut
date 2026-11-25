#!/usr/bin/env python3
"""配置档的离线测试（2026-11-24）：读法和分层、新建 IP，以及用一个假的第二 IP（示例IP）把建项目、出封面、配音参数、上传预检各走到不需要外部服务的那一步，
证明换 IP 真的换了行为、两个 IP 互不串。测试用自己造的“测试IP”（和示例IP 风格不同），不靠任何真 IP 的配置档。不联网、不调豆包和蚁小二、不碰用户的片子。
  python3 bin/test_cutdesk_config.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
os.environ['THINKCUT_APP_FLAVOR'] = 'release'           # 开发版会把片子文件夹换成试验田（见 Flavor）；这里测正式版的读法
os.environ['THINKCUT_RESOLVE_PREFS'] = tempfile.mkdtemp(prefix='假达芬奇设置_')      # 建项目会登记达芬奇库列表：测试只碰假的
os.environ['THINKCUT_RESOLVE_LOCK'] = os.path.join(tempfile.mkdtemp(prefix='假达芬奇锁_'), 'lock')
sys.path.insert(0, str(ROOT / 'bin'))
import cutdesk_config as C  # noqa: E402

PY = [sys.executable, '-B']
from 测试配置档 import TEST_IP  # noqa: E402  测试用的 IP：颜色、音色、平台都和 示例IP 不一样，好证明两个 IP 互不串（默认 IP 也是它）


def run(*cmd, env=None, cwd=None):
    return subprocess.run([*PY, *map(str, cmd)], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          env=dict(os.environ, **(env or {})), cwd=cwd)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.t = Path(self.tmp.name).resolve()
        self.prof = self.t / '配置档'; shutil.copytree(ROOT / '配置档', self.prof, ignore=shutil.ignore_patterns('本机.json'))
        for d in self.prof.iterdir():                               # 只留 公共.json 和 示例IP，再放测试IP
            if d.is_dir() and d.name != '示例IP': shutil.rmtree(d)
        (self.prof / '测试IP').mkdir(); (self.prof / '测试IP' / 'ip.json').write_text(json.dumps(TEST_IP, ensure_ascii=False), encoding='utf-8')
        (self.prof / '测试IP' / '规矩.md').write_text('# 测试IP 的规矩\n', encoding='utf-8')
        pub = json.loads((self.prof / '公共.json').read_text(encoding='utf-8')); pub['默认IP'] = '测试IP'
        (self.prof / '公共.json').write_text(json.dumps(pub, ensure_ascii=False), encoding='utf-8')
        self.env = mock.patch.dict(os.environ, {'THINKCUT_PROFILES': str(self.prof)}); self.env.start()
        os.environ.pop('THINKCUT_IP', None)

    def tearDown(self):
        self.env.stop(); self.tmp.cleanup()

    def project(self, ip=None, name='261201 测试'):
        d = self.t / '片子' / name; d.mkdir(parents=True)
        (d / '项目.json').write_text(json.dumps({'name': name, **({'ip': ip} if ip else {})}, ensure_ascii=False), encoding='utf-8')
        (d / '02_制作' / '自动剪辑').mkdir(parents=True)
        return d


class Reading(Base):
    def test_ip_values_and_common_defaults(self):
        c = C.load()
        self.assertEqual(c.ip, '测试IP')
        self.assertEqual(c.path('片子文件夹'), C.PROGRAM.parent / '测试IP视频')        # 公共层 ../{IP}视频
        self.assertEqual(c.get('做完的片子子文件夹'), 'finish')
        self.assertEqual(c.path('回收站'), C.PROGRAM.parent / '#recycle')
        self.assertEqual(c.get('配音'), {'音色': 'zh_female_xiaohe_uranus_bigtts', '音色名': '小何'})
        self.assertEqual(tuple(c.get('发布.平台')), ('抖音', '快手', '小红书', '视频号', '哔哩哔哩', 'Youtube'))
        self.assertEqual((c.get('发布.youtube分类'), c.get('发布.原创'), c.get('发布.只发这些平台')), ('26', True, False))
        self.assertEqual(tuple(c.get('发布.领域词')), ('装修', '家装', '家居', '房', '设计', '避坑', '知识', '科普', '干货'))
        f = c.get('封面')
        self.assertEqual((tuple(f['标题色']), tuple(f['副标题字色']), f['主角色相'], tuple(f['圈箭头色']), f['标题横向压缩']),
                         ((38, 84, 160), (250, 244, 236), 215, (226, 84, 44), 0.88))
        self.assertEqual((f['标题字体'], f['副标题字体']), ('/System/Library/Fonts/Supplemental/Songti.ttc', '/System/Library/Fonts/Hiragino Sans GB.ttc'))
        self.assertEqual((c.get('剪辑.画幅'), c.get('剪辑.字幕字体')), ('3:4满屏', '/System/Library/Fonts/Hiragino Sans GB.ttc'))   # IP 盖公共层（公共层是 3:4）
        self.assertTrue(c.rules_file.is_file())

    def test_layers_merge_in_order(self):
        (self.prof / '本机.json').write_text(json.dumps({'片子文件夹': '/别处/片子', '发布': {'原创': False}}, ensure_ascii=False), encoding='utf-8')
        c = C.load()
        self.assertEqual(c.path('片子文件夹'), Path('/别处/片子'))                       # 本机盖 IP
        self.assertEqual((c.get('发布.原创'), c.get('发布.youtube分类')), (False, '26'))   # 字典逐层合并：只盖写了的那项
        d = self.project('测试IP'); meta = json.loads((d / '项目.json').read_text(encoding='utf-8'))
        meta['配置覆盖'] = {'剪辑': {'画幅': '9:16'}}; (d / '项目.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
        self.assertEqual(C.load(project=d / '02_制作' / '自动剪辑').get('剪辑.画幅'), '9:16')   # 单条片子盖所有层；往上找 项目.json
        self.assertEqual(C.load().get('剪辑.画幅'), '3:4满屏')

    def test_which_ip(self):
        a, b, old = self.project('示例IP', 'a'), self.project('测试IP', 'b'), self.project(None, 'old')
        self.assertEqual(C.load(project=a).ip, '示例IP')
        self.assertEqual(C.load(project=b).ip, '测试IP')
        self.assertEqual(C.load(project=old).ip, '测试IP')                                    # 改之前建的片子没写 ip：默认 IP，行为不变
        self.assertEqual(C.load(ip='测试IP', project=a).ip, '测试IP')                          # 明说的优先
        with mock.patch.dict(os.environ, {'THINKCUT_IP': '示例IP'}): self.assertEqual(C.load(project=b).ip, '示例IP')
        (self.prof / '本机.json').write_text('{"默认IP": "示例IP"}', encoding='utf-8')
        self.assertEqual(C.load(project=old).ip, '示例IP')

    def test_errors_are_clear(self):
        with self.assertRaises(SystemExit) as e: C.load(ip='没有这个')
        self.assertIn('现有：测试IP、示例IP', str(e.exception))
        with self.assertRaises(SystemExit) as e: C.load().get('不存在.的键')
        self.assertIn('没有 不存在.的键', str(e.exception))
        self.assertEqual(C.load().get('不存在', 5), 5)
        (self.prof / '测试IP' / 'ip.json').write_text('{坏', encoding='utf-8')
        with self.assertRaises(SystemExit) as e: C.load()
        self.assertIn('不是合法的 JSON', str(e.exception))

    def test_new_ip(self):
        r = run(ROOT / 'bin' / 'cutdesk_config.py', 'new', '第三个', env={'THINKCUT_PROFILES': str(self.prof)})
        self.assertEqual(r.returncode, 0, r.stderr)
        c = C.load(ip='第三个')
        self.assertEqual((c.get('名字'), c.path('片子文件夹')), ('第三个', C.PROGRAM.parent / '第三个视频'))
        self.assertEqual(c.get('配音.音色名'), 'vv')                                        # 从示例IP复制
        self.assertTrue((self.prof / '第三个' / '规矩.md').is_file())
        r = run(ROOT / 'bin' / 'cutdesk_config.py', 'new', '测试IP二号', '--from', '测试IP', env={'THINKCUT_PROFILES': str(self.prof)})
        c2 = C.load(ip='测试IP二号')
        self.assertEqual((c2.get('配音.音色名'), c2.get('封面.主角色相'), c2.path('片子文件夹').name), ('小何', 215, '测试IP二号视频'))   # 复用测试IP的风格，片子分开放
        for bad in ('测试IP', 'a/b', '.x'):
            with self.assertRaises(SystemExit): C.new_ip(bad)
        self.assertEqual(run(ROOT / 'bin' / 'cutdesk_config.py', 'rules', '--ip', '测试IP', env={'THINKCUT_PROFILES': str(self.prof)}).returncode, 0)

    def test_new_blank_ip(self):
        """首页“新建 IP…”：只写名字，其余全跟公共层（2026-11-24 用户定）。"""
        r = run(ROOT / 'bin' / 'cutdesk_config.py', 'new', '空白的', '--blank', env={'THINKCUT_PROFILES': str(self.prof)})
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn('空白', r.stdout)
        raw = json.loads((self.prof / '空白的' / 'ip.json').read_text(encoding='utf-8'))
        self.assertEqual({k for k in raw if not k.startswith('_')}, {'名字'}, 'ip.json 只写名字')
        c, pub = C.load(ip='空白的'), C._read(self.prof / '公共.json')
        for k in ('剪辑', '发布', '回收站'): self.assertEqual(c.get(k), pub[k], k)
        self.assertEqual(c.get('封面.文件名前缀'), '封面_'); self.assertEqual(c.path('片子文件夹'), C.PROGRAM.parent / '空白的视频', '片子文件夹按公共层的 {IP}视频，各放各的')
        self.assertEqual(C.new_ip('  两头空格 ', None).name, '两头空格', '名字两头的空格去掉')
        for bad in ('空白的', '测试ip', '', '   ', 'a:b', '_x', 'x' * 41):
            with self.assertRaises(SystemExit, msg=bad): C.new_ip(bad, None)

    def test_remove_and_restore_ip(self):
        """删 IP：配置挪到回收站（能还原），片子文件夹不动；默认 IP、没有的、只剩一个的都不能删；首页记着它就一起去掉。"""
        box = self.t / '回收站'; box.mkdir()
        C.new_ip('要删的', None); C.set_home_ip('要删的')
        films = C.load(ip='要删的').path('片子文件夹')
        to = C.remove_ip('要删的', box)
        self.assertEqual(to.parent, box / 'ThinkCut配置档'); self.assertTrue((to / 'ip.json').is_file()); self.assertNotIn('要删的', C.ips())
        self.assertIsNone(C.home_ip()); self.assertNotIn('首页IP', json.loads((self.prof / '本机.json').read_text(encoding='utf-8')))
        self.assertEqual(C.restore_ip(to), '要删的'); self.assertIn('要删的', C.ips(), '撤销：挪回来')
        with self.assertRaises(SystemExit): C.restore_ip(to)
        for bad in ('测试IP', '没有这个'):
            with self.assertRaises(SystemExit, msg=bad): C.remove_ip(bad, box)
        with self.assertRaises(SystemExit): C.remove_ip('要删的', self.t / '不在的回收站')
        self.assertIn('要删的', C.ips(), '回收站不在：没删')
        C.remove_ip('要删的', box); C.remove_ip('示例IP', box)
        with self.assertRaises(SystemExit): C.remove_ip('测试IP', box)
        r = run(ROOT / 'bin' / 'cutdesk_config.py', 'new', '没样板也能建', env={'THINKCUT_PROFILES': str(self.prof)})
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn('空白', r.stdout, '示例IP 删了：new 建空白的')
        self.assertFalse(films.exists() and any(films.iterdir()), '没碰片子文件夹')

    def test_home_ip_remembered(self):
        """首页选的 IP 记在 本机.json 的 "首页IP"：别的键原样留着、不改默认 IP、不混进配置值；记的 IP 没了就当没记。"""
        self.assertIsNone(C.home_ip(), '没选过')
        (self.prof / '本机.json').write_text(json.dumps({'片子文件夹': '/x/{IP}', '_说明': '这台机器'}, ensure_ascii=False), encoding='utf-8')
        C.set_home_ip('示例IP'); self.assertEqual(C.home_ip(), '示例IP')
        raw = json.loads((self.prof / '本机.json').read_text(encoding='utf-8'))
        self.assertEqual(raw, {'片子文件夹': '/x/{IP}', '_说明': '这台机器', '首页IP': '示例IP'})
        self.assertEqual(C.default_ip(), '测试IP', '首页选的不改默认 IP（AI 命令行不带 --ip 时照旧）')
        self.assertNotIn('首页IP', C.load(ip='测试IP')); self.assertEqual(C.load().ip, '测试IP')
        with self.assertRaises(SystemExit): C.set_home_ip('没有这个')
        shutil.rmtree(self.prof / '示例IP'); self.assertIsNone(C.home_ip(), '记的 IP 被删了：当没记')
        (self.prof / '本机.json').write_text('{坏的', encoding='utf-8')
        with self.assertRaises(SystemExit): C.set_home_ip('测试IP')
        self.assertEqual((self.prof / '本机.json').read_text(encoding='utf-8'), '{坏的', '本机.json 坏了不覆盖')


class UserProfiles(Base):
    """2026-11-25 QA 1.6：用户的 IP 放程序文件夹外面的用户配置档，首页新建／删除 IP 不再改程序文件，发版不被拦。"""
    def setUp(self):
        super().setUp(); self.user = self.t / '用户配置档'
        (self.user / '测试IP').mkdir(parents=True); shutil.move(str(self.prof / '测试IP' / 'ip.json'), self.user / '测试IP' / 'ip.json')
        shutil.rmtree(self.prof / '测试IP')                                   # 程序带的只剩 公共.json＋示例IP，测试IP 在用户配置档
        self.u = mock.patch.dict(os.environ, {'THINKCUT_USER_PROFILES': str(self.user)}); self.u.start()

    def tearDown(self):
        self.u.stop(); super().tearDown()

    def program_files(self):
        return sorted(str(p.relative_to(self.prof)) for p in self.prof.rglob('*') if p.name != '本机.json')

    def test_new_remove_restore_never_touch_program(self):
        before = self.program_files(); box = self.t / '回收站'; box.mkdir()
        self.assertEqual(C.ips(), ['测试IP', '示例IP']); self.assertEqual(C.load().folder, self.user / '测试IP')
        C.new_ip('新的', None); self.assertTrue((self.user / '新的' / 'ip.json').is_file()); self.assertIn('新的', C.ips())
        C.new_ip('复制的', '示例IP'); self.assertTrue((self.user / '复制的' / '规矩.md').is_file())
        to = C.remove_ip('新的', to=box); self.assertFalse((self.user / '新的').exists()); self.assertNotIn('新的', C.ips())
        self.assertEqual(C.restore_ip(to), '新的'); self.assertIn('新的', C.ips())
        self.assertEqual(self.program_files(), before)                             # 程序文件夹一个文件都没多、没少

    def test_remove_shipped_ip_hides_it(self):
        """删随程序发的 示例IP：程序文件不动，本机.json 记“隐藏”，回收站放一份拷贝；还原放进用户配置档。"""
        before = self.program_files(); box = self.t / '回收站'; box.mkdir()
        to = C.remove_ip('示例IP', to=box)
        self.assertNotIn('示例IP', C.ips()); self.assertEqual(self.program_files(), before); self.assertTrue((to / 'ip.json').is_file())
        self.assertEqual(json.loads((self.prof / '本机.json').read_text(encoding='utf-8'))['隐藏的IP'], ['示例IP'])
        self.assertEqual(C.load(ip='示例IP').ip, '示例IP')                          # 只是不列出来、不能切过去；它的老片子照样读得到配置
        self.assertEqual(C.restore_ip(to), '示例IP'); self.assertIn('示例IP', C.ips())
        self.assertEqual(C.load(ip='示例IP').folder, self.user / '示例IP')
        self.assertEqual(json.loads((self.prof / '本机.json').read_text(encoding='utf-8'))['隐藏的IP'], [])

    def test_old_ip_in_program_moves_out(self):
        """升级前首页在程序文件夹里建的 IP：第一次读配置就挪进用户配置档；用户配置档已有同名的不动它。"""
        (self.prof / '老的').mkdir(); (self.prof / '老的' / 'ip.json').write_text('{"名字": "老的"}', encoding='utf-8')
        C._migrated.clear(); self.assertIn('老的', C.ips())
        self.assertFalse((self.prof / '老的').exists()); self.assertTrue((self.user / '老的' / 'ip.json').is_file())

    def test_home_ip_tmp_name_not_fixed(self):
        C.set_home_ip('示例IP'); self.assertEqual(C.home_ip(), '示例IP')
        self.assertEqual([p.name for p in self.prof.glob('.*tmp')], [])


class Flavor(Base):
    """QA 1.1：开发版（程序文件夹名以 -开发 结尾、或是 git worktree）默认看试验田，不碰真片子；THINKCUT_APP_FLAVOR 能强制。"""
    def test_dev_uses_trial_folder(self):
        with mock.patch.dict(os.environ, {'THINKCUT_APP_FLAVOR': 'dev'}):
            self.assertEqual(C.load().path('片子文件夹'), C.PROGRAM.parent / '测试IP试验田')
        with mock.patch.dict(os.environ, {'THINKCUT_APP_FLAVOR': 'release'}):
            self.assertEqual(C.load().path('片子文件夹'), C.PROGRAM.parent / '测试IP视频')

    def test_detect(self):
        with mock.patch.dict(os.environ, {'THINKCUT_APP_FLAVOR': ''}):
            self.assertEqual(C.flavor(self.t / 'thinkcut-开发'), 'dev'); self.assertEqual(C.flavor(self.t / 'thinkcut'), 'release')
            wt = self.t / 'wt'; wt.mkdir(); (wt / '.git').write_text('gitdir: x', encoding='utf-8'); self.assertEqual(C.flavor(wt), 'dev')


class SecondIP(Base):
    """示例IP 和测试IP各走一遍：建项目、封面、配音参数、上传预检的平台。"""

    def test_project_init(self):
        sys.path.insert(0, str(ROOT / '视频项目管理' / 'bin'))
        import project as P
        root = self.t / '根'
        d1, m1 = P.initialize(root / '261201 测试IP的')
        d2, m2 = P.initialize(root / '261201 示例的', ip='示例IP')
        self.assertEqual((m1['ip'], m1['youtube_category'], m1['original']), ('测试IP', '26', True))
        self.assertEqual((m2['ip'], m2['youtube_category'], m2['original']), ('示例IP', '', False))
        self.assertEqual(P.platforms_of(d1), ('抖音', '快手', '小红书', '视频号', '哔哩哔哩', 'Youtube'))
        self.assertEqual(P.platforms_of(d2), ('抖音', '小红书', '视频号'))
        accounts = [{'platformName': n, 'status': 1} for n in ('抖音', '快手', '小红书', '视频号', '哔哩哔哩', 'Youtube')]
        self.assertEqual(P.platform_gaps(accounts[:3], [], P.platforms_of(d2)), {'抖音': '项目.json 里没启用', '小红书': '项目.json 里没启用', '视频号': '蚁小二里没绑定这个平台的账号'})
        self.assertEqual(set(P.platform_gaps(accounts[:3], None, P.platforms_of(d1))), {'视频号', '哔哩哔哩', 'Youtube'})
        import activities as A
        with mock.patch.object(A, 'command', side_effect=RuntimeError('不该联网')):
            try: A.candidates(d2, {'tags': ['红烧肉']}, run=lambda *a, **k: {'data': []})
            except RuntimeError: pass
        self.assertIn('美食', [w for w in C.load(project=d2).get('发布.领域词')])
        self.assertNotIn('装修', C.load(project=d2).get('发布.领域词'))
        # 命令行：--ip 不写 --root 时用那个 IP 的片子文件夹
        (self.prof / '本机.json').write_text(json.dumps({'片子文件夹': str(self.t / '{IP}的片子')}, ensure_ascii=False), encoding='utf-8')
        r = run(ROOT / '视频项目管理' / 'bin' / 'project.py', 'init', '261202 命令行', '--ip', '示例IP', env={'THINKCUT_PROFILES': str(self.prof)})
        self.assertTrue((self.t / '示例IP的片子' / '261202 命令行' / '项目.json').is_file(), r.stdout + r.stderr)
        self.assertEqual(json.loads((self.t / '示例IP的片子' / '261202 命令行' / '项目.json').read_text(encoding='utf-8'))['ip'], '示例IP')
        self.assertFalse((self.t / '测试IP的片子').exists())

    def test_tts_defaults(self):
        sys.path.insert(0, str(ROOT / '口播剪辑' / 'bin'))
        import doubao_tts as T
        for ip, voice, fmt in (('测试IP', 'zh_female_xiaohe_uranus_bigtts', '3:4满屏'), ('示例IP', 'zh_female_vv_uranus_bigtts', '3:4')):
            d = self.project(ip, '配音_' + ip); (d / '01_原始素材').mkdir()
            seen = {}
            def fake_build(root, text, note, args, **kw): seen.update(speaker=args.speaker, format=args.format); return None
            with mock.patch.object(T, 'build', fake_build):
                T.main(['--project', str(d), '--script', str(self.write(d / 'a.txt', '你好。')), '--dry-run', '--no-prepare'])
            self.assertEqual((seen['speaker'], seen['format']), (voice, fmt), ip)

    def write(self, p, s):
        p.write_text(s, encoding='utf-8'); return p

    def test_cover_style_changes_with_ip(self):
        cover = ROOT / '封面' / 'skill' / 'thinkcut-cover' / 'scripts' / 'thinkcut_cover.py'
        sample = ROOT / '封面' / 'samples' / '烟道这样包'
        out = {}
        for ip in ('测试IP', '示例IP'):
            d = self.project(ip, '封面_' + ip) / '03_成片' / '封面'; (d / '_制作文件').mkdir(parents=True)
            for f in ('spec.json', 'bg_portrait.png', 'bg_landscape.png'): shutil.copy(sample / f, d / '_制作文件' / f)
            r = run(cover, 'render', d, '--ratios', '3:4', env={'THINKCUT_PROFILES': str(self.prof)})
            self.assertEqual(r.returncode, 0, r.stderr[-400:])
            qc = json.loads((d / '_制作文件' / 'qc_3x4.json').read_text())
            out[ip] = Path(qc[0]['file'])
        from PIL import Image
        import numpy as np
        def near(p, rgb):
            a = np.asarray(Image.open(p).convert('RGB')).astype(int)
            return int((np.abs(a - rgb).max(axis=2) < 30).sum())
        self.assertGreater(near(out['测试IP'], (38, 84, 160)), 2000)          # 测试IP：深蓝标题
        self.assertLess(near(out['示例IP'], (38, 84, 160)), 200)
        self.assertGreater(near(out['示例IP'], (30, 110, 70)), 2000)       # 示例IP：深绿标题
        self.assertLess(near(out['测试IP'], (30, 110, 70)), 200)



class ExportClean(unittest.TestCase):
    """一键导出干净版本：不带 git 历史、不带自己的 IP 配置档和内部记录；导出的那份自己能跑配置命令。"""

    def test_export(self):
        import export_clean as E
        with tempfile.TemporaryDirectory() as t:
            dest = Path(t) / '干净版'; lines = []
            got = E.export(dest, log=lines.append)
            names = {str(p.relative_to(dest)) for p in dest.rglob('*')}
            self.assertFalse((dest / '.git').exists())
            self.assertFalse(any(n.startswith('内部') for n in names), '内部记录不该带走')
            self.assertEqual({p.name for p in (dest / '配置档').iterdir()}, {'公共.json', '示例IP'}, '自己的 IP 配置档不该带走')
            self.assertFalse(any(p.is_symlink() for p in dest.rglob('*')), '给旧名字留的软链接不该带走')
            self.assertFalse((dest / 'bin' / '旧名兼容.sh').exists())
            self.assertFalse(any(n.endswith('CHANGELOG.md') or n.startswith('口播自动剪辑/经验') for n in names))
            for f in ('bin/release.py', 'bin/placeholder.py', 'bin/merge_once.py', 'bin/dev.sh'): self.assertNotIn(f, names)
            self.assertTrue((dest / '配置档' / '示例IP' / 'ip.json').is_file() and (dest / 'bin' / 'cutdesk_config.py').is_file())
            r = subprocess.run([*PY, str(dest / 'bin' / 'cutdesk_config.py'), 'list'], capture_output=True, text=True, env={k: v for k, v in os.environ.items() if k != 'THINKCUT_PROFILES'})
            self.assertEqual(r.stdout.split(), ['示例IP（默认）'], r.stderr)
            self.assertEqual(got['left'].get('人名', 0), 0, lines)          # 反馈人名字只在自己 IP 的 ip.json，导出里没有
            self.assertTrue(any('还剩这些个人痕迹' in l or '没查到' in l for l in lines))
            with self.assertRaises(SystemExit): E.export(dest, log=lambda *_: None)      # 已存在不覆盖
            with self.assertRaises(SystemExit): E.export(ROOT / '里面', log=lambda *_: None)


if __name__ == '__main__':
    r = unittest.main(exit=False, verbosity=1).result
    print('全部测试通过' if r.wasSuccessful() else '有测试未通过')
    sys.exit(0 if r.wasSuccessful() else 1)
