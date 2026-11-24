import copy
import unittest
from unittest.mock import patch
from paragraph_editor import change
from review_build import effective_cuts
from timing import kept_segments,source_to_timeline,timeline_to_source

ALIGNED={'alignment_id':'fixture','tokens':[
 {'id':'u0c0','unit_id':0,'char_start':0,'text':'甲','start_ms':1000,'end_ms':1200,'ready':True},
 {'id':'u0c1','unit_id':0,'char_start':1,'text':'乙','start_ms':1250,'end_ms':1400,'ready':True},
 {'id':'u0c2','unit_id':0,'char_start':2,'text':'丙','start_ms':1500,'end_ms':1600,'ready':False}]}
MANIFEST={'cards':[{'id':'existing','cut_frames':[60,90],'default':'apply'}]}

class ParagraphEditorTests(unittest.TestCase):
 def setUp(self):
  self.state={'overrides':{'existing':'apply'},'notes':{'existing':'original user note'},'seen':['existing']}
  self.patcher=patch('paragraph_editor.alignment',return_value=ALIGNED);self.patcher.start();self.addCleanup(self.patcher.stop)
 def apply(self,action,ids=(),state=None,revision=None):
  state=self.state if state is None else state
  req={'action':action,'ids':list(ids),'alignment_id':'fixture','revision':state.get('editor',{}).get('revision',0) if revision is None else revision}
  return change(state,req,MANIFEST)
 def test_selected_characters_cut_and_preserve_prior_choices(self):
  s=self.apply('cut',['u0c0','u0c1'])
  self.assertEqual(effective_cuts(MANIFEST['cards'],s),[[30,42],[60,90]])
  for k in ['overrides','notes','seen']:self.assertEqual(s[k],self.state[k])
  self.assertNotIn('editor',self.state)
 def test_undo_and_redo_restore_exact_ranges(self):
  s=self.apply('cut',['u0c0']);u=self.apply('undo',state=s)
  self.assertEqual(effective_cuts(MANIFEST['cards'],u),[[60,90]])
  redo=self.apply('redo',state=u);self.assertEqual(effective_cuts(MANIFEST['cards'],redo),[[30,36],[60,90]])
 def test_restore_then_undo_restoration(self):
  s=self.apply('cut',['u0c0','u0c1']);s=self.apply('restore',['u0c0'],s)
  self.assertEqual(effective_cuts(MANIFEST['cards'],s),[[36,42],[60,90]])
  s=self.apply('undo',state=s);self.assertEqual(effective_cuts(MANIFEST['cards'],s),[[30,42],[60,90]])
 def test_new_edit_after_undo_discards_only_redo_history(self):
  s=self.apply('cut',['u0c0']);s=self.apply('undo',state=s);s=self.apply('cut',['u0c1'],s)
  self.assertEqual(len(s['editor']['operations']),1)
  self.assertEqual(effective_cuts(MANIFEST['cards'],s),[[37,42],[60,90]])
 def test_invalid_timing_does_not_partially_apply(self):
  original=copy.deepcopy(self.state)
  with self.assertRaises(ValueError):self.apply('cut',['u0c0','u0c2'])
  self.assertEqual(self.state,original)
 def test_short_character_can_be_cut_when_its_frame_is_unambiguous(self):
  aligned={'alignment_id':'fixture','tokens':[
   {'id':'brief','unit_id':0,'char_start':0,'text':'啊','start_ms':1000,'end_ms':1010,'ready':True},
   {'id':'later','unit_id':0,'char_start':1,'text':'好','start_ms':1500,'end_ms':1600,'ready':True}]}
  with patch('paragraph_editor.alignment',return_value=aligned):
   s=self.apply('cut',['brief'])
  self.assertIn([30,31],effective_cuts(MANIFEST['cards'],s))
 def test_same_frame_character_cut_is_rejected(self):
  aligned={'alignment_id':'fixture','tokens':[
   {'id':'a','unit_id':0,'char_start':0,'text':'啊','start_ms':1000,'end_ms':1010,'ready':True},
   {'id':'b','unit_id':0,'char_start':1,'text':'好','start_ms':1010,'end_ms':1020,'ready':True},
   {'id':'later','unit_id':0,'char_start':2,'text':'呀','start_ms':1500,'end_ms':1600,'ready':True}]}
  with patch('paragraph_editor.alignment',return_value=aligned):
   with self.assertRaisesRegex(ValueError,'within one video frame'):
    self.apply('cut',['a'])
 def test_same_frame_characters_can_be_deleted_and_restored_together(self):
  aligned={'alignment_id':'fixture','tokens':[
   {'id':'a','unit_id':0,'char_start':0,'text':'啊','start_ms':1000,'end_ms':1010,'ready':True},
   {'id':'b','unit_id':0,'char_start':1,'text':'好','start_ms':1010,'end_ms':1020,'ready':True},
   {'id':'later','unit_id':0,'char_start':2,'text':'呀','start_ms':1500,'end_ms':1600,'ready':True}]}
  with patch('paragraph_editor.alignment',return_value=aligned):
   removed=self.apply('cut',['a','b'])
   with self.assertRaisesRegex(ValueError,'within one video frame'):
    self.apply('restore',['a'],removed)
   restored=self.apply('restore',['a','b'],removed)
   self.assertEqual(effective_cuts(MANIFEST['cards'],restored),[[60,90]])
 def test_stale_whole_state_rejected_even_when_editor_revision_did_not_change(self):
  from paragraph_editor import state_revision
  req={'action':'cut','ids':['u0c0'],'alignment_id':'fixture','revision':0,'state_revision':state_revision(self.state)}
  other=copy.deepcopy(self.state);other['notes']['existing']='changed in legacy page'
  with self.assertRaises(ValueError):change(other,req,MANIFEST)
 def test_stale_revision_rejected(self):
  s=self.apply('cut',['u0c0'])
  with self.assertRaises(ValueError):self.apply('cut',['u0c1'],s,revision=0)
 def test_timeline_mapping_roundtrips_every_retained_frame(self):
  s=self.apply('cut',['u0c0','u0c1']);segments=kept_segments(200,effective_cuts(MANIFEST['cards'],s))
  for segment in segments:
   for frame in range(segment['src_in'],segment['src_out']):
    mapped=source_to_timeline(frame,segments)
    self.assertFalse(mapped['cut']);self.assertEqual(timeline_to_source(mapped['frame'],segments),frame)

 def test_manual_cut_origin_uses_only_active_operations(self):
  from paragraph_editor import manual_changes
  s=self.apply('cut',['u0c0'])
  result=manual_changes(MANIFEST,s,ALIGNED,['u0c0'])
  self.assertEqual(result['manual_cut_token_ids'],['u0c0'])
  self.assertEqual(result['manual_changes'][0]['text'],'甲')
  undone=self.apply('undo',state=s)
  self.assertEqual(manual_changes(MANIFEST,undone,ALIGNED,[])['manual_changes'],[])
  redo=self.apply('redo',state=undone)
  self.assertEqual(manual_changes(MANIFEST,redo,ALIGNED,['u0c0'])['manual_cut_token_ids'],['u0c0'])
 def test_ai_words_in_mixed_manual_selection_stay_ai(self):
  from paragraph_editor import manual_changes
  manifest={'cards':[{'id':'ai','default':'apply','cut_frames':[30,36]}]}
  s={'editor':{'operations':[{'action':'cut','ids':['u0c0','u0c1'],'ranges':[[30,42]]}],'cursor':1}}
  original=copy.deepcopy(s)
  result=manual_changes(manifest,s,ALIGNED,['u0c0','u0c1'])
  self.assertEqual(result['manual_cut_token_ids'],['u0c1']);self.assertEqual(s,original)
 def test_restored_ai_text_marked_and_undo_keeps_ai_origin(self):
  from paragraph_editor import manual_changes
  manifest={'cards':[{'id':'ai','default':'apply','cut_frames':[30,36]}]}
  s={'editor':{'operations':[{'action':'restore','ids':['u0c0'],'ranges':[[30,36]]}],'cursor':1}}
  self.assertEqual(manual_changes(manifest,s,ALIGNED,[])['manual_restored_token_ids'],['u0c0'])
  s['editor']['cursor']=0
  self.assertEqual(manual_changes(manifest,s,ALIGNED,['u0c0'])['manual_changes'],[])
 def test_cut_then_restore_originally_kept_word_has_no_effective_change(self):
  from paragraph_editor import manual_changes
  s=self.apply('cut',['u0c0']);s=self.apply('restore',['u0c0'],s)
  self.assertEqual(manual_changes(MANIFEST,s,ALIGNED,[])['manual_changes'],[])
 def test_legacy_manual_choices_are_distinguished_from_default_ai_cuts(self):
  from paragraph_editor import manual_changes
  manifest={'cards':[{'id':'manual','default':'keep','cut_frames':[30,36]},
                      {'id':'ai','default':'apply','cut_frames':[37,42]}]}
  s={'overrides':{'manual':'apply','ai':'keep'}}
  result=manual_changes(manifest,s,ALIGNED,['u0c0'])
  self.assertEqual(result['manual_cut_token_ids'],['u0c0'])
  self.assertEqual(result['manual_restored_token_ids'],['u0c1'])

if __name__=='__main__':unittest.main()
