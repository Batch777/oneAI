/* Explicit update acceptance: never reload while someone is editing. */
(()=>{
 const loaded=document.querySelector('meta[name="oneai-release"]')?.content;
 const known=/^[a-f0-9]{40}$/.test(loaded||'');
 const row=document.createElement('div');row.className='release-status';
 const label=document.createElement('small');label.textContent=known?'界面版本 '+loaded.slice(0,7):'开发版本';
 const button=document.createElement('button');button.className='text-button';button.hidden=true;
 button.textContent='有新版本 · 重新加载';button.classList?.add('release-update');
 row.append(label);const devices=document.querySelector('#updates-panel');
 if(devices?.append)devices.append(row);else document.body.append(row);
 row.append(button);
 const checkButton=document.createElement('button');checkButton.className='secondary';checkButton.textContent='检查更新';
 const feedback=document.createElement('p');feedback.className='hint';feedback.setAttribute?.('role','status');
 row.append(checkButton,feedback);
 let pending;
 async function check(){
  if(document.hidden)return;
  if(pending)return pending;
  pending=(async()=>{try{
   const response=await fetch('/api/version',{cache:'no-store'});
   if(!response.ok)throw new Error('version_unavailable');
   const value=await response.json();
   const valid=/^[a-f0-9]{40}$/.test(value.commit||'');
   button.hidden=!valid||value.commit===loaded;
   feedback.textContent=!valid?'服务未提供正式版本号。':button.hidden?'当前已是最新界面。':'新版已部署，可保存草稿后重新加载。';
  }catch{feedback.textContent='暂时无法检查更新，请重试。';}finally{pending=null;}})();return pending;
 }
 checkButton.addEventListener('click',async()=>{checkButton.disabled=true;feedback.textContent='正在检查…';try{await check();}finally{checkButton.disabled=false;}});
 button.addEventListener('click',()=>{
  if(confirm('重新加载会关闭当前页面。请先保存正在编辑的草稿，确认现在更新？'))location.reload();
 });
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)check();});
 document.querySelector('#refresh')?.addEventListener('click',check);
 window.addEventListener('online',check);check();
})();
