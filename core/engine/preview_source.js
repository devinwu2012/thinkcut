// Keep source time continuous while loading only the requested proxy segment.
(function(root){root.PreviewSource={attach(el){
 if(!el.dataset.segmentSeconds)return el;
 const span=Number(el.dataset.segmentSeconds),duration=Number(el.dataset.sourceDuration);
 let part=-1,target=0,loading=null,wantsPlay=false,generation=0;
 function seek(sec){
  target=Math.max(0,Math.min(duration-.001,Number(sec)||0));
  const index=Math.floor(target/span);
  if(index===part){if(!loading)el.currentTime=target-part*span;return;}
  part=index;const version=++generation,rate=el.playbackRate;
  loading=new Promise((resolve,reject)=>{
   const ready=()=>{cleanup();if(version!==generation){resolve();return;}el.playbackRate=rate;el.currentTime=target-part*span;loading=null;resolve();
    // load() pauses the native element; an edit jump must retain play intent.
    if(wantsPlay)play().catch(()=>{});
   };
   const failed=()=>{cleanup();if(version===generation){loading=null;wantsPlay=false;}reject(Error('预览片段读取失败，请检查审核服务后重试。'));};
   function cleanup(){el.removeEventListener('loadedmetadata',ready);el.removeEventListener('error',failed);}
   el.addEventListener('loadedmetadata',ready);el.addEventListener('error',failed);
   el.src='/source-part/'+index+'.mp4';el.load();
  });
  loading.catch(()=>{}); // A paused seek may fail before the user asks to play.
 }
 async function play(){wantsPlay=true;while(loading)await loading;if(wantsPlay){const version=generation;try{return await el.play();}catch(error){if(version===generation)wantsPlay=false;throw error;}}}
 function pause(){wantsPlay=false;el.pause();}
 // A native pause caused by swapping src is not a user pause.
 el.addEventListener('pause',event=>{if(wantsPlay)event.stopImmediatePropagation();},true);
 el.addEventListener('ended',event=>{
  if(wantsPlay&&(part+1)*span<duration){event.stopImmediatePropagation();seek((part+1)*span);play().catch(()=>{});}
 },true);
 const proxy=new Proxy(el,{
  get(_,key){if(key==='currentTime')return loading?target:part*span+el.currentTime;if(key==='duration')return duration;
   if(key==='paused')return !wantsPlay;if(key==='seeking')return !!loading||el.seeking;
   if(key==='readyState')return loading?0:el.readyState;if(key==='play')return play;if(key==='pause')return pause;
   const value=Reflect.get(el,key,el);return typeof value==='function'?value.bind(el):value;},
  set(_,key,value){if(key==='currentTime'){seek(value);return true;}return Reflect.set(el,key,value,el);}
 });
 seek(0);return proxy;
}};})(typeof globalThis!=='undefined'?globalThis:this);
