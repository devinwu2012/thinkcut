// Export the frozen approved cut using the same cue builder as the editor.
const fs=require('fs'),path=require('path'),captionText=require('./caption_ranges.js'),engine=captionText.engine;
const dir=path.resolve(process.argv[2]);
const outputName=process.argv[3];if(!outputName)throw Error('Usage: export_approved_captions.cjs <dir> <output name>');
const read=n=>JSON.parse(fs.readFileSync(path.join(dir,n),'utf8'));
const raw=read('character_alignment.json').tokens,units=read('sentence_units.json');
const {segments,cut_frames:cuts,retained_token_ids:retainedIds}=read('trial_keep_segments.json');
const fps=30000/1001;
const retainedSet=retainedIds===undefined?null:new Set(retainedIds);
const rawIds=new Set(raw.map(t=>t.id));
if(retainedSet&&(retainedSet.size!==retainedIds.length||retainedIds.some(id=>!rawIds.has(id))))throw Error('Invalid retained token IDs');
const removed=t=>retainedSet?!retainedSet.has(t.id):cuts.some(([a,b])=>a<=(t.start_ms+t.end_ms)*fps/2000&&(t.start_ms+t.end_ms)*fps/2000<b);
const corrected=captionText.correctTokens(raw,units,read('review_v2_state.json').subtitle_token_corrections||[]);
const kept=corrected.tokens.filter(t=>!removed(t));
const keptIds=new Set(kept.map(t=>t.id));
const changes=corrected.changes.filter(c=>keptIds.has(c.id));
const {word_order:unitOrder}=read('trial_keep_segments.json');
const timelineMap=engine.timeline(segments,fps);
// Sentences moved in the editor: place each word by the segment holding its middle, so a word edge
// rounding just past a split point never lands in the neighbouring (now distant) sentence.
const near=f=>segments.reduce((best,s)=>Math.min(Math.abs(s.src_in-f),Math.abs(s.src_out-f))<Math.min(Math.abs(best.src_in-f),Math.abs(best.src_out-f))?s:best);
const place=(ms,s)=>(s.tl_in+Math.min(Math.max(ms*fps/1000-s.src_in,0),s.src_out-s.src_in))*1000/fps;
const map=(ms,t)=>{if(!unitOrder)return timelineMap(ms);const mid=(t.start_ms+t.end_ms)*fps/2000;return place(ms,segments.find(s=>mid>=s.src_in&&mid<s.src_out)??near(mid))};
// Sorted by cut-timeline time: moved sentences play in their new order (stable sort keeps unmoved projects unchanged).
const mapped=kept.map(t=>({...t,source_start_ms:t.start_ms,source_end_ms:t.end_ms,start_ms:map(t.start_ms,t),end_ms:map(t.end_ms,t)})).sort((a,b)=>a.start_ms-b.start_ms);
for(const t of mapped)if(!Number.isFinite(t.start_ms)||!Number.isFinite(t.end_ms)||t.end_ms<=t.start_ms)throw Error('Invalid retained token time: '+t.id);
const cues=captionText.balanceBrandLines(engine.build(mapped).cues,engine);
const tc=ms=>{ms=Math.round(ms);const h=Math.floor(ms/3600000),m=Math.floor(ms/60000)%60,s=Math.floor(ms/1000)%60;return [h,m,s].map(n=>String(n).padStart(2,'0')).join(':')+','+String(ms%1000).padStart(3,'0')};
const maxMs=segments.at(-1).tl_out*1000/fps;
for(let i=0;i<cues.length;i++){const c=cues[i];c.start_ms=Math.max(0,Math.round(c.start*1000));c.end_ms=Math.min(Math.round(maxMs),Math.round(c.end*1000),i+1<cues.length?Math.round(cues[i+1].start*1000):Infinity);if(c.end_ms<=c.start_ms||c.lines.length>2||c.lines.some(line=>engine.width(line)>18))throw Error('Invalid cue '+i);}
const ids=cues.flatMap(c=>c.ids);if(ids.length!==kept.length||new Set(ids).size!==ids.length)throw Error('Token coverage mismatch');
fs.writeFileSync(path.join(dir,outputName+'.srt'),cues.map((c,i)=>`${i+1}\n${tc(c.start_ms)} --> ${tc(c.end_ms)}\n${c.lines.join('\n')}\n`).join('\n'));
fs.writeFileSync(path.join(dir,'subtitle_cues.json'),JSON.stringify({cues,tokens:mapped,corrections:changes,coverage:{retained_tokens:kept.length,caption_tokens:ids.length},timing:'elapsed milliseconds from timeline start; 30000/1001 fps'},null,2));
fs.writeFileSync(path.join(dir,'字幕纠错记录.json'),JSON.stringify(changes,null,2));
console.log(JSON.stringify({cues:cues.length,corrections:changes.length,retained_tokens:kept.length,first_ms:cues[0].start_ms,last_ms:cues.at(-1).end_ms}));
