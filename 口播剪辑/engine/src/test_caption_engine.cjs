// The editor preview (source tokens + timeline mapper) must split cues exactly like the SRT export (mapped tokens).
const assert=require('assert'),engine=require('../caption_engine.js');
const fps=30000/1001,f=ms=>ms*fps/1000;
const tok=(id,text,start,end)=>({id,unit_id:0,text,start_ms:start,end_ms:end});
const tokens=[tok('a','我们',0,400),tok('x','重录',500,1700),tok('b','今天',1800,2200),tok('c','讲',2250,2500)];
const removed=t=>t.id==='x';
// Source frames kept: [0, 0.45s) and [1.75s, 2.6s); the 1.2 s retake is cut.
const segments=[{src_in:0,src_out:Math.round(f(450)),tl_in:0,tl_out:Math.round(f(450))}];
segments.push({src_in:Math.round(f(1750)),src_out:Math.round(f(2600)),tl_in:segments[0].tl_out,tl_out:segments[0].tl_out+Math.round(f(2600))-Math.round(f(1750))});
const map=engine.timeline(segments,fps);
const preview=engine.build(tokens,removed,map).cues.map(c=>c.ids);
const exported=engine.build(tokens.filter(t=>!removed(t)).map(t=>({...t,start_ms:map(t.start_ms),end_ms:map(t.end_ms)}))).cues.map(c=>c.ids);
assert.deepStrictEqual(preview,exported);
assert.deepStrictEqual(preview,[['a','b','c']]);
// Without the mapper the old behaviour split the cue at the removed retake.
assert.deepStrictEqual(engine.build(tokens,removed).cues.map(c=>c.ids),[['a'],['b','c']]);
// Preview cue times stay in source time for playback lookup.
assert.strictEqual(engine.build(tokens,removed,map).cues[0].start,0);
assert.strictEqual(engine.build(tokens,removed,map).cues[0].end,2.5);
console.log('caption engine: preview and export split identically');
console.log('__ALL_TESTS_DONE__');
