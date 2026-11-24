"""19B feedback regressions; all files and SDK state are disposable fakes."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tempfile import TemporaryDirectory
import unittest
from engine.broll.live.workspace import infer_work_dir, work_project_dir


class WorkspaceFeedbackTests(unittest.TestCase):
    def thinkcut(self, root):
        (root/'01_原始素材/A roll').mkdir(parents=True); (root/'项目.json').write_text('{}')

    def test_thinkcut_project_uses_02_and_old_work_wins(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp); self.thinkcut(root); work = root/'02_制作'/'B-roll_AI'
            for media_path in (root/'01_原始素材/A roll/口播.mov', root/'02_制作/口播转码/口播.mov'):
                model = {'items': [{'type':'video', 'track':1, 'media_path':str(media_path)}]}
                for explicit in (None, root):
                    self.assertEqual(infer_work_dir(model, explicit), work)
            self.assertEqual(work_project_dir(work), root)
            self.assertFalse(work.exists())
            old = root/'B-roll_AI'; old.mkdir()
            self.assertEqual(infer_work_dir(model, root), old)
            self.assertEqual(work_project_dir(old), root)

    def test_missing_ai_folder_keeps_old_default(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(infer_work_dir({}, tmp), Path(tmp)/'B-roll_AI')


class CoverageFeedbackTests(unittest.TestCase):
    def test_live_profile_overrides_default_without_thirty_percent_stop(self):
        from engine.broll.live.test_arrange import catalog, matches
        from engine.broll.live._fixtures import snapshot
        from engine.broll.live.timeline import build_model
        from engine.broll.live.arrange import arrange
        model=build_model(snapshot())
        value=matches(*[(n,n,[str(n-1)]) for n in range(1,7)])
        cat=catalog()
        self.assertEqual(arrange(value,cat,model)['coverage'],1)
        for group in ('rules','parameters'):
            profile={group:{'max_coverage':.95}}
            result=arrange(value,cat,model,profile)
            self.assertGreater(result['coverage'],.3)
            self.assertLessEqual(result['coverage'],.95)
        result=arrange(value,cat,model,{'rules':{'max_coverage':.3}})
        self.assertLessEqual(result['coverage'],.3)
        self.assertTrue(any('覆盖上限' in p['reason'] for p in result['placements']))

    def test_coverage_limit_reads_rules_and_default_profile_keeps_the_config_value(self):
        from engine.broll.live.arrange import default_profile
        from engine.broll.coverage import coverage_limit
        self.assertEqual(coverage_limit({'rules':{'max_coverage':.95}}),.95)
        self.assertEqual(coverage_limit(default_profile('示例IP')),1)
        self.assertEqual(coverage_limit({}, {'max_coverage':.4}),.4)
        for bad in (-1,1.1,True,float('nan')):
            with self.assertRaises(ValueError):coverage_limit({'rules':{'max_coverage':bad}})


class SkillFeedbackTests(unittest.TestCase):
    def test_guidance_keeps_story_evidence_and_tool_limits_explicit(self):
        root=Path(__file__).resolve().parents[3]
        guide=(root/'skills/b-roll/references/看图与匹配.md').read_text()
        for phrase in ('这段要让观众看到/相信什么事','账号主页→相机→实际拍摄',
                       '截图、录屏','画面里的文字、操作和结果','工作或使用场景里的本人',
                       '旁边叠真实证据','约0.7秒','不能按某个覆盖率强铺','2–3个实际候选',
                       '先试剪一小段','扩大范围时先确认','超出自动排布能力'):
            self.assertIn(phrase,guide)
        skill=(root/'skills/b-roll/SKILL.md').read_text()
        self.assertNotIn('现有 `apply/adjust` 若仍生成替代片段',skill)
        self.assertIn('转换原因',skill)


class CompositeFeedbackTests(unittest.TestCase):
    def test_unresolved_fusion_does_not_create_deletion_preference(self):
        from copy import deepcopy
        from engine.broll.live._fixtures import with_ai, item
        from engine.broll.live.reconcile import diff_snapshots
        before,book=with_ai(1)
        after=deepcopy(before)
        after['items']=[r for r in after['items'] if r['id']!=book['spots']['0']['placement']['id']]
        clip=item('Fusion Clip 2',0,100)
        clip.update(media_type='Fusion Clip',media_path=None)
        after['items'].append(clip)
        delta=diff_snapshots(before,after,book)
        self.assertTrue(delta['update_allowed'])
        self.assertEqual(delta['actions'],[])
        self.assertEqual(delta['skipped'][0]['name'],'Fusion Clip 2')
        self.assertEqual(delta['ledger']['reserved'],book['reserved'])

    def test_nonlinear_nested_source_is_skipped_instead_of_inventing_frames(self):
        from engine.broll.live._fixtures import snapshot, item
        from engine.broll.live.timeline import build_model
        snap=snapshot()
        child=item('inner',0,300,track=1,source_start=0)
        child['source_end']=600
        clip=item('Compound Clip 2',300,400,source_start=0)
        clip.update(media_type='Compound',media_path=None,nested={
            'start':0,'fps':29.97,'items':[child]})
        snap['items'].append(clip)
        model=build_model(snap)
        self.assertIn('不能可靠映射',model['learning_skipped'][0]['reason'])
        self.assertEqual(model['learning_skipped'][0]['sources'][0]['file'],child['media_path'])
