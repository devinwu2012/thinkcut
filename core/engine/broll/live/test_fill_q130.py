# -*- coding: utf-8 -*-
"""Q130：3:4 画面的 B-roll 衍生片段铺满（裁切不留黑边），crop_anchor 定裁哪边、只重出点名那一段。"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).absolute().parents[3]))
import json
import subprocess
import unittest
from copy import deepcopy
from tempfile import TemporaryDirectory
from unittest.mock import patch
from engine.broll import media
from engine.broll.live._fake_resolve import Resolve, Media
from engine.broll.live.resolve_adapter import Adapter
from engine.broll.live.service import Session

W,H = 240,320            # 3:4 竖画面（真片子是 2160×2880，缩小测）
RED,BLUE,GREEN,YELLOW,GRAY = (255,0,0),(0,0,255),(0,255,0),(255,255,0),(128,128,128)


def make_source(path, size):
    """灰底；最上一条红、最下一条蓝、最左一条绿、最右一条黄，能分辨裁到了哪边。"""
    w,h = size;band = h//8;side = w//18
    vf = (f'drawbox=x=0:y=0:w={w}:h={band}:color=red:t=fill,drawbox=x=0:y={h-band}:w={w}:h={band}:color=blue:t=fill,'
          f'drawbox=x=0:y={band}:w={side}:h={h-2*band}:color=lime:t=fill,drawbox=x={w-side}:y={band}:w={side}:h={h-2*band}:color=yellow:t=fill')
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',f'color=gray:s={w}x{h}:r=30000/1001:d=3','-vf',vf,
                    '-c:v','libx264','-crf','0','-pix_fmt','yuv444p',str(path)],check=True,capture_output=True)


def first_frame(path):
    info = media.probe(path);w,h = info['width'],info['height']
    raw = subprocess.run(['ffmpeg','-v','error','-i',str(path),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'],check=True,capture_output=True).stdout
    return w,h,lambda x,y:tuple(raw[(y*w+x)*3:(y*w+x)*3+3])


def near(pixel, color, tol=60):
    return all(abs(a-b)<=tol for a,b in zip(pixel,color))


def black(pixel):
    return max(pixel)<40


class FillRenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base = Path(self.tmp.name)

    def render(self, size, resolution=(W,H), name='v.mov', **kw):
        source = self.base/name
        if not source.exists():make_source(source,size)
        c = {'kind':'video','file_path':str(source),'usable_start':0,'usable_end':80}
        return media.render_live_clip(c,self.base/'clips','B01',10,30,resolution=resolution,**kw)

    def edges(self, path):
        w,h,px = first_frame(path)
        return w,h,{'上':[px(x,1) for x in range(0,w,8)],'下':[px(x,h-2) for x in range(0,w,8)],
                    '左':[px(1,y) for y in range(0,h,8)],'右':[px(w-2,y) for y in range(0,h,8)]},px

    def test_vertical_source_fills_without_black_bars_default_center(self):
        out = self.render((180,320))               # 9:16 竖拍放进 3:4
        w,h,edges,px = self.edges(out['path'])
        self.assertEqual((w,h),(W,H))
        for side,pixels in edges.items():
            self.assertFalse(any(black(p) for p in pixels),side+'边有黑边')
        self.assertTrue(near(px(1,h//2),GREEN));self.assertTrue(near(px(w-2,h//2),YELLOW))   # 左右没裁
        # 默认居中：上下各裁掉一截，最上一行不是红条、最下一行不是蓝条
        self.assertFalse(near(px(w//2,1),RED));self.assertFalse(near(px(w//2,h-2),BLUE))
        m = out['metadata'];self.assertEqual((m['fit'],m['crop_anchor']),('fill',0.5))
        self.assertEqual(out['crop']['axis'],'上下');self.assertAlmostEqual(out['crop']['cropped'],0.25)
        self.assertIsNone(out['crop']['warning'])
        self.assertEqual(json.loads(Path(out['path']).with_suffix('.json').read_text()),m)

    def test_anchor_top_and_bottom_take_different_content(self):
        top = self.render((180,320),crop_anchor=0);bottom = self.render((180,320),crop_anchor=1)
        self.assertNotEqual(top['path'],bottom['path'])
        _,h,_,t = self.edges(top['path']);_,_,_,b = self.edges(bottom['path'])
        self.assertTrue(near(t(W//2,1),RED));self.assertFalse(near(t(W//2,h-2),BLUE))
        self.assertTrue(near(b(W//2,h-2),BLUE));self.assertFalse(near(b(W//2,1),RED))
        # 只改裁切位置：其余身份相同、同一个源入点
        self.assertEqual({k:v for k,v in top['metadata'].items() if k!='crop_anchor'},
                         {k:v for k,v in bottom['metadata'].items() if k!='crop_anchor'})

    def test_wide_source_center_crop_warns(self):
        out = self.render((320,180),name='h.mov')  # 横拍放进竖画面：裁掉一大半，要提醒
        w,h,edges,px = self.edges(out['path'])
        self.assertEqual((w,h),(W,H))
        for side,pixels in edges.items():
            self.assertFalse(any(black(p) for p in pixels),side+'边有黑边')
        self.assertTrue(near(px(w//2,1),RED));self.assertTrue(near(px(w//2,h-2),BLUE))      # 上下完整
        self.assertFalse(near(px(1,h//2),GREEN));self.assertFalse(near(px(w-2,h//2),YELLOW)) # 左右裁掉
        self.assertEqual(out['crop']['axis'],'左右');self.assertGreater(out['crop']['cropped'],0.5)
        self.assertIn('给用户看一眼',out['crop']['warning']);self.assertIn('左/右',out['crop']['warning'])
        left = self.render((320,180),name='h.mov',crop_anchor=0)
        _,_,_,l = self.edges(left['path']);self.assertTrue(near(l(1,H//2),GREEN))

    def test_other_formats_keep_old_letterbox_identity(self):
        # 16:9 放竖拍、9:16 放横拍：仍是老做法（整个缩进、补黑边），身份里没有 fit，老片段照常复用
        for resolution,size,bar in (((320,180),(180,320),'左'),((180,320),(320,180),'上')):
            with self.subTest(resolution=resolution):
                out = self.render(size,resolution=resolution,name=f'{resolution[0]}.mov')
                self.assertNotIn('fit',out['metadata']);self.assertNotIn('crop',out)
                w,h,edges,_ = self.edges(out['path'])
                self.assertEqual((w,h),resolution);self.assertTrue(all(black(p) for p in edges[bar]))
                with self.assertRaisesRegex(ValueError,'crop_anchor'):
                    self.render(size,resolution=resolution,name=f'{resolution[0]}.mov',crop_anchor=0.2)

    def test_bad_anchor_rejected(self):
        for value in (-0.1,1.5,True,'0.5'):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'crop_anchor'):
                self.render((180,320),crop_anchor=value)

    def test_rotated_phone_video_uses_display_size(self):
        source = self.base/'rot.mov';make_source(source,(320,180))
        rotated = self.base/'rot90.mov'
        subprocess.run(['ffmpeg','-v','error','-y','-display_rotation','90','-i',str(source),'-c','copy',str(rotated)],check=True,capture_output=True)
        self.assertEqual(media.display_size(rotated),(180,320))
        self.assertAlmostEqual(media.fill_crop(media.display_size(rotated),W,H)['cropped'],0.25)


class CropAnchorServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root = Path(self.temp.name)
        self.r = Resolve();self.io = Adapter(self.r,self.r.project,self.r.project.current)
        self.sleep = patch('engine.fx.resolve_io.time.sleep');self.sleep.start();self.addCleanup(self.sleep.stop)
        self.s = Session(self.io,{'creator':'示例IP','ledger_index':str(self.root/'registry.json'),'learning_root':str(self.root/'learning')},self.root/'film')
        self.source = self.root/'product.mov';self.source.write_bytes(b'fake test media')
        self.io.p.pool.root.clips.append(Media(str(self.source)))
        st = self.source.stat()
        self.s.catalog = {name:dict(segment=name,parent_segment=name,file=str(self.source),file_path=str(self.source),kind='video',in_media_pool=True,
                                    fps=fps,vfr=False,duration=40,usable_start=start,usable_end=start+600,focus='sharp',subject='centered',people='none',
                                    use='operation',sample_of=None,probe={'has_audio':True},size=st.st_size,mtime_ns=st.st_mtime_ns)
                          for name,start,fps in [('one',0,30),('two',600,'29.97')]}    # one 是 30fps，要出衍生片段；two 直接引用
        self.calls = []

    def matches(self, segment, **extra):
        return {'version':3,'segments':[{'spot':'S01','lines':[2,2],'shots':[{'end':200,'candidates':[dict(segment=segment,reason='产品画面对上口播',**extra)]}]}],'face':[]}

    def fake_render(self, meta, out_dir, spot, start, frames, **kw):
        self.calls.append(dict(kw,spot=spot,segment=meta['segment'],source_start=start))
        path = self.root/f'clip{len(self.calls)}.mov';path.write_bytes(b'fixture')
        return {'path':str(path),'startFrame':0,'crop':{'warning':'裁掉约58%画面（左右），给用户看一眼' if kw.get('crop_anchor') is None else None}}

    def test_match_field_reaches_render_and_adjust_rerenders_only_that_clip(self):
        with patch('engine.broll.live.service.media.render_live_clip',side_effect=self.fake_render):
            out = self.s.apply(self.matches('one',crop_anchor=0.3),True)
            self.assertTrue(out['applied']);self.assertEqual(self.calls[-1]['crop_anchor'],0.3)
            self.s.snapshot = self.io.snapshot()
            out = self.s.adjust(['S01'],crop_anchor=0.8,apply=True)
        self.assertTrue(out['applied']);self.assertEqual(len(self.calls),2)
        self.assertEqual((self.calls[-1]['crop_anchor'],self.calls[-1]['segment'],self.calls[-1]['source_start']),(0.8,'one',self.calls[0]['source_start']))
        row = self.s.ledger['spots']['S01'];self.assertEqual(row['segment'],'one')
        self.assertTrue(row['placement']['media_path'].endswith('clip2.mov'))

    def test_large_crop_is_reported(self):
        with patch('engine.broll.live.service.media.render_live_clip',side_effect=self.fake_render),\
             patch('engine.broll.live.service.media.rehearsal_sheet',return_value=str(self.root/'sheet.jpg')):
            out = self.s.apply(self.matches('one'))
        self.assertEqual(len(out['裁切提醒']),1);self.assertIn('给用户看一眼',out['report'])

    def test_direct_clip_refuses_anchor(self):
        with self.assertRaisesRegex(ValueError,'直接引用'):self.s.apply(self.matches('two',crop_anchor=0.2))
        self.s.apply(self.matches('two'),True);self.s.snapshot = self.io.snapshot()
        with self.assertRaisesRegex(ValueError,'直接引用'):self.s.adjust(['S01'],crop_anchor=0.2)

    def test_match_validation(self):
        for value in (-1,2,'上',True):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'crop_anchor'):self.s.apply(self.matches('one',crop_anchor=value))


if __name__=='__main__':unittest.main()
