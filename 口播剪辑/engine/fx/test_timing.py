from copy import deepcopy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from engine.fx.test_executor import plan, timeline
from engine.fx.timing import prepare_timing, continuous_states
from engine.fx.layout import prepare_layout
from engine.fx.tracks import title_tracks
from engine.fx.plan import validate
from engine.fx.markers import delivery


def speech_timeline():
    t=timeline();t['items'].append(dict(id='a1',type='audio',track=1,start=100,end=200,
        source_start=300,source_end=399,media_path='/speech.mov',enabled=True))
    t['items'].append(dict(id='cue',type='subtitle',track=1,start=118,end=145,subtitle_text='拍照'))
    return t


def spoken_plan():
    p=plan();e=p['effects'][0];e.pop('speech');e['end']=160
    e['source_marker']=dict(frame=12,name='',note='保留标记')
    e['sounds']=[dict(filename='UI+弹出，弹出+6.mp3',start=110)]
    return p


class TimingTests(unittest.TestCase):
    def test_word_mapping_current_a1_cut_wins_over_marker_and_subtitle(self):
        p=spoken_plan();t=speech_timeline()
        p['transcripts']={'/speech.mov':{'result':{'utterances':[{'words':[
            {'text':'旧的','start_time':0},{'text':'拍','start_time':10400},
            {'text':'照','start_time':10500}]}]}}}
        e=prepare_timing(p,t)['effects'][0]
        self.assertEqual(e['start'],112)  # round(10400ms*29.97)-source300+timeline100
        self.assertEqual(e['sounds'][0]['start'],112)
        self.assertEqual(e['timing_evidence']['method'],'word')
        self.assertEqual(p['effects'][0]['start'],110)

    def test_subtitle_fallback_is_marked_estimate_even_without_marker(self):
        p=spoken_plan();p['effects'][0]['source_marker']=None
        prepared=prepare_timing(p,speech_timeline());e=prepared['effects'][0]
        self.assertEqual(e['start'],118);self.assertIn('估算',e['timing_basis'])
        with tempfile.TemporaryDirectory() as d:
            file,_=delivery(d,prepared,speech_timeline(),{})
            self.assertIn('估算',Path(file).read_text())

    def test_missing_or_retimed_evidence_stops_and_visual_is_explicit(self):
        p=spoken_plan();t=speech_timeline();t['items']=[i for i in t['items'] if i['type']!='subtitle']
        with self.assertRaisesRegex(ValueError,'首字'):prepare_timing(p,t)
        p['transcripts']={'/speech.mov':{'result':{'utterances':[]}}};t['items'][-1]['source_end']=350
        with self.assertRaisesRegex(ValueError,'变速'):prepare_timing(p,t)
        p['effects'][0]['speech']={'mode':'visual'}
        self.assertEqual(prepare_timing(p,t)['effects'][0]['start'],110)

    def test_three_list_states_touch_on_one_low_track_and_keep_previous_lines(self):
        p=plan();base=p['effects'][0];p['effects']=[]
        for n,text in enumerate(('第一项','第二项','第三项')):
            e=deepcopy(base);e.update(effect_id=str(n),text=text,start=110+20*n,end=190,
                                      list_group='list',layer=n+1,animation=[])
            p['effects'].append(e)
        out=continuous_states(p)
        self.assertEqual([(e['start'],e['end']) for e in out['effects']],[(110,130),(130,150),(150,190)])
        self.assertEqual(out['effects'][-1]['text'],'第一项\n第二项\n第三项')
        self.assertEqual(out['effects'][0]['text'],'第一项\n\n')
        self.assertTrue(all(len(e['text'].split('\n'))==3 for e in out['effects']))
        assigned,created=title_tracks(timeline(),out['effects'])
        self.assertEqual(set(assigned.values()),{2});self.assertEqual(created,[])
        self.assertFalse(validate(out));self.assertEqual(continuous_states(out),out)
        bad=deepcopy(out);bad['effects'][1].update(track=2,start=120,layer=2);bad['effects'][0]['track']=2
        self.assertTrue(any('同轨' in e for e in validate(bad)))

    def test_empty_word_arrays_fall_back_and_source_offset_wins(self):
        p=spoken_plan();t=speech_timeline()
        p['transcripts']={'/speech.mov':{'result':{'utterances':[{'words':[]}]}}}
        self.assertIn('估算',prepare_timing(p,t)['effects'][0]['timing_basis'])
        p['transcripts']['/speech.mov']['result']['utterances'][0]['words']=[{'text':'拍照','start_time':10400}]
        t['items'][-2]['source_offset']=301
        self.assertEqual(prepare_timing(p,t)['effects'][0]['start'],111)

    def test_list_timing_is_each_new_line_first_word(self):
        p=spoken_plan();e=p['effects'][0];e.update(list_group='list',animation=[])
        other=deepcopy(e);other.update(effect_id='next',text='视频',start=140)
        p['effects'].append(other);t=speech_timeline()
        t['items'].append(dict(type='subtitle',start=145,end=165,subtitle_text='视频'))
        rows=continuous_states(prepare_timing(p,t))['effects']
        self.assertEqual([(e['start'],e['end']) for e in rows],[(118,145),(145,160)])


class LayoutTests(unittest.TestCase):
    def test_move_beside_person_without_shrinking_and_explicit_center_is_retained(self):
        p=plan();e=p['effects'][0];e['anchor'].update(subject_kind='person',subject_bbox=[.4,.2,.6,.95],center=[.5,.7])
        def measure(effect,*args):
            x,y=effect['anchor']['center'];return {'bbox':[x-.1,y-.04,x+.1,y+.04]}
        with patch('engine.fx.layout.estimate',side_effect=measure),patch('engine.fx.layout.check_layout',return_value={'violations':[]}):
            out=prepare_layout(p,timeline())['effects'][0]
            self.assertLess(out['anchor']['center'][0],.4)
            self.assertEqual(out['style']['size'],e['style']['size'])
            e['anchor']['placement']='top_center'
            self.assertEqual(prepare_layout(p,timeline())['effects'][0]['anchor']['center'],[.5,.7])

    def test_entire_list_uses_full_state_for_common_position(self):
        p=plan();base=p['effects'][0];base['anchor'].update(subject_kind='person',subject_bbox=[.4,.2,.6,.95],center=[.5,.7])
        second=deepcopy(base);base.update(text='短',list_group='x',start=110,end=130)
        second.update(effect_id='long',text='短\n更长的一行',list_group='x',start=130,end=160)
        p['effects'].append(second)
        def measure(effect,*args):
            x,y=effect['anchor']['center'];half=.15 if '更长' in effect['text'] else .03
            return {'bbox':[x-half,y-.04,x+half,y+.04]}
        with patch('engine.fx.layout.estimate',side_effect=measure),patch('engine.fx.layout.check_layout',return_value={'violations':[]}):
            rows=prepare_layout(p,timeline())['effects']
        self.assertEqual(rows[0]['anchor']['center'],rows[1]['anchor']['center'])

    def test_no_space_stops_and_chapter_has_no_punctuation(self):
        p=plan();e=p['effects'][0];e['anchor'].update(subject_kind='person',subject_bbox=[.2,.2,.8,.95])
        with patch('engine.fx.layout.estimate',return_value={'bbox':[.2,.6,.8,.8]}),patch('engine.fx.layout.check_layout',return_value={'violations':['edge']}):
            with self.assertRaisesRegex(ValueError,'空处'):prepare_layout(p,timeline())
        e.update(kind='chapter',text='第一章：准备，开始！\n下一步。')
        out=prepare_layout(p,timeline())['effects'][0]
        self.assertEqual(out['text'],'第一章 准备 开始\n下一步')
