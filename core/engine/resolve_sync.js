/* Save only the confirmed editor revision; never retry a mutation automatically. */
const ResolveSync={attach({getData,onLock,pause,onConflict}){
 const button=document.getElementById('saveResolve'),label=document.getElementById('resolveStatus');
 let starting=false,running=false,serial=0;
 function draw(job){
  running=!!job.running;onLock(starting||running);
  button.textContent=starting?'正在提交…':running?'正在保存到达芬奇…':'保存到达芬奇';
  if(running){const elapsed=Math.max(0,Math.floor((Date.now()-Date.parse(job.started_at))/1000));label.textContent=(job.message||'正在同步…')+(Number.isFinite(elapsed)?`（${elapsed} 秒）`:'')}
  else if(job.status==='complete')label.textContent=job.current===false?'文字已更新，请再次保存到达芬奇。上次时间线：'+job.timeline:'已保存并核对：'+job.timeline+'；旧版保留。';
  else if(job.status==='failed')label.textContent='保存失败：'+job.error+'。请检查达芬奇已打开后重试；日志：'+job.log;
 }
 async function poll(){const version=++serial;try{const r=await fetch('/resolve-status');if(!r.ok)throw Error('无法读取同步状态');const job=await r.json();if(version===serial&&!starting)draw(job)}catch(e){if(version===serial)label.textContent='无法读取达芬奇同步状态，请刷新核对；不会自动重复提交。'}}
 button.onclick=async()=>{
  if(starting||running||button.disabled||!getData()?.alignment)return;
  starting=true;++serial;pause();onLock(true);button.textContent='正在提交…';label.textContent='正在提交当前已保存的文字选择…';
  try{const r=await fetch('/save-to-resolve',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({state_revision:getData().state_revision})});const job=await r.json();if(!r.ok){if(job.reload)await onConflict();throw Error(job.error||'提交失败')}starting=false;draw(job)}
  catch(e){starting=false;label.textContent=e.message;onLock(running);await poll()}
 };
 poll();setInterval(poll,2000);
}};
