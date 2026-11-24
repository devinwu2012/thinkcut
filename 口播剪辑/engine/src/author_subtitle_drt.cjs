// Local offline format writer from the installed Resolve MCP package.
// Live import/readback is performed through MCP; no UI or database patching.
const fs=require('fs'),path=require('path');
const mcpRoot=process.env.DAVINCI_RESOLVE_MCP_ROOT||path.join(process.env.CODEX_HOME||path.join(require('os').homedir(),'.codex'),'tools/davinci-resolve-mcp');
const vendor=path.join(mcpRoot,'resolve-advanced/vendor/drp-format');
const {loadDrpZip}=require(vendor+'/seq-surgery');
const {placeSubtitles,parseSrt}=require(vendor+'/place-subtitles');
(async()=>{
 const dir=path.resolve(process.argv[2]);
 if(process.argv.length<7)throw Error('Usage: author_subtitle_drt.cjs <dir> <input.drt> <output.drt> <subtitles.srt> <timeline name>');
 const input=path.join(dir,process.argv[3]);
 const output=path.join(dir,process.argv[4]);
 const subtitle=path.join(dir,process.argv[5]);
 if(fs.existsSync(output))throw Error('Output exists; inspect before rewriting');
 const approval=JSON.parse(fs.readFileSync(path.join(dir,'tight_manifest.json'),'utf8'));
 const name=process.argv[6];
 const zip=await loadDrpZip(input);
 for(const entry of Object.keys(zip.files).filter(n=>n.endsWith('.xml'))){
  let xml=await zip.file(entry).async('string');
  xml=xml.replaceAll(approval.timeline_name,name);
  if(entry.startsWith('SeqContainer/')){
   const match=xml.match(/<SubtitleTrackVec>([\s\S]*?)<\/SubtitleTrackVec>/);
   if(match){if(/<Sm2TiGenerator/.test(match[1]))throw Error('Existing captions would be overwritten');xml=xml.replace(match[0],'<SubtitleTrackVec/>');}
  }
  zip.file(entry,xml);
 }
 const buffer=await zip.generateAsync({type:'nodebuffer'});
 const subtitles=parseSrt(fs.readFileSync(subtitle,'utf8'),30000/1001).map(c=>({...c,startFrame:c.startFrame+108000}));
 const result=await placeSubtitles(buffer,{subtitles});fs.writeFileSync(output,result.buffer);
 fs.writeFileSync(path.join(dir,'subtitle_drt_spec.json'),JSON.stringify({timeline_name:name,origin:108000,subtitles},null,2));
 console.log(JSON.stringify({path:output,timeline_name:name,cues:result.count}));
})();
