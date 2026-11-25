// Run the actual inline editor script against a minimal browser adapter.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
// 2026-11-22 页面搬进审片台“口播剪辑”栏：thinkcut-edit 的 assets/审片工作台/文案剪辑.html（THINKCUT_SKILL 指到 thinkcut-edit 时用它，否则兄弟目录 口播自动剪辑/skill/thinkcut-edit）。
const skill=process.env.THINKCUT_SKILL||path.join(__dirname,'../../../口播自动剪辑/skill/thinkcut-edit');
const script=fs.readFileSync(path.join(skill,'assets/审片工作台/文案剪辑.html'),'utf8').split('<script>')[1].split('</script>')[0].replace(/load\(true\)\.catch\(e=>\$\('error'\)\.textContent=(?:e\.message|humanErr\(e\))\);setInterval\([\s\S]*$/, '');
const fixture={alignment:{alignment_id:'fixture',tokens:[
 {id:'a',text:'甲',display:'甲',unit_id:0,char_start:0,start_ms:1000,end_ms:1200,ready:true},
 {id:'b',text:'乙',display:'乙',unit_id:0,char_start:1,start_ms:1500,end_ms:1700,ready:true}]},
 units:[{id:0,text:'甲乙',start_ms:1000}],cuts:[[0,30],[36,45],[51,300]],text_cuts:[],
 segments:[{src_in:30,src_out:36,tl_in:0,tl_out:6},{src_in:45,src_out:51,tl_in:6,tl_out:12}],
 fps:30,duration:.4,cursor:0,history_length:0,revision:0,state_revision:'before',notes:{},prior_choices:0,removed_token_ids:[]};
const response=(data,ok=true)=>({ok,json:async()=>structuredClone(data)});
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}};
function browser(){
 const elements=new Map(),documentEvents={},intervals=[];
 function element(id){
  if(elements.has(id))return elements.get(id);
  const classes=new Set(),events={};
  const el={id,dataset:{},style:{},events,textContent:'',value:'',disabled:false,hidden:false,innerHTML:'',scrollTop:0,
   classList:{add:k=>classes.add(k),remove:k=>classes.delete(k),contains:k=>classes.has(k),toggle:(k,v)=>v?classes.add(k):classes.delete(k)},
   addEventListener:(type,fn)=>{(events[type]??=[]).push(fn)},getBoundingClientRect:()=>({top:0,bottom:600,height:600}),scrollTo(){},blur(){this.blurred=true},closest(){return null}};
  if(id==='video')Object.assign(el,{currentTime:0,paused:true,seeking:false,readyState:1,playbackRate:1,playCalls:0,
   pause(){this.paused=true},play(){this.paused=false;this.playCalls++;return Promise.resolve()},requestVideoFrameCallback(){}});
  elements.set(id,el);return el;
 }
 const context=vm.createContext({console,Map,Set,Date,Number,Math,String,Array,JSON,Promise,
  document:{getElementById:element,documentElement:{style:{setProperty(){}}},body:{style:{},appendChild(){}},querySelectorAll:()=>[],createElement:()=>({style:{},classList:{add(){},remove(){}},remove(){},hidden:false}),
   addEventListener:(type,fn)=>{(documentEvents[type]??=[]).push(fn)},activeElement:null},
  window:{getSelection:()=>({isCollapsed:true,rangeCount:0,removeAllRanges(){}})},
  localStorage:{getItem:()=>null,setItem(){}},setTimeout:()=>1,clearTimeout(){},setInterval:(fn,ms)=>intervals.push(ms),
  CaptionEngine:{build:()=>({cues:[],byId:new Map()}),at:()=>null,timeline:()=>ms=>ms},
  CaptionText:{correctTokens:tokens=>({tokens}),balanceBrandLines(){}},fetch:async()=>response(fixture)});
 vm.runInContext(script,context);
 return {context,el:element,events:documentEvents,intervals,run:source=>vm.runInContext(source,context)};
}
async function test(name,work){await work();console.log('ok - '+name)}
(async()=>{
 await test('late poll cannot overwrite successful save',async()=>{
  const b=browser();await b.context.load();const poll=deferred();const saved={...fixture,revision:1,state_revision:'saved',cursor:1,history_length:1,removed_token_ids:['a']};
  b.context.fetch=()=>poll.promise;const polling=b.context.load();let sent;
  b.context.fetch=async(_url,options)=>{sent=JSON.parse(options.body);return response(saved)};
  await b.context.save('cut',['a']);poll.resolve(response(fixture));await polling;
  assert.equal(b.run('data.revision'),1);assert.equal(b.run('data.state_revision'),'saved');assert.equal(sent.state_revision,'before');
  assert.equal(b.run('removed(tokens[0])'),true);
 });
 await test('conflict reloads whole current state without retrying mutation',async()=>{
  const b=browser();await b.context.load();let posts=0,gets=0;
  b.context.fetch=async(url)=>{if(url==='/editor-action'){posts++;return response({error:'stale',reload:true},false)}gets++;return response({...fixture,revision:1,state_revision:'other-tab'})};
  await b.context.save('cut',['a']);assert.equal(posts,1);assert.equal(gets,1);assert.equal(b.run('data.state_revision'),'other-tab');assert.equal(b.el('video').paused,true);
 });
 await test('lost successful response is reconciled instead of claiming nothing saved',async()=>{
  const b=browser();await b.context.load();b.context.fetch=async(url)=>{if(url==='/editor-action')throw Error('connection closed');return response({...fixture,revision:1,state_revision:'committed',removed_token_ids:['a']})};
  await b.context.save('cut',['a']);assert.equal(b.run('data.state_revision'),'committed');assert.equal(b.run('removed(tokens[0])'),true);assert.match(b.el('status').textContent,/核对/);
 });
 await test('saving prevents new drag and video click playback',async()=>{
  const b=browser();await b.context.load();const pending=deferred();b.context.fetch=()=>pending.promise;const saving=b.context.save('cut',['a']);let prevented=false;
  b.el('textPane').events.pointerdown[0]({preventDefault:()=>prevented=true,target:{closest:()=>({})}});b.context.togglePlay();
  assert.equal(prevented,true);assert.equal(b.run('dragging'),false);assert.equal(b.el('video').playCalls,0);
  pending.resolve(response({...fixture,revision:1,state_revision:'saved'}));await saving;
 });
 await test('play after edited ending restarts from first retained word',async()=>{
  const b=browser();await b.context.load();b.run('seekPending=false');b.el('video').currentTime=2;b.el('video').paused=false;b.context.tick();
  assert.equal(b.run('playbackEnded'),true);assert.equal(b.el('video').paused,true);b.context.togglePlay();assert.equal(b.el('video').currentTime,1);assert.equal(b.el('video').paused,false);
 });
 await test('seeking current time does not wait forever for seeked',async()=>{
  const b=browser();await b.context.load();b.el('video').currentTime=1;b.context.seekTo(1);assert.equal(b.run('seekPending'),false);
 });
 await test('new alignment clears stale selected ids and missing alignment disables edits',async()=>{
  const b=browser();await b.context.load();b.run("lastSelection=['a'];locatedId='a'");b.context.fetch=async()=>response({...fixture,state_revision:'new-align',alignment:{...fixture.alignment,alignment_id:'replacement'}});
  await b.context.load();assert.equal(b.run('lastSelection.length'),0);assert.equal(b.run('locatedId'),null);
  b.context.fetch=async()=>response({...fixture,state_revision:'no-align',alignment:null});await b.context.load();assert.equal(b.run('tokens.length'),0);assert.equal(b.el('play').disabled,true);
 });
 await test('paused frame-capable browsers do not run 16ms full-text scans',async()=>{
  const b=browser();await b.context.load();assert.deepEqual(b.intervals,[]);
 });

 await test('AI and manual deletions keep distinct origins and change list',async()=>{
  const b=browser();b.context.fetch=async()=>response({...fixture,removed_token_ids:['a','b'],manual_cut_token_ids:['b'],manual_changes:[{action:'cut',ids:['b'],text:'乙',start_ms:1500}]});await b.context.load();
  b.run("elements.set('a',document.getElementById('word-a'));elements.set('b',document.getElementById('word-b'));update()");
  assert.equal(b.el('word-a').classList.contains('by-ai'),true);assert.equal(b.el('word-b').classList.contains('by-user'),true);
  assert.equal(b.el('word-a').dataset.changeOrigin,'ai');assert.equal(b.el('word-b').dataset.changeOrigin,'user');assert.match(b.el('changeList').innerHTML,/我删除/);
 });
 await test('restored user text has no strike and can disappear after undo',async()=>{
  const b=browser();b.context.fetch=async()=>response({...fixture,manual_restored_token_ids:['a']});await b.context.load();b.run("elements.set('a',document.getElementById('word-a'));update()");
  assert.equal(b.el('word-a').classList.contains('restored-by-user'),true);assert.equal(b.el('word-a').classList.contains('deleted'),false);
  b.run('data.manual_restored_token_ids=[];update()');assert.equal(b.el('word-a').classList.contains('restored-by-user'),false);
 });
 await test('space controls playback from speed select, slider, and save button focus',async()=>{
  for(const control of ['select','range','button']){
   const b=browser();await b.context.load();let prevented=0,stopped=0;
   const input=control==='range'?{type:'range'}:null;
   const target={isContentEditable:false,closest:selector=>selector==='input'?input:null};
   const event={code:'Space',key:' ',target,preventDefault:()=>prevented++,stopPropagation:()=>stopped++};
   b.events.keydown[0](event);assert.equal(b.el('video').paused,false);b.events.keydown[0](event);assert.equal(b.el('video').paused,true);
   assert.equal(prevented,2);assert.equal(stopped,2);
  }
 });
 await test('holding space toggles only once and text entry keeps its space',async()=>{
  const b=browser();await b.context.load();let prevented=0;const event={code:'Space',key:' ',target:{isContentEditable:false,closest:()=>null},preventDefault:()=>prevented++,stopPropagation(){}};
  b.events.keydown[0](event);b.events.keydown[0]({...event,repeat:true});assert.equal(b.el('video').playCalls,1);
  const text={type:'text'};b.events.keydown[0]({...event,target:{closest:selector=>selector==='input'?text:null}});assert.equal(prevented,2);
  b.el('playbackSpeed').value='1.5';b.el('playbackSpeed').onchange();assert.equal(b.el('playbackSpeed').blurred,true);assert.equal(b.el('video').playbackRate,1.5);
 });
 await test('fixing one subtitle word posts a correction and marks the word',async()=>{
  const b=browser();await b.context.load();const requests=[];
  b.context.fetch=async(url,options)=>{requests.push([url,JSON.parse(options.body)]);return response({...fixture,state_revision:'after',subtitle_token_corrections:[{id:'b',before:'乙',after:'已',reason:'网页手动修改字幕'}]})};
  b.run("prompt=()=>' 已 '");b.run("lastSelection=['b'];update()");assert.equal(b.el('fixCaption').disabled,false);
  await b.context.fixCaption();
  assert.deepEqual(requests,[['/subtitle-corrections',{rows:[{id:'b',before:'乙',after:'已',reason:'网页手动修改字幕'}],state_revision:'before'}]]);
  b.run("prompt=()=>'乙'");requests.length=0;await b.context.fixCaption();
  assert.deepEqual(requests[0][1].rows,[{id:'b',remove:true}]);
  b.run("lastSelection=['a','b'];update()");assert.equal(b.el('fixCaption').disabled,true);
 });
 await test('reordered segments play in timeline order, jumping back in the source',async()=>{
  const b=browser();b.context.fetch=async()=>response({...fixture,unit_order:[1,0],segments:[{src_in:45,src_out:51,tl_in:0,tl_out:6},{src_in:30,src_out:36,tl_in:6,tl_out:12}]});await b.context.load();
  b.context.seekTo(1.5);b.run('seekPending=false');assert.equal(b.run('playSeg'),0);assert.equal(b.run('sourceToTimeline(1.0)'),0.2);
  b.el('video').currentTime=51/30;b.el('video').paused=false;b.context.tick();
  assert.equal(b.el('video').currentTime,1);assert.equal(b.run('playSeg'),1);assert.equal(b.run('playbackEnded'),false);
  b.run('seekPending=false');b.el('video').currentTime=36/30;b.context.tick();assert.equal(b.run('playbackEnded'),true);
 });
 await test('dropping a clause handle on the first half of another clause moves it before that clause',async()=>{
  const two={...fixture,units:[{id:0,text:'甲，乙。',start_ms:1000}],
   alignment:{alignment_id:'fixture',tokens:[{...fixture.alignment.tokens[0]},{...fixture.alignment.tokens[1],char_start:2}]}};
  const b=browser();b.context.fetch=async()=>response(two);await b.context.load();
  assert.deepEqual(JSON.parse(b.run('JSON.stringify(clauseRuns.map(r=>r.ids))')),[['a'],['b']]);
  let sent;b.context.fetch=async(url,options)=>{sent=[url,JSON.parse(options.body)];return response({...two,word_order:['b','a'],revision:1,state_revision:'moved'})};
  const span={dataset:{run:'0'},classList:{add(){},remove(){}}},grabbed={dataset:{run:'1'},classList:{add(){},remove(){}}};
  const over={closest:sel=>sel==='.unit'?span:sel==='.word'?{dataset:{id:'a'}}:null};
  b.run('zoneUnit=()=>grabbedUnit');b.context.grabbedUnit=grabbed;const pane=b.el('textPane').events;
  const down={button:0,target:{closest:()=>null},preventDefault(){},stopPropagation(){}};for(const f of pane.pointerdown)f(down);
  assert.equal(b.run('dragRun'),1);
  for(const f of pane.pointermove)f({target:over,clientX:0,clientY:100,buttons:1});assert.equal(b.run('dropTarget.before'),'a');
  for(const f of pane.pointerup)f({});await new Promise(r=>setImmediate(r));
  assert.equal(sent[0],'/editor-action');assert.equal(sent[1].action,'move');assert.deepEqual(sent[1].ids,['b']);assert.equal(sent[1].before,'a');assert.equal(sent[1].side,'before');
  assert.equal(b.run('data.state_revision'),'moved');
  assert.match(b.el('paragraphs').innerHTML,/data-id="b"[\s\S]*data-id="a"/);
 });
 await test('selecting only deleted words restores them; a selection with kept words deletes',async()=>{
  const b=browser();b.context.fetch=async()=>response({...fixture,removed_token_ids:['a']});await b.context.load();const sent=[];
  b.context.fetch=async(url,options)=>{sent.push(JSON.parse(options.body));return response({...fixture,revision:1,state_revision:'after'})};
  b.run("selectedWords=()=>['a'];dragging=true");b.events.pointerup[0]();await new Promise(r=>setImmediate(r));
  assert.equal(sent[0].action,'restore');assert.deepEqual(sent[0].ids,['a']);
  assert.equal(b.run('lastSelection.length'),0);
  b.run("data.removed_token_ids=['a'];removedIds=new Set(['a']);selectedWords=()=>['a','b'];dragging=true");b.events.pointerup[0]();await new Promise(r=>setImmediate(r));
  assert.equal(sent[1].action,'cut');
 });
 await test('article layout: chapters only, no automatic paragraphs; Enter splits and Backspace joins',async()=>{
  const units=[{id:0,text:'开场白。',start_ms:0},{id:1,text:'第一个，卫生间。',start_ms:1000},{id:2,text:'那我们怎么解决呢？',start_ms:2000}];
  const two={...fixture,units,alignment:{alignment_id:'fixture',tokens:[{...fixture.alignment.tokens[0],unit_id:1},{...fixture.alignment.tokens[1],unit_id:2,char_start:0}]}};
  const b=browser();b.context.fetch=async()=>response(two);await b.context.load();
  const auto=JSON.parse(b.run('JSON.stringify((({sections,breaks})=>({sections,breaks:[...breaks]}))(layout()))'));
  assert.deepEqual(auto.sections.map(s=>s.start),[0,1]);assert.equal(auto.sections[1].title,'第一个，卫生间');assert.deepEqual(auto.breaks,[]);
  b.run("data.outline={sections:[{start:0,title:'开场'}],breaks:['b']}");
  const given=JSON.parse(b.run('JSON.stringify((({sections,breaks})=>({sections,breaks:[...breaks]}))(layout()))'));
  assert.deepEqual(given.sections,[{start:0,title:'开场'}]);assert.deepEqual(given.breaks,['b']);
  const sent=[];b.context.fetch=async(url,options)=>{sent.push([url,JSON.parse(options.body)]);return response({...two,outline:{sections:[],breaks:['b']}})};
  b.run("data.outline=null;draw();lastSelection=['b']");const key=k=>b.events.keydown[0]({key:k,code:k,target:{isContentEditable:false,closest:()=>null},preventDefault(){},stopPropagation(){}});
  key('Enter');await new Promise(r=>setImmediate(r));assert.deepEqual(sent[0],['/paragraph-break',{id:'b',on:true}]);
  b.run("lastSelection=['b']");key('Backspace');await new Promise(r=>setImmediate(r));assert.deepEqual(sent[1],['/paragraph-break',{id:'b',on:false}]);
 });
 console.log('__ALL_TESTS_DONE__');
})().catch(error=>{console.error(error);process.exitCode=1});
