/* Explicit update acceptance: never reload while someone is editing. */
(()=>{
 const loaded=document.querySelector('meta[name="oneai-release"]')?.content;
 if(!/^[a-f0-9]{40}$/.test(loaded||''))return;
 const row=document.createElement('div');row.className='release-status';
 const label=document.createElement('small');label.textContent='界面版本 '+loaded.slice(0,7);
 const button=document.createElement('button');button.className='text-button';button.hidden=true;
 button.textContent='有新版本 · 重新加载';row.append(label,button);document.body.append(row);
 let pending;
 async function check(){
  if(document.hidden)return;
  if(pending)return pending;
  pending=(async()=>{try{
   const response=await fetch('/api/version',{cache:'no-store'});
   if(!response.ok)return;
   const value=await response.json();
   button.hidden=!/^[a-f0-9]{40}$/.test(value.commit||'')||value.commit===loaded;
  }catch{}finally{pending=null;}})();return pending;
 }
 button.addEventListener('click',()=>{
  if(confirm('重新加载会关闭当前页面。请先保存正在编辑的草稿，确认现在更新？'))location.reload();
 });
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)check();});
 document.querySelector('#refresh')?.addEventListener('click',check);
 window.addEventListener('online',check);check();
})();
