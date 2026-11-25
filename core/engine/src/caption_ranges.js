// Range corrections shared by the served preview and frozen SRT, without changing source word IDs.
(function(root,factory){
 const node=typeof module==='object'&&module.exports;
 const api=factory(node?require('../caption_text.js'):root.CaptionText,node?require('../caption_engine.js'):root.CaptionEngine);
 if(node)module.exports=api;else{root.CaptionText=api;root.CaptionEngine=api.engine;}
})(typeof globalThis!=='undefined'?globalThis:this,(base,engine)=>{
 function correctTokens(source,units,rows=[]){
  const {tokens}=base.correctTokens(source,units,[]),changes=[],used=new Set();
  if(!Array.isArray(rows))throw Error('字幕纠错须为列表');
  for(const row of rows){
   const ids=row.ids??[row.id],indexes=ids.map(id=>tokens.findIndex(t=>t.id===id)),ts=indexes.map(i=>tokens[i]);
   if(!ids.length||indexes.some((v,i)=>v<0||(i&&v!==indexes[i-1]+1))||ts.some(t=>t.unit_id!==ts[0].unit_id||used.has(t.id)))throw Error('范围纠错必须对应同一句连续且不重叠的字编号');
   const u=units.find(u=>u.id===ts[0].unit_id),last=ts.at(-1);
   const before=Array.from(u.text).slice(ts[0].char_start,last.char_start+Array.from(last.text).length).join('');
   if(before.replace(/\s/g,'')!==String(row.before).replace(/\s/g,'')||typeof row.after!=='string'||!row.after.trim())throw Error('字幕纠错原文已变化：'+ids[0]);
   if(ids.length===1){
    // Preserve punctuation and word spacing once, rather than retaining two spaces.
    let suffix=ts[0].display.slice(ts[0].text.length);
    if(/\s$/.test(row.after))suffix=suffix.replace(/^\s+/,'');
    ts[0].display=row.after+suffix;ts[0].text=row.after;
   }else{
    const suffix=last.display.slice(last.text.length);
    ts.forEach((t,i)=>{t.text=i?'':row.after;t.display=i?'':row.after+suffix;t.correction_ids=ids;});
   }
   ids.forEach(id=>used.add(id));changes.push({...row,id:ids[0]});
  }
  return{tokens,changes};
 }
 function build(tokens,isRemoved=()=>false,toTimeline=ms=>ms){
  const grouped=[],members=new Map();
  for(let i=0;i<tokens.length;i++){
   const t=tokens[i],ids=t.correction_ids;
   if(!ids){if(!isRemoved(t))grouped.push(t);continue;}
   const group=tokens.slice(i,i+ids.length);
   if(group.length!==ids.length||group.some((x,j)=>x.id!==ids[j]))throw Error('范围纠错只剩部分字，请重新校对该范围');
   const kept=group.filter(x=>!isRemoved(x));
   if(kept.length&&kept.length!==group.length)throw Error('范围纠错只剩部分字，请重新校对该范围');
   if(kept.length){grouped.push({...t,end_ms:group.at(-1).end_ms});members.set(t.id,ids);}
   i+=ids.length-1;
  }
  const track=engine.build(grouped,()=>false,toTimeline);track.byId=new Map();
  for(const cue of track.cues){cue.ids=cue.ids.flatMap(id=>members.get(id)??[id]);for(const id of cue.ids)track.byId.set(id,cue);}
  return track;
 }
 return{correctTokens,balanceBrandLines:base.balanceBrandLines,engine:{...engine,build}};
});
