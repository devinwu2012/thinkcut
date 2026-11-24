const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'../preview_source.js'),'utf8');
function fixture(){
 const handlers=new Map();
 const el={dataset:{segmentSeconds:'12',sourceDuration:'60'},currentTime:0,playbackRate:1.5,paused:true,seeking:false,readyState:0,playCalls:0,
  addEventListener(type,fn,capture=false){const list=handlers.get(type)||[];list.push({fn,capture});handlers.set(type,list)},
  removeEventListener(type,fn){handlers.set(type,(handlers.get(type)||[]).filter(x=>x.fn!==fn))},
  emit(type){let stopped=false;const event={stopImmediatePropagation(){stopped=true}};for(const h of [...(handlers.get(type)||[])].sort((a,b)=>Number(b.capture)-Number(a.capture))){if(stopped)break;h.fn(event)}},
  load(){this.paused=true;this.currentTime=0;this.readyState=0;this.emit('pause')},
  ready(){this.readyState=1;this.emit('loadedmetadata')},
  play(){this.playCalls++;this.paused=false;this.emit('play');return Promise.resolve()},
  pause(){this.paused=true;this.emit('pause')}
 };
 const context=vm.createContext({Promise,Proxy,Reflect,Math,Number,Error});vm.runInContext(code,context);
 const video=context.PreviewSource.attach(el);return {el,video};
}
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve()};
async function test(name,work){await work();console.log('ok - '+name)}
(async()=>{
 await test('deleted-range jump into another source part resumes playback',async()=>{
  const {el,video}=fixture();el.ready();await video.play();video.currentTime=25;
  assert.equal(video.paused,false);assert.equal(video.currentTime,25);el.ready();await flush();
  assert.equal(el.paused,false);assert.equal(el.currentTime,1);assert.equal(video.currentTime,25);assert.equal(el.playbackRate,1.5);
 });
 await test('user pause during pending source load stays paused',async()=>{
  const {el,video}=fixture();el.ready();await video.play();video.currentTime=25;video.pause();el.ready();await flush();
  assert.equal(el.paused,true);assert.equal(video.paused,true);
 });
 await test('paused word location does not autoplay',async()=>{
  const {el,video}=fixture();el.ready();video.currentTime=36;el.ready();await flush();
  assert.equal(el.playCalls,0);assert.equal(video.currentTime,36);assert.equal(video.paused,true);
 });
 await test('natural source-part ending continues at next part',async()=>{
  const {el,video}=fixture();el.ready();await video.play();el.currentTime=12;el.paused=true;el.emit('ended');
  assert.equal(video.paused,false);el.ready();await flush();assert.equal(el.paused,false);assert.equal(video.currentTime,12);
 });
 await test('rapid cross-part seeks use the final target',async()=>{
  const {el,video}=fixture();el.ready();await video.play();video.currentTime=25;video.currentTime=49;el.ready();await flush();
  assert.equal(video.currentTime,49);assert.equal(el.paused,false);assert.equal(el.src,'/source-part/4.mp4');
 });
 await test('failed source load leaves playback retryable',async()=>{
  const {el,video}=fixture();el.ready();await video.play();video.currentTime=25;el.emit('error');await flush();
  assert.equal(video.paused,true);video.currentTime=37;el.ready();await video.play();assert.equal(el.paused,false);
 });
 console.log('__ALL_TESTS_DONE__');
})().catch(error=>{console.error(error);process.exitCode=1});
