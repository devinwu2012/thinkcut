import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from prepare_new_review import repair_alignment


class NewProjectTests(unittest.TestCase):
    def test_runtime_is_bound_to_independent_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp,'project_runtime.json').write_text(json.dumps({'name':'New','project':'_mcp_New','source_frames':300,'fps':[30000,1001]}))
            result=subprocess.run([sys.executable,'-c','from project_runtime import NAME,TOTAL,PROJECT; print(NAME,TOTAL,PROJECT)'],
                env={**os.environ,'AI_EDIT_RUN_DIR':tmp},cwd=Path(__file__).parent,capture_output=True,text=True,check=True)
            self.assertEqual(result.stdout.strip(),'New 300 _mcp_New')

    def test_project_without_config_never_falls_back_to_c0451(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=subprocess.run([sys.executable,'-c','import project_runtime'],
                env={**os.environ,'AI_EDIT_RUN_DIR':tmp},cwd=Path(__file__).parent,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('Missing project_runtime.json',result.stderr)

    def test_bad_boundary_falls_back_to_actual_asr(self):
        units=[{'id':0,'text':'你好'}]
        response={'result':{'utterances':[{'words':[{'text':'你','start_time':10,'end_time':100},{'text':'好','start_time':100,'end_time':200}]}]}}
        aligned={'tokens':[{'id':'u0c0','unit_id':0,'char_start':0,'char_end':1,'text':'你','start_ms':0,'end_ms':0,'ready':False},
                           {'id':'u0c1','unit_id':0,'char_start':1,'char_end':2,'text':'好','start_ms':80,'end_ms':180,'ready':True}]}
        result=repair_alignment(copy.deepcopy(aligned),response,units)
        self.assertEqual([(t['start_ms'],t['end_ms']) for t in result['tokens']],[(10,100),(100,200)])
        self.assertEqual(result['issues'],[])

    def test_whole_number_word_stays_whole_with_genuine_time(self):
        units=[{'id':0,'text':'九十'}]
        response={'result':{'utterances':[{'words':[{'text':'九十','start_time':10,'end_time':200}]}]}}
        aligned={'tokens':[{'id':'u0c0','unit_id':0,'char_start':0,'char_end':1,'text':'九','start_ms':0,'end_ms':0,'ready':False}]}
        result=repair_alignment(aligned,response,units)
        self.assertEqual([(t['text'],t['char_start'],t['char_end'],t['start_ms'],t['end_ms']) for t in result['tokens']],
                         [('九十',0,2,10,200)])

    def test_split_number_word_is_merged_with_genuine_asr_time(self):
        units=[{'id':0,'text':'X五百好'}]
        response={'result':{'utterances':[{'words':[{'text':'X','start_time':0,'end_time':100},
            {'text':'五百','start_time':100,'end_time':300},{'text':'好','start_time':300,'end_time':400}]}]}}
        aligned={'tokens':[{'id':'u0c0','unit_id':0,'char_start':0,'char_end':1,'text':'X','start_ms':0,'end_ms':120,'ready':True},
                           {'id':'u0c1','unit_id':0,'char_start':1,'char_end':2,'text':'五','start_ms':120,'end_ms':120,'ready':False},
                           {'id':'u0c2','unit_id':0,'char_start':2,'char_end':3,'text':'百','start_ms':120,'end_ms':280,'ready':True},
                           {'id':'u0c3','unit_id':0,'char_start':3,'char_end':4,'text':'好','start_ms':290,'end_ms':400,'ready':True}]}
        result=repair_alignment(aligned,response,units)
        self.assertEqual([(t['id'],t['text'],t['start_ms'],t['end_ms']) for t in result['tokens']],
                         [('u0c0','X',0,100),('u0c1','五百',100,300),('u0c3','好',300,400)])

if __name__=='__main__':unittest.main()
