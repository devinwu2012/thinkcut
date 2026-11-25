const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(require('node:path').join(__dirname,'../resolve_sync.js'),'utf8');
const response=(job,ok=true)=>({ok,json:async()=>job});
const deferred=()=>{let resolve;let promise=new Promise(r=>resolve=r);return{promise,resolve}};
function setup(fetch){const elements={saveResolve:{disabled:false},resolveStatus:{}},locks=[],intervals=[];let pauses=0,reloads=0;
 const ctx=vm.createContext({fetch,Date,Math,Number,Error,document:{getElementById:id=>elements[id]},setInterval:fn=>intervals.push(fn)});
 vm.runInContext(source,ctx);vm.runInContext('globalThis.sync=ResolveSync',ctx);
 ctx.sync.attach({getData:()=>({state_revision:'latest',alignment:{}}),onLock:v=>{locks.push(v);elements.saveResolve.disabled=v},pause:()=>pauses++,onConflict:async()=>reloads++});
 return {ctx,elements,locks,intervals,pauses:()=>pauses,reloads:()=>reloads};}
const settle=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 let submitted;const b=setup(async(url,options)=>{if(url==='/resolve-status')return response({status:'idle',running:false});submitted=JSON.parse(options.body);return response({status:'running',running:true,started_at:new Date().toISOString()})});await settle();
 await b.elements.saveResolve.onclick();assert.equal(submitted.state_revision,'latest');assert.equal(b.pauses(),1);assert.equal(b.elements.saveResolve.disabled,true);console.log('ok - sends latest confirmed selection and locks edits');
 const pending=deferred();let count=0;const c=setup(async(url)=>url==='/resolve-status'?response({status:'idle',running:false}):(count++,pending.promise));await settle();const first=c.elements.saveResolve.onclick();await c.elements.saveResolve.onclick();assert.equal(count,1);pending.resolve(response({status:'running',running:true}));await first;console.log('ok - duplicate click cannot duplicate a job');
 const d=setup(async()=>response({status:'complete',running:false,current:true,timeline:'new_version'}));await settle();assert.match(d.elements.resolveStatus.textContent,/new_version/);assert.equal(d.elements.saveResolve.disabled,false);console.log('ok - page reload restores verified completion');
 const e=setup(async()=>response({status:'complete',running:false,current:false,timeline:'old_version'}));await settle();assert.match(e.elements.resolveStatus.textContent,/再次保存/);console.log('ok - subsequent edits are marked unsynchronized');
 const f=setup(async(url)=>url==='/resolve-status'?response({status:'idle',running:false}):response({error:'stale',reload:true},false));await settle();await f.elements.saveResolve.onclick();assert.equal(f.reloads(),1);assert.equal(f.elements.saveResolve.disabled,false);assert.match(f.elements.resolveStatus.textContent,/stale/);console.log('ok - stale selection requires reload without resubmitting');
 let posts=0;const g=setup(async(url)=>{if(url==='/save-to-resolve'){posts++;throw Error('lost response')}return response({status:'running',running:true})});await settle();g.elements.saveResolve.disabled=false;await g.elements.saveResolve.onclick();assert.equal(posts,0);console.log('ok - resumed active job blocks another submission');
 console.log('__ALL_TESTS_DONE__');
})().catch(e=>{console.error(e);process.exitCode=1});
