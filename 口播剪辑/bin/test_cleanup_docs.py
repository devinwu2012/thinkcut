# -*- coding: utf-8 -*-
"""2026-11-21 体检第一部分的清理和说明对齐（台账 Q204、Q205、Q196）：
没人用的 bin/lock_test.py 已下线且没有地方还在引用；三句和现行规矩矛盾的说明已改掉，不再改回去。"""
import subprocess, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(name): return (ROOT / name).read_text(encoding='utf-8')


class CleanupTests(unittest.TestCase):
    def test_lock_test_script_is_gone_and_unreferenced(self):
        self.assertFalse((ROOT / 'bin/lock_test.py').exists())
        tracked = subprocess.run(['git', '-c', 'core.quotepath=off', 'ls-files'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split('\n')
        me = Path(__file__).name
        users = [f for f in tracked if f and f != 'CHANGELOG.md' and not f.endswith(me) and f.rsplit('.', 1)[-1] in ('py', 'sh', 'md', 'cjs', 'js', 'json')
                 and (ROOT / f).is_file() and 'lock_test' in (ROOT / f).read_text(encoding='utf-8', errors='replace')]
        self.assertEqual(users, [])        # 更新记录里的历史记载不算


class DocsAgreeWithRulesTests(unittest.TestCase):
    def test_fusion_is_allowed_everywhere(self):
        # AGENTS.md：可以使用 Fusion（2026-10-23 用户定，取代 2026-10-17 的禁止规定）
        self.assertIn('可以使用 Fusion', read('AGENTS.md'))
        notes = read('docs/达芬奇接口实测.md')
        self.assertNotIn('默认禁止使用 Fusion', notes)
        self.assertIn('可以使用 Fusion（2026-10-23 用户定', notes)

    def test_readme_says_where_project_files_go(self):
        # AGENTS.md：标准项目放 <项目根>/02_制作/ 下；不是标准项目时才放原片旁
        intro = read('README.md').split('\n')[2]
        self.assertIn('02_制作', intro)
        self.assertNotIn('每条视频的文件留在原片旁的', intro)

    def test_delivery_route_is_described_as_it_is(self):
        # AGENTS.md：口播交付经 MCP；B-roll 与花字用 DaVinciResolveScript 直连
        self.assertIn('口播交付经 MCP', read('AGENTS.md'))
        line = next(l for l in read('docs/达芬奇接口实测.md').split('\n') if '`_archived` 副本时间线' in l)
        self.assertIn('B-roll 与花字全部用直连写入', line)
        self.assertIn('口播交付仍经 MCP', line)

    def test_resize_setting_key_is_recorded(self):
        # 261118 反馈单新13（Q196）：imageResizeMode 不是时间线设置的键名，正确的键和现有程序用的一致
        line = next(l for l in read('docs/达芬奇接口实测.md').split('\n') if 'imageResizeMode' in l)
        self.assertIn('timelineInputResMismatchBehavior', line)
        self.assertIn("'timelineInputResMismatchBehavior'", read('engine/fx/review.py'))


class SlimDocsTests(unittest.TestCase):
    """2026-11-21 说明瘦身：重复的规矩只留一处，讲已下线东西的段落搬到 docs/历史做法.md，现行规矩一条不少。"""
    PROTECTION = ('- 适用于所有工程与所有制作 Skill', '- 每次写入前以当前工程为准，对照上次 AI 写入记录辨认手改', '- 手工参考只供只读学习',
                  '- 只有用户明确点名该对象并要求再次修改时', '- 工具若不能排除受保护对象')

    def skill_docs(self):
        return sorted(p for p in (ROOT / 'skills').rglob('*.md'))

    def test_hand_edit_protection_full_text_lives_only_in_agents(self):
        agents = read('AGENTS.md')
        self.assertIn('## 用户手工定稿保护（所有工程、所有 Skill，2026-10-17）', agents)
        for line in self.PROTECTION:
            self.assertIn(line, agents)
            for doc in self.skill_docs():
                self.assertNotIn(line, doc.read_text(encoding='utf-8'), doc.name)
        # 各分项开头都留一句指路
        self.assertIn('见根目录 AGENTS.md「用户手工定稿保护」', read('skills/b-roll/SKILL.md'))
        self.assertIn('`口播剪辑/AGENTS.md`（硬性边界和「用户手工定稿保护」全文）', read('skills/a-roll/SKILL.md'))
        self.assertIn('`口播剪辑/AGENTS.md`「用户手工定稿保护」全文照做', read('skills/fx/SKILL.md'))

    def test_each_part_keeps_its_own_way_of_recognising_hand_edits(self):
        broll = read('skills/b-roll/references/写入与核验.md')
        for phrase in ('## 写入前对账', '## 只动自己的片段', '任何差异都视为她动过，拒绝删除或覆盖', '记成保留位，之后不自动补回',
                       '不能把对账后的新状态当作已授权删除基线', '只有名字像或只有路径像的认不准', '身份不确定超过 20%'):
            self.assertIn(phrase, broll, phrase)
        fx = read('skills/fx/references/写入与交付.md')
        for phrase in ('用户手改的对象整项退出自动重建', 'kept_manual_titles', '用户移动、调音量、换文件或删除的音效不重放'):
            self.assertIn(phrase, fx, phrase)
        self.assertIn('不能用旧审核状态或新映射重新交付覆盖', read('skills/a-roll/references/editing-workflow.md'))

    def test_retired_things_are_only_described_in_history(self):
        history = read('docs/历史做法.md')
        for phrase in ('旧版 B-roll（2026-11-21 下线）', 'B-roll 学习（2026-11-21 下线）', '花字学习：定稿入库到花字案例库（2026-11-21 下线）',
                       'broll-index', 'learn --forget last', 'bin/fx.py learn', 'allow_postgres_apply', '没有下线的（操作台账）'):
            self.assertIn(phrase, history, phrase)
        self.assertFalse((ROOT / 'skills/b-roll/references/学习.md').exists())
        self.assertFalse((ROOT / 'engine/broll/live/T3接口.md').exists())
        retired = ('learn', 'broll.py profile', '`profile`', '案例库/', '定稿入库', 'learning_root', 'allow_postgres_apply', '学习.md', 'broll-index', 'broll-plan', '学习功能')
        for doc in self.skill_docs() + [ROOT / 'README.md', ROOT / 'AGENTS.md', ROOT.parent / '内部/经验/口播剪辑旧经验/README.md', ROOT / 'engine/broll/live/README.md']:
            if not doc.exists(): continue      # 内部/ 开源版不带，没有就跳过
            text = doc.read_text(encoding='utf-8')
            for word in retired:
                self.assertNotIn(word, text, doc.name + ' ' + word)
        # 历史做法不进任何 Skill 的必读清单
        for doc in self.skill_docs():
            self.assertNotIn('历史做法', doc.read_text(encoding='utf-8'), doc.name)

    def test_skill_links_still_point_at_existing_files(self):
        import re
        for doc in self.skill_docs():
            for target in re.findall(r'\]\(([^)#]+\.(?:md|jpg))\)', doc.read_text(encoding='utf-8')):
                if '://' in target: continue
                self.assertTrue((doc.parent / target).exists(), doc.name + ' → ' + target)


if __name__ == '__main__':
    unittest.main()
