const assert=require('assert'),fs=require('fs'),path=require('path'),os=require('os'),cp=require('child_process'),vm=require('vm');
const api=require('./caption_ranges.js');
const raw=[{id:'u3c1',unit_id:3,char_start:0,text:'Mac',start_ms:0,end_ms:400},
 {id:'u3c2',unit_id:3,char_start:4,text:'Studio',start_ms:420,end_ms:900}];
const units=[{id:3,text:'Mac Studio。'}],row={ids:['u3c1','u3c2'],before:'Mac Studio',after:'Mac Studio',reason:'专有名词'};
const result=api.correctTokens(raw,units,[row]);
assert.equal(api.engine.build(result.tokens).cues[0].lines.join(''),'Mac Studio。');
assert.deepEqual(api.engine.build(result.tokens).cues[0].ids,['u3c1','u3c2']);
assert.equal(raw[0].text,'Mac');
assert.throws(()=>api.engine.build(result.tokens,t=>t.id==='u3c2'),/只剩部分/);
assert.throws(()=>api.correctTokens(raw,units,[{...row,ids:[...row.ids].reverse()}]),/连续/);
assert.throws(()=>api.correctTokens(raw,units,[{...row,before:'Mac Pro'}]),/原文已变化/);
assert.equal(api.engine.build(api.correctTokens(raw,units,[{id:'u3c1',before:'Mac',after:'Mac S',reason:'旧式拆改'}]).tokens).cues[0].lines[0],'Mac S Studio。');
// Same module is served after the existing browser libraries.
const browser={};vm.createContext(browser);
for(const file of ['../caption_engine.js','../caption_text.js','caption_ranges.js'])vm.runInContext(fs.readFileSync(path.join(__dirname,file),'utf8'),browser);
const browserTokens=browser.CaptionText.correctTokens(raw,units,[row]).tokens;
assert.equal(browser.CaptionEngine.build(browserTokens).cues[0].lines[0],'Mac Studio。');
const tmp=fs.mkdtempSync(path.join(os.tmpdir(),'aroll-range-'));
try{
 const put=(n,v)=>fs.writeFileSync(path.join(tmp,n),JSON.stringify(v));
 put('character_alignment.json',{tokens:raw});put('sentence_units.json',units);
 put('review_v2_state.json',{subtitle_token_corrections:[row]});
 put('trial_keep_segments.json',{segments:[{src_in:0,src_out:30,tl_in:0,tl_out:30}],cut_frames:[],retained_token_ids:raw.map(t=>t.id)});
 cp.execFileSync('node',[path.join(__dirname,'export_approved_captions.cjs'),tmp,'out']);
 const srt=fs.readFileSync(path.join(tmp,'out.srt'),'utf8');assert(srt.includes('Mac Studio。'));assert(!srt.includes('Mac S tudio'));
 assert.deepEqual(JSON.parse(fs.readFileSync(path.join(tmp,'subtitle_cues.json'))).cues[0].ids,raw.map(t=>t.id));
}finally{fs.rmSync(tmp,{recursive:true,force:true});}
console.log('__ALL_TESTS_DONE__');
