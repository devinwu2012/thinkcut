import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from engine.library import catalog, search

FFMPEG = shutil.which('ffmpeg') and shutil.which('ffprobe')


def make_video(path, seconds=2, size='64x112', color='red'):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color={color}:s={size}:r=10', '-t', str(seconds),
                    '-pix_fmt', 'yuv420p', str(path)], check=True, stdin=subprocess.DEVNULL)


def quiet(*_args, **_kwargs):
    pass


class LocationTests(unittest.TestCase):
    def test_videos_root_default_follows_the_share_the_repository_sits_on(self):
        # 片子文件夹按配置档写的相对程序目录的位置推（默认 IP 的 ../<IP>视频 这类）：NAS 挂成 /Volumes/work-1 也跟着变
        home = Path(__file__).resolve().parents[3]   # 本仓库的上一级（程序目录）
        self.assertEqual(catalog.VIDEOS_ROOT, catalog.thinkcut_config.load().path('片子文件夹'))
        self.assertEqual(catalog.VIDEOS_ROOT.parent, home.parent)
        self.assertEqual(catalog.INDEX_DIR, home/'素材库'/'索引')


@unittest.skipUnless(FFMPEG, '缺 ffmpeg')
class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='library-test-'))
        self.root = self.tmp/'片子'
        self.index = self.tmp/'索引'
        self.project = self.root/'261022 测试项目'
        raw = self.project/'01_原始素材'
        make_video(raw/'B roll'/'地漏芯老化_展示不密封_01.mov')
        make_video(raw/'B roll'/'通气阀桌面_摆拍展示_01.mov', size='112x64', color='blue')
        make_video(raw/'A roll'/'讲解_口播录制_01.mov', color='gray')
        items = [
            {'category': 'B roll', 'content': '地漏芯老化', 'purpose': '展示不密封', 'path': '01_原始素材/B roll/地漏芯老化_展示不密封_01.mov'},
            {'category': 'B roll', 'content': '通气阀桌面', 'purpose': '摆拍展示', 'path': '01_原始素材/B roll/通气阀桌面_摆拍展示_01.mov', '说明': '桌面上摆着白色通气阀'},
            {'category': 'A roll', 'content': '讲解', 'purpose': '口播录制', 'path': '01_原始素材/A roll/讲解_口播录制_01.mov'}]
        (raw/'素材清单.json').write_text(json.dumps({'schema_version': 1, 'items': items}, ensure_ascii=False), encoding='utf-8')
        cut = self.cut = self.project/'02_制作'/'自动剪辑'
        beats = {'shots': [{'id': 'F_0', 'kind': 'F', 'a': 0, 'b': 1}, {'id': 'R01', 'kind': 'B', 'a': 1, 'b': 2}, {'id': 'S02', 'kind': 'B', 'a': 2, 'b': 3.5}]}
        (cut/'卡片合成').mkdir(parents=True)
        (cut/'分镜.json').write_text(json.dumps(beats), encoding='utf-8')
        words = [{'c': c, 't0': 1+i*0.1, 't1': 1.05+i*0.1} for i, c in enumerate('要不然地漏芯坏了')]
        words += [{'c': c, 't0': 2.2+i*0.1, 't1': 2.25+i*0.1} for i, c in enumerate('水封被抽干')]
        (cut/'口播字时间.json').write_text(json.dumps({'tokens': words}, ensure_ascii=False), encoding='utf-8')
        (cut/'卡片合成'/'make_r.py').write_text("V = dict(core='/x/地漏芯老化_展示不密封_01.mov')\nPLAN = {\n 'R01': [('core', 0.8, None)],\n}\n", encoding='utf-8')
        (cut/'动画').mkdir()
        (cut/'动画'/'动画.html').write_text('<script>const SHOTS={R01:"",S02:"水封"}</script>', encoding='utf-8')  # 打包文件含全片镜头，不算
        (cut/'动画'/'src').mkdir()
        (cut/'动画'/'src'/'80_s02.js').write_text('shot("S02", drawSeal)', encoding='utf-8')
        make_video(cut/'合成'/'B肉.mp4', seconds=4, color='green')
        (self.root/'素材').mkdir()
        shutil.copy2(raw/'B roll'/'地漏芯老化_展示不密封_01.mov', self.root/'素材'/'IMG_0001.MOV')
        make_video(self.root/'素材'/'IMG_0002.MOV', color='yellow')
        self.before = self.snapshot()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def snapshot(self):
        return {str(p): (p.stat().st_size, p.stat().st_mtime_ns, catalog.sha256(p))
                for p in sorted(self.root.rglob('*')) if p.is_file() and ('01_原始素材' in str(p) or p.parent.name == '素材')}

    def build(self, **kwargs):
        return catalog.build(root=self.root, index_dir=self.index, log=quiet, **kwargs)

    def records(self):
        return [json.loads(line) for line in (self.index/'素材库.jsonl').read_text(encoding='utf-8').splitlines()]

    def test_build_links_usage_animation_and_unorganized(self):
        result = self.build()
        records = self.records()
        core = next(r for r in records if r['path'].endswith('地漏芯老化_展示不密封_01.mov'))
        self.assertEqual([(u['shot'], u['voice']) for u in core['used_in']], [('R01', '要不然地漏芯坏了')])
        self.assertEqual(core['other_copies'], [str(self.root/'素材'/'IMG_0001.MOV')])
        self.assertEqual(core['orientation'], '竖拍')
        valve = next(r for r in records if '通气阀' in r['path'])
        self.assertEqual((valve['orientation'], valve['description_source'], valve['hdr']), ('横拍', '素材清单', False))
        animations = [r for r in records if r['kind'] == '动画']
        self.assertEqual([(a['shot'], a['animation_types'], a['used_in'][0]['voice']) for a in animations], [('S02', ['代码动画'], '水封被抽干')])
        self.assertEqual(animations[0]['clip']['start'], 2)
        unsorted = [r for r in records if r['unorganized']]
        self.assertEqual([Path(r['path']).name for r in unsorted], ['IMG_0002.MOV'])
        # 待描述：没说明的 B roll、动画镜头、未整理各一条；A roll 不要
        self.assertEqual(result['pending'], 3)
        pending = json.loads((self.index/'待描述.json').read_text(encoding='utf-8'))
        self.assertTrue(all(Path(i['sheet']).exists() for i in pending['items']))
        self.assertNotIn('A roll', {i['已知信息']['类别'] for i in pending['items']})
        self.assertEqual(self.snapshot(), self.before)

    def test_finished_projects_stay_in_library(self):
        # 2026-11-17：项目移进 片子文件夹/finish 后，素材和动画仍在素材库里，路径跟着换（搬完第一次建索引会重算这个项目的摘要）
        self.build()
        before = sorted((r['kind'], Path(r['path']).name if r.get('path') else r.get('shot')) for r in self.records() if r['project'] == self.project.name)
        moved = self.root/catalog.FINISHED_NAME/self.project.name
        moved.parent.mkdir()
        self.project.rename(moved)
        self.build()
        records = [r for r in self.records() if r['project'] == self.project.name]
        self.assertEqual(sorted((r['kind'], Path(r['path']).name if r.get('path') else r.get('shot')) for r in records), before)
        self.assertTrue(all(str(moved) in r['path'] for r in records if r['kind'] == '素材'))
        self.assertTrue(all(Path(r['path']).exists() for r in records if r['kind'] == '素材'))

    def test_same_name_in_root_and_finish_keeps_root_copy_and_warns(self):
        # 2026-11-20 审查：复制进 finish 还没删原处时，同名项目不能互相覆盖、悄悄消失
        moved = self.root/catalog.FINISHED_NAME/self.project.name
        moved.parent.mkdir()
        self.project.rename(moved)
        self.build()  # 先只在 finish 里建过一次，缓存记的是 finish 那份
        shutil.copytree(moved, self.project)
        result = self.build()
        records = [r for r in self.records() if r['project'] == self.project.name and r['kind'] == '素材']
        self.assertTrue(records)
        self.assertTrue(all(str(self.project)+os.sep in r['path'] and str(moved) not in r['path'] for r in records))
        warning = next(w for w in result['warnings'] if '两份同名' in w)
        self.assertIn(str(self.project), warning)
        self.assertIn(str(moved), warning)
        self.assertIn('只收了 '+str(self.project), warning)
        self.assertEqual(sorted(p.name for p in (self.index/'项目').glob('*.json')), ['261022 测试项目.json', '_未整理.json'])

    def test_incremental_add_project_hashes_only_new_files(self):
        self.build()
        with patch.object(catalog, 'sha256', wraps=catalog.sha256) as counted:
            self.build()
            self.assertEqual(counted.call_count, 0)
        make_video(self.project/'01_原始素材'/'B roll'/'网线标签_打标签_01.mov', color='white')
        registry = self.project/'01_原始素材'/'素材清单.json'
        value = json.loads(registry.read_text(encoding='utf-8'))
        value['items'].append({'category': 'B roll', 'content': '网线标签', 'purpose': '打标签', 'path': '01_原始素材/B roll/网线标签_打标签_01.mov'})
        registry.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        with patch.object(catalog, 'sha256', wraps=catalog.sha256) as counted:
            self.build(projects=[self.project])
            self.assertEqual(counted.call_count, 1)
        self.assertTrue(any(r['content'] == '网线标签' for r in self.records()))
        self.assertTrue(any(r['unorganized'] for r in self.records()))  # 单个项目更新不丢未整理

    def test_describe_validates_then_merges(self):
        self.build()
        pending = json.loads((self.index/'待描述.json').read_text(encoding='utf-8'))['items']
        key = next(i['key'] for i in pending if '地漏芯' in i['file'])
        with self.assertRaises(ValueError):
            catalog.merge_descriptions(self.index, [{'key': key, 'description': '手拿地漏芯', 'tags': ['地漏芯']}])
        self.assertFalse((self.index/'描述.json').exists())
        count, result = catalog.merge_descriptions(self.index, [{'key': key, 'description': '手拿老化地漏芯，翻板合不严', 'tags': ['地漏芯', '老化', '近景']}])
        self.assertEqual((count, result['pending']), (1, 2))
        core = next(r for r in self.records() if r['path'].endswith('地漏芯老化_展示不密封_01.mov'))
        self.assertEqual((core['description_source'], core['tags']), ('看图补写', ['地漏芯', '老化', '近景']))
        self.build()  # 重建不丢已写的描述
        self.assertEqual(json.loads((self.index/'描述.json').read_text(encoding='utf-8'))[key]['description'], '手拿老化地漏芯，翻板合不严')

    def test_find_uses_synonyms_and_skips_a_roll(self):
        self.build()
        (self.index/'同义词.txt').write_text('坏了 老化\n防臭芯 地漏芯\n', encoding='utf-8')
        results = search.find(self.index, '防臭芯坏了')
        self.assertTrue(results[0]['路径'].endswith('地漏芯老化_展示不密封_01.mov'))
        self.assertIn('同义词', results[0]['匹配理由'])
        self.assertEqual(results[0]['用在哪'][0]['镜头'], 'R01')
        self.assertFalse(any(r['类别'] == 'A roll' for r in search.find(self.index, '讲解口播录制')))
        self.assertEqual(search.find(self.index, '讲解口播录制', categories={'A roll'})[0]['类别'], 'A roll')
        animation = search.find(self.index, '水封', categories={'动画'})
        self.assertEqual((animation[0]['动画类型'], animation[0]['片段']), ('代码动画', '2.00–3.50 秒'))
        self.assertIn('没找到', search.format_results(search.find(self.index, '完全无关的词语')))

    def test_find_says_nothing_suitable_when_only_numbers_and_function_words_match(self):
        # 2026-11-23 初稿模式实测：没拍过的题材（开关插座）返回了一串只对上“左右”“后面的”“十公分”的无关素材，分数还不低
        listing = self.project/'01_原始素材'/'素材清单.json'; data = json.loads(listing.read_text(encoding='utf-8'))
        data['items'][1]['说明'] = '筒灯离墙三十公分左右，后面的墙面是亮的'
        listing.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        self.build()
        results = search.find(self.index, '沙发后面的插座离地三十公分左右')
        self.assertEqual(list(results), []); self.assertTrue(results.weak_only)
        text = search.format_results(results)
        self.assertIn('素材库里没有合适的', text); self.assertIn('不算', text); self.assertIn('做动画', text)
        self.assertTrue(all(search.weak(t) for t in results.weak_only))
        loose = search.find(self.index, '沙发后面的插座离地三十公分左右', loose=True)
        self.assertTrue(loose and loose[0]['路径'].endswith('通气阀桌面_摆拍展示_01.mov'))
        # 对上了实词（筒灯）就照常算找到，弱词照样加分、写进理由
        hit = search.find(self.index, '筒灯离墙三十公分')
        self.assertTrue(hit and '筒灯' in hit[0]['匹配理由'])
        # 2026-11-24 第二次实测：整句靠「很多人」「是装」「人家」「一做」这种夹着虚字的片段对上的也不算
        data['items'][1]['说明'] = '很多人家里是装了通气阀的，一做就漏'; listing.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8'); self.build()
        again = search.find(self.index, '橱柜台面多高，很多人家都是装修公司按八十公分统一做的')
        self.assertEqual(list(again), []); self.assertIn('素材库里没有合适的', search.format_results(again))
        self.assertTrue(search.clean('烟道') and not search.clean('很多人') and not search.clean('一做'))
        self.assertTrue(search.weak('十公分') and search.weak('后面的') and search.weak('左右') and not search.weak('开关') and not search.weak('插座'))

    def test_project_session_notes(self):
        self.build()
        blender = self.cut/'Blender_水封'
        blender.mkdir()
        (blender/'水封.blend').write_bytes(b'')
        result = catalog.notes_template(self.index, self.project, log=quiet)
        path = Path(result['file'])
        self.assertEqual(path, (self.project/'02_制作'/'素材库说明.json').resolve())
        value = json.loads(path.read_text(encoding='utf-8'))
        rows = {r.get('文件') or r.get('镜头'): r for r in value['items']}
        self.assertEqual(set(rows), {'地漏芯老化_展示不密封_01.mov', '通气阀桌面_摆拍展示_01.mov', '讲解_口播录制_01.mov', 'S02'})
        self.assertIn('shots', rows['讲解_口播录制_01.mov'])
        self.assertIn('tool', rows['S02'])
        # 错一条就一条都不写
        rows['地漏芯老化_展示不密封_01.mov'].update(description='手拿老化地漏芯', tags=['地漏芯', '老化', '近景'], shots=['X99'])
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        with self.assertRaises(ValueError):
            catalog.merge_project_notes(self.index, self.project)
        self.assertFalse((self.index/'描述.json').exists())
        rows['地漏芯老化_展示不密封_01.mov'].update(shots=[], usage_note='讲地漏芯老化不密封', notes='竖拍 HDR，头发在下半部')
        rows['讲解_口播录制_01.mov'].update(description='设计师对镜头讲解精装房臭味', tags=['口播', '讲解', '室内'])
        rows['S02'].update(description='存水弯水封被抽干的剖面动画', tags=['水封', '存水弯', '剖面'], tool=str(blender))
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        count, skipped, _ = catalog.merge_project_notes(self.index, self.project)
        self.assertEqual((count, skipped), (3, 1))
        records = {Path(r['path']).name if r['kind'] == '素材' else r['shot']: r for r in self.records() if r['project'] == self.project.name}
        core = records['地漏芯老化_展示不密封_01.mov']
        self.assertEqual((core['description_source'], core['usage_note']), ('项目会话', '讲地漏芯老化不密封'))
        self.assertEqual(records['讲解_口播录制_01.mov']['description_source'], '项目会话')
        self.assertEqual((records['S02']['animation_types'], records['S02']['tools'][0]['path']), (['Blender'], str(blender)))
        hit = search.find(self.index, '地漏芯老化不密封')[0]
        self.assertEqual((hit['描述来源'], hit['用途说明']), ('项目会话', '讲地漏芯老化不密封'))
        # 素材写明用在 S02 后，S02 算实拍镜头，不再单列成动画
        value = json.loads(path.read_text(encoding='utf-8'))
        for row in value['items']:
            if row.get('文件') == '通气阀桌面_摆拍展示_01.mov':
                row.update(description='桌面上的白色通气阀', tags=['通气阀', '桌面', '摆拍'], shots=['S02'])
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        catalog.merge_project_notes(self.index, self.project)
        valve = next(r for r in self.records() if '通气阀' in (r.get('path') or ''))
        self.assertEqual([u['shot'] for u in valve['used_in']], ['S02'])
        self.assertFalse(any(r['kind'] == '动画' for r in self.records()))
        # 重新生成模板保留已填的
        catalog.notes_template(self.index, self.project, log=quiet)
        again = {r.get('文件') or r.get('镜头'): r for r in json.loads(path.read_text(encoding='utf-8'))['items']}
        self.assertEqual(again['地漏芯老化_展示不密封_01.mov']['notes'], '竖拍 HDR，头发在下半部')
        self.assertEqual(self.snapshot(), self.before)

    def test_notes_template_lists_spec_referenced_files(self):
        # 2026-11-22 261122 反馈单 新10：分镜“实拍／图片／盖实拍”引用的项目内动画 mp4、生成图也要列进素材库说明（261122 只列了 5 条，三镜动画和 10 张图都没进）
        cut = self.cut
        make_video(cut/'动画对比'/'Motion'/'镜头1_开场留言_4K.mp4', seconds=1, color='white')
        make_video(cut/'实拍'/'盖一段.mp4', seconds=1, color='black')
        (cut/'实拍'/'AI图').mkdir(parents=True)
        for name, color in (('tv_画面.png', 'red'), ('baby_画面.png', 'blue')):
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color={color}:s=32x32', '-frames:v', '1', str(cut/'实拍'/'AI图'/name)], check=True, stdin=subprocess.DEVNULL)
        outside = self.root/'261001 往期'/'出片'/'B04.mp4'
        make_video(outside, seconds=1)
        spec = {'shots': [
            {'id': 'R01', 'a': 1, 'b': 2, '实拍': '动画对比/Motion/镜头1_开场留言_4K.mp4'},
            {'id': 'S02', 'a': 2, 'b': 3.5, '图片': ['实拍/AI图/tv_画面.png', '实拍/AI图/baby_画面.png'],
             '盖实拍': [{'从': 2.5, '文件': '实拍/盖一段.mp4', '入点': 0}]},
            {'id': 'R09', 'a': 3.5, 'b': 4, '实拍': {'文件': str(outside), '入点': 0}},                         # 项目外：不列
            {'id': 'R10', 'a': 4, 'b': 4.5, '实拍': {'文件': '../01_原始素材/B roll/地漏芯老化_展示不密封_01.mov'}},   # 已登记：不重复
            {'id': 'R11', 'a': 4.5, 'b': 5, '实拍': '实拍/找不到.mp4'}]}
        (cut/'分镜规格.json').write_text(json.dumps(spec, ensure_ascii=False), encoding='utf-8')
        # 总装写的签名文件一行里有全片镜头号和源文件路径：不能拿它对“哪一镜用了哪个素材”
        (cut/'合成'/'实拍镜头_签名.json').write_text(json.dumps({'R01': '地漏芯老化_展示不密封_01.mov', 'S02': '地漏芯老化_展示不密封_01.mov'}, ensure_ascii=False), encoding='utf-8')
        result = catalog.notes_template(self.index, self.project, log=quiet)
        rows = {r.get('文件') or r.get('镜头'): r for r in json.loads(Path(result['file']).read_text(encoding='utf-8'))['items']}
        self.assertEqual(set(rows), {'地漏芯老化_展示不密封_01.mov', '通气阀桌面_摆拍展示_01.mov', '讲解_口播录制_01.mov',
                                     '镜头1_开场留言_4K.mp4', '盖一段.mp4', 'tv_画面.png', 'baby_画面.png'})
        self.assertEqual((rows['镜头1_开场留言_4K.mp4']['类别'], rows['tv_画面.png']['类别']), ('分镜引用视频', '分镜引用图片'))
        self.assertEqual((rows['镜头1_开场留言_4K.mp4']['用在'], rows['盖一段.mp4']['用在']), (['R01：要不然地漏芯坏了'], ['S02：水封被抽干']))
        self.assertIn('shots', rows['tv_画面.png'])                      # 和素材一样可填
        # 填了能写进素材库
        value = json.loads(Path(result['file']).read_text(encoding='utf-8'))
        for row in value['items']:
            if row.get('文件') == 'tv_画面.png':
                row.update(description='客厅里一家人看电视的生成图', tags=['电视', '客厅', '生成图'])
        Path(result['file']).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        self.assertEqual(catalog.merge_project_notes(self.index, self.project)[:2], (1, 6))
        tv = next(r for r in self.records() if (r.get('path') or '').endswith('tv_画面.png'))
        self.assertEqual((tv['description_source'], [u['shot'] for u in tv['used_in']]), ('项目会话', ['S02']))
        core = next(r for r in self.records() if (r.get('path') or '').endswith('地漏芯老化_展示不密封_01.mov'))
        self.assertEqual([u['shot'] for u in core['used_in']], ['R01'])      # 签名文件里的 S02 不算
        self.assertEqual(self.snapshot(), self.before)

    def test_full_build_refuses_missing_root_and_keeps_cache(self):
        self.build()
        caches = {p.name: p.read_bytes() for p in (self.index/'项目').glob('*.json')}
        records = (self.index/'素材库.jsonl').read_bytes()
        not_folder = self.tmp/'不是文件夹.txt'
        not_folder.write_text('x', encoding='utf-8')
        for bad in (self.tmp/'没挂载的盘', not_folder):
            with self.assertRaisesRegex(ValueError, '找不到放片子的文件夹'):
                catalog.build(root=bad, index_dir=self.index, log=quiet)
        self.assertEqual({p.name: p.read_bytes() for p in (self.index/'项目').glob('*.json')}, caches)
        self.assertEqual((self.index/'素材库.jsonl').read_bytes(), records)
        self.assertTrue(caches)

    def test_bad_project_file_is_skipped_and_reported(self):
        good = self.root/'261023 好项目'/'01_原始素材'
        make_video(good/'B roll'/'水封_近景_01.mov', color='white')
        (good/'素材清单.json').write_text(json.dumps({'items': [{'category': 'B roll', 'content': '水封', 'purpose': '近景', 'path': '01_原始素材/B roll/水封_近景_01.mov'}]}, ensure_ascii=False), encoding='utf-8')
        beats = json.loads((self.cut/'分镜.json').read_text(encoding='utf-8'))
        del beats['shots'][1]['a']
        (self.cut/'分镜.json').write_text(json.dumps(beats), encoding='utf-8')
        result = self.build()
        warning = next(w for w in result['warnings'] if '261022 测试项目' in w)
        self.assertIn('分镜.json', warning)
        self.assertIn('跳过', warning)
        self.assertTrue(any(r['project'] == '261023 好项目' for r in self.records()))
        beats['shots'][1]['a'] = 1
        (self.cut/'分镜.json').write_text(json.dumps(beats), encoding='utf-8')
        (self.cut/'口播字时间.json').write_text(json.dumps({'tokens': [{'c': '要', 't1': 1.2}]}, ensure_ascii=False), encoding='utf-8')
        spec = __import__('importlib.util').util.spec_from_file_location('library_cli', Path(__file__).resolve().parents[2]/'bin'/'library.py')
        cli = __import__('importlib.util').util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(['--index', str(self.index), '--root', str(self.root), 'build']), 0)
        self.assertRegex(out.getvalue(), '注意：项目「261022 测试项目」这次跳过.*口播字时间.json')
        self.assertNotIn('Traceback', out.getvalue()+err.getvalue())

    def test_main_cut_prefers_folder_with_final_cut(self):
        other = self.project/'02_制作'/'自动剪辑_v2'
        other.mkdir(parents=True)
        (other/'分镜.json').write_text('{"shots": []}', encoding='utf-8')
        self.assertEqual(catalog.main_cut_dir(self.project).name, '自动剪辑_v2')
        (self.project/'02_制作'/'自动剪辑'/'成片').mkdir()
        self.assertEqual(catalog.main_cut_dir(self.project).name, '自动剪辑')


class SmallTests(unittest.TestCase):
    def test_jpeg_orientation(self):
        ifd = struct.pack('<H', 1)+struct.pack('<HHIHH', 0x0112, 3, 1, 6, 0)+struct.pack('<I', 0)
        tiff = b'II*\x00'+struct.pack('<I', 8)+ifd
        app1 = b'Exif\x00\x00'+tiff
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'a.jpg'
            path.write_bytes(b'\xff\xd8\xff\xe1'+struct.pack('>H', len(app1)+2)+app1+b'\xff\xd9')
            self.assertEqual(catalog.jpeg_orientation(path), 6)
            path.write_bytes(b'\xff\xd8\xff\xd9')
            self.assertIsNone(catalog.jpeg_orientation(path))

    def test_query_terms(self):
        terms = search.query_terms('地漏芯坏了', [['坏了', '老化']])
        self.assertIn('地漏芯', terms)
        self.assertIn('老化', terms)
        self.assertNotIn('了', terms)
        self.assertEqual(search.merged_hits('只进气不出气', ['只进气', '进气不', '不出气']), ['只进气不出气'])

    def test_cli_arguments(self):
        spec = __import__('importlib.util').util.spec_from_file_location('library_cli', Path(__file__).resolve().parents[2]/'bin'/'library.py')
        cli = __import__('importlib.util').util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        for args in (['build'], ['add-project', '项目'], ['notes-template', '项目'], ['notes', '项目'], ['find', '地漏芯', '--limit', '3', '--json', '--category', '动画'], ['describe', 'a.json']):
            self.assertTrue(cli.parser().parse_args(args).command)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(['--index', '/nonexistent/索引', 'find', '地漏芯']), 1)


if __name__ == '__main__':
    unittest.main()
