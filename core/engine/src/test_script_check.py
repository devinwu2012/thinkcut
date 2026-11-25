import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import script_check


def tokens(text, unit=0):
    return [{'id': f'u{unit}c{i}', 'unit_id': unit, 'text': c} for i, c in enumerate(text)]


class ScriptCheckTests(unittest.TestCase):
    def test_homophone_is_a_candidate_and_unspoken_sentence_is_reported(self):
        result = script_check.compare(tokens('通常都是支出拍完就能看'), '座位离舞台特别远。通常都是直出，拍完就能看。')
        self.assertEqual([(c['token_ids'], c['spoken'], c['script']) for c in result['candidates']],
                         [(['u0c4'], ['支'], '直')])
        self.assertEqual(result['possibly_missing'], ['座位离舞台特别远'])

    def test_long_mismatch_is_not_a_typo_candidate(self):
        result = script_check.compare(tokens('今天我们来聊一聊这台手机的拍照能力怎么样'), '今天先说结论：它的长焦和人像都比我预想得更好用。')
        self.assertTrue(all(len(c['token_ids']) <= 8 for c in result['candidates']))

    def test_matching_script_has_no_candidates(self):
        result = script_check.compare(tokens('拍完就能看'), '拍完就能看。')
        self.assertEqual(result, {'candidates': [], 'possibly_missing': []})

    def apply(self, rows, state=None):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run/'review_v2_state.json').write_text(json.dumps(state or {}))
            (run/'rows.json').write_text(json.dumps(rows, ensure_ascii=False))
            with patch.object(script_check, 'RUN', run), patch.object(script_check, 'STATE', run/'review_v2_state.json'):
                script_check.apply(run/'rows.json', {'tokens': tokens('就是支出')})
            return json.loads((run/'review_v2_state.json').read_text())

    def test_valid_correction_is_merged_into_state(self):
        state = self.apply([{'id': 'u0c2', 'before': '支', 'after': '直', 'reason': '同音'}],
                           {'subtitle_token_corrections': [{'id': 'u0c0', 'before': '就', 'after': '就', 'reason': '旧'}]})
        self.assertEqual([r['id'] for r in state['subtitle_token_corrections']], ['u0c0', 'u0c2'])

    def test_correction_bound_to_changed_word_is_rejected(self):
        for row in [{'id': 'u0c2', 'before': '只', 'after': '直', 'reason': '同音'},
                    {'id': 'u0c9', 'before': '支', 'after': '直', 'reason': '同音'},
                    {'id': 'u0c2', 'before': '支', 'after': '', 'reason': '同音'},
                    {'id': 'u0c2', 'before': '支', 'after': '直', 'reason': ' '}]:
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.apply([row])


    def test_range_correction_validation_merge_and_removal(self):
        aligned={'tokens':[{'id':'a','unit_id':0,'text':'Mac'}, {'id':'b','unit_id':0,'text':'Studio'}, {'id':'c','unit_id':0,'text':'好'}]}
        row={'ids':['a','b'],'before':'Mac Studio','after':'Mac Studio','reason':'英文整体'}
        clean=script_check.validate_rows([row],aligned)
        self.assertEqual(clean[0]['id'],'a')
        state=script_check.merge({'subtitle_token_corrections':[{'id':'b','before':'Studio','after':'tudio'}]},clean)
        self.assertEqual(state['subtitle_token_corrections'],clean)
        self.assertEqual(script_check.merge(state,[{'id':'b','remove':True}])['subtitle_token_corrections'],[])
        for ids in (['b','a'],['a','c'],['a','a']):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                script_check.validate_rows([{**row,'ids':ids}],aligned)
        aligned['tokens'][1]['unit_id']=1
        with self.assertRaises(ValueError):script_check.validate_rows([row],aligned)

    def test_editing_one_word_inside_a_range_correction_is_refused_not_dropped(self):
        whole={'id':'a','ids':['a','b'],'before':'Mac Studio','after':'Mac Studio','reason':'英文整体'}
        state={'subtitle_token_corrections':[whole,{'id':'c','before':'好','after':'号','reason':'旧'}]}
        for row in ({'id':'b','before':'Studio','after':'studio','reason':'网页手动修改字幕'},
                    {'id':'b','ids':['b','c'],'before':'Studio好','after':'Studio 号','reason':'跨到范围外'}):
            with self.subTest(row=row), self.assertRaisesRegex(ValueError,'属于一段整体纠错「Mac Studio」.*a、b.*先撤销整段'):
                script_check.merge(state,[row])
        self.assertEqual(state['subtitle_token_corrections'][0],whole)
        rewritten={**whole,'after':'MacStudio'}
        self.assertEqual(script_check.merge(state,[rewritten])['subtitle_token_corrections'][-1],rewritten)
        single={'id':'b','before':'Studio','after':'studio','reason':'先撤销再单改'}
        merged=script_check.merge(state,[{'id':'a','ids':['a','b'],'remove':True},single])['subtitle_token_corrections']
        self.assertEqual([r['id'] for r in merged],['c','b'])

    def test_partial_range_is_not_deliverable(self):
        from review_build import proofread_problem
        state={'subtitle_proofread':{'token_ids':['a','b']},'subtitle_token_corrections':[{'id':'a','ids':['a','b']}]}
        self.assertIn('只剩部分',proofread_problem(state,['a'],[]))

    def test_parse_glossary_comments_blank_lines_and_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'词库.txt'
            path.write_text('  # 注释\n\n 吉布利 | 吉布力 => 吉博力 \n Oatey 通气阀 \n', encoding='utf-8')
            self.assertEqual(script_check.parse_glossary(path), [
                {'right': '吉博力', 'wrong': ['吉布利', '吉布力']}, {'right': 'Oatey 通气阀', 'wrong': []}])

    def test_parse_glossary_missing_or_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'词库.txt'
            self.assertEqual(script_check.parse_glossary(path), [])
            for content in ('', '  \n# 注释\n'):
                path.write_text(content, encoding='utf-8')
                self.assertEqual(script_check.parse_glossary(path), [])

    def test_proofread_sheet_includes_shared_glossary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'词库.txt'
            path.write_text('吉布利|吉布力 => 吉博力\nStudor\n', encoding='utf-8')
            with patch.object(script_check, 'GLOSSARY', path), patch.object(script_check, 'ip_glossary', return_value=None), patch.object(script_check, 'kept_tokens', return_value=tokens('吉布利')):
                sheet = script_check.proofread_sheet({}, {}, {})
            self.assertEqual(sheet['glossary'], [{'right': '吉博力', 'wrong': ['吉布利', '吉布力']}, {'right': 'Studor', 'wrong': []}])
            self.assertEqual(sheet['sentences'][0]['subtitle'], '吉布利')
            self.assertTrue(any('wrong' in rule and 'right' in rule for rule in sheet['rules']))


if __name__ == '__main__':
    unittest.main()
