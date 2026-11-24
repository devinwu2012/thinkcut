"""Comparison timing must follow evidence, never borrow a different spoken word."""
import copy,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from engine.fx.compare import schedule,map_a1_words,validate_comparisons
from engine.fx.plan import validate
from engine.fx.review import review_ranges,marker_text
from engine.fx.resolve_io import ResolveIO
from unittest.mock import Mock, patch

def labels():
 base=json.loads((Path(__file__).parent/'testdata/plan_example.json').read_text())['effects'][0]
 rows=[]
 for side,text,x in [('left','长焦',.25),('right','主摄',.75)]:
  e=copy.deepcopy(base);e.update(effect_id=side,group_id='compare',text=text,layer=len(rows)+1)
  e['anchor']['center']=[x,.9];e['style']['size']=.06;rows.append(e)
 return rows
class CompareTests(unittest.TestCase):
 def test_no_word_uses_fixed_rhythm_and_review_is_honest(self):
  rows=schedule(labels(),100,172,words=[{'text':'画面','frame':101}])
  self.assertEqual([e['start'] for e in rows],[103,129]);self.assertEqual(validate_comparisons(rows),[])
  record={'effects':[{'effect':e,'effect_id':e['effect_id'],'written_state':{'start':e['start'],'end':e['end'],'StyledText':e['text']}} for e in rows]}
  mark=marker_text(1,review_ranges(record,100,200)[0]);self.assertIn('按画面节奏，不是口播对齐',mark['note'])
 def test_words_first_occurrence_and_each_word_has_its_own_frame(self):
  words=[{'text':t,'frame':f} for t,f in [('长',108),('焦',110),('主摄',125),('长焦',150)]]
  rows=schedule(labels(),100,172,words=words);self.assertEqual([e['start'] for e in rows],[108,125])
  rows[1]['start']=108;self.assertTrue(validate_comparisons(rows))
 def test_outside_mentions_do_not_align(self):
  rows=schedule(labels(),100,172,words=[{'text':'长焦','frame':99},{'text':'主摄','frame':172}])
  self.assertEqual([e['start'] for e in rows],[103,129])
 def test_subtitle_estimate_only_without_words(self):
  cues=[{'start':100,'end':160,'text':'看长焦再主摄'}]
  rows=schedule(labels(),100,172,subtitles=cues);self.assertEqual([e['start'] for e in rows],[110,140])
  self.assertTrue(all(e['comparison']['method']=='subtitle_estimate' for e in rows))
  rows=schedule(labels(),100,172,words=[],subtitles=cues);self.assertEqual(rows[0]['comparison']['method'],'rhythm')
 def test_short_range_missing_side_static_animation_or_wrong_sound_rejected(self):
  with self.assertRaises(ValueError):schedule(labels(),100,118,words=[])
  rows=schedule(labels(),100,172,words=[])
  self.assertTrue(validate_comparisons(rows[:1]))
  mixed=schedule(labels(),100,172,words=[{'text':'主摄','frame':150}])
  self.assertEqual([e['comparison']['method'] for e in mixed],['rhythm','word'])
  self.assertEqual([e['start'] for e in mixed],[103,150])
  for field,value in [('animation',[{'input':'Size','frame':103,'value':.06}]),('sounds',[]),('end',170)]:
   bad=copy.deepcopy(rows);bad[0][field]=value;self.assertTrue(validate_comparisons(bad))
 def test_static_default_and_explicit_pop(self):
  rows=schedule(labels(),100,172,words=[])
  self.assertTrue(all(e['animation']==[] for e in rows))
  rows[0]['animation']=[dict(input='Size',frame=103,value=.048),dict(input='Size',frame=106,value=.06)]
  self.assertEqual(validate_comparisons(rows),[])

 def test_structural_validator_invokes_comparison_check(self):
  p=json.loads((Path(__file__).parent/'testdata/plan_example.json').read_text());p['timeline'].update(start=100,end=200)
  p['effects']=schedule(labels(),100,172,words=[]);self.assertEqual(validate(p),[])
  p['effects'][1]['comparison']['word']='长焦';self.assertTrue(validate(p))
 def test_a1_trim_mapping_and_retiming_refused(self):
  clip=dict(type='audio',track=1,enabled=True,start=100,end=120,source_start=30,source_end=49,media_path='/a.mov',id='a')
  tl={'fps':30,'items':[clip]};data={'result':{'utterances':[{'words':[{'text':'长','start_time':1100},{'text':'焦','start_time':1200},{'text':'外','start_time':2000}]}]}}
  self.assertEqual([w['frame'] for w in map_a1_words(tl,{'/a.mov':data})],[103,106])
  self.assertIsNone(map_a1_words(tl,{}))
  self.assertIsNone(map_a1_words(tl,{'/a.mov':data},130,150))
  clip['source_end']=55
  with self.assertRaises(ValueError):map_a1_words(tl,{'/a.mov':data})
class BackgroundWriteTests(unittest.TestCase):
 def test_motion_keys_short_clip_and_list_states(self):
  from engine.fx.resolve_io import motion_keys
  from engine.fx.plan import config
  cfg=config('fx_styles.json');e={'style':{'profile':'brand_label'}};v={'Enabled1':1,'Opacity1':1}
  k=motion_keys(e,v,10,30000/1001,cfg)['Opacity1']   # 10 帧的短片段：按 12:9 缩成 6 帧进、4 帧出
  self.assertEqual(sorted(k),[0,6,10]);self.assertEqual(set(k[6]),{1,'LH','RH'})
  e['motion']={'in':False,'out':True};self.assertEqual(sorted(motion_keys(e,v,90,30000/1001,cfg)),['Opacity1'])
  e['motion']={'in':False,'out':False};self.assertEqual(motion_keys(e,v,90,30000/1001,cfg),{})
  e['style']['static']=True;e.pop('motion');self.assertEqual(motion_keys(e,v,90,30000/1001,cfg),{})
 def io_values(self):
  values={};keys={};curves={}
  tool=Mock();comp=Mock();item=Mock()
  comp.GetToolList.return_value={'text':tool};comp.GetAttrs.return_value={'COMPN_RenderStart':0}
  item.GetFusionCompByIndex.return_value=comp;item.GetDuration.return_value=69
  tool.SetInput.side_effect=lambda key,value:values.update({key:value}) or True
  def spline(name,reg='BezierSpline'):
   m=Mock();m.GetAttrs.return_value={'TOOLS_RegID':reg}
   m.SetKeyFrames.side_effect=lambda data,replace:curves.update({name:data}) or True
   return m
  def modifier(owner,prefix=''):
   def add(inp,reg):
    tool_=spline(prefix+inp,reg)
    if reg=='XYPath':tool_.AddModifier.side_effect=modifier(tool_,inp+'.')
    getattr(owner,inp).GetConnectedOutput.return_value.GetTool.return_value=tool_
    return True
   return add
  tool.AddModifier.side_effect=modifier(tool)
  def get_input(key,frame):
   if key=='Size' and 'Size' in curves and frame in curves['Size']:return curves['Size'][frame][1]
   if key=='LineOffset':
    c=curves.get('LineOffset.Y',{});return {1:0,2:c[frame][1] if frame in c else 0}
   if key in curves and frame in curves[key]:return curves[key][frame][1]
   if key.endswith('3') and key!='Enabled3' and values.get('Enabled3')==0:return None
   return values.get(key)
  tool.GetInput.side_effect=get_input
  io=ResolveIO.__new__(ResolveIO);io.write=lambda fn,*args:fn(*args)
  io.main=Mock();io.main.GetSetting.return_value='29.97'
  return io,item,tool,values,curves
 def test_default_label_is_pingfang_white_soft_shadow_without_background(self):
  # 2026-11-12 包装系统：苹方中粗白字、不加底、柔和投影；不再有冬青黑体、#2F7BFF 蓝底、#FF6F00 橙底
  io,item,tool,values,keys=self.io_values()
  e=schedule(labels(),100,172,words=[])[0];io.style(item,e)
  self.assertEqual((values['Font'],values['Style']),('PingFang SC','Semibold'))
  self.assertEqual([values[k] for k in ('Red1','Green1','Blue1')],[1,1,1])
  self.assertEqual(values['Enabled2'],0);self.assertNotIn('ElementShape2',values)
  self.assertEqual(values['Enabled3'],1);self.assertEqual(values['ElementShape3'],0)
  self.assertEqual([values[k] for k in ('Red3','Green3','Blue3')],[0,0,0]);self.assertEqual(values['Opacity3'],.67)
  self.assertEqual(values['Offset3'],{1:0,2:-.045});self.assertEqual((values['SoftnessX3'],values['SoftnessY3']),(10,10))
  # 默认淡入淡出（2026-11-12 用户定）：12 帧淡入＋行偏移上移，9 帧淡出；字和投影一起淡
  self.assertEqual(sorted(keys),['LineOffset.Y','Opacity1','Opacity3'])
  self.assertEqual(sorted(keys['Opacity1']),[0,12,60,69]);self.assertEqual(keys['Opacity3'][12][1],.67)
  self.assertEqual(keys['LineOffset.Y'][0][1],-.01296);self.assertEqual(keys['LineOffset.Y'][12][1],0)
  e['style']['static']=True;e['style']['shadow_enabled']=False;values.clear();keys.clear();tool.AddModifier.reset_mock();io.style(item,e)
  self.assertEqual(values['Enabled3'],0);self.assertFalse(any(k.endswith('3') and k!='Enabled3' for k in values))
  self.assertEqual(keys,{});tool.AddModifier.assert_not_called()
  e['animation']=[{'input':'Size','frame':e['start'],'value':e['style']['size']*.8},{'input':'Size','frame':e['start']+3,'value':e['style']['size']}]
  io.style(item,e)
  self.assertEqual(keys['Size'][0][1],e['style']['size']*.8);self.assertEqual(keys['Size'][3][1],e['style']['size'])
 def test_dark_label_background_is_card_color_semi_transparent_and_read_back(self):
  io,item,tool,values,keys=self.io_values()
  rows=labels()
  for r in rows:r['style']['profile']='brand_label_dark'
  rows=schedule(rows,100,172,words=[]);self.assertEqual(validate_comparisons(rows),[])
  self.assertEqual(rows[0]['style']['profile'],'brand_label_dark')
  io.style(item,rows[0])
  self.assertEqual((values['Font'],values['Style']),('PingFang SC','Medium'))
  self.assertEqual([round(values[k]*255) for k in ('Red2','Green2','Blue2')],[16,18,22])
  self.assertEqual((values['ElementShape2'],values['Enabled2'],values['Opacity2']),(2,1,.66))
  self.assertEqual(values['Enabled3'],0);self.assertNotIn('Offset3',values)
  self.assertEqual(sorted(keys),['LineOffset.Y','Opacity1','Opacity2']);self.assertEqual(keys['Opacity2'][12][1],.66)
  for name,style in json.loads((Path(__file__).resolve().parents[1]/'config/fx_styles.json').read_text())['styles'].items():
   self.assertEqual(style['font'],'PingFang SC',name)
   rgb=style.get('background',{}).get('rgb')
   self.assertNotIn(rgb,([47/255,123/255,1],[1,111/255,0]),name)
 def test_accent_color_is_written_and_removed_outline_profile_has_no_special_path(self):
  # B8：强调色 #64A9E8 登记成可引用的 style.color="accent"；B10：已删掉的 outline 样式残留分支以前会因缺字段 KeyError
  from engine.fx.plan import config
  io,item,tool,values,keys=self.io_values()
  e=schedule(labels(),100,172,words=[])[0];e['style'].update(color='accent',static=True);io.style(item,e)
  self.assertEqual([round(values[k]*255) for k in ('Red1','Green1','Blue1')],[0x64,0xA9,0xE8])
  cfg=config('fx_styles.json');cfg['styles']['outline']=copy.deepcopy(cfg['styles']['brand_label'])
  e['style'].update(profile='outline');e['style'].pop('color');values.clear()
  with patch('engine.fx.resolve_io.config',return_value=cfg):io.style(item,e)       # 改前：KeyError: 'inner_thickness'
  self.assertEqual((values['Enabled1'],values['Enabled2'],values['Enabled3']),(1,0,1));self.assertNotIn('Thickness2',values)
  self.assertEqual(values['ElementShape3'],0);self.assertEqual(values['Opacity3'],.67)   # 和普通样式一样走柔和投影
  for name in ('resolve_io.py','safety.py'):
   self.assertNotIn("'outline'",(Path(__file__).parent/name).read_text(encoding='utf-8'),name)
  self.assertNotIn('outline',config('fx_styles.json')['styles'])


class PlanRuleTests(unittest.TestCase):
 def plan(self):
  from engine.fx.test_executor import plan
  p=plan();base=p['effects'][0];base['sounds']=[];p['effects']=[]
  for n,start in enumerate((110,150,190,400)):
   e=copy.deepcopy(base);e.update(effect_id=f'e{n}',group_id=f'g{n}',start=start,end=start+20);p['effects'].append(e)
  p['timeline']['end']=600
  return p
 def test_plan_text_color_only_white_or_accent(self):
  # B8：说明规定“颜色只用白和 #64A9E8”，以前计划里 overrides 的 Red1/Green1/Blue1 能写任意颜色
  from engine.fx.plan import color_errors,validation_report
  p=self.plan();self.assertEqual(validate(p),[]);self.assertEqual(color_errors(p),[])
  p['effects'][0]['style']['color']='accent';self.assertEqual(validate(p),[]);self.assertEqual(color_errors(p),[])
  p['effects'][1]['overrides']={'Red1':100/255+1e-6,'Green1':169/255,'Blue1':232/255-1e-6}     # 极小浮点误差算同一个颜色
  self.assertEqual(color_errors(p),[])
  p['effects'][1]['overrides']['Red1']=101/255                                                  # 差一级色阶就不算
  self.assertEqual(len(color_errors(p)),1);self.assertIn('e1',color_errors(p)[0]);self.assertIn('#65A9E8',color_errors(p)[0])
  p['effects'][2]['overrides']={'Red1':1,'Green1':111/255,'Blue1':0}                            # 旧的主题橙
  errors=color_errors(p);self.assertEqual(len(errors),2);self.assertIn('#FF6F00',errors[1]);self.assertIn('style.color="accent"',errors[1])
  with patch('engine.fx.safety.check_layout',return_value={'valid':True,'violations':[]}):
   report=validation_report(p);self.assertFalse(report['valid']);self.assertEqual(sum('文字颜色' in x for x in report['errors']),2)
   self.assertFalse(validation_report(p,allow_unsafe=True)['valid'])                              # 颜色不能用 --allow-unsafe 放行
   # 用户在达芬奇里手改、整条保留的花字（操作记录里 manual）不是计划写的颜色：不拦
   record={'effects':[{'effect_id':'e1','manual':True},{'effect_id':'e2','manual':True},{'effect_id':'e0'}]}
   self.assertTrue(validation_report(p,record=record)['valid'])
   record['effects'][1].pop('manual');self.assertFalse(validation_report(p,record=record)['valid'])
  p['effects'][0]['style']['color']='orange';self.assertTrue(any('style.color' in x for x in validate(p)))
  p['effects'][0]['style']['color']='note';self.assertTrue(validate(p))                           # 配置里的说明文字不是颜色
 def test_sound_rules_blacklist(self):
  # 2026-11-22：用户否掉过的音效（收藏表 blacklist）选到就停，--allow-sounds 才放行
  from engine.fx.plan import sound_rules,validation_report,config
  bl={b['filename'] for b in config('sfx_favorites.json')['blacklist']}
  self.assertIn('唰音效刷出标题.mp3',bl)                                                           # 261110、261117 用户否掉三次
  self.assertIn('唰音效刷出标题.mp3',{f['filename'] for f in config('sfx_favorites.json')['favorites']})   # 还在收藏表里（老片子能重出），只是选到会停
  p=self.plan();p['effects'][0]['sounds']=[{'filename':'唰音效刷出标题.mp3','start':p['effects'][0]['start']}]
  r=sound_rules(p);self.assertEqual([v['rule'] for v in r['violations']],['blacklist']);self.assertIn('不喜欢',r['violations'][0]['message'])
  self.assertTrue(any('否掉' in x for x in validation_report(p)['errors']))
  self.assertTrue(validation_report(p,allow_sounds=True)['sound_rules']['forced'])
  p['effects'][0]['sounds']=[{'filename':'UI+弹出，弹出+6.mp3','start':p['effects'][0]['start']}];self.assertTrue(sound_rules(p)['valid'])
  self.assertTrue(sound_rules(p|{'effects':[dict(p['effects'][0],sounds=[{'filename':'x.mp3','start':1}])]},blacklist={'x.mp3':'测试'})['valid'] is False)
 def test_sound_rules_two_second_window_and_same_event_same_file(self):
  # B9：说明规定“一个镜头 2 秒内不超过两声、同类事件全片同一声音”，以前程序只限制“每条花字最多一个音效”
  from engine.fx.plan import sound_rules,validation_report
  a,b='UI+弹出，弹出+6.mp3','02_清脆鼠标点击.mp3'
  p=self.plan()
  def sounds(*names):
   for e,name in zip(p['effects'],names):e['sounds']=[{'filename':name,'start':e['start']}] if name else []
  sounds(a,a,a,a);self.assertEqual(validate(p),[])
  self.assertTrue(sound_rules(p)['valid'])                                                       # 110/150/190：每 2 秒（60 帧）内最多两声
  p['effects'][2].update(start=169,end=189);sounds(a,a,a,a)                                      # 110、150、169：59 帧内三声
  report=sound_rules(p);self.assertFalse(report['valid'])
  self.assertEqual([(v['rule'],v['effect_ids']) for v in report['violations']],[('window',['e0','e1','e2'])])
  self.assertIn('第 169 帧',report['violations'][0]['message']);self.assertEqual(report['needs_user_decision'][0]['effect_ids'],['e0','e1','e2'])
  p['effects'][2].update(start=170,end=190);sounds(a,a,a,a);self.assertTrue(sound_rules(p)['valid'])   # 正好隔满 60 帧不算
  self.assertTrue(sound_rules(p|{'effects':p['effects'][:1]+p['effects'][2:]})['valid'])
  sounds(a,a,None,b);report=sound_rules(p)                                                       # 同类（同 kind、各自组里第 1 个）用了两种声音
  self.assertEqual([v['rule'] for v in report['violations']],['same_event']);self.assertEqual(sorted(report['violations'][0]['effect_ids']),['e0','e1','e3'])
  self.assertIn(a,report['violations'][0]['message']);self.assertIn(b,report['violations'][0]['message'])
  p['effects'][3]['sounds'][0]['event']='划掉';self.assertEqual(validate(p),[]);self.assertTrue(sound_rules(p)['valid'])   # 写明是另一类事件
  p['effects'][1]['sounds'][0]['event']='划掉';self.assertFalse(sound_rules(p)['valid'])            # 同一个 event 也必须同一个声音
  for e in p['effects']:
   for snd in e['sounds']:snd.pop('event',None)
  self.assertTrue(sound_rules(p,skip={'e3'})['valid'])                                           # 用户动过、不重放的音效不算
  p['effects'][3]['group_id']=p['effects'][0]['group_id'];self.assertTrue(sound_rules(p)['valid'])  # 同组第 1 个、第 2 个本来就可以不同声（连续入场）
  p['effects'][3]['group_id']='g3'
  with patch('engine.fx.safety.check_layout',return_value={'valid':True,'violations':[]}):
   report=validation_report(p);self.assertFalse(report['valid']);self.assertTrue(report['needs_user_decision'])
   self.assertTrue(any('音效需要用户决定' in x for x in report['errors']))
   self.assertFalse(validation_report(p,allow_unsafe=True)['valid'])                              # 版面的 --allow-unsafe 不放行音效（2026-11-13 用户定分开）
   forced=validation_report(p,allow_sounds=True);self.assertTrue(forced['valid']);self.assertTrue(forced['sound_rules']['forced'])
   self.assertEqual(len(forced['sound_rules']['violations']),1)                                   # 放行也保留违规记录
  p['effects'][0]['sounds'][0]['event']='';self.assertTrue(validate(p))

if __name__=='__main__':unittest.main()
