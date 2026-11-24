// Subtitle corrections (from the review state) and brand-name line balancing, shared by the web preview and the SRT.
(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;root.CaptionText=api})(typeof globalThis!=='undefined'?globalThis:this,()=>{
 function correctTokens(source,units,overrides=[]){
  const tokens=source.map(t=>({...t})),changes=[];
  for(const u of units){const ts=tokens.filter(t=>t.unit_id===u.id),chars=Array.from(u.text);ts.forEach((t,i)=>t.display=chars.slice(t.char_start,ts[i+1]?.char_start??chars.length).join(''));}
  if(!Array.isArray(overrides))throw Error('Invalid subtitle corrections');
  for(const row of overrides){const t=tokens.find(t=>t.id===row.id);if(!t||t.text!==row.before||typeof row.after!=='string'||!row.after)throw Error('Subtitle correction source changed: '+row.id);t.display=row.after+t.display.slice(t.text.length);changes.push({id:t.id,before:t.text,after:row.after,reason:row.reason});t.text=row.after;}
  return{tokens,changes};
 }
 function balanceBrandLines(cues,engine){
  for(const c of cues){if(c.lines.length<2)continue;const text=c.lines.join('');
   const spans=[...text.matchAll(/(?:DJI\s+)?Mic\s*(?:Pro|[23])?|影石|大疆/gi)].map(m=>[m.index,m.index+m[0].length]);
   const current=c.lines[0].length;if(!spans.some(([a,b])=>a<current&&current<b))continue;
   const options=Array.from(new Intl.Segmenter('zh',{granularity:'word'}).segment(text)).slice(1).map(w=>w.index).filter(i=>!spans.some(([a,b])=>a<i&&i<b)).map(i=>({lines:[text.slice(0,i).trim(),text.slice(i).trim()]})).filter(x=>x.lines.every(s=>s&&engine.width(s)<=18));
   options.sort((a,b)=>Math.abs(engine.width(a.lines[0])-engine.width(a.lines[1]))-Math.abs(engine.width(b.lines[0])-engine.width(b.lines[1])));if(options.length)c.lines=options[0].lines;
  }
  return cues;
 }
 return{correctTokens,balanceBrandLines};
});
