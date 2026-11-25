import copy
import unittest
from unittest.mock import Mock
from engine.fx.tracks import title_tracks, audio_track
from engine.fx.direct import insert
from engine.fx.test_executor import timeline, plan
from engine.fx.plan import config


def tr(kind,n,locked=False,name=None):
    return dict(type=kind,index=n,name=name or f'{kind}{n}',locked=locked,enabled=True)


class TrackTests(unittest.TestCase):
    def test_low_empty_tracks_ignore_empty_high_tracks_and_skip_locks(self):
        t=timeline();t['tracks'] += [tr('video',3,True),tr('video',4),tr('video',5),tr('video',6)]
        t['tracks'][1]['locked']=True
        e=plan()['effects'][0];other=copy.deepcopy(e);other.update(effect_id='other',layer=2)
        assignment,created=title_tracks(t,[e,other])
        self.assertEqual(assignment,{'effect-1':4,'other':5});self.assertEqual(created,[])
        other['track']=6
        self.assertEqual(title_tracks(t,[e,other])[0]['other'],6)

    def test_future_items_rejected_before_lock_or_insert(self):
        io=Mock();t=io.main;t.GetIsTrackLocked.return_value=False;t.GetIsTrackEnabled.return_value=True
        i=Mock();i.GetEnd.return_value=400;t.GetItemListInTrack.return_value=[i]
        with self.assertRaisesRegex(ValueError,'之后有片段'):insert(io,plan()['effects'][0],2,Mock())
        io.write.assert_not_called()
        t.InsertFusionTitleIntoTimeline.assert_not_called()

    def test_two_tracks_limit_and_explicit_name(self):
        t=timeline();e=plan()['effects'][0];rows=[]
        for n in range(3):
            row=copy.deepcopy(e);row.update(effect_id=str(n),layer=n+1);rows.append(row)
        with self.assertRaisesRegex(ValueError,'两条'):title_tracks(t,rows)
        rows[-1]['track']='花字'
        # Explicit overlapping targets are still unsafe.
        with self.assertRaisesRegex(ValueError,'指定花字轨'):title_tracks(t,rows)

    def test_each_interval_uses_its_own_picture_floor(self):
        t=timeline();t['tracks'] += [tr('video',n) for n in range(3,7)]
        t['items'].append(dict(id='high-broll',type='video',track=5,start=210,end=240))
        first=plan()['effects'][0];second=copy.deepcopy(first);second.update(effect_id='later',start=210,end=240)
        assignment,_=title_tracks(t,[first,second])
        self.assertEqual(assignment,{'effect-1':2,'later':6})

    def test_numeric_audio_override_is_preflighted_before_any_title_write(self):
        from engine.fx.executor import audio_placement_preflight
        t=timeline();t['tracks'] += [tr('audio',n) for n in (1,2,3)]
        t['items'].append(dict(id='voice',type='audio',track=2,start=100,end=120,media_path='/voice.wav'))
        pp=plan();pp['effects'][0]['sounds']=[dict(filename='sfx',start=110,track=2)]
        from unittest.mock import patch
        with patch('engine.fx.executor.sound_placement',return_value=dict(start=110,length=10)),self.assertRaisesRegex(ValueError,'人声'):
            audio_placement_preflight({'timeline':t},pp,{'sfx':dict(path='/fake.wav',duration_frames=10)},[],[])

    def test_foreground_track_is_not_used_for_titles_or_pushed_under_them(self):
        t=timeline();t['tracks'].append(tr('video',3,name='人物抠像'))
        t['tracks'][1]['locked']=True
        with self.assertRaisesRegex(ValueError,'人物抠像'):title_tracks(t,plan()['effects'])

    def test_audio_reuses_a3_a4_and_user_override(self):
        t={'tracks':[tr('audio',i) for i in range(1,7)],'items':[]}
        self.assertEqual(audio_track(t,100,110),(3,[]))
        t['tracks'][2]['locked']=True
        self.assertEqual(audio_track(t,100,110),(4,[]))
        self.assertEqual(audio_track(t,100,110,2),(2,[]))
        t['items']=[dict(id='voice',type='audio',track=4,start=200,end=210,media_path='/voice.wav')]
        self.assertEqual(audio_track(t,100,110),(5,[]))
        with self.assertRaisesRegex(ValueError,'人声'):audio_track(t,100,110,4)
        with self.assertRaisesRegex(ValueError,'两条'):audio_track(t,100,110,used={3,4})

    def test_audio_empty_timeline_reserves_a1_a2(self):
        n,created=audio_track(dict(tracks=[],items=[]),0,10)
        self.assertEqual(n,3);self.assertEqual([n for n,name in created],[1,2,3])

    def test_unnamed_sfx_track_is_reused_without_mixing(self):
        t={'tracks':[tr('audio',i) for i in range(1,5)],'items':[
            dict(type='audio',track=3,start=10,end=20,media_path=config('sfx_favorites.json')['root']+'/test.mp3')]}
        self.assertEqual(audio_track(t,30,40),(3,[]))
        self.assertEqual(audio_track(t,15,25),(4,[]))

if __name__=='__main__':unittest.main()
