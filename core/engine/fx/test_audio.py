import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from engine.fx.audio import audio_preflight,sound_track,is_sound
from engine.fx.plan import config
from engine.fx.review import clean_audio_copy,sound_ranges,read_segments,marker_text


class AudioTests(unittest.TestCase):
    def test_track_names_and_source_classification(self):
        for name in ['音效','音效2','音效10']:self.assertTrue(sound_track(name))
        for name in ['A1','人声','B-roll原声','B-roll 原声','音效1','音效旧',1]:self.assertFalse(sound_track(name))
        self.assertFalse(is_sound({'media_path':'/voice.mp3'}))
        self.assertFalse(is_sound({'id':'owned','media_path':'/speech.mp4'}))

    def test_dedicated_track_rejects_voice_even_if_disabled_or_no_time_overlap(self):
        tl={'tracks':[{'type':'audio','index':1,'name':'音效'}],
            'items':[{'type':'audio','track':1,'id':'speech','media_path':'/speech.wav','enabled':False}]}
        with self.assertRaisesRegex(ValueError,'人声'):audio_preflight(tl)
        # A once-owned SFX clip replaced with speech must not inherit SFX classification.
        with self.assertRaisesRegex(ValueError,'人声'):audio_preflight(tl,{'sounds':[{'item_id':'speech'}]})
        tl['tracks'][0]['name']='人声与原声'
        with self.assertRaisesRegex(ValueError,'移入'):audio_preflight(tl,{'sounds':[{'item_id':'speech'}]})

    def test_copy_mutes_sfx_on_mixed_track_keeps_voice_and_original_untouched(self):
        source=Mock(spec=['DuplicateTimeline']);target=Mock()
        source.DuplicateTimeline.return_value=target
        rows=[{'id':'voice','type':'audio','track':1,'start':100,'end':200,'source_start':0,'source_end':100,
               'media_path':'/speech.wav','enabled':True,'props':{'AudioVolume':-4}},
              {'id':'sfx','type':'audio','track':1,'start':200,'end':210,'source_start':7,'source_end':17,
               'media_path':str(Path(config('sfx_favorites.json')['root'])/'pop.wav'),'enabled':True,'props':{'AudioVolume':-3}}]
        before={'timeline':{'tracks':[{'type':'audio','index':1,'name':'原声'}],'items':rows}}
        copied=copy.deepcopy(before['timeline']);after=copy.deepcopy(copied);after['items'][1]['enabled']=False
        handles=[]
        for row in rows:
            i=Mock();i.GetUniqueId.return_value=row['id'];handles.append(i)
        target.GetTrackCount.return_value=1;target.GetTrackName.return_value='原声'
        target.GetItemListInTrack.return_value=handles;target.GetUniqueId.return_value='copy-id'
        target.GetMediaPoolItem.return_value.GetUniqueId.return_value='copy-media'
        io=Mock();io.main=source;io.write.side_effect=lambda fn,*args:fn(*args)
        io.p.GetTimelineCount.return_value=0
        with tempfile.TemporaryDirectory() as d,patch('engine.fx.cleanup.register'),patch('engine.fx.review.read_timeline',side_effect=[copied,after]):
            _,proof=clean_audio_copy(io,{},before,'new',Path(d))
        handles[0].SetClipEnabled.assert_not_called();handles[1].SetClipEnabled.assert_called_once_with(False)
        self.assertTrue(proof['all_sounds_disabled']);self.assertTrue(rows[1]['enabled'])
        source.DuplicateTimeline.assert_called_once_with('new')

    def test_live_sound_offsets_trim_volume_and_deleted(self):
        timeline=Mock();timeline.GetTrackCount.return_value=1;timeline.GetIsTrackEnabled.return_value=True
        item=Mock();item.GetUniqueId.return_value='present';item.GetClipEnabled.return_value=True
        item.GetStart.return_value=105;item.GetEnd.return_value=120;item.GetLeftOffset.return_value=9
        item.GetProperty.return_value=-8;item.GetMediaPoolItem.return_value.GetUniqueId.return_value='media'
        item.GetMediaPoolItem.return_value.GetClipProperty.return_value='/pop.wav'
        timeline.GetItemListInTrack.return_value=[item]
        record={'sounds':[{'item_id':'present','effect_id':'x'},{'item_id':'deleted','effect_id':'y'}]}
        segments=[{'start':110,'end':115,'review_start':300}]
        rows,_=sound_ranges(record,timeline,segments)
        self.assertEqual(len(rows),1)
        self.assertEqual((rows[0]['review_start'],rows[0]['review_end'],rows[0]['source_offset'],rows[0]['gain_db']),
                         (300,305,14,-8))
        timeline.GetIsTrackEnabled.return_value=False
        self.assertEqual(sound_ranges(record,timeline,segments)[0],[])

    def test_a2_readback_rejects_wrong_media_gain_and_offset(self):
        timeline=Mock();timeline.GetTrackCount.side_effect=lambda kind:2 if kind=='audio' else 1
        timeline.GetTrackName.side_effect=lambda kind,n:'人声与原声' if n==1 else '音效'
        item=Mock();item.GetUniqueId.return_value='s';item.GetStart.return_value=100;item.GetEnd.return_value=110
        item.GetLeftOffset.return_value=7;item.GetProperty.return_value=-3;item.GetClipEnabled.return_value=True
        item.GetMediaPoolItem.return_value.GetUniqueId.return_value='sfx'
        timeline.GetItemListInTrack.side_effect=lambda kind,n:[item] if kind=='audio' and n==2 else []
        timeline.GetMarkers.return_value={}
        expected=[{'review_start':100,'review_end':110,'source_offset':7,'gain_db':-3,'media_id':'sfx'}]
        self.assertEqual(len(read_segments(timeline,[],'video','clean',expected)),1)
        for method,value,restore in [(item.GetLeftOffset,8,7),(item.GetProperty,-4,-3),
                                     (item.GetMediaPoolItem.return_value.GetUniqueId,'voice','sfx')]:
            method.return_value=value
            with self.assertRaisesRegex(RuntimeError,'A2音效'):read_segments(timeline,[],'video','clean',expected)
            method.return_value=restore


if __name__=='__main__':unittest.main()


def click_wave(path, amplitude=25000):
    import struct, wave
    rate=48000; samples=[0]*rate
    # 02_清脆鼠标点击-like: leading silence, two attacks, second stronger.
    for seconds,peak in ((.2,amplitude//3),(.6,amplitude)):
        n=round(seconds*rate)
        for j in range(240): samples[n+j]=round(peak*(1-j/240))
    with wave.open(str(path),'wb') as out:
        out.setparams((1,2,rate,0,'NONE','not compressed'))
        out.writeframes(struct.pack('<'+'h'*len(samples),*samples))


class LocationTests(unittest.TestCase):
    """音效库的位置按本仓库在哪儿推，NAS 挂成 /Volumes/work-1 也能用；环境变量和配置照样优先。"""
    def test_sfx_root_falls_back_to_the_library_next_to_the_repository(self):
        from engine.fx import plan
        with tempfile.TemporaryDirectory() as d,patch.dict('os.environ') as env:
            env.pop('AI_EDIT_SFX_DIR',None)
            base=Path(d).resolve();located=base/'ThinkCut'/'素材库'/'音效素材';written=base/'原来的挂载点'/'音效素材'
            cfg={'root':str(written),'root_env':'AI_EDIT_SFX_DIR'}
            with patch.object(plan,'CONFIG',base/'ThinkCut'/'core'/'engine'/'config'),patch('engine.fx.audio.config',return_value=cfg):
                self.assertEqual(plan.sfx_roots(cfg),[written])              # 两处都不在：和以前一样，用配置里写的
                located.mkdir(parents=True)
                self.assertEqual(plan.sfx_roots(cfg),[located,written])      # 配置里写的不在、仓库旁边的在：用仓库旁边的
                for path,expected in ((located/'咻.mp3',True),(written/'咻.mp3',True),(base/'口播.wav',False)):
                    self.assertEqual(is_sound({'media_path':str(path)}),expected)
                written.mkdir(parents=True)
                self.assertEqual(plan.sfx_roots(cfg),[written])              # 配置里写的在：和以前一样
                self.assertFalse(is_sound({'media_path':str(located/'咻.mp3')}))
                env['AI_EDIT_SFX_DIR']=str(base/'别处')
                self.assertEqual(plan.sfx_roots(cfg),[base/'别处'])           # 环境变量照样最优先


class OnsetTests(unittest.TestCase):
    def test_two_clicks_keep_leading_silence_choose_second_and_cache(self):
        from engine.fx.audio import sound_placement, effective_onset
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'02_清脆鼠标点击.wav';cache=Path(d)/'cache.json'
            click_wave(path);info=dict(path=str(path),source_start_frame=0,duration_frames=29)
            detected=effective_onset(info,cache)
            self.assertEqual((detected['sync_frame'],detected['ordinal'],detected['transient_count']),(18,2,2))
            self.assertIn('自动检测，第2下',detected['method'])
            with patch('subprocess.run',side_effect=AssertionError('cache missed')):
                placed=sound_placement(dict(start=100),info,cache)
            self.assertEqual((placed['start'],placed['source_start'],placed['sync_timeline_frame']),(82,0,100))
            self.assertEqual(placed['length'],29)
            # Explicit sync overrides the strongest attack; file measurement stays cached.
            explicit=sound_placement(dict(start=100,sync_frame=6),info,cache)
            self.assertEqual((explicit['start'],explicit['sync_timeline_frame']),(94,100))
            with self.assertRaisesRegex(ValueError,'选段内'):
                sound_placement(dict(start=100,sync_frame=30),info,cache)
            click_wave(path,10000)
            self.assertLess(effective_onset(info,cache)['peak_dbfs'],detected['peak_dbfs'])

    def test_loud_and_quiet_sources_normalize_actual_peak_user_gain_wins(self):
        from engine.fx.audio import sound_placement
        with tempfile.TemporaryDirectory() as d:
            for amplitude in (32000,2000):
                path=Path(d)/f'{amplitude}.wav';click_wave(path,amplitude)
                info=dict(path=str(path),source_start_frame=0,duration_frames=29)
                p=sound_placement(dict(start=100),info)
                self.assertLessEqual(p['output_peak_dbfs'],-5)
                self.assertGreater(p['output_peak_dbfs'],-5.002)
                explicit=sound_placement(dict(start=100,gain_db=-12),info)
                self.assertEqual(explicit['gain_db'],-12)
                self.assertIn('用户',explicit['volume_basis'])

    def test_selection_peak_boundary_trim_and_failure(self):
        from engine.fx.audio import sound_placement, effective_onset
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'click.wav';click_wave(path)
            info=dict(path=str(path),source_start_frame=0,duration_frames=29)
            placed=sound_placement(dict(start=102),info,timeline_start=100)
            self.assertEqual((placed['start'],placed['source_start'],placed['length'],placed['sync_timeline_frame']),(100,16,13,102))
            # Selected segment excludes the loud second click: normalize the first.
            info['duration_frames']=12
            p=sound_placement(dict(start=100),info)
            self.assertEqual(p['onset']['ordinal'],1)
            self.assertLessEqual(p['output_peak_dbfs'],-5)
            info['duration_frames']=3
            with self.assertRaisesRegex(ValueError,'静音'):effective_onset(info)
            path.write_bytes(b'invalid')
            with self.assertRaisesRegex(ValueError,'检测失败'):effective_onset(info)


class PlacementTests(unittest.TestCase):
    def test_writer_uses_detected_source_onset_a3_and_default_gain(self):
        from engine.fx.test_direct import IO,Project,Timeline,Item
        from engine.fx.test_executor import plan
        from engine.fx.executor import place_media
        p=Project();t=Timeline(p);p.timelines['main']=t;p.active=t;io=IO(Mock(),p,t,'p')
        for n in (1,2,3):t.data['tracks'].append(dict(type='audio',index=n,name=f'A{n}',locked=False,enabled=True))
        class SoundItem(Item):
            def GetLeftOffset(self,*args):return self.row['source_start']
            def SetProperty(self,key,value):self.row.setdefault('props',{})[key]=value;return True
            def GetProperty(self,key):return self.row['props'][key]
        def append(media,kind,track,start,source,length):
            row=dict(id='new-sound',type=kind,track=track,start=start,end=start+length,
                     source_start=source,source_end=source+length,media_path=media,enabled=True,props={})
            t.data['items'].append(row);return SoundItem(row)
        io.append=append;io.media=lambda path:path
        pp=plan();name='电子触摸-科技-综艺特效.mp3'
        pp['effects'][0]['sounds']=[dict(filename=name,start=110)]
        files={name:dict(path='/fake.wav',source_start_frame=0,sync_frame=8,duration_frames=14)}
        with tempfile.TemporaryDirectory() as td,patch('engine.fx.audio.analyze_sound',return_value=dict(sync_frame=8,peak_dbfs=-2,method='自动检测，第2下')):
            after,removed,added,tracks,sounds,kept,mattes=place_media(io,pp,{},files,io.snapshot(),work_dir=td)
        state=sounds[0]['written_state']
        self.assertEqual((state['start'],state['end'],state['source_start'],state['track'],state['AudioVolume']),
                         (102,116,0,3,-3))
        self.assertEqual(tracks,[]);self.assertEqual(added,['new-sound']);self.assertEqual(removed,[])
