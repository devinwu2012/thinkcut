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
