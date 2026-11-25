"""Guard the explicitly requested workflow rules without connecting to Resolve."""
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[2]
def skill_text():   # 2026-11-13 说明瘦身：花字说明拆成 SKILL.md＋计划与安全.md＋写入与交付.md，规则在三份里找
    return '\n'.join((ROOT/'skills/fx'/n).read_text() for n in ('SKILL.md','references/计划与安全.md','references/写入与交付.md'))


class RuleTests(unittest.TestCase):
    def test_skill_preserves_native_titles_and_fusion_boundary(self):
        text=skill_text()
        for token in ('## 制作方式','TEMPLATE_ID','默认静态','独立复合片段','不在原片上直接加',
                      'FlowView','只给要跟随物体的标签做跟踪','超过 10 分钟','不导图、不关闭重开','--mode small'):
            self.assertIn(token,text)
    def test_track_output_and_current_marker_rules(self):
        text=skill_text()
        for token in ('最低的一两条可用轨','A3','A2','track','02_制作/花字音效','不搬家','全部标记的名称和备注','没写入的不得称完成'):
            self.assertIn(token,text)
        self.assertNotIn('放在主线最上方「花字」',text)
        self.assertNotIn('成功后默认创建',text)
    def test_styleguide_is_thinkcut_only(self):
        text=(ROOT/'skills/fx/references/风格手册.md').read_text()
        for token in ('花字风格手册','跟随标签','PingFang SC','柔和投影','brand_label_dark','画面太花','76px','render_labels.py','跟着那句口播的长度走','没有用户要求，不加口播花字','brand_label','已在达芬奇实测校准','定稿案例索引','Text+花字_包装系统对比.jpg'):
            self.assertIn(token,text)

class SupplementRules(unittest.TestCase):
    def test_d_rules_are_in_skill_and_styleguide_without_overriding_user_sync_rule(self):
        skill=skill_text()
        for token in ('真实波形的目标瞬态对齐文字实际出现帧','最终交付时，AI 新建的过程、测试、备份时间线先导出',
                      'speech.mode="visual"','估算','list_group','连续状态','cleanup','--apply','不按名称猜'):
            self.assertIn(token,skill)
        audio=(ROOT/'skills/fx/references/音效.md').read_text()
        self.assertNotIn('有效波形默认先于花字1帧',audio)
        self.assertIn('sync_frame',audio);self.assertIn('不超过 -5 dBFS',audio)

    def test_cleanup_and_prepare_cli_help_are_offline(self):
        import subprocess
        for args in (['cleanup','--help'],['plan','--help']):
            r=subprocess.run(['python3',str(ROOT/'bin/fx.py'),*args],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
            self.assertIn('--apply' if args[0]=='cleanup' else '--prepare-out',r.stdout)

if __name__=='__main__':unittest.main()

