#!/usr/bin/env python3
"""离线验证真实约束：命名、路径、上传门禁、去重及中断。"""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
os.environ['THINKCUT_RESOLVE_LOCK'] = os.path.join(tempfile.mkdtemp(prefix='假达芬奇锁_'), 'lock')    # 测试不碰真的达芬奇写入锁
os.environ['THINKCUT_RESOLVE_PREFS'] = tempfile.mkdtemp(prefix='假达芬奇设置_')    # 测试不碰真的达芬奇库列表
import unittest
from unittest.mock import patch

import project as p
import yxer_secure as secure
import delivery
import publish_fields


class ProjectTests(unittest.TestCase):
    def setUp(self):
        # 2026-11-20 审查：测试不看这台 Mac 上达芬奇开没开；要测“达芬奇开着被拦”的地方再局部换成 True
        import resolve_library
        running = patch.object(resolve_library, 'resolve_running', return_value=False); running.start(); self.addCleanup(running.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.d, self.meta = p.initialize(Path(self.temp.name) / '测试项目')

    def tearDown(self):
        self.temp.cleanup()

    def test_roots_follow_repo_location(self):
        # 2026-11-21 体检：片子文件夹、封面技能的位置按本仓库所在位置推，不写死 /Volumes/work（NAS 挂成 work-1 也能用）
        repo = Path(p.__file__).resolve().parents[1]
        self.assertEqual(p.AI_ROOT, repo.parent)
        self.assertEqual(p.VIDEO_ROOT, p.CFG.path('片子文件夹')); self.assertEqual(p.VIDEO_ROOT.parent, repo.parent.parent)
        self.meta.update(cover_main='第一行\n第二行', cover_sub='副标题')
        self.assertEqual(delivery.cover_brief(self.d, self.meta)['skill'], str(repo.parent / 'cover/skill/thinkcut-cover/SKILL.md'))
        for name in ('project.py', 'delivery.py'):
            code = (repo / 'bin' / name).read_text(encoding='utf-8')
            self.assertNotIn("Path('/Volumes/work", code); self.assertNotIn("'skill': '/Volumes", code)

    def test_names_and_original_protection(self):
        original = self.d / '原片.mov'; original.write_bytes(b'original')
        with self.assertRaises(RuntimeError): p.initialize(self.d, adopt=True)
        self.assertEqual(original.read_bytes(), b'original')
        self.meta['name'] = '另一项目'; p.write_json(self.d / '项目.json', self.meta)
        with self.assertRaises(RuntimeError): p.load(self.d)

    def test_raw_material_inventory_checks_every_file_and_unchanged_bytes(self):
        self.assertEqual({x.name for x in (self.d / '01_原始素材').iterdir() if x.is_dir()}, {'A roll', 'B roll', '音乐'})
        self.assertEqual(p.check_materials(self.d)['files'], 0)
        original = self.d / '01_原始素材/C0001.MOV'
        original.write_bytes(b'original camera bytes')
        with self.assertRaises(RuntimeError): p.check_materials(self.d)
        target = original.parent / 'A roll/烟道反味讲解_口播录制_01.MOV'
        original.rename(target)
        row = {'category': 'A roll', 'content': '烟道反味讲解', 'purpose': '口播录制', 'sequence': 1,
               'original_name': 'C0001.MOV', 'path': str(target.relative_to(self.d)), 'sha256': p.digest(target)}
        p.write_json(self.d / '01_原始素材/素材清单.json', {'schema_version': 1, 'items': [row]})
        self.assertEqual(p.check_materials(self.d)['files'], 1)
        self.assertEqual(target.read_bytes(), b'original camera bytes')
        target.write_bytes(b'changed')
        with self.assertRaises(RuntimeError): p.check_materials(self.d)

    def test_broll_accepts_images_and_music_includes_sound_effects(self):
        rows = []
        for category, content, purpose, suffix in [('B roll', '楼板缝裂口', '解释漏烟位置', '.jpg'),
                                                    ('音乐', '轻鼓点', '背景配乐', '.wav'),
                                                    ('音乐', '短提示声', '强调出现', '.mp3')]:
            path = self.d / '01_原始素材' / category / (content + '_' + purpose + '_01' + suffix)
            path.write_bytes(b'inventory fixture')
            rows.append({'category': category, 'content': content, 'purpose': purpose, 'sequence': 1,
                         'original_name': 'source' + suffix, 'path': str(path.relative_to(self.d)), 'sha256': p.digest(path)})
        p.write_json(self.d / '01_原始素材/素材清单.json', {'schema_version': 1, 'items': rows})
        self.assertEqual(p.check_materials(self.d), {'state': 'materials_verified', 'files': 3, 'categories': ['B roll', '音乐']})

    def test_material_setup_preserves_catalog_and_inventory_rejects_escape(self):
        index = self.d / '01_原始素材/素材清单.json'
        value = {'schema_version': 1, 'items': [], 'note': '用户已补充的来源'}
        p.write_json(index, value)
        p.initialize_materials(self.d)
        self.assertEqual(p.read_json(index), value)
        outside = Path(self.temp.name) / 'source.mov'; outside.write_bytes(b'keep')
        link = self.d / '01_原始素材/A roll/烟道_口播_01.mov'; link.symlink_to(outside)
        value['items'] = [{'category': 'A roll', 'content': '烟道', 'purpose': '口播', 'sequence': 1,
                           'original_name': 'source.mov', 'path': str(link.relative_to(self.d)), 'sha256': p.digest(outside)}]
        p.write_json(index, value)
        with self.assertRaises(RuntimeError): p.check_materials(self.d)
        self.assertEqual(outside.read_bytes(), b'keep')

    def test_path_escape_and_symlink_escape(self):
        outside = Path(self.temp.name) / 'outside.mov'; outside.write_bytes(b'x')
        (self.d / '逃逸.mov').symlink_to(outside)
        for name in ('../outside.mov', str(outside), '逃逸.mov', ''):
            with self.assertRaises(RuntimeError): p.asset_path(self.d, name)

    def test_blank_cannot_upload(self):
        with patch.object(p, 'command') as cmd:
            with self.assertRaises(RuntimeError): p.upload(self.d, self.meta, execute=True)
            cmd.assert_not_called()

    def test_upload_needs_the_users_board_confirmation_behind_the_export(self):
        """2026-11-22 用户定“先做出来再改”：前面不问，只在导出和上传前留闸。走了剪辑的片子（有 自动剪辑/job.json），
        成片没有“用户在审片台确认后导出”的记录（export_ok）就不上传，预检也拦；有了才放行。没走剪辑的项目不查。"""
        self.fixture()
        self.assertIsNone(p.final_gate(self.d))                                   # 没有 job.json：不查
        job = self.d / '02_制作' / '自动剪辑' / 'job.json'; job.parent.mkdir(parents=True)
        p.write_json(job, {'review_timeline': 'v1_卡点审核'})
        with patch.object(p, 'command') as cmd:
            for execute in (False, True):
                with self.assertRaisesRegex(RuntimeError, '(?s)没有 export_ok.*定稿.*AI 不能替用户点'): p.upload(self.d, self.meta, execute=execute)
            cmd.assert_not_called()
        p.write_json(job, {'export_ok': {'timeline': 'v1_卡点审核', 'via': '审片台：用户点了审核线“这一栏定了”', 'at': '2026-11-23 10:00:00'}})
        self.assertIn('审片台：用户点了审核线“这一栏定了”（2026-11-23 10:00:00，时间线 v1_卡点审核）', p.final_gate(self.d))
        p.write_json(job, {'export_ok': {'timeline': '审核线', 'user_said': 'OK 导出吧', 'at': '2026-11-20 10:00:00'}})   # 这条规矩之前导出的老片子
        self.assertIn('OK 导出吧', p.final_gate(self.d))
        p.write_json(job, {'export_ok': {'timeline': '审核线', 'user_said': '  '}})
        with self.assertRaisesRegex(RuntimeError, '没有 export_ok'): p.final_gate(self.d)

    def test_concurrent_upload_is_blocked_before_external_calls(self):
        with p.upload_lock(self.d), patch.object(p, '_upload') as submit:
            with self.assertRaises(RuntimeError): p.upload(self.d, self.meta, execute=True)
            submit.assert_not_called()

    def test_safe_cli_denies_publication(self):
        for args in (['publish', 'video'], ['publish', 'video', '--dry-run'], ['config', 'init'], ['accounts', 'update']):
            self.assertFalse(secure.allowed(args))
        self.assertTrue(secure.allowed(['draft', 'save', 'payload.json']))

    def fixture(self):
        self.meta.update(title='已确认标题', materials_confirmed=True)
        for rel in ('03_成片/261018_测试项目.mp4', '03_成片/封面/cover.jpg'):
            (self.d / rel).write_bytes(b'test fixture')
        self.meta['assets'].update(video='03_成片/261018_测试项目.mp4')
        self.meta['assets']['covers']['3:4'] = '03_成片/封面/cover.jpg'
        report = self.d / '04_达芬奇归档/验收.json'
        p.write_json(report, {'structural_verification': True, 'visual_review_required': False, 'delivery_sha256': p.digest(self.d / '03_成片/261018_测试项目.mp4')})
        self.meta['resolve']['report'] = '04_达芬奇归档/验收.json'
        self.meta['targets'] = [dict(enabled=True, platform='抖音', account_id='id-1', account_name='小王', cover='3:4')]
        self.package_fixture()
        calls = []
        def cli(args):
            calls.append(args)
            if args[1:3] == ['accounts', 'list']:
                return {'ok': True, 'data': [{'id': 'id-1', 'platformName': '抖音', 'platformAccountName': '小王', 'status': 1}]}
            if args[1:3] == ['schema', 'get']:
                return {'ok': True, 'data': {'minimalTemplate': {'action': 'publish', 'publishType': 'video', 'platforms': ['抖音'], 'publishArgs': {'accountForms': [{'contentPublishForm': {'formType': 'task'}}]}}, 'businessFields': {'title': {}}}}
            if args[1] == 'upload':
                return {'ok': True, 'data': {'key': 'fixture-only', 'width': 10, 'height': 10, 'size': 12, 'duration': 1}}
            if args[1:3] == ['draft', 'save']:
                if '--dry-run' in args:
                    payload = p.read_json(args[3])
                    return {'ok': True, 'action': 'draft.save.dry-run', 'data': {'dryRun': True, 'request': {'isDraft': True, 'publishType': 'video', 'platforms': payload['platforms'], 'publishArgs': payload['publishArgs']}}}
                return {'ok': True, 'action': 'draft.save', 'data': {'statusCode': 0, 'data': '00000000-0000-4000-8000-000000000001'}}
            return {'ok': True, 'data': {}}
        return cli, calls

    def package_fixture(self):
        files = {}
        folder = self.d / '04_达芬奇归档/测试交付包'; folder.mkdir(exist_ok=True)
        for key, relative in [('video', self.meta['assets']['video']), ('cover:3:4', self.meta['assets']['covers']['3:4'])]:
            source = self.d / relative; target = folder / source.name
            target.write_bytes(source.read_bytes())
            files[key] = {'path': str(target.relative_to(self.d)), 'source': relative, 'sha256': p.digest(source)}
        version = 'test-only'
        manifest = folder / '交付清单.json'
        p.write_json(manifest, {'project': self.meta['name'], 'signature': version, 'content': delivery.content(self.meta), 'files': files, 'dependencies': {}})
        self.meta['delivery_package'] = {'manifest': str(manifest.relative_to(self.d)), 'signature': version}

    def multi_fixture(self):
        """抖音、视频号、B站三个账号，带横版封面；schema 带各平台真实字段名。"""
        cli, calls = self.fixture()
        for r in ('4:3', '16:9'):
            rel = f'03_成片/封面/cover_{r.replace(":", "x")}.jpg'; (self.d / rel).write_bytes(b'cover ' + r.encode())
            self.meta['assets']['covers'][r] = rel
        self.meta.update(tags=['精装房', '地漏'], description='简介', original=True, bilibili_category='家装房产', cover_main='精装房还没住\n就臭气熏天')
        self.meta['targets'] = [dict(enabled=True, platform='抖音', account_id='id-1', account_name='小王', cover='3:4', horizontal_cover='4:3'),
                                dict(enabled=True, platform='视频号', account_id='id-2', account_name='小王啊', cover='3:4', horizontal_cover='4:3'),
                                dict(enabled=True, platform='哔哩哔哩', account_id='id-3', account_name='小王啊', cover='16:9')]
        files = {}
        folder = self.d / '04_达芬奇归档/测试交付包'
        for key, relative in [('video', self.meta['assets']['video'])] + [('cover:' + r, v) for r, v in self.meta['assets']['covers'].items() if v]:
            source = self.d / relative; target = folder / source.name; target.write_bytes(source.read_bytes())
            files[key] = {'path': str(target.relative_to(self.d)), 'source': relative, 'sha256': p.digest(source)}
        manifest = folder / '交付清单.json'
        p.write_json(manifest, {'project': self.meta['name'], 'signature': 'test-only', 'content': delivery.content(self.meta), 'files': files, 'dependencies': {}})
        fields = {'抖音': {'title': {}, 'description': {}, 'tags': {}, 'horizontalCover': {}, 'allow_save': {}},
                  '视频号': {'title': {}, 'description': {}, 'horizontalCover': {}, 'createType': {}, 'short_title': {}},
                  '哔哩哔哩': {'title': {}, 'description': {}, 'tags': {}, 'category': {}, 'createType': {}}}
        accounts = [{'id': t['account_id'], 'platformName': t['platform'], 'platformAccountName': t['account_name'], 'status': 1} for t in self.meta['targets']]
        def multi(args):
            if args[1:3] == ['accounts', 'list']: return {'ok': True, 'data': accounts}
            if args[1:3] == ['schema', 'get']:
                pf = args[3]
                return {'ok': True, 'data': {'minimalTemplate': {'action': 'publish', 'publishType': 'video', 'platforms': [pf], 'publishArgs': {'accountForms': [{'contentPublishForm': {'formType': 'task'}}]}}, 'businessFields': fields[pf]}}
            if args[1:3] == ['query', 'categories']:
                return {'ok': True, 'data': {'paths': [{'path': '家装房产', 'category': [{'raw': {'id': 1015}, 'yixiaoerId': '1015', 'yixiaoerName': '家装房产'}]}]}}
            if args[1] == 'upload':
                w, h = (1440, 1080) if '4x3' in args[2] else (1080, 1440)
                return {'ok': True, 'data': {'key': 'k-' + Path(args[2]).name, 'width': w, 'height': h, 'size': 12, 'format': 'jpg', 'duration': 1}}
            return cli(args)
        return multi, calls

    def test_platform_limits_checked_before_upload(self):
        # 2026-11-12 水电：预检通过、视频传完才被平台拦（抖音/B站话题最多 10 个、小红书标题最多 20 字）
        cli, calls = self.multi_fixture()
        def limited(args):
            r = cli(args)
            if args[1:3] == ['schema', 'get'] and args[3] in ('抖音', '哔哩哔哩'):
                r = copy.deepcopy(r); r['data']['businessFields']['tags'] = {'type': 'array', 'maxItems': 10}
                r['data']['businessFields']['title'] = {'type': 'string', 'maxLength': 20}
            return r
        self.meta['tags'] = [f'话题{i}' for i in range(12)]; self.meta['title'] = '预算充足的家，水电阶段这4件事要一次做到位'
        manifest = self.d / '04_达芬奇归档/测试交付包/交付清单.json'; m = p.read_json(manifest); m['content'] = delivery.content(self.meta); p.write_json(manifest, m)
        with patch.object(p, 'command', side_effect=limited), patch.object(publish_fields, 'command', side_effect=limited):
            with self.assertRaises(RuntimeError) as e:
                p.upload(self.d, self.meta, execute=True)
        msg = str(e.exception)
        self.assertIn('抖音 tags 12 个，最多 10 个', msg); self.assertIn('哔哩哔哩 tags 12 个', msg); self.assertIn('抖音 title 21 字，最多 20 字', msg)
        self.assertFalse([a for a in calls if a[1] == 'upload'])          # 一个文件都没传

    def test_schedule_goes_to_platforms_that_support_it(self):
        """2026-11-22 261122 反馈单 新9：定时发布一条命令完成；没有定时字段的平台（YouTube）合在同一条里、点发布即发（用户：“那YouTube就直接发了就行了，没有定时功能就没有吧，不需要”）。"""
        cli, calls = self.multi_fixture()
        def with_schedule(args):
            r = cli(args)
            if args[1:3] == ['schema', 'get'] and args[3] in ('抖音', '视频号'):
                r = copy.deepcopy(r); r['data']['businessFields']['scheduledTime'] = {'type': 'integer'}
            return r
        with patch.object(p, 'command', side_effect=with_schedule), patch.object(publish_fields, 'command', side_effect=with_schedule):
            with self.assertRaisesRegex(RuntimeError, '写成“2026-11-23 20:00”这样'): p.upload(self.d, self.meta, execute=True, schedule='明天晚上8点')
            with self.assertRaisesRegex(RuntimeError, '已经过了或太近'): p.upload(self.d, self.meta, execute=True, schedule='2020-01-01 20:00')
            self.assertFalse([a for a in calls if a[1] == 'upload'])                    # 时间不对：一个文件都没传
            receipt = p.upload(self.d, self.meta, execute=True, schedule='2099-11-23 20:00')
        import datetime as _dt
        ms = int(_dt.datetime(2099, 11, 23, 12, 0, tzinfo=_dt.timezone.utc).timestamp() * 1000)      # 北京时间 20:00 = 世界时 12:00
        self.assertEqual(p.parse_schedule('2099-11-23 20:00')['ms'], ms)
        self.assertEqual(receipt['schedule'], {'time': '2099-11-23 20:00', 'platforms': {'抖音': '2099-11-23 20:00 定时', '视频号': '2099-11-23 20:00 定时', '哔哩哔哩': '不支持定时，点发布即发'}})
        run = next((self.d / '05_上传记录').glob('*/00_合并草稿参数.json')); forms = {f['platformAccountId']: f['contentPublishForm'] for f in p.read_json(run)['publishArgs']['accountForms']}
        self.assertEqual((forms['id-1'].get('scheduledTime'), forms['id-2'].get('scheduledTime'), forms['id-3'].get('scheduledTime')), (ms, ms, None))
        self.assertEqual(len(list((self.d / '05_上传记录').glob('*/回执.json'))), 1)   # 还是合成一条草稿

    def test_missing_platforms_reported(self):
        # 2026-11-12 水电：少了视频号，预检和回执都没说
        accounts = [{'platformName': '抖音', 'status': 1}, {'platformName': '视频号', 'status': 0}, {'platformName': '快手', 'status': 1},
                    {'platformName': '小红书', 'status': 1}, {'platformName': '哔哩哔哩', 'status': 1}]
        targets = [{'platform': n, 'enabled': n != '快手'} for n in ('抖音', '快手', '小红书', '哔哩哔哩')]
        gaps = p.platform_gaps(accounts, targets)
        self.assertEqual(set(gaps), {'视频号', 'Youtube', '快手'})
        self.assertIn('重新登录', gaps['视频号']); self.assertIn('没绑定', gaps['Youtube']); self.assertIn('没启用', gaps['快手'])
        self.assertEqual(set(p.platform_gaps(accounts)), {'视频号', 'Youtube'})

    def test_all_platforms_saved_as_one_draft_with_defaults(self):
        cli, calls = self.multi_fixture()
        with patch.object(p, 'command', side_effect=cli), patch.object(publish_fields, 'command', side_effect=cli):
            receipt = p.upload(self.d, self.meta, execute=True)
        saves = [a for a in calls if a[1:3] == ['draft', 'save'] and '--dry-run' not in a]
        self.assertEqual(len(saves), 1)
        combined = p.read_json(saves[0][3])
        self.assertEqual(combined['platforms'], ['抖音', '视频号', '哔哩哔哩'])
        forms = {f['platformAccountId']: f['contentPublishForm'] for f in combined['publishArgs']['accountForms']}
        self.assertEqual(forms['id-1']['horizontalCover']['width'], 1440)
        self.assertIn('<topic text="精装房">', forms['id-1']['description']); self.assertEqual(forms['id-1']['allow_save'], 1)
        self.assertEqual(forms['id-2']['createType'], 1); self.assertEqual(forms['id-2']['short_title'], '精装房还没住就臭气熏天')
        self.assertEqual(forms['id-3']['createType'], 1); self.assertEqual(forms['id-3']['category'][0]['yixiaoerName'], '家装房产')
        self.assertEqual(len(receipt['drafts']), 1)

    def test_same_title_needs_again_and_unclear_blocks_again(self):
        cli, calls = self.multi_fixture()
        with patch.object(p, 'command', side_effect=cli), patch.object(publish_fields, 'command', side_effect=cli):
            p.upload(self.d, self.meta, execute=True)
            self.meta['platform_extra'] = {'抖音': {'allow_save': 0}}   # 平台字段变了：同标题已存过，要 --again
            with self.assertRaises(RuntimeError): p.upload(self.d, self.meta, execute=True)
            second = p.upload(self.d, self.meta, execute=True, again='客户端已清空')
            self.assertEqual(second['state'], 'draft_saved')
            r = next(self.d.glob('05_上传记录/*/回执.json')); x = p.read_json(r); x['state'] = 'draft_save_result_unverified'; p.write_json(r, x)
            calls.clear()
            with self.assertRaises(RuntimeError): p.upload(self.d, self.meta, execute=True, again='再来一次')
            self.assertFalse(any(a[1:3] == ['draft', 'save'] for a in calls))

    def test_again_rerun_and_checked_receipt_do_not_duplicate(self):
        # 2026-11-13 审查：同一条 --again 重跑不多存；核对过的回执（客户端里可能有草稿）也算同标题存过
        cli, calls = self.multi_fixture()
        saves = lambda: [a for a in calls if a[1:3] == ['draft', 'save'] and '--dry-run' not in a]
        with patch.object(p, 'command', side_effect=cli), patch.object(publish_fields, 'command', side_effect=cli):
            p.upload(self.d, self.meta, execute=True)
            first = p.upload(self.d, self.meta, execute=True, again='客户端已清空')
            self.assertEqual(first['again']['reason'], '客户端已清空')
            p.upload(self.d, self.meta, execute=True, again='客户端已清空')
            self.assertEqual(len(saves()), 2)                                   # 重跑同一条命令没有第三份
            for r in self.d.glob('05_上传记录/*/回执.json'):
                x = p.read_json(r); x['state'] = 'checked_in_client'; p.write_json(r, x)
            with self.assertRaises(RuntimeError) as e: p.upload(self.d, self.meta, execute=True)
            self.assertIn('--again', str(e.exception))                           # 同一份资料：提示怎么走
            self.meta['platform_extra'] = {'抖音': {'allow_save': 0}}
            with self.assertRaises(RuntimeError) as e: p.upload(self.d, self.meta, execute=True)
            self.assertIn('已经存过草稿', str(e.exception)); self.assertEqual(len(saves()), 2)

    def test_resave_reuses_uploaded_files_or_says_why_it_reuploads(self):
        # 2026-11-21《装修最后那点细节》：存草稿结果不明确、客户端里没有，--again 重存又把成片传了一遍（约 2 分钟）
        cli, calls = self.multi_fixture()
        uploads = lambda: [a for a in calls if a[1] == 'upload']
        def pending_then_ok(args):
            calls.append(args)
            if args[1:3] == ['draft', 'save'] and '--dry-run' not in args and not hasattr(pending_then_ok, 'failed'):
                pending_then_ok.failed = True; raise RuntimeError('yxer-safe：Internal server error；')
            return cli(args)
        def resave(reason):
            for r in self.d.glob('05_上传记录/*/回执.json'):
                x = p.read_json(r)
                if x['state'] != 'draft_saved': x['state'] = 'checked_in_client'; p.write_json(r, x)
            out = io.StringIO(); calls.clear()
            with contextlib.redirect_stdout(out): receipt = p.upload(self.d, self.meta, execute=True, again=reason)
            return receipt, out.getvalue()
        with patch.object(p, 'command', side_effect=pending_then_ok), patch.object(publish_fields, 'command', side_effect=pending_then_ok):
            with self.assertRaises(RuntimeError), contextlib.redirect_stdout(io.StringIO()): p.upload(self.d, self.meta, execute=True)
            first = next(self.d.glob('05_上传记录/*/回执.json'))
            self.assertEqual(p.read_json(first)['state'], 'draft_save_pending')
            self.assertEqual(len(uploads()), 6)
            # 1. 文件没变、刚传过：一个都不重传，草稿用的还是上次的 key
            receipt, out = resave('客户端里没有')
            self.assertEqual(receipt['state'], 'draft_saved'); self.assertEqual(uploads(), [])
            self.assertIn('不重传：261018_测试项目.mp4', out); self.assertNotIn('要重传', out)
            self.assertEqual({v['data']['key'] for v in receipt['uploads'].values()}, {v['data']['key'] for v in p.read_json(first)['uploads'].values()})
            self.assertTrue(all(v['reused_from'] == first.parent.name for v in receipt['uploads'].values()))
            # 2. 上次的记录太久（或没写时间）：先说要重传多大、为什么，再传
            for r in self.d.glob('05_上传记录/*/回执.json'):
                x = p.read_json(r)
                for v in x['uploads'].values(): v['uploaded_at'] = '2026-01-01 08:00:00'
                next(iter(x['uploads'].values())).pop('uploaded_at')
                p.write_json(r, x)
            receipt, out = resave('第二次清空')
            self.assertEqual(len(uploads()), 6)
            self.assertIn('要重传 ', out); self.assertIn('超过 24 小时', out); self.assertIn('没写传的时间', out)
            self.assertLess(out.index('要重传'), out.index('开始传：'))
            self.assertTrue(all(v.get('uploaded_at') for v in receipt['uploads'].values()))
        # 3. 成片变了：不能用上次的
        found, why = p.earlier_upload([(first, {'uploads': {'a.mp4': {'ok': True, 'data': {'key': 'k'}, 'uploaded_at': '2026-11-21 10:00:00'}},
                                                'file_hashes': {'a.mp4': '旧哈希'}})], 'a.mp4', 'a.mp4', '新哈希', p.datetime(2026, 11, 21, 11))
        self.assertIsNone(found); self.assertIn('不一样', why)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sys, 'argv', ['project.py', *argv]), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = p.main()
        return code, out.getvalue(), err.getvalue()

    def test_project_number_moves_to_next_free_day(self):
        # 2026-11-17 反馈单 新1：编号撞了按日期往后顺延一天，跨月是真实日期；finish 里做完的也算
        root = Path(self.temp.name).resolve() / '根'; (root / '261130 甲').mkdir(parents=True); (root / p.FINISHED / '261201 乙').mkdir(parents=True)
        self.assertEqual(p.free_name('261130 丙', root), ('261202 丙', '261130'))
        self.assertEqual(p.free_name('261129 丙', root), ('261129 丙', None))
        self.assertEqual(p.free_name('没有编号', root), ('没有编号', None))
        (root / '261399 坏日期').mkdir()
        with self.assertRaisesRegex(RuntimeError, '不是一个真实日期'): p.free_name('261399 丁', root)
        code, out, _ = self.run_cli('init', '261130 丙', '--root', str(root))
        self.assertEqual(code, 0); self.assertIn('原编号 261130 已被占用，改用 261202', out)
        self.assertIn('下一步：打开审片台写文案', out); self.assertIn('thinkcut_edit.py" workbench "' + str(root / '261202 丙'), out)   # 2026-11-24：建完项目先去审片台“文案”栏写文案
        self.assertEqual(p.read_json(root / '261202 丙' / '项目.json')['name'], '261202 丙')
        self.assertFalse((root / '261130 丙').exists())
        (root / '261202 已有文件夹').mkdir()
        code, _, err = self.run_cli('adopt', str(root / '261202 已有文件夹'))
        self.assertEqual(code, 1); self.assertIn('先把文件夹改名为「261203 已有文件夹」再 adopt', err)
        self.assertFalse((root / '261202 已有文件夹' / '项目.json').exists())

    def test_rename_does_everything_in_one_command(self):
        # 2026-11-17 反馈单 新2
        import resolve_library
        root = Path(self.temp.name).resolve() / '根'; d, _ = p.initialize(root / '261117 旧名字'); p.initialize(root / '261118 别的项目')
        (d / '02_制作' / '261117 旧名字.md').write_text('# 261117 旧名字\n正文', encoding='utf-8')
        lst = resolve_library.PREFS / 'dblist.conf'
        self.assertIn('261117 旧名字:', lst.read_text(encoding='utf-8'))
        with patch.object(resolve_library, 'resolve_running', return_value=True), self.assertRaisesRegex(RuntimeError, '达芬奇开着'): p.rename(d, '261119 新名字')
        with patch.object(resolve_library, 'resolve_running', return_value=False):
            with self.assertRaisesRegex(RuntimeError, '编号 261118 已被另一个项目占用；下一个没被占用的是 261119'): p.rename(d, '261118 新名字')
            with self.assertRaisesRegex(RuntimeError, '已经存在'): p.rename(d, '261118 别的项目')
            self.assertTrue(d.exists())
            result = p.rename(d, '261119 新名字')
        new = root / '261119 新名字'
        self.assertFalse(d.exists()); self.assertEqual(p.load(new)[1]['name'], '261119 新名字')
        self.assertIn('261119 新名字', (new / '02_制作' / '261119 新名字.md').read_text(encoding='utf-8'))
        self.assertFalse((new / '02_制作' / '261117 旧名字.md').exists())
        self.assertIn('261119 新名字', (new / '项目说明.md').read_text(encoding='utf-8'))
        rows = lst.read_text(encoding='utf-8').splitlines()
        self.assertFalse([r for r in rows if r.startswith('261117 旧名字:')])
        self.assertIn(f"261119 新名字:{new / '02_制作/达芬奇工程'}::::DISK", rows)
        self.assertIn('261118 别的项目:', lst.read_text(encoding='utf-8'))
        self.assertTrue(Path(result['库列表备份']).exists())
        (new / '03_成片' / '成片.mov').write_bytes(b'x')
        with patch.object(resolve_library, 'resolve_running', return_value=False):
            with self.assertRaisesRegex(RuntimeError, '已有成片.*先问用户'): p.rename(new, '261120 再改')
            self.assertEqual(p.rename(new, '261120 再改', user_ok=True)['还要人工核对'], ['成片'])

    def test_init_same_name_twice_stops_instead_of_moving_number(self):
        # 2026-11-20 审查：同名建两次会顺延出「261201 甲」；同名已存在要报错，顺延只在编号被别的项目占用时
        root = Path(self.temp.name).resolve() / '根'
        code, _, _ = self.run_cli('init', '261130 甲', '--root', str(root)); self.assertEqual(code, 0)
        code, out, err = self.run_cli('init', '261130 甲', '--root', str(root))
        self.assertEqual(code, 1); self.assertIn('目录已存在；维护已有项目请用 adopt', err); self.assertNotIn('改用', out)
        self.assertEqual(sorted(x.name for x in root.iterdir()), ['261130 甲'])
        code, out, _ = self.run_cli('init', '261130 乙', '--root', str(root))
        self.assertEqual(code, 0); self.assertIn('原编号 261130 已被占用，改用 261201', out)
        (root / p.FINISHED / '261205 做完的').mkdir(parents=True)      # finish 里已有同名：也不顺延
        code, out, err = self.run_cli('init', '261205 做完的', '--root', str(root))
        self.assertEqual(code, 1); self.assertIn('finish 里已有同名项目', err); self.assertFalse((root / '261206 做完的').exists())

    def test_rename_changes_only_titles_and_lists_other_mentions(self):
        # 2026-11-20 审查：整篇替换会改掉口播正文和反馈记录里的用户原话；新名包含旧名时出现“灯光光”
        root = Path(self.temp.name).resolve() / '根'; d, _ = p.initialize(root / '261117 灯')
        make = d / '02_制作'
        (make / '261117 灯.md').write_text('# 261117 灯\n这期 261117 灯 讲的是灯。\n', encoding='utf-8')
        (make / '反馈记录.md').write_text('11-17 | 用户说“261117 灯 这个名字不好” |\n', encoding='utf-8')
        (d / '笔记.txt').write_text('# 261117 灯光 已经是新名\n# 261117 灯\n正文 261117 灯\n', encoding='utf-8')
        agents = (d / 'AGENTS.md').read_text(encoding='utf-8') + '\n# 261117 灯\n'; (d / 'AGENTS.md').write_text(agents, encoding='utf-8')
        result = p.rename(d, '261117 灯光')
        new = root / '261117 灯光'
        self.assertTrue((new / '项目说明.md').read_text(encoding='utf-8').startswith('# 261117 灯光\n'))
        self.assertNotIn('灯光光', (new / '项目说明.md').read_text(encoding='utf-8'))
        self.assertEqual((new / '02_制作' / '261117 灯光.md').read_text(encoding='utf-8'), '# 261117 灯光\n这期 261117 灯 讲的是灯。\n')
        self.assertEqual((new / '02_制作' / '反馈记录.md').read_text(encoding='utf-8'), '11-17 | 用户说“261117 灯 这个名字不好” |\n')
        self.assertEqual((new / '笔记.txt').read_text(encoding='utf-8'), '# 261117 灯光 已经是新名\n# 261117 灯光\n正文 261117 灯\n')
        self.assertEqual((new / 'AGENTS.md').read_text(encoding='utf-8'), agents)
        self.assertEqual(result['改了旧名的文件'], ['02_制作/261117 灯光.md', '笔记.txt', '项目说明.md'])
        self.assertEqual(result['还提到旧名、没自动改的文件'], ['02_制作/261117 灯光.md', '02_制作/反馈记录.md', 'AGENTS.md', '笔记.txt'])
        self.assertEqual(p.replace_old_name('新灯 和 灯', '灯', '新灯'), '新灯 和 新灯')          # 多出来的在前面也不重复

    def test_rename_stops_early_on_library_conflict_and_editing_job(self):
        import resolve_library
        root = Path(self.temp.name).resolve() / '根'; d, _ = p.initialize(root / '261117 旧名字')
        lst = resolve_library.PREFS / 'dblist.conf'
        other = Path(self.temp.name).resolve() / '另一块盘' / '261119 新名字'; other.mkdir(parents=True)
        lst.write_text(lst.read_text(encoding='utf-8') + f'261119 新名字:{other}::::DISK\n', encoding='utf-8'); before = lst.read_text(encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '已有同名项目库「261119 新名字」指向另一个还在的文件夹'): p.rename(d, '261119 新名字')
        self.assertTrue(d.exists()); self.assertFalse((root / '261119 新名字').exists()); self.assertEqual(lst.read_text(encoding='utf-8'), before)
        # 自动剪辑的 job.json 记着带项目文件夹名的绝对路径：也要先问用户
        job = d / '02_制作' / '自动剪辑' / '片子' / 'job.json'; job.parent.mkdir(parents=True); job.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '已有剪辑工程.*先问用户'): p.rename(d, '261120 新名字')
        self.assertTrue(d.exists())
        self.assertEqual(p.rename(d, '261120 新名字', user_ok=True)['还要人工核对'], ['剪辑工程'])

    def test_rename_rolls_back_when_a_later_step_fails(self):
        # 2026-11-20 审查：文件夹改完名后某一步出错，要整个恢复原样
        import resolve_library
        root = Path(self.temp.name).resolve() / '根'; d, _ = p.initialize(root / '261117 旧名字')
        (d / '02_制作' / '261117 旧名字.md').write_text('# 261117 旧名字\n正文', encoding='utf-8')
        lst = resolve_library.PREFS / 'dblist.conf'; files = {x: x.read_bytes() for x in d.rglob('*') if x.is_file()}; before = lst.read_bytes()
        with patch.object(resolve_library, 'register', side_effect=RuntimeError('模拟登记失败')):
            with self.assertRaisesRegex(RuntimeError, '改名失败，已恢复原样：模拟登记失败'): p.rename(d, '261119 新名字')
        self.assertFalse((root / '261119 新名字').exists()); self.assertEqual(p.load(d)[1]['name'], '261117 旧名字')
        self.assertEqual({x: x.read_bytes() for x in d.rglob('*') if x.is_file()}, files)
        self.assertEqual(lst.read_bytes(), before)
        # 恢复本身也失败：如实说哪项没恢复、文件夹现在在哪
        with patch.object(resolve_library, 'register', side_effect=RuntimeError('模拟登记失败')), patch.object(p, 'backup_dblist', return_value=Path(self.temp.name) / '没有这个备份'):
            with self.assertRaisesRegex(RuntimeError, '改名失败：模拟登记失败。恢复时这几项没恢复成功：达芬奇库列表.*现在项目文件夹在：' + str(d)): p.rename(d, '261119 新名字')
        self.assertEqual(p.load(d)[1]['name'], '261117 旧名字')
        lst.write_bytes(before)

    def test_library_list_tolerates_bare_lines_and_unregister_needs_exact_path(self):
        # 2026-11-20 审查：不带冒号的行会 IndexError；unregister 不再删“路径不存在”的同名行（NAS 没挂载时会误删）
        import resolve_library as rl
        f = rl.PREFS / 'dblist.conf'; mine = Path(self.temp.name).resolve() / '片子'; mine.mkdir()
        f.write_text('片子\n', encoding='utf-8')
        self.assertEqual(rl.register(mine, running=False), '新登记')
        self.assertEqual(f.read_text(encoding='utf-8').splitlines(), ['片子', f'片子:{rl.folder(mine)}::::DISK'])
        f.write_text(f'片子\n片子:/Volumes/没挂载的盘/片子/02_制作/达芬奇工程::::DISK\n片子:{rl.folder(mine)}::::DISK\n', encoding='utf-8')
        self.assertEqual(rl.unregister('片子', rl.folder(mine)), 1)
        self.assertEqual(f.read_text(encoding='utf-8').splitlines(), ['片子', '片子:/Volumes/没挂载的盘/片子/02_制作/达芬奇工程::::DISK'])
        f.write_text('', encoding='utf-8')

    def test_finish_moves_project_and_back(self):
        # 2026-11-17 用户：做完的项目移进 finish，根目录只留正在做的
        import resolve_library
        root = Path(self.temp.name).resolve() / '根'; d, _ = p.initialize(root / '261110 做完的'); (d / '01_原始素材' / 'A roll' / '原片.mov').write_bytes(b'raw')
        lst = resolve_library.PREFS / 'dblist.conf'
        with patch.object(resolve_library, 'resolve_running', return_value=True), self.assertRaisesRegex(RuntimeError, '达芬奇开着'): p.finish(d)
        with patch.object(resolve_library, 'resolve_running', return_value=False):
            result = p.finish(d); done = root / p.FINISHED / '261110 做完的'
            self.assertFalse(d.exists()); self.assertEqual((done / '01_原始素材' / 'A roll' / '原片.mov').read_bytes(), b'raw')
            self.assertEqual(p.load(done)[1]['name'], '261110 做完的')
            self.assertIn(f"261110 做完的:{done / '02_制作/达芬奇工程'}::::DISK", lst.read_text(encoding='utf-8').splitlines())
            self.assertEqual(len([r for r in lst.read_text(encoding='utf-8').splitlines() if r.startswith('261110 做完的:')]), 1)
            self.assertIn('还没有封存的交付包', result['提醒'])
            self.assertEqual(p.free_name('261110 新的', root), ('261111 新的', '261110'))
            with self.assertRaisesRegex(RuntimeError, '已经在 finish 里'): p.finish(done)
            with self.assertRaisesRegex(RuntimeError, '不在 finish 里'): p.finish(p.initialize(root / '261112 还在做')[0], back=True)
            p.finish(done, back=True)
        self.assertTrue(d.exists()); self.assertFalse(done.exists())
        self.assertIn(f"261110 做完的:{d / '02_制作/达芬奇工程'}::::DISK", lst.read_text(encoding='utf-8').splitlines())

    def test_final_script_required_before_archive_and_seal(self):
        # 2026-11-14 反馈单：每条片子存一份最终口播文案
        with self.assertRaisesRegex(RuntimeError, '缺最终文案'): p.check_script(self.d)
        (self.d / '02_制作' / '口播文案.md').write_text('旧名字的文案', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '03_成片/测试项目.md（文件名和项目名一样）.*挪过去并改成这个名字'): p.check_script(self.d)
        (self.d / '02_制作' / '测试项目.md').write_text('  \n', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '缺最终文案'): p.check_script(self.d)
        code, _, err = self.run_cli('seal', str(self.d))
        self.assertEqual(code, 1); self.assertIn('缺最终文案', err)
        # 2026-11-20 审查：archive 也要拦（成片文件名合格，才走得到口播文案这一步）
        import resolve_archive
        self.meta['assets']['video'] = '03_成片/261018_测试项目.mp4'; p.write_json(self.d / '项目.json', self.meta)
        with patch.object(resolve_archive, 'archive') as archive:
            code, _, err = self.run_cli('archive', str(self.d))
            archive.assert_not_called()
        self.assertEqual(code, 1); self.assertIn('缺最终文案', err)
        (self.d / '02_制作' / '测试项目.md').write_text('定稿全文', encoding='utf-8')
        p.check_script(self.d)
        self.assertIn('03_成片/<项目名>.md', (self.d / 'AGENTS.md').read_text(encoding='utf-8'))

    def test_adopt_keeps_existing_project_note(self):
        d = Path(self.temp.name) / '已有项目'; d.mkdir(); (d / '项目说明.md').write_text('我自己写的', encoding='utf-8')
        with patch('resolve_library.register', return_value='测试'): p.initialize(d, adopt=True)
        self.assertEqual((d / '项目说明.md').read_text(encoding='utf-8'), '我自己写的')

    def test_register_final_writes_paths_and_checks_name(self):
        self.fixture()
        out = p.register_final(self.d, '03_成片/261018_测试项目.mp4', None, '工程', '审核线')
        self.assertEqual(p.load(self.d)[1]['resolve']['timeline'], '审核线'); self.assertEqual(out['video'], '03_成片/261018_测试项目.mp4')
        with self.assertRaises(RuntimeError): p.register_final(self.d, '/etc/hosts', None, '工程', '审核线')

    def test_package_checks_both_archived_and_working_files(self):
        self.fixture()
        package = delivery.verify_package(self.d, self.meta)
        archived = self.d / package['files']['video']['path']
        archived.write_bytes(b'changed')
        with self.assertRaises(RuntimeError): delivery.verify_package(self.d, self.meta)
        self.package_fixture()
        (self.d / self.meta['assets']['video']).write_bytes(b'new cut')
        with self.assertRaises(RuntimeError): delivery.verify_package(self.d, self.meta)

    def test_package_allows_removed_work_copy_but_rejects_changed_title(self):
        self.fixture()
        (self.d / self.meta['assets']['video']).unlink()
        delivery.verify_package(self.d, self.meta)
        self.meta['title'] = '改过的标题'
        with self.assertRaises(RuntimeError): delivery.verify_package(self.d, self.meta)

    def test_cover_text_comes_from_document_without_rewriting(self):
        self.meta.update(cover_main='第一行\n第二行', cover_sub='副标题')
        brief = delivery.cover_brief(self.d, self.meta)
        self.assertEqual([brief['line1'], brief['line2'], brief['sub']], ['第一行', '第二行', '副标题'])
        self.assertFalse((self.d / '02_制作/封面制作/_制作文件').exists())

    def test_one_line_cover_title_splits_at_comma(self):
        self.meta.update(cover_main='水电这四件事儿，多少钱都得花', cover_sub='十三年的装修经验')
        brief = delivery.cover_brief(self.d, self.meta)
        self.assertEqual([brief['line1'], brief['line2']], ['水电这四件事儿', '多少钱都得花'])
        self.assertEqual(delivery.cover_lines('第一行\n第二行'), ['第一行', '第二行'])
        self.assertEqual(delivery.cover_lines('甲，乙乙乙，丙丙'), ['甲，乙乙乙', '丙丙'])
        with self.assertRaises(RuntimeError):
            delivery.cover_brief(self.d, dict(self.meta, cover_main='没有逗号的一行'))

    def test_three_line_cover_title(self):      # 2026-11-20 用户定：一般两行，三行也可以
        self.meta.update(cover_main='为什么外地\n灯光设计\n我不接了', cover_sub='十三年的装修经验')
        p.write_json(self.d / '项目.json', self.meta)
        brief = delivery.cover_brief(self.d, self.meta)
        self.assertEqual([brief['line1'], brief['line2'], brief['line3']], ['为什么外地', '灯光设计', '我不接了'])
        self.assertIn('主标题第三行：我不接了', (self.d / '02_制作/封面制作/封面任务.md').read_text(encoding='utf-8'))
        self.assertNotIn('line3', delivery.cover_brief(self.d, dict(self.meta, cover_main='第一行\n第二行')))
        with self.assertRaises(RuntimeError): delivery.cover_brief(self.d, dict(self.meta, cover_main='一\n二\n三\n四'))
        work = self.d / '02_制作/封面制作'; (work / '_制作文件').mkdir(parents=True, exist_ok=True)
        cover = work / '封面.jpg'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=white:s=300x400', '-frames:v', '1', str(cover)], check=True)
        p.write_json(work / '_制作文件/spec.json', {'line1': '为什么外地', 'line2': '灯光设计', 'sub': '十三年的装修经验'})
        import os, time; old = time.time() - 600; os.utime(work / '_制作文件/spec.json', (old, old))
        with self.assertRaisesRegex(RuntimeError, '不一致'): delivery.cover_register(self.d, p.load(self.d)[1], cover, '3:4')
        p.write_json(work / '_制作文件/spec.json', {'line1': '为什么外地', 'line2': '灯光设计', 'line3': '我不接了', 'sub': '十三年的装修经验'})
        os.utime(work / '_制作文件/spec.json', (old, old))
        self.assertTrue(delivery.cover_register(self.d, p.load(self.d)[1], cover, '3:4')['path'])

    def test_cover_without_subtitle(self):      # 2026-11-21 用户定：封面文字不分主副，副标题可以不填
        self.meta.update(cover_main='7米挑空的采光井\n灯装哪？才够亮？', cover_sub='')
        p.write_json(self.d / '项目.json', self.meta)
        brief = delivery.cover_brief(self.d, self.meta)
        self.assertEqual([brief['line1'], brief['line2'], brief['sub']], ['7米挑空的采光井', '灯装哪？才够亮？', ''])
        self.assertIn('副标题：（无）', (self.d / '02_制作/封面制作/封面任务.md').read_text(encoding='utf-8'))
        work = self.d / '02_制作/封面制作'; (work / '_制作文件').mkdir(parents=True, exist_ok=True)
        cover = work / '封面.jpg'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=white:s=300x400', '-frames:v', '1', str(cover)], check=True)
        import os, time; old = time.time() - 600
        p.write_json(work / '_制作文件/spec.json', {'line1': '7米挑空的采光井', 'line2': '灯装哪？才够亮？', 'sub': '见光不见灯'})
        os.utime(work / '_制作文件/spec.json', (old, old))
        with self.assertRaisesRegex(RuntimeError, '不一致'): delivery.cover_register(self.d, p.load(self.d)[1], cover, '3:4')
        p.write_json(work / '_制作文件/spec.json', {'line1': '7米挑空的采光井', 'line2': '灯装哪？才够亮？', 'sub': ''})   # 封面程序不给 --sub 时登记的是空字符串
        os.utime(work / '_制作文件/spec.json', (old, old))
        self.assertTrue(delivery.cover_register(self.d, p.load(self.d)[1], cover, '3:4')['path'])

    def test_cover_register_accepts_one_line_title(self):
        self.meta.update(cover_main='水电这四件事儿，多少钱都得花', cover_sub='十三年的装修经验')
        p.write_json(self.d / '项目.json', self.meta)
        work = self.d / '02_制作/封面制作'; (work / '_制作文件').mkdir(parents=True, exist_ok=True)
        p.write_json(work / '_制作文件/spec.json', {'line1': '水电这四件事儿', 'line2': '多少钱都得花', 'sub': '十三年的装修经验'})
        cover = work / '封面.jpg'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=white:s=300x400', '-frames:v', '1', str(cover)], check=True)
        result = delivery.cover_register(self.d, p.load(self.d)[1], cover, '3:4')
        self.assertEqual(p.load(self.d)[1]['assets']['covers']['3:4'], result['path'])

        # 2026-11-13 审查：改字重新登记后没重新出图，旧图不能登记
        import os, time
        spec = work / '_制作文件/spec.json'; later = time.time() + 600; os.utime(spec, (later, later))
        with self.assertRaisesRegex(RuntimeError, '还没重新出图'): delivery.cover_register(self.d, p.load(self.d)[1], cover, '3:4')

    def test_seal_copies_every_asset_and_reuses_same_version(self):
        self.fixture()
        base = self.d / '04_达芬奇归档'
        for name in ('工程.drp', '最终时间线.drt', '时间线快照.json'):
            (base / name).write_bytes(b'archive fixture')
        media = base / '媒体.mov'; media.write_bytes(b'collected source')
        p.write_json(base / '媒体清单.json', {'source.mov': {'target': str(media), 'sha256': p.digest(media)}})
        self.meta['resolve']['archive'] = '04_达芬奇归档/工程.drp'
        p.write_json(self.d / '项目.json', self.meta)
        first = delivery.seal(self.d, self.meta)
        second = delivery.seal(self.d, p.load(self.d)[1])
        self.assertEqual(first, second)
        self.assertEqual(len(list((base / '交付包').iterdir())), 1)
        meta = p.load(self.d)[1]
        package = delivery.verify_package(self.d, meta)
        self.assertEqual(set(package['files']), {'video', 'cover:3:4'})
        self.assertEqual(Path(package['files']['video']['path']).name, '261018_测试项目.mp4')
        media.write_bytes(b'changed source')
        with self.assertRaises(RuntimeError): delivery.verify_package(self.d, meta)

    def test_final_video_requires_valid_timestamp_and_preserved_package_name(self):
        for name in ('成片.mov', '261018.mov', '261018_193025.mov', '261018_1930_测试项目.mov',
                     '260230_测试项目.mov', '261018_246000_测试项目.mov',
                     '261018_其他项目.mov', '261018_已确认标题.mp4'):
            with self.assertRaises(RuntimeError): p.check_final_video_name(name, '测试项目')
        self.assertEqual(p.check_final_video_name('03_成片/000229_测试项目.mov', '测试项目'), '000229_测试项目.mov')
        project_name = '261015 老房_烟道.v2'
        self.assertEqual(p.check_final_video_name('261018_' + project_name + '.mov', project_name), '261018_' + project_name + '.mov')
        self.fixture()
        binding = self.meta['delivery_package']['manifest']
        package = p.read_json(self.d / binding)
        source = self.d / package['files']['video']['path']
        renamed = source.with_name('成片.mp4')
        source.rename(renamed)
        package['files']['video']['path'] = str(renamed.relative_to(self.d))
        p.write_json(self.d / binding, package)
        with self.assertRaises(RuntimeError): delivery.verify_package(self.d, self.meta)

    def test_draft_handoff_and_idempotence(self):
        cli, calls = self.fixture()
        with patch.object(p, 'command', side_effect=cli):
            first = p.upload(self.d, self.meta, execute=True)
            calls.clear(); second = p.upload(self.d, self.meta, execute=True)
        self.assertEqual(first, second)
        self.assertFalse(any(args[1] in ('publish', 'upload', 'draft') for args in calls))

    def test_ambiguous_draft_preview_never_submits(self):
        cli, calls = self.fixture()
        def no_draft(args):
            value = cli(args)
            if args[1:3] == ['draft', 'save']: return {'ok': True, 'data': {}}
            return value
        with patch.object(p, 'command', side_effect=no_draft):
            with self.assertRaises(RuntimeError): p.upload(self.d, self.meta, execute=True)
        self.assertTrue(all('--dry-run' in args for args in calls if args[1:3] == ['draft', 'save']))

    def test_unknown_save_result_is_kept_and_never_retried(self):
        cli, calls = self.fixture()
        def unknown(args):
            value = cli(args)
            if args[1:3] == ['draft', 'save'] and '--dry-run' not in args:
                return {'ok': True, 'data': {}}
            return value
        with patch.object(p, 'command', side_effect=unknown):
            with self.assertRaises(RuntimeError): p.upload(self.d, self.meta, execute=True)
            receipts = list((self.d / '05_上传记录').glob('*/回执.json'))
            self.assertEqual(p.read_json(receipts[0])['state'], 'draft_save_result_unverified')
            calls.clear()
            with self.assertRaises(RuntimeError): p.upload(self.d, self.meta, execute=True)
            self.assertFalse(any(args[1:3] == ['draft', 'save'] for args in calls))

    def test_failure_before_draft_save_can_resume(self):
        # 2026-10-29 审查：存草稿之前就失败（传素材断网），回执停在 uploading，不能把以后的上传永久拦住
        cli, calls = self.fixture()
        def flaky(args):
            if args[1] == 'upload' and not flaky.done:
                flaky.done = True; raise RuntimeError('网络断开')
            return cli(args)
        flaky.done = False
        with patch.object(p, 'command', side_effect=flaky):
            with self.assertRaises(RuntimeError): p.upload(self.d, self.meta, execute=True)
            receipts = list((self.d / '05_上传记录').glob('*/回执.json'))
            self.assertEqual(p.read_json(receipts[0])['state'], 'uploading')
            calls.clear()
            done = p.upload(self.d, self.meta, execute=True)
        self.assertEqual(done['state'], 'draft_saved')
        self.assertEqual(len(list((self.d / '05_上传记录').glob('*/回执.json'))), 1)
        self.assertEqual(sum(1 for a in calls if a[1:3] == ['draft', 'save'] and '--dry-run' not in a), 1)

    def test_other_uploading_receipt_does_not_block(self):
        cli, calls = self.fixture()
        stale = self.d / '05_上传记录/旧的一次'; stale.mkdir(parents=True)
        p.write_json(stale / '回执.json', {'state': 'uploading', 'uploads': {}, 'drafts': []})
        p.write_json(stale / '本次发布资料.json', {'title': '别的标题'})
        with patch.object(p, 'command', side_effect=cli):
            self.assertEqual(p.upload(self.d, self.meta, execute=True)['state'], 'draft_saved')

    def test_archive_switches_to_project_library_and_back(self):
        # 2026-10-29：达芬奇停在别的协作库时，存档要切到片子自己的项目库做，失败也切回原库、原文件夹、原工程和时间线
        import resolve_archive as ra
        LIB, COLLAB = {'DbType': 'Disk', 'DbName': self.d.name}, {'DbType': 'PostgreSQL', 'DbName': '别的协作库', 'IpAddress': '192.0.2.10'}
        class TL:
            def __init__(s, n): s.n = n
            def GetName(s): return s.n
        class Proj:
            def __init__(s, n, tls): s.n, s.tls, s.cur = n, [TL(x) for x in tls], None
            def GetName(s): return s.n
            def GetCurrentTimeline(s): return s.cur
            def GetTimelineCount(s): return len(s.tls)
            def GetTimelineByIndex(s, i): return s.tls[i - 1]
            def SetCurrentTimeline(s, t): s.cur = t; return True
        class PM:
            def __init__(s):
                s.tree = {'别的协作库': {('Master', '别人外'): {'别人的工程': Proj('别人的工程', ['02brol时间线'])}},
                          self.d.name: {('Master',): {'本项目': Proj('本项目', ['定稿'])}}}
                s.db, s.path, s.cur = COLLAB, ('Master', '别人外'), None
                s.cur = s.tree['别的协作库'][s.path]['别人的工程']; s.cur.cur = s.cur.tls[0]
            def GetCurrentDatabase(s): return dict(s.db)
            def GetDatabaseList(s): return [COLLAB, LIB]
            def SetCurrentDatabase(s, db): s.db, s.cur, s.path = db, None, ('Master',); return True
            def GetCurrentProject(s): return s.cur
            def SaveProject(s): return True
            def GetCurrentFolder(s): return s.path[-1]
            def GotoParentFolder(s):
                if len(s.path) == 1: return False
                s.path = s.path[:-1]; return True
            def GotoRootFolder(s): s.path = ('Master',); return True
            def OpenFolder(s, f):
                if s.path + (f,) not in s.tree[s.db['DbName']]: return False
                s.path = s.path + (f,); return True
            def LoadProject(s, n):
                q = s.tree[s.db['DbName']].get(s.path, {}).get(n)
                if q: s.cur = q
                return q
        pm = PM(); res = ra.with_library(self.d, {'project': '本项目', 'timeline': '定稿'})
        with self.assertRaises(RuntimeError): ra.remember_user_project(pm, res, switch=False)
        back = ra.remember_user_project(pm, res, switch=True)
        self.assertEqual((back['db']['DbName'], back['folders'], back['project'], back['timeline']), ('别的协作库', ['Master', '别人外'], '别人的工程', '02brol时间线'))
        self.assertEqual(pm.path, ('Master', '别人外'))
        q = ra.open_project(pm, res)
        self.assertEqual((pm.db['DbName'], q.GetName()), (self.d.name, '本项目'))
        q.cur = None; ra.restore_user_project(pm, back)
        self.assertEqual((pm.db['DbName'], pm.path, pm.cur.GetName(), pm.cur.cur.GetName()), ('别的协作库', ('Master', '别人外'), '别人的工程', '02brol时间线'))
        # 正式开始前就失败（工程里没有最终时间线）：也切回用户原来的地方
        self.meta['resolve'].update(project='本项目', timeline='不存在的时间线'); p.write_json(self.d / '项目.json', self.meta)
        pm2 = PM()
        with patch.object(ra, 'connect', return_value=type('R', (), {'GetProjectManager': lambda s: pm2})()), patch('time.sleep'):
            with self.assertRaises(RuntimeError): ra.archive(self.d, switch=True)
        self.assertEqual((pm2.db['DbName'], pm2.path, pm2.cur.GetName()), ('别的协作库', ('Master', '别人外'), '别人的工程'))

    def test_archive_keeps_full_project_drp_in_library_folder(self):
        # 2026-10-29 用户：达芬奇工程 文件夹里要看得见一个工程 .drp；存档成功才放，新的覆盖旧的
        import resolve_archive as ra
        seed = Path(self.temp.name) / 'seed.drp'; seed.write_bytes(b'v1')
        got = ra.keep_project_drp(self.d, '工程甲', seed)
        self.assertEqual(got, self.d / '02_制作/达芬奇工程/工程甲.drp'); self.assertEqual(got.read_bytes(), b'v1')
        seed.write_bytes(b'v2'); ra.keep_project_drp(self.d, '工程甲', seed)
        self.assertEqual(got.read_bytes(), b'v2')
        self.assertEqual(sorted(x.name for x in got.parent.iterdir() if x.suffix == '.drp' or '写入中' in x.name), ['工程甲.drp'])

    def test_project_library_registered_in_resolve_list(self):
        # 2026-10-29：建项目时登记片子自己的达芬奇项目库；达芬奇开着不改（退出时会被覆盖）；片子挪位置后改路径
        import resolve_library as rl
        f = rl.PREFS / 'dblist.conf'; keep = 'Local Database:/x/Resolve Project Library::::DISK'
        f.write_text(keep + '\n', encoding='utf-8')
        other = Path(self.temp.name) / '另一个' / '片子'; other.mkdir(parents=True)
        with self.assertRaises(RuntimeError): rl.register(other, running=True)
        self.assertEqual(f.read_text(encoding='utf-8'), keep + '\n')
        self.assertEqual(rl.register(other, running=False), '新登记')
        self.assertIn(f'片子:{other}/02_制作/达芬奇工程::::DISK', f.read_text(encoding='utf-8'))
        self.assertEqual(rl.register(other, running=True), '已登记')
        moved = Path(self.temp.name) / '挪过' / '片子'; moved.mkdir(parents=True)
        with self.assertRaises(RuntimeError): rl.register(moved, running=False)      # 原文件夹还在：同名冲突
        import shutil; shutil.rmtree(other.parent)
        self.assertEqual(rl.register(moved, running=False), '路径已更新')
        lines = f.read_text(encoding='utf-8').splitlines()
        self.assertEqual(lines, [keep, f'片子:{moved}/02_制作/达芬奇工程::::DISK'])

    def test_script_sync_writes_title_and_cover_text_into_project_json(self):
        # 2026-11-23 初稿模式实测：新项目的标题、封面文字用一条命令从文案同步进 项目.json，不用 AI 手改 JSON
        f = p.script_path(self.d); f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('## 标题\n开关插座装多高才顺手\n\n## 封面文字\n开关插座\n装多高\n才顺手\n\n## 口播正文\n大家好。\n', encoding='utf-8')
        self.meta.update(title='', cover_main='', cover_sub=''); p.write_json(self.d / '项目.json', self.meta)
        out = p.sync_script(self.d, p.read_json(self.d / '项目.json')); meta = p.read_json(self.d / '项目.json')
        self.assertEqual((meta['title'], meta['cover_main'], meta['cover_sub']), ('开关插座装多高才顺手', '开关插座\n装多高\n才顺手', ''))
        self.assertEqual(set(out['写进项目.json']), {'title', 'cover_main'})
        self.assertIn('没有要改的', p.sync_script(self.d, meta)['写进项目.json'])
        f.write_text('## 口播正文\n大家好。\n', encoding='utf-8')
        self.assertEqual(p.sync_script(self.d, meta)['文案里没写、没动'], ['标题', '封面文字'])
        self.assertEqual(p.read_json(self.d / '项目.json')['title'], '开关插座装多高才顺手')

    def test_color_management(self):
        # 2026-11-28 用户定“工程开色彩管理”：新工程建好就开；已有工程 resolve-color 补上（只认当前开着的就是这条片子的）
        import resolve_library as rl
        pm, r = self._fake_resolve([{'DbType': 'Disk', 'DbName': '别的库'}], '别的库', {'别的库': {'别的片子': ['v1']}})
        cur = pm.GetCurrentProject()
        with self.assertRaisesRegex(RuntimeError, '不是《'): rl.enable_color_management(self.d, r)
        self.assertEqual(cur.set, {})
        cur.n = self.d.name
        out = rl.enable_color_management(self.d, r)
        self.assertEqual({k: cur.set.get(k) for k, _ in rl.COLOR_MANAGED}, dict(rl.COLOR_MANAGED)); self.assertTrue(rl.is_color_managed(cur))
        self.assertEqual(cur.set['colorSpaceOutput'], 'Rec.709-A'); self.assertEqual(cur.set['timelineWorkingLuminanceMode'], 'SDR 100')
        self.assertEqual(len(out['改了']), len(rl.COLOR_MANAGED)); self.assertEqual(pm.saved[-1], self.d.name)
        self.assertEqual(rl.enable_color_management(self.d, r)['改了'], '本来就开着')
        cur.SetSetting = lambda k, v: False
        cur.set.clear()
        with self.assertRaisesRegex(RuntimeError, '没设上'): rl.color_manage(cur)

    def _fake_resolve(self, dbs, now, projects):
        """达芬奇替身：dbs 库列表，now 当前库名，projects {库名: {工程名: [时间线…]}}；当前工程是 now 库里第一个。"""
        class TL:
            def __init__(s, n): s.n = n
            def GetName(s): return s.n
        class Proj:
            def __init__(s, n, tls): s.n, s.tls, s.set, s.rendering = n, [TL(x) for x in tls], {}, False
            def GetName(s): return s.n
            def GetCurrentTimeline(s): return s.tls[0] if s.tls else None
            def GetTimelineCount(s): return len(s.tls)
            def IsRenderingInProgress(s): return s.rendering
            def SetSetting(s, k, v): s.set[k] = v; return True
            def GetSetting(s, k=None): return s.set.get(k, '') if k else dict(s.set)
        class PM:
            def __init__(s):
                s.dbs = [dict(x) for x in dbs]; s.tree = {k: {n: Proj(n, t) for n, t in v.items()} for k, v in projects.items()}
                s.db = next(x for x in s.dbs if x['DbName'] == now); s.cur = next(iter(s.tree.get(now, {}).values()), None)
                s.saved, s.save_ok, s.exported = [], True, {}
            def GetCurrentDatabase(s): return dict(s.db)
            def GetDatabaseList(s): return [dict(x) for x in s.dbs]
            def SetCurrentDatabase(s, db):
                hit = next((x for x in s.dbs if x['DbName'] == db['DbName'] and x['DbType'] == db['DbType']), None)
                if not hit: return False
                s.db, s.cur = hit, None; return True
            def GetCurrentProject(s): return s.cur
            def SaveProject(s):
                if s.save_ok: s.saved.append(s.cur.GetName())
                return s.save_ok
            def GotoRootFolder(s): return True
            def GetProjectListInCurrentFolder(s): return list(s.tree.get(s.db['DbName'], {}))
            def LoadProject(s, n):
                q = s.tree.get(s.db['DbName'], {}).get(n)
                if q: s.cur = q
                return q
            def CreateProject(s, n):
                if n in s.tree.setdefault(s.db['DbName'], {}): return None
                s.cur = s.tree[s.db['DbName']][n] = Proj(n, []); return s.cur
            def ExportProject(s, n, path, stills=True):
                if n not in s.tree.get(s.db['DbName'], {}): return False
                Path(path).write_bytes(b'drp'); s.exported[n] = s.db['DbName']; return True
            def ImportProject(s, path, n=None):
                s.tree.setdefault(s.db['DbName'], {})[n] = Proj(n, ['审核线']); return True
        pm = PM()
        return pm, type('R', (), {'GetProjectManager': lambda s: pm})()

    def test_resolve_open_borrows_current_library_when_resolve_is_open(self):
        # 2026-11-23 初稿模式实测：达芬奇开着、库列表里没有新片子的库 → 不停、不问：保存用户工程并记一笔，
        # 在当前开着的库里新建同名工程（29.97、缓存在项目文件夹），项目.json 记“借用”；存档后 migrate 迁回
        import resolve_library as rl
        LOCAL, OLD = {'DbType': 'Disk', 'DbName': 'Local Database'}, {'DbType': 'Disk', 'DbName': '261118 试验田'}
        pm, r = self._fake_resolve([LOCAL, OLD], '261118 试验田', {'261118 试验田': {'试验田工程': ['试验田_审核线4']}})
        with patch('time.sleep'):
            out = rl.open_project(self.d, r)
            meta = p.read_json(self.d / '项目.json')
            self.assertEqual(meta['resolve']['database'], OLD); self.assertIn('--migrate', meta['resolve']['borrowed']['说明'])
            self.assertEqual((pm.db['DbName'], pm.cur.GetName(), pm.saved), ('261118 试验田', self.d.name, ['试验田工程', self.d.name]))
            self.assertEqual(pm.cur.set['timelineFrameRate'], '29.97')
            self.assertTrue(rl.is_color_managed(pm.cur))                                   # 新工程开色彩管理（2026-11-28）
            self.assertEqual(pm.cur.set['perfCacheClipsLocation'], str(self.d / '02_制作/达芬奇缓存'))
            log = rl.switch_log(self.d)
            self.assertEqual((log[0]['原项目库'], log[0]['原工程'], log[0]['原时间线']), ('261118 试验田', '试验田工程', '试验田_审核线4'))
            self.assertIn('《试验田工程》', out['切走了']); self.assertIn('新建了工程', out['结果'])
            # 再跑一次：已经开着，不重复建、不再记
            self.assertEqual(rl.open_project(self.d, r)['结果'], '已经开着'); self.assertEqual(len(rl.switch_log(self.d)), 1)
            # 用户切回自己的工程后再跑：打开已有工程，不新建
            pm.LoadProject('试验田工程')
            self.assertEqual(rl.open_project(self.d, r)['结果'], '打开了已有工程'); self.assertEqual(len(rl.switch_log(self.d)), 2)
            # 迁库：达芬奇开着但列表里还没有自己的库 → 说明迁不了、什么都不动
            self.assertIn('这次迁不了', rl.migrate(self.d, r)['结果'])
            self.assertIn('borrowed', p.read_json(self.d / '项目.json')['resolve'])
            # 自己的库登记好了（达芬奇重启过）→ 导出、切库、导入、改 项目.json；借用库里那份不删
            pm.dbs.append(rl.db(self.d))
            got = rl.migrate(self.d, r); meta = p.read_json(self.d / '项目.json')
            self.assertEqual((pm.db['DbName'], pm.cur.GetName()), (self.d.name, self.d.name))
            self.assertNotIn('database', meta['resolve']); self.assertNotIn('borrowed', meta['resolve']); self.assertIn('迁回时间', meta['resolve']['migrated'])
            self.assertIn(self.d.name, pm.tree['261118 试验田']); self.assertIn('没删', got['提醒'])
            self.assertEqual(rl.migrate(self.d, r)['结果'], '不用迁：工程没有借用别的库')

    def test_resolve_open_uses_own_library_and_never_switches_on_failed_save(self):
        import resolve_library as rl
        OWN, COLLAB = rl.db(self.d), {'DbType': 'PostgreSQL', 'DbName': '协作库', 'IpAddress': '1.2.3.4'}
        # 自己的库在列表里：切过去新建，不借用
        pm, r = self._fake_resolve([COLLAB, OWN], '协作库', {'协作库': {'别人的工程': ['线']}})
        with patch('time.sleep'):
            out = rl.open_project(self.d, r)
        self.assertEqual((pm.db['DbName'], pm.cur.GetName()), (self.d.name, self.d.name))
        self.assertNotIn('borrowed', p.read_json(self.d / '项目.json')['resolve']); self.assertNotIn('借用', out)
        # 用户工程保存失败：不切、不建、不记借用以外的东西
        pm, r = self._fake_resolve([COLLAB, OWN], '协作库', {'协作库': {'别人的工程': ['线']}}); pm.save_ok = False
        with self.assertRaisesRegex(RuntimeError, '保存.*失败，没有切换'): rl.open_project(self.d, r)
        self.assertEqual((pm.db['DbName'], pm.cur.GetName()), ('协作库', '别人的工程'))
        # 正在渲染：不动
        pm, r = self._fake_resolve([COLLAB, OWN], '协作库', {'协作库': {'别人的工程': ['线']}}); pm.cur.rendering = True
        with self.assertRaisesRegex(RuntimeError, '正在渲染'): rl.open_project(self.d, r)
        # 当前开着网络协作库、自己的库不在列表：不往协作库里放，借最近登记的磁盘库
        (self.d / '项目.json').write_text(json.dumps(dict(self.meta, resolve={'project': '', 'timeline': ''}), ensure_ascii=False), encoding='utf-8')
        pm, r = self._fake_resolve([{'DbType': 'Disk', 'DbName': 'Local Database'}, COLLAB, {'DbType': 'Disk', 'DbName': '261122 上一条'}], '协作库', {'协作库': {'别人的工程': ['线']}})
        with patch('time.sleep'):
            rl.open_project(self.d, r)
        self.assertEqual((pm.db['DbName'], pm.cur.GetName()), ('261122 上一条', self.d.name))
        # 达芬奇关着做迁库：只登记库
        with patch.object(rl, 'resolve_running', return_value=False):
            self.assertIn('达芬奇关着', rl.migrate(self.d)['结果'])
        self.assertIn(self.d.name + ':', (rl.PREFS / 'dblist.conf').read_text(encoding='utf-8'))

    def test_saved_id_accepts_observed_response_and_rejects_business_failure(self):
        observed = {'ok': True, 'action': 'draft.save', 'data': {'statusCode': 0, 'data': '00000000-0000-4000-8000-000000000001'}}
        self.assertEqual(publish_fields.saved_draft_id(observed), observed['data']['data'])
        observed['data']['statusCode'] = 1
        self.assertIsNone(publish_fields.saved_draft_id(observed))
        self.assertEqual(publish_fields.saved_draft_id({'ok': True, 'data': {'taskSetId': 'legacy-id'}}), 'legacy-id')

    def test_preview_rejects_unrelated_flags_and_changed_account(self):
        payload = {'platforms': ['抖音'], 'publishArgs': {'accountForms': [{'platformAccountId': 'a', 'video': {'key': 'v'}, 'cover': {'key': 'c'}, 'contentPublishForm': {'title': '本次标题'}}]}}
        value = {'ok': True, 'action': 'draft.save.dry-run', 'data': {'dryRun': True, 'request': {'isDraft': True, 'publishType': 'video', **copy.deepcopy(payload)}}}
        self.assertTrue(publish_fields.verify_draft_preview(value, payload))
        value['data']['request']['publishArgs']['accountForms'][0]['platformAccountId'] = 'another'
        self.assertFalse(publish_fields.verify_draft_preview(value, payload))
        value['data']['request']['publishArgs'] = copy.deepcopy(payload['publishArgs'])
        value['data']['request']['isDraft'] = False
        value['data']['unrelated'] = {'isDraft': True}
        self.assertFalse(publish_fields.verify_draft_preview(value, payload))

    def test_tags_mapped_without_omitting_them(self):
        self.meta.update(description='正文 & 原文', tags=['装修', '老房'])
        schema = {'businessFields': {'title': {}, 'description': {}}, 'minimalTemplate': {'publishArgs': {'accountForms': [{'contentPublishForm': {'formType': 'task'}}]}}}
        fields = publish_fields.content_form(self.meta, {'platform': '小红书'}, schema, {})
        self.assertIn('正文 &amp; 原文', fields['description'])
        self.assertIn('<topic text="装修">#装修</topic>', fields['description'])
        self.assertNotIn('tags', fields)

    def test_bilibili_requires_user_category_and_preserves_query_raw(self):
        target = {'platform': '哔哩哔哩', 'account_id': 'bilibili-only'}
        schema = {'businessFields': {'title': {}, 'description': {}, 'tags': {'required': True, 'minItems': 1}, 'category': {'required': True, 'minItems': 1}}, 'minimalTemplate': {'publishArgs': {'accountForms': [{'contentPublishForm': {}}]}}}
        self.meta['tags'] = ['装修']
        with patch.object(publish_fields, 'command') as command:
            with self.assertRaises(RuntimeError): publish_fields.content_form(self.meta, target, schema, {})
            command.assert_not_called()
        self.meta['bilibili_category'] = '家装房产'
        nodes = [{'yixiaoerId': 'id-from-query', 'yixiaoerName': '家装房产', 'raw': {'id': 15, 'name': '家装房产', 'other': 'preserve'}}]
        with patch.object(publish_fields, 'command', return_value={'ok': True, 'data': {'paths': [{'path': '家装房产', 'category': nodes}]}}):
            mapped = publish_fields.content_form(self.meta, target, schema, {})
        self.assertEqual(mapped['category'], nodes)
        self.assertEqual(mapped['category'][0]['raw']['other'], 'preserve')


    def test_platform_topics_added_only_to_that_platform(self):
        self.meta.update(tags=['装修', '防水'], platform_topics={'小红书': ['防水', '家装灵感'], '抖音': ['装修避坑']})
        schema = {'businessFields': {'title': {}, 'description': {}}, 'minimalTemplate': {'publishArgs': {'accountForms': [{'contentPublishForm': {}}]}}}
        xhs = publish_fields.content_form(self.meta, {'platform': '小红书'}, schema, {})['description']
        ks = publish_fields.content_form(self.meta, {'platform': '快手'}, schema, {})['description']
        self.assertEqual(xhs.count('<topic text="防水">'), 1)          # 重复的不加两次
        self.assertIn('<topic text="家装灵感">', xhs)
        self.assertNotIn('家装灵感', ks); self.assertNotIn('装修避坑', ks)

    def test_accounts_fill_missing_only_adds_missing_platforms(self):      # 2026-11-21 新20
        rows = [{'id': 1, 'platformName': '抖音', 'platformAccountName': '小王抖音', 'status': 1},
                {'id': 2, 'platformName': '快手', 'platformAccountName': '小王快手', 'status': 0},
                {'id': 3, 'platformName': '小红书', 'platformAccountName': '小王红书', 'status': 1}]
        def cli(args): return {'ok': True, 'data': rows if args[1:3] == ['accounts', 'list'] else {}}
        with patch.object(p, 'command', side_effect=cli):
            self.assertEqual(self.run_cli('accounts', str(self.d))[0], 0)
            self.assertEqual([t['platform'] for t in p.read_json(self.d / '项目.json')['targets']], ['抖音', '小红书'])
            self.assertEqual(self.run_cli('accounts', str(self.d))[0], 1)             # 不带参数：照旧不覆盖，并提示怎么补
            self.assertIn('--fill-missing', self.run_cli('accounts', str(self.d))[2])
            meta = p.read_json(self.d / '项目.json')
            meta['targets'][0].update(enabled=False, cover='4:3'); p.write_json(self.d / '项目.json', meta)   # 用户手动改过的设置
            before = p.read_json(self.d / '项目.json')['targets']
            rows[1]['status'] = 1                                                      # 快手修好了
            rows.append({'id': 4, 'platformName': '抖音', 'platformAccountName': '另一个抖音', 'status': 1})   # 已有平台的新账号不能混进来
            code, out, _ = self.run_cli('accounts', str(self.d), '--fill-missing')
            after = p.read_json(self.d / '项目.json')['targets']
            self.assertEqual(code, 0); self.assertEqual(after[:2], before)             # 已有的两条一个字没动（含用户关掉的抖音）
            self.assertEqual([(t['platform'], t['account_id'], t['enabled']) for t in after[2:]], [('快手', '2', True)])
            self.assertIn('补上了：快手（小王快手）', out)
            self.assertEqual(self.run_cli('accounts', str(self.d), '--fill-missing')[1].count('补上了：没有'), 1)   # 再跑一次什么也不加
            self.assertEqual(p.read_json(self.d / '项目.json')['targets'], after)

    def test_accounts_fill_missing_needs_existing_targets(self):
        with patch.object(p, 'command', side_effect=AssertionError('不该调用')):
            code, _, err = self.run_cli('accounts', str(self.d), '--fill-missing')
        self.assertEqual(code, 1); self.assertIn('直接跑 accounts', err)

    def test_platform_description_overrides_top_level_and_keeps_topics(self):      # 2026-11-21 新20：platform_copy 并进 platform_extra
        self.meta.update(description='通用简介', tags=['装修'], platform_extra={'小红书': {'description': '小红书长简介\n第二段'}}, platform_copy={'抖音': {'description': '没人读'}})
        schema = {'businessFields': {'title': {}, 'description': {}}, 'minimalTemplate': {'publishArgs': {'accountForms': [{'contentPublishForm': {}}]}}}
        xhs = publish_fields.content_form(self.meta, {'platform': '小红书'}, schema, {})['description']
        ks = publish_fields.content_form(self.meta, {'platform': '快手'}, schema, {})['description']
        self.assertIn('小红书长简介<br/>第二段', xhs); self.assertIn('<topic text="装修">', xhs)   # 平台简介生效，话题没被盖掉
        self.assertIn('通用简介', ks); self.assertNotIn('小红书长简介', ks)
        self.meta['platform_extra'] = None                                             # 261117 的项目.json 里就是 null
        self.assertIn('通用简介', publish_fields.content_form(self.meta, {'platform': '小红书'}, schema, {})['description'])
        err = io.StringIO()
        with contextlib.redirect_stderr(err): p.warn_unused_fields(self.meta)
        self.assertIn('platform_copy 程序不认', err.getvalue())

    def test_copy_apply_platform_descriptions_checked_and_written(self):
        import copywriting
        (self.d / '02_制作' / '口播稿.txt').write_text('灯一个项目要去现场跑3次。', encoding='utf-8')
        tags = ['装修', '水电', '网线', '光纤', '排风', '地漏', '防臭', '换气扇', '灯带', '避坑']
        f = self.d / '定稿.json'
        base = {'title': '装灯先试', 'description': '现场跑3次', 'tags': tags}
        p.write_json(f, {**base, 'platform_descriptions': {'B站': '要点', '小红书': '花了5万'}})
        with self.assertRaises(RuntimeError) as e: copywriting.apply(self.d, self.meta, f)
        self.assertIn('B站', str(e.exception)); self.assertIn('小红书 的单独简介里这些数字口播没说过：5', str(e.exception))
        self.assertNotIn('platform_extra', p.read_json(self.d / '项目.json'))          # 没通过就一个字不写
        p.write_json(f, {**base, 'platform_descriptions': {'小红书': '一个项目跑3次现场'}})
        copywriting.apply(self.d, self.meta, f)
        self.assertEqual(p.read_json(self.d / '项目.json')['platform_extra']['小红书']['description'], '一个项目跑3次现场')

    def test_activity_candidates_drop_expired_and_unrelated(self):
        import activities
        from datetime import date
        self.meta.update(tags=['地下室防水'], targets=[{'platform': '快手', 'account_id': 'k1'}])
        acts = [{'yixiaoerName': '装修季征稿', 'yixiaoerDesc': '带话题投稿', 'start_date': '10.26', 'end_date': '11.30'},
                {'yixiaoerName': '老家装修大赛', 'yixiaoerDesc': '', 'start_date': '10.01', 'end_date': '10.20'},      # 已结束
                {'yixiaoerName': '电影二创大赛', 'yixiaoerDesc': '剪电影', 'start_date': '10.26', 'end_date': '11.30'}]  # 无关
        tops = [{'yixiaoerName': '地下室防水', 'viewNum': 10}, {'yixiaoerName': '防水补漏', 'viewNum': 99}]
        def run(args):
            if args[0] != 'yxer-safe' or args[1] != 'query' or 'publish' in args: raise AssertionError(args)
            return {'ok': True, 'data': {'list': acts if args[2] == 'activities' else tops}}
        out = activities.candidates(self.d, self.meta, today=date(2026, 11, 1), run=run)
        row = out['platforms']['快手']
        self.assertEqual([a['name'] for a in row['activities']], ['装修季征稿'])
        self.assertEqual([t['name'] for t in row['topics']], ['防水补漏', '地下室防水'])
        self.assertTrue((self.d / '02_制作/平台活动候选.json').exists())

    def test_copy_draft_and_apply(self):
        import copywriting
        (self.d / '02_制作' / '口播稿.txt').write_text('水电阶段这4件事要一次做到位，超六类网线差价也就几百块，万兆比千兆快10倍。', encoding='utf-8')
        f = copywriting.draft(self.d, self.meta)
        self.assertEqual(p.read_json(f)['口播里的数字'], ['4', '10'])
        tags = ['装修', '水电', '网线', '光纤', '排风', '地漏', '防臭', '换气扇', '灯带', '避坑']
        bad = self.d / '坏.json'; p.write_json(bad, {'title': '预算充足的家', 'description': '差价约500元', 'tags': tags[:5] + ['#网线']})
        with self.assertRaises(RuntimeError) as e:
            copywriting.apply(self.d, self.meta, bad)
        self.assertIn('500', str(e.exception)); self.assertIn('标签 6 个', str(e.exception)); self.assertIn('不带 #', str(e.exception))
        title = '预算充足的家，水电阶段这4件事要一次做到位'          # 21 字：小红书最多 20 字，要单独写（2026-11-12 水电）
        good = self.d / '定稿.json'; p.write_json(good, {'title': title, 'description': '万兆比千兆快10倍', 'tags': tags})
        with self.assertRaises(RuntimeError) as e:
            copywriting.apply(self.d, self.meta, good)
        self.assertIn('小红书最多 20 字', str(e.exception))
        p.write_json(good, {'title': title, 'description': '万兆比千兆快10倍', 'tags': tags, 'platform_titles': {'小红书': '水电这4件事要一次做到位'}})
        copywriting.apply(self.d, self.meta, good)
        meta = p.read_json(self.d / '项目.json')
        self.assertEqual((meta['title'], meta['tags'], meta['platform_extra']['小红书']['title']), (title, tags, '水电这4件事要一次做到位'))

    def test_copy_uses_script_doc_and_writes_back(self):
        # 2026-11-21 用户：“写封面、写标题的时候，咱们在MD文档里都有了，让他去那里找去”；简介、标签 AI 写一版用户过目，确认后写进 MD
        import copywriting, script_doc
        md = self.d / '02_制作' / '测试项目.md'
        md.write_text('# 测试｜口播文案\n\n## 标题与封面\n\n**标题**：装灯前先去现场试一遍灯光效果到底怎么样\n\n**封面文字**：\n\n现场试灯\n\n## 口播正文\n\n一个项目跑3次现场。\n', encoding='utf-8')
        dr = p.read_json(copywriting.draft(self.d, self.meta))
        self.assertEqual(dr['文案里的标题'], '装灯前先去现场试一遍灯光效果到底怎么样'); self.assertIn('没写', dr['文案里的简介']); self.assertIn('不出备选', dr['写法要求']['标题'])
        tags = ['装修', '灯光', '射灯', '筒灯', '现场', '试灯', '别墅', '大平层', '全案', '设计']
        f = self.d / '定稿.json'
        p.write_json(f, {'title': '换了个标题', 'description': '跑3次现场', 'tags': tags, 'platform_titles': {'小红书': '装灯前先去现场试灯'}})
        with self.assertRaisesRegex(RuntimeError, '和项目文案里的「标题」不一样'): copywriting.apply(self.d, self.meta, f)   # MD 写定的标题不许换
        p.write_json(f, {'title': '装灯前先去现场试一遍灯光效果到底怎么样', 'description': '跑3次现场', 'tags': tags, 'platform_titles': {'小红书': '装灯前先去现场试灯'}})
        out = copywriting.apply(self.d, self.meta, f)
        self.assertEqual(out['补进项目文案'], ['简介', '标签', '小红书标题'])
        r = script_doc.read(md)
        self.assertEqual((r['简介'], r['标签'], r['小红书标题'], r['口播正文']), ('跑3次现场', tags, '装灯前先去现场试灯', '一个项目跑3次现场。'))
        self.assertLess(md.read_text(encoding='utf-8').index('## 简介'), md.read_text(encoding='utf-8').index('## 口播正文'))
        self.assertEqual(p.show_script(self.d, '标签'), '、'.join(tags)); self.assertIn('【简介】跑3次现场', p.show_script(self.d))
        p.write_json(f, {'title': '装灯前先去现场试一遍灯光效果到底怎么样', 'description': '别的简介', 'tags': tags, 'platform_titles': {'小红书': '装灯前先去现场试灯'}})
        with self.assertRaisesRegex(RuntimeError, '「简介」不一样'): copywriting.apply(self.d, self.meta, f)           # 写回去以后也按 MD 的来
        self.assertEqual(script_doc.add_sections(md, [('简介', '再写一遍')]), [])                                      # 已有的不动
        self.assertEqual(script_doc.parse('## 标签\n\n#装修 #灯光、筒灯\n')['标签'], ['装修', '灯光', '筒灯'])

    def test_copy_draft_reads_final_script_first(self):
        # 2026-11-20 审查：口播文案已改名为 02_制作/<项目名>.md，copy-draft 还在读 口播稿.txt
        import copywriting
        with self.assertRaisesRegex(RuntimeError, r'03_成片/测试项目\.md'): copywriting.draft(self.d, self.meta)
        (self.d / '02_制作' / '口播稿.txt').write_text('老稿子说了3件事', encoding='utf-8')
        (self.d / '02_制作' / '测试项目.md').write_text('定稿说了5件事', encoding='utf-8')
        self.assertEqual(p.read_json(copywriting.draft(self.d, self.meta))['口播原话'], '定稿说了5件事')
        (self.d / '02_制作' / '测试项目.md').unlink()
        self.assertEqual(p.read_json(copywriting.draft(self.d, self.meta))['口播原话'], '老稿子说了3件事')

    # ---------- 2026-11-13 审查 D1–D7 ----------
    def test_template_folder_index_has_same_fields_as_program_template(self):
        # D1：模板文件夹里的 项目.json 比程序用的 templates/project.json 少 original、youtube_category
        def keys(v, at=''):
            return {at + '/' + k for k in v} | {x for k, c in v.items() for x in keys(c, at + '/' + k)} if isinstance(v, dict) else set()
        used = p.read_json(p.ROOT / 'templates/project.json'); folder = p.read_json(p.ROOT / 'templates/视频项目模板/项目.json')
        self.assertEqual(keys(folder), keys(used))
        self.assertEqual({k: v for k, v in folder.items() if k != 'name'}, {k: v for k, v in used.items() if k != 'name'})
        self.assertEqual((folder['original'], folder['youtube_category'], folder['name']), (True, '26', '视频项目模板'))

    def test_resolve_api_returning_nothing_gives_chinese_reason(self):
        # D4：达芬奇接口返回空、接着用就是 AttributeError/IndexError，以前最外层不接，漏出英文报错栈
        import resolve_archive as ra
        def empty_object(*a, **k): return None.GetTimelineByIndex(1)
        def empty_list(*a, **k): return [][0]
        for cmd, name, boom in (('archive-stills', 'archive_stills', empty_object), ('archive-stills', 'archive_stills', empty_list)):
            err = io.StringIO()
            with patch.object(ra, name, side_effect=boom), patch.object(sys, 'argv', ['project.py', cmd, str(self.d)]), contextlib.redirect_stderr(err):
                self.assertEqual(p.main(), 1)
            self.assertIn('达芬奇接口没返回对象', err.getvalue()); self.assertIn('下一步', err.getvalue()); self.assertNotIn('Traceback', err.getvalue())

    def fake_cli(self, name, body):
        folder = Path(self.temp.name) / '假命令'; folder.mkdir(exist_ok=True)
        f = folder / name; f.write_text('#!' + sys.executable + '\n# -*- coding: utf-8 -*-\nimport json, os, sys, time\n' + body, encoding='utf-8'); f.chmod(0o755)
        return f

    def run_safe(self, args, **env):
        """用假的 yxer（放在 PATH 最前）和假 Key 跑包装器；不读钥匙串、不碰真的 ~/.yxer。"""
        home = Path(self.temp.name) / '假家目录'; home.mkdir(exist_ok=True)
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {'PATH': str(Path(self.temp.name) / '假命令') + os.pathsep + os.environ['PATH'], 'HOME': str(home), **env}), \
                patch.object(secure, 'credential', return_value='fake-key-123'), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = secure.invoke(args)
        return code, out.getvalue(), err.getvalue()

    def test_safe_cli_passes_only_whitelisted_environment(self):
        # D5：以前把环境变量原样传给 yxer；YIXIAOER_API_URL 这类被设置时 Key 可能发往别的地址
        self.fake_cli('yxer', "print(json.dumps({'env': dict(os.environ), 'args': sys.argv[1:], 'config': open(os.environ['YIXIAOER_CONFIG']).read()}))\n")
        code, out, err = self.run_safe(['doctor'], YIXIAOER_API_URL='https://evil.example/api', YXER_DOWNLOAD_BASE_URL='https://evil.example/dl',
                                       https_proxy='http://127.0.0.1:1', NODE_OPTIONS='--require /tmp/x.js', THINKCUT_UNRELATED='x', LC_ALL='zh_CN.UTF-8')
        self.assertEqual(code, 0)
        got = json.loads(out)
        self.assertEqual(got['args'], ['doctor'])
        self.assertEqual([k for k in got['env'] if k.startswith(('YIXIAOER_', 'YXER_'))], ['YIXIAOER_CONFIG'])
        for name in ('https_proxy', 'NODE_OPTIONS', 'THINKCUT_UNRELATED'): self.assertNotIn(name, got['env'])
        self.assertEqual(got['env']['LC_ALL'], 'zh_CN.UTF-8'); self.assertIn('PATH', got['env']); self.assertIn('HOME', got['env'])
        self.assertIn('已忽略', err)
        for name in ('YIXIAOER_API_URL', 'YXER_DOWNLOAD_BASE_URL', 'https_proxy', 'NODE_OPTIONS'): self.assertIn(name, err)
        self.assertNotIn('THINKCUT_UNRELATED', err); self.assertNotIn('evil.example', err)          # 只提示有风险的，值不打印
        self.assertIn('https://www.yixiaoer.cn/api', got['config'])                              # 地址仍是写死的官方地址
        self.assertNotIn('fake-key-123', out); self.assertIn('[REDACTED]', out)
        code, out, err = self.run_safe(['doctor'])
        self.assertEqual((code, err), (0, ''))                                                  # 没有这类变量时不多话

    def test_safe_cli_stops_after_time_limit(self):
        # D5：以前子进程卡住就无限等
        self.fake_cli('yxer', "print('已经输出的一行', flush=True)\ntime.sleep(30)\n")
        for args, name in ((['query', 'records'], 'THINKCUT_YXER_TIMEOUT'), (['upload', '成片.mp4'], 'THINKCUT_YXER_UPLOAD_TIMEOUT')):
            began = time.monotonic()
            code, out, err = self.run_safe(args, **{name: '1'})
            self.assertLess(time.monotonic() - began, 15)
            self.assertEqual(code, 124); self.assertIn('等了 1 秒还没结束，已中止', err); self.assertIn(name, err); self.assertIn('已经输出的一行', out)
        code, out, err = self.run_safe(['draft', 'save', 'x.json'], THINKCUT_YXER_TIMEOUT='1')
        self.assertIn('客户端', err); self.assertIn('不要直接重试', err)                         # 存草稿超时＝结果不明确
        self.assertEqual(secure.time_limit(['doctor'], {})[1], 300); self.assertEqual(secure.time_limit(['upload', 'x'], {})[1], 6 * 3600)
        self.assertEqual(secure.time_limit(['upload', 'x'], {'THINKCUT_YXER_UPLOAD_TIMEOUT': '90'})[1], 90)
        with self.assertRaisesRegex(RuntimeError, '大于 0 的秒数'): secure.time_limit(['doctor'], {'THINKCUT_YXER_TIMEOUT': '很久'})

    def test_command_reports_progress_and_stops_after_time_limit(self):
        # D6：传 2.7GB 时全程没有输出，卡住就无限等
        fake = self.fake_cli('假yxer-safe', "open(sys.argv[3], 'w').write(str(os.getpid()))\ntime.sleep(float(sys.argv[4]))\n"
                             "if sys.argv[1] == 'validate':\n    print('提示：已忽略这些环境变量：X', file=sys.stderr); print(json.dumps({'ok': False, 'error': {'message': '账号失效', 'hint': '重新登录'}}), file=sys.stderr); sys.exit(1)\n"
                             "print('提示：已忽略这些环境变量：X', file=sys.stderr)\nprint(json.dumps({'ok': True, 'data': {'key': 'k'}}))\n")
        pid = Path(self.temp.name) / 'pid'
        out, err = io.StringIO(), io.StringIO()
        with patch.object(p, 'HEARTBEAT', 0.3), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(p.command([fake, 'upload', '/某处/261018_测试项目.mp4', pid, '1.1']), {'ok': True, 'data': {'key': 'k'}})
        self.assertGreaterEqual(out.getvalue().count('还在传 261018_测试项目.mp4，已 '), 2)
        self.assertIn('已忽略', err.getvalue())                                                   # 包装器的提示成功时也带出来
        out = io.StringIO()
        with patch.object(p, 'HEARTBEAT', 0.3), contextlib.redirect_stdout(out):                   # 普通命令不报“还在传”
            p.command([fake, 'doctor', '-', pid, '0.7'])
            with self.assertRaisesRegex(RuntimeError, '账号失效；重新登录'): p.command([fake, 'validate', '-', pid, '0'])   # 提示行不挡住报错里的原因
        self.assertEqual(out.getvalue(), '')
        for args, name in (([fake, 'upload', '/某处/成片.mp4', pid, '30'], 'THINKCUT_YXER_UPLOAD_TIMEOUT'), ([fake, 'doctor', '-', pid, '30'], 'THINKCUT_YXER_TIMEOUT')):
            began = time.monotonic()
            with patch.object(p, 'GRACE', 0), patch.dict(os.environ, {name: '1'}), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, '还没结束，已中止.*' + name): p.command(args)
            self.assertLess(time.monotonic() - began, 15)
            with self.assertRaises(ProcessLookupError): os.kill(int(pid.read_text()), 0)          # 超时后子进程已结束，不留在后台
        with patch.dict(os.environ, {'THINKCUT_YXER_TIMEOUT': '-5'}):
            with self.assertRaisesRegex(RuntimeError, '大于 0 的秒数'): p.command([fake, 'doctor', '-', pid, '0'])

    def test_upload_prints_which_file_how_big_and_how_long(self):
        # D6：开始传哪个文件、多大，传完用时
        cli, calls = self.fixture()
        out = io.StringIO()
        with patch.object(p, 'command', side_effect=cli), contextlib.redirect_stdout(out):
            p.upload(self.d, self.meta, execute=True)
        text = out.getvalue()
        self.assertIn('开始传：261018_测试项目.mp4（0 KB）', text); self.assertIn('传完：261018_测试项目.mp4，用时 ', text)
        self.assertIn('开始传：cover.jpg', text)
        self.assertLess(text.index('传完：261018_测试项目.mp4'), text.index('内部草稿已保存'))
        self.assertEqual((p.size_text(2.7 * 1024 ** 3), p.size_text(5 * 1024 ** 2), p.minutes_text(45), p.minutes_text(600)), ('2.70 GB', '5.0 MB', '45 秒', '10 分钟'))

    def test_collect_records_media_relative_to_archive_folder(self):
        # D7：媒体清单.json 以前记绝对路径
        import resolve_archive as ra
        source = Path(self.temp.name) / '别处/源.mov'; source.parent.mkdir(); source.write_bytes(b'camera bytes')
        out = self.d / '04_达芬奇归档/20261113_120000'; out.mkdir()
        relinked = []
        clip = type('Clip', (), {'GetClipProperty': lambda s, k: str(source)})()
        folder = type('Folder', (), {'GetClipList': lambda s: [clip, clip], 'GetSubFolderList': lambda s: []})()
        pool = type('Pool', (), {'RelinkClips': lambda s, clips, where: relinked.append(where) or True})()
        sources = {}; ra.collect(pool, folder, out, sources)
        sha = p.digest(source)
        self.assertEqual(sources, {str(source): {'target': f'媒体/{sha[:12]}/源.mov', 'sha256': sha}})
        self.assertEqual(relinked, [str(out / '媒体' / sha[:12])] * 2)
        self.assertEqual(delivery.media_target(out, sources[str(source)]['target']), (out / '媒体' / sha[:12] / '源.mov').resolve())

    def test_seal_and_verify_survive_moving_the_project(self):
        # D7：项目文件夹挪位置（或 NAS 挂成 /Volumes/work-1）后，seal 报“收集媒体缺失、越界”，只能整个重新存档
        for kind, relocate in (('新清单_相对路径', shutil.move), ('旧清单_绝对路径', shutil.move), ('旧清单_原处还留着一份', shutil.copytree)):
            with self.subTest(kind):
                with contextlib.redirect_stderr(io.StringIO()):
                    self.d, self.meta = p.initialize(Path(self.temp.name) / kind / '测试项目')
                self.fixture()
                base = self.d / '04_达芬奇归档/20261113_120000'; media = base / '媒体/0123456789ab/源.mov'; media.parent.mkdir(parents=True)
                for name in ('测试项目.drp', '最终时间线.drt', '时间线快照.json'):
                    (base / name).write_bytes(b'archive fixture')
                media.write_bytes(b'collected source')
                target = str(media) if kind.startswith('旧清单') else str(media.relative_to(base))
                p.write_json(base / '媒体清单.json', {'/原来的位置/源.mov': {'target': target, 'sha256': p.digest(media)}})
                self.meta['resolve']['archive'] = '04_达芬奇归档/20261113_120000/测试项目.drp'
                p.write_json(self.d / '项目.json', self.meta)
                first = delivery.seal(self.d, self.meta)
                moved = Path(self.temp.name) / (kind + '_挪到别处') / '测试项目'; moved.parent.mkdir()
                relocate(str(self.d), str(moved))
                d, meta = p.load(moved)
                delivery.verify_package(d, meta)
                self.assertEqual(delivery.seal(d, meta), first)                                   # 同一份内容：不另起新包
                self.assertEqual(len(list((d / '04_达芬奇归档/20261113_120000/交付包').iterdir())), 1)
                self.assertIn('04_达芬奇归档/20261113_120000/媒体/0123456789ab/源.mov', delivery.verify_package(d, p.load(d)[1])['dependencies'])
                (d / '04_达芬奇归档/20261113_120000/媒体/0123456789ab/源.mov').unlink()               # 真缺了仍然要拦
                with self.assertRaisesRegex(RuntimeError, '收集媒体缺失'): delivery.seal(d, dict(meta, title='换个标题就得重新封存'))

    def test_media_manifest_rejects_paths_outside_archive(self):
        base = self.d / '04_达芬奇归档/20261113_120000'; base.mkdir()
        outside = self.d / '03_成片/别的.mov'; outside.write_bytes(b'x')
        self.assertIsNone(delivery.media_target(base, '../../03_成片/别的.mov'))
        self.assertIsNone(delivery.media_target(base, str(outside)))
        self.assertIsNone(delivery.media_target(base, '媒体/没有这个文件.mov'))

    # ---------- 2026-11-20 Q131：项目文案按用途分三类取 ----------
    # 夹具只照真实文案的结构（261114 的“发布信息”、261117/261118 的“标题与封面”、finish 里的老纯口播），内容是编的
    DOC_PUBLISH_INFO = ('# 某某改造｜口播文案\n\n定稿：2026-11-14（用户确认）\n\n## 发布信息\n\n'
                        '标题（所有平台通用）：买房前必看的3个坑\n\n9 个字（含标点），小红书标题上限放得下。\n\n'
                        '封面文字（所有平台通用）：\n\n```text\n买房前\n必看\n3个坑\n```\n\n三行分别是 3、2、3 个字，每行都不超过 6 个字。\n\n---\n\n'
                        '## 口播正文\n\n　　开头先说一句话。今天讲3个坑。\n\n### 第一个坑\n\n　　**墙**是空心的。\n\n---\n\n### 结尾\n\n　　我是设计师小王，我们下期见。\n')
    DOC_TITLE_COVER = ('# 261117 某某｜口播文案\n\n来源：Claude 文档\n\n## 标题与封面\n\n**标题**：灯装哪才够亮\n\n**封面文字**：\n\n'
                       '7米挑空\n\n灯装哪？\n\n## 口播正文\n\n做灯光，我们从来不出模拟图。\n\n一个项目跑三到五次。\n')
    DOC_OLD = ('# 261015 老房反味｜口播文案\n\n> 2026-11-17 补存：内容原样取自成片字幕，没有改写。\n\n'
               '新装好房子一天没住，\n1. **先止水。** 漏的先堵上。\n我是设计师小王。\n')

    def test_script_doc_publish_info_layout(self):
        import script_doc
        r = script_doc.parse(self.DOC_PUBLISH_INFO)
        self.assertEqual(r['标题'], '买房前必看的3个坑')
        self.assertEqual(r['封面文字'], ['买房前', '必看', '3个坑'])           # 代码块外的解释句不混进来
        body = r['口播正文']
        self.assertEqual(body, '开头先说一句话。今天讲3个坑。\n墙是空心的。\n我是设计师小王，我们下期见。')
        for word in ('第一个坑', '结尾', '#', '**', '---', '　', '小红书', '标题'): self.assertNotIn(word, body)

    def test_script_doc_title_and_cover_layout(self):
        import script_doc
        r = script_doc.parse(self.DOC_TITLE_COVER)
        self.assertEqual((r['标题'], r['封面文字']), ('灯装哪才够亮', ['7米挑空', '灯装哪？']))
        self.assertEqual(r['口播正文'], '做灯光，我们从来不出模拟图。\n一个项目跑三到五次。')

    def test_script_doc_standard_layout_skips_explanations(self):
        import script_doc
        r = script_doc.parse('## 标题\n\n装灯前先到现场试\n\n这个标题 8 个字，放得下。\n\n## 封面文字\n\n现场试灯\n才知道亮不亮\n\n两行都不超过 6 个字。\n\n'
                             '## 口播正文\n\n### 开头\n今天说灯。\n\n## 备注\n剪辑时注意别删这句。\n')
        self.assertEqual({k: r[k] for k in script_doc.PARTS}, {'标题': '装灯前先到现场试', '封面文字': ['现场试灯', '才知道亮不亮'], '口播正文': '今天说灯。'})

    def test_script_doc_old_plain_script_is_all_spoken(self):
        import script_doc
        r = script_doc.parse(self.DOC_OLD)
        self.assertEqual({k: r[k] for k in script_doc.PARTS}, {'标题': None, '封面文字': None, '口播正文': '新装好房子一天没住，\n先止水。 漏的先堵上。\n我是设计师小王。'})

    def test_script_doc_missing_spoken_part_is_not_guessed(self):
        import script_doc
        r = script_doc.parse('# 某某\n\n## 标题与封面\n\n**标题**：只有标题\n\n## 拍摄提纲\n\n先拍厨房，再拍卫生间。\n')
        self.assertEqual({k: r[k] for k in script_doc.PARTS}, {'标题': '只有标题', '封面文字': None, '口播正文': None})

    def test_script_command_prints_parts_and_stops_on_missing(self):
        doc = p.script_path(self.d)
        with self.assertRaisesRegex(RuntimeError, r'缺项目文案 03_成片/测试项目\.md'): p.show_script(self.d)
        doc.write_text(self.DOC_PUBLISH_INFO, encoding='utf-8')
        self.assertEqual(p.show_script(self.d, '封面'), '买房前\n必看\n3个坑')
        self.assertEqual(p.show_script(self.d, '标题'), '买房前必看的3个坑')
        self.assertTrue(p.show_script(self.d, '口播').startswith('开头先说一句话。'))
        doc.write_text('# 某某\n\n## 标题\n\n只有标题\n', encoding='utf-8')
        out = p.show_script(self.d)
        self.assertIn('【标题】\n只有标题', out); self.assertIn('【封面文字】缺', out); self.assertIn('【口播正文】缺：在文案里口播开始的地方加「## 口播正文」小标题', out)
        with self.assertRaisesRegex(RuntimeError, '认不出「口播正文」'): p.show_script(self.d, '口播')
        cli = subprocess.run([sys.executable, '-B', str(Path(p.__file__)), 'script', str(self.d), '--part', '标题'], capture_output=True, text=True,
                             env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        self.assertEqual((cli.returncode, cli.stdout.strip()), (0, '只有标题'))

    def test_copy_draft_uses_only_spoken_part_and_lists_cover_sources(self):
        import copywriting
        p.script_path(self.d).write_text(self.DOC_PUBLISH_INFO, encoding='utf-8')
        out = p.read_json(copywriting.draft(self.d, self.meta))
        self.assertEqual(out['口播里的数字'], ['3'])                      # 解释句里的 9、2、6 不算口播说过
        self.assertNotIn('小红书', out['口播原话'])
        self.assertEqual(out['文案里的标题'], '买房前必看的3个坑')
        self.assertEqual(out['封面文字']['行'], ['买房前', '必看', '3个坑'])    # 项目.json 没填：用文案的
        meta = dict(self.meta, cover_main='买房前必看', cover_sub='3个坑')
        self.assertEqual(p.read_json(copywriting.draft(self.d, meta))['封面文字'], {'cover_main': '买房前必看', 'cover_sub': '3个坑'})   # 两边一样
        meta = dict(self.meta, cover_main='别的封面', cover_sub='')
        both = p.read_json(copywriting.draft(self.d, meta))['封面文字']      # 两边不一样：都列出，不自动选
        self.assertEqual(both['项目.json']['cover_main'], '别的封面')
        self.assertEqual(both['项目文案 03_成片/测试项目.md'], ['买房前', '必看', '3个坑'])
        self.assertIn('注意', both)
        self.assertEqual(p.read_json(self.d / '项目.json').get('cover_main', ''), self.meta.get('cover_main', ''))   # 不改项目.json
        p.script_path(self.d).write_text('# 某某\n\n## 标题\n\n只有标题\n', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '## 口播正文'): copywriting.draft(self.d, self.meta)


    # —— 2026-11-22 用户定：定稿文案放 03_成片/<项目名>.md（AI 找标题、封面文字先来这里）——
    def test_script_lives_in_finished_folder_and_old_place_still_read(self):
        d = self.d; new = d / '03_成片' / '测试项目.md'; old = d / '02_制作' / '测试项目.md'
        self.assertEqual(p.script_path(d), new)                      # 新项目：03_成片
        old.parent.mkdir(parents=True, exist_ok=True); old.write_text(self.DOC_PUBLISH_INFO, encoding='utf-8')
        self.assertEqual(p.script_path(d), old)                      # 还没搬的老项目：照读 02_制作 那份
        self.assertEqual(p.show_script(d, '标题'), '买房前必看的3个坑')
        new.parent.mkdir(parents=True, exist_ok=True); new.write_text('# 测试项目\n\n## 标题\n\n新位置的标题\n', encoding='utf-8')
        self.assertEqual(p.script_path(d), new)                      # 两处都有：以 03_成片 为准
        self.assertEqual(p.show_script(d, '标题'), '新位置的标题')

    def test_script_move_fills_old_plain_script_from_project_json(self):
        import script_doc
        d = self.d; old = d / '02_制作' / '测试项目.md'; old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text('# 测试项目｜口播文案\n\n新装好房子一天没住，\n先止水。\n\n### 第二段\n\n漏的地方在墙角。\n', encoding='utf-8')
        spoken = script_doc.read(old)['口播正文']
        meta = dict(self.meta, title='买房前必看的3个坑', cover_main='买房前\n必看', cover_sub='3个坑', description='一段简介', tags=['装修', '避坑'])
        rc = Path(self.temp.name) / '回收站'
        out = p.move_script(d, meta, recycle=rc)
        new = d / '03_成片' / '测试项目.md'; r = script_doc.read(new)
        self.assertEqual(out['结果'], '已搬到 03_成片'); self.assertFalse(old.exists()); self.assertTrue((rc / '测试项目.md').exists())
        self.assertEqual(r['口播正文'], spoken)                       # 口播一字不差
        self.assertEqual((r['标题'], r['封面文字'], r['简介'], r['标签']), ('买房前必看的3个坑', ['买房前', '必看', '3个坑'], '一段简介', ['装修', '避坑']))
        self.assertTrue(new.read_text(encoding='utf-8').startswith('# 测试项目｜口播文案\n'))
        self.assertEqual(p.move_script(d, meta, recycle=rc)['结果'], '已在 03_成片')      # 再跑不重复

    def test_script_move_keeps_new_style_doc_and_only_adds_missing(self):
        import script_doc
        d = self.d; old = d / '02_制作' / '测试项目.md'; old.parent.mkdir(parents=True, exist_ok=True)
        old.write_text(self.DOC_PUBLISH_INFO, encoding='utf-8'); before = script_doc.read(old)
        meta = dict(self.meta, title='项目里另一个标题', description='一段简介', tags=['装修'])
        out = p.move_script(d, meta, recycle=Path(self.temp.name) / '回收站'); r = script_doc.read(d / '03_成片' / '测试项目.md')
        self.assertEqual((r['标题'], r['封面文字'], r['口播正文']), (before['标题'], before['封面文字'], before['口播正文']))   # 文案里写定的不动
        self.assertEqual((r['简介'], r['标签']), ('一段简介', ['装修'])); self.assertEqual(out['补上的小节'], ['简介', '标签'])

    def test_script_move_stops_when_nothing_to_move_or_check_fails(self):
        d = self.d
        self.assertEqual(p.move_script(d, self.meta)['结果'], '没有项目文案，没搬')
        self.assertFalse((d / '03_成片' / '测试项目.md').exists())

    def test_rename_renames_script_in_its_own_folder(self):
        self.assertIn("renamed_to = script.with_name(new_name + '.md')", Path(p.__file__).read_text(encoding='utf-8'))


    # —— 261122 反馈单（2026-11-22，用户：“你看着办吧，看着办提交给他就行了”）——
    def test_rename_ignores_script_doc_in_finished_folder(self):
        # 新1：03_成片 里只有定稿文案（和空的 字幕/封面 文件夹）不算“已有成片”；放进视频或登记了成片照样拦
        import resolve_library
        root = Path(self.temp.name).resolve() / '根2'; d, _ = p.initialize(root / '261122 灯光模拟软件')
        (d / '03_成片' / '261122 灯光模拟软件.md').write_text('# 261122\n\n## 标题\n\n别墅要不要单独请灯光设计师\n', encoding='utf-8')
        with patch.object(resolve_library, 'resolve_running', return_value=False):
            new = root / '261122 别墅要不要单独请灯光设计师'
            p.rename(d, new.name)                                       # 不用 --user-ok
            self.assertTrue((new / '03_成片' / f'{new.name}.md').exists())   # 文案跟着改名，还在 03_成片
            (new / '03_成片' / '261122_成片.mp4').write_bytes(b'x')
            with self.assertRaisesRegex(RuntimeError, '已有成片.*先问用户'): p.rename(new, '261122 再改')

    def test_name_must_match_title(self):
        # 新3：项目名＝6 位编号＋空格＋发布标题，不一样就提醒（check、upload、archive 前）
        m = dict(name='261122 灯光模拟软件为什么替代不了现场', title='别墅、大平层要不要单独请灯光设计师')
        w = p.name_title_warning(m)
        self.assertIn('和发布标题「别墅、大平层要不要单独请灯光设计师」不一样', w); self.assertIn('rename', w)
        self.assertIsNone(p.name_title_warning(dict(name='261122 别墅、大平层要不要单独请灯光设计师', title='别墅、大平层要不要单独请灯光设计师')))
        self.assertIsNone(p.name_title_warning(dict(name='261122 随便', title='')))     # 标题还没定不提醒
        code, _, err = self.run_cli('check', str(self.d))
        self.assertEqual(code, 0)

    def test_no_more_claude_doc_sync_reminder(self):
        # 2026-11-24 用户定：“可以，只留文案栏”——文案只在审片台“文案”栏写，上传、存档前不再提醒“和 Claude 文档对一遍”（取代 261122 反馈单新2）
        src = Path(p.__file__).read_text(encoding='utf-8')
        self.assertFalse(hasattr(p, 'doc_sync_reminder')); self.assertNotIn('先对一遍', src); self.assertEqual(src.count('name_title_warning(meta),)'), 2)   # 项目名和标题不一样的提醒照旧


class SetSectionTests(unittest.TestCase):
    """审片台改字（2026-11-22）：script_doc.set_section 只换对应小节，其余一字不动；改不了的写法说清楚、不动文件。"""
    SRC = "# 片名\n\n> 说明\n\n## 标题\n\n旧标题\n\n## 封面文字\n\n```text\n第一行\n第二行\n```\n\n## 口播正文\n\n### 开头\n第一段。\n> 备注\n第二段。\n"

    def setUp(self):
        import script_doc
        self.sd = script_doc; self.tmp = Path(tempfile.mkdtemp()); self.md = self.tmp / 'a.md'; self.md.write_text(self.SRC, encoding='utf-8')

    def tearDown(self): shutil.rmtree(self.tmp, ignore_errors=True)

    def test_only_that_section_changes(self):
        self.assertEqual(self.sd.set_section(self.md, '标题', '新标题'), '新标题')
        self.assertEqual(self.md.read_text(encoding='utf-8'), self.SRC.replace('旧标题', '新标题'))          # 其余一字不动
        self.sd.set_section(self.md, '封面文字', ['甲', '乙'])
        t = self.md.read_text(encoding='utf-8'); a, b = self.SRC.split('## 封面文字')[0], self.SRC.split('## 口播正文')[1]
        self.assertTrue(t.startswith(a.replace('旧标题', '新标题')) and t.endswith('## 口播正文' + b))
        self.assertEqual(self.sd.read(self.md)['封面文字'], ['甲', '乙'])
        self.sd.set_section(self.md, '口播正文', '### 开头\n改过的第一段。\n> 备注\n第二段。')
        r = self.sd.read(self.md); self.assertEqual((r['标题'], r['口播正文']), ('新标题', '改过的第一段。\n第二段。'))
        self.assertEqual(self.sd.section_body(self.md, '口播正文'), '### 开头\n改过的第一段。\n> 备注\n第二段。')
        self.sd.set_section(self.md, '标签', ['灯光', '筒灯'])                                                          # 没有的小节加在口播正文前面
        t = self.md.read_text(encoding='utf-8'); self.assertLess(t.index('## 标签'), t.index('## 口播正文')); self.assertEqual(self.sd.read(self.md)['标签'], ['灯光', '筒灯'])

    def test_refuses_old_layouts(self):
        old = '第一段口播。\n第二段。\n'; self.md.write_text(old, encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '老写法'): self.sd.set_section(self.md, '标题', 'x')
        self.assertEqual(self.md.read_text(encoding='utf-8'), old)
        lab = '## 发布信息\n\n标题：老标题\n\n## 口播正文\n\n口播。\n'; self.md.write_text(lab, encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, '别的小节'): self.sd.set_section(self.md, '标题', 'x')
        self.assertEqual(self.md.read_text(encoding='utf-8'), lab)
        with self.assertRaises(ValueError): self.sd.set_section(self.md, '别的', 'x')


if __name__ == '__main__':
    unittest.main()
