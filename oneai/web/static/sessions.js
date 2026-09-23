'use strict';
// Shared by browser, iPhone and Mac WKWebView. No runtime-specific UI protocol.
const sessionView = {selected:localStorage.getItem('oneai.session.selected'), items:[], hosts:[], cursor:0, loading:false, rendered:null, renderSeq:0};
const sessionLabels = {starting:'正在启动',idle:'等待消息',queued:'等待执行主机',running:'正在处理',stopping:'正在停止',unknown:'需要核对状态',failed:'启动失败',closing:'正在关闭',closed:'已关闭',observing:'桌面会话 · 只读同步'};
const sessionPage = document.querySelector('#sessions-page');
function sessionNotice(message){document.querySelector('#session-notice').textContent=message;}
async function sessionCommand(command){
  const key='oneai.session.pending';
  const pending=localStorage.getItem(key);
  if(pending && JSON.parse(pending).id!==command.id)throw new Error('上一条指令尚未确认，请先重试。');
  localStorage.setItem(key,JSON.stringify(command));
  try{
    const result=await api('/agent/commands',{method:'POST',body:JSON.stringify(command)});
    localStorage.removeItem(key);
    return result;
  }catch(error){
    if(error.status && error.status<500)localStorage.removeItem(key);
    throw error;
  }finally{document.querySelector('#session-retry').hidden=!localStorage.getItem(key);}
}
async function loadSessions(){
  if(sessionView.loading)return;
  sessionView.loading=true;
  try{
    const [hosts,sessions]=await Promise.all([api('/agent/hosts'),api('/agent/sessions')]);
    sessionView.hosts=hosts.items;sessionView.items=sessions.items;
    if(typeof loadSessionCatalog==='function')await loadSessionCatalog();
    const hostSelect=document.querySelector('#session-host');
    const before=hostSelect.value;
    const hostOptions=hosts.items.filter(h=>h.capabilities.providers.length),signature=JSON.stringify(hostOptions.map(h=>[h.id,h.name,h.online]));
    if(hostSelect.dataset.options!==signature){hostSelect.dataset.options=signature;hostSelect.replaceChildren();for(const host of hostOptions){const o=text('option',host.name+(host.online?' · 在线':' · 离线'));o.value=host.id;hostSelect.append(o);}}
    if(hosts.items.some(h=>h.id===before))hostSelect.value=before;
    refreshSessionCapabilities();
    const list=document.querySelector('#session-list');list.replaceChildren();
    for(const row of sessions.items){
      const button=text('button','','task-row'+(row.id===sessionView.selected?' selected':''));
      const host=hosts.items.find(h=>h.id===row.host_id);
      button.append(text('h3',row.title),text('small',row.provider+' · '+(host?.name||'主机')+' · '+(host?.online?sessionLabels[row.state]:'执行主机离线')));
      button.onclick=()=>selectSession(row.id);list.append(button);
    }
    if(!sessions.items.length)list.append(text('p','选择执行主机，开始一段新会话。','hint'));
    const noHosts='尚未连接执行主机。请先在服务器注册主机，再运行 oneAI 会话服务。';
    if(!hosts.items.length)sessionNotice(noHosts);
    else if(document.querySelector('#session-notice').textContent===noHosts)sessionNotice('执行主机已连接，可以选择会话继续。');
    if(sessionView.selected&&!sessions.items.some(x=>x.id===sessionView.selected)){sessionView.selected=null;sessionView.rendered=null;localStorage.removeItem('oneai.session.selected');document.querySelector('#session-title').textContent='选择会话';document.querySelector('#session-transcript').replaceChildren();document.querySelector('#session-send').disabled=true;document.querySelector('#session-stop').disabled=true;document.querySelector('#session-close').disabled=true;document.querySelector('#session-controls')?.setAttribute('hidden','');}
    if(sessionView.selected)await renderSession();
    document.querySelector('#session-retry').hidden=!localStorage.getItem('oneai.session.pending');
  }catch(error){sessionNotice(error.message==='sessions_not_enabled'?'会话控制尚未启用。管理员启用并连接执行主机后即可使用。':error.message);}
  finally{sessionView.loading=false;}
}
function refreshSessionCapabilities(){
  const host=sessionView.hosts.find(h=>h.id===document.querySelector('#session-host').value);
  for(const [id,values] of [['session-provider',host?.capabilities.providers||[]],['session-workspace',host?.capabilities.workspaces||[]]]){
    const select=document.querySelector('#'+id),before=select.value,signature=JSON.stringify(values);
    if(select.dataset.options!==signature){select.dataset.options=signature;select.replaceChildren();for(const value of values){const o=text('option',value);o.value=value;select.append(o);}}
    if(values.includes(before))select.value=before;
  }
  document.querySelector('#session-create').disabled=!host||!host.capabilities.providers.length;
  if(typeof refreshSessionModelOptions==='function')refreshSessionModelOptions();
}
function selectSession(id){
  sessionView.selected=id;sessionView.cursor=0;sessionView.rendered=null;
  localStorage.setItem('oneai.session.selected',id);
  document.querySelector('#session-transcript').replaceChildren();
  document.querySelector('#session-message').value=localStorage.getItem('oneai.session.draft.'+id)||'';
  // Switch visible identity immediately, even during a background refresh.
  renderSession().catch(error=>sessionNotice(error.message));
}
async function renderSession(){
  const row=sessionView.items.find(s=>s.id===sessionView.selected);if(!row)return;
  sessionPage.classList.add('has-session');
  const host=sessionView.hosts.find(h=>h.id===row.host_id);
  document.querySelector('#session-title').textContent=row.title;
  document.querySelector('#session-state').textContent=(host?.online?sessionLabels[row.state]:'执行主机离线')+' · '+row.provider;
  document.querySelector('#session-send').disabled=row.mode==='observe'||row.state!=='idle'||!host?.online;
  document.querySelector('#session-message').disabled=row.mode==='observe';
  document.querySelector('#session-message').placeholder=row.mode==='observe'?'当前绑定为只读。继续操作请回到原桌面会话。':'消息草稿会保存在当前设备。';
  document.querySelector('#session-stop').disabled=row.mode==='observe'||!['running','queued','unknown','stopping'].includes(row.state);
  document.querySelector('#session-close').disabled=row.mode==='observe'||!['idle','closed'].includes(row.state);
  document.querySelector('#session-close').textContent=row.state==='closed'?'恢复会话':'关闭会话';
  if(sessionView.rendered!==row.id){
    sessionView.cursor=0;sessionView.rendered=row.id;
    document.querySelector('#session-transcript').replaceChildren();
    document.querySelector('#session-message').value=localStorage.getItem('oneai.session.draft.'+row.id)||'';
  }
  if(typeof renderSessionControls==='function')renderSessionControls(row);
  const id=row.id,sequence=++sessionView.renderSeq;
  const result=await api('/agent/sessions/'+id+'/events?after='+sessionView.cursor);
  if(sessionView.selected!==id||sessionView.renderSeq!==sequence)return;
  const box=document.querySelector('#session-transcript');
  const atBottom=box.scrollTop+box.clientHeight>=box.scrollHeight-70;
  for(const event of result.items){
    const data=event.payload;
    if(event.kind==='text'){
      let block=box.lastElementChild;
      if(!block?.classList.contains('assistant-text')){block=text('pre','','assistant-text');box.append(block);}
      block.textContent+=data.text||'';
    }else if(event.kind==='command'&&data.action==='prompt'){
      box.append(text('pre',data.text,'user-text'));
    }else if(event.kind==='message'){
      let block=[...box.querySelectorAll('[data-item]')].find(el=>el.dataset.item===data.item_id);
      if(!block){block=text('pre','',data.role==='user'?'user-text':'assistant-text');block.dataset.item=data.item_id;box.append(block);}
      block.textContent=data.text||'';
    }else if(event.kind==='tool'){
      const detail=document.createElement('details');detail.append(text('summary',data.type||'工具'),text('pre',data.text||''));box.append(detail);
    }else if(event.kind==='error')box.append(text('p',data.message,'error'));
    else if(event.kind==='status'&&data.message&&data.state!=='observing')box.append(text('p',data.message,'hint'));
  }
  sessionView.cursor=result.cursor;
  if(atBottom)box.scrollTop=box.scrollHeight;
}
document.querySelector('#session-host').onchange=refreshSessionCapabilities;
document.querySelector('#session-new-form').onsubmit=async event=>{
  event.preventDefault();const button=document.querySelector('#session-create');button.disabled=true;
  try{const result=await sessionCommand({id:crypto.randomUUID(),action:'create',title:document.querySelector('#session-new-title').value||'新会话',host_id:document.querySelector('#session-host').value,provider:document.querySelector('#session-provider').value,workspace:document.querySelector('#session-workspace').value,...(typeof newSessionSettings==='function'?{settings:newSessionSettings()}: {})});sessionNotice('会话已保存，等待执行主机。');selectSession(result.session_id);}catch(error){sessionNotice(error.message);}finally{button.disabled=false;}
};
document.querySelector('#session-message').oninput=event=>{if(sessionView.selected)localStorage.setItem('oneai.session.draft.'+sessionView.selected,event.target.value);};
document.querySelector('#session-compose').onsubmit=async event=>{
  event.preventDefault();const row=sessionView.items.find(s=>s.id===sessionView.selected);if(!row)return;
  const button=document.querySelector('#session-send');button.disabled=true;
  try{await sessionCommand({id:crypto.randomUUID(),action:'prompt',session_id:row.id,revision:row.revision,text:document.querySelector('#session-message').value});localStorage.removeItem('oneai.session.draft.'+row.id);if(sessionView.selected===row.id)document.querySelector('#session-message').value='';sessionNotice('消息已保存。');await loadSessions();}catch(error){sessionNotice(error.message==='stale_session'?'另一台设备已经更新了会话，请刷新核对后再发送。':error.message);await loadSessions();}
};
for(const [id,action] of [['session-stop','interrupt'],['session-close','close']])document.querySelector('#'+id).onclick=async()=>{
  const row=sessionView.items.find(s=>s.id===sessionView.selected);if(!row)return;
  try{await sessionCommand({id:crypto.randomUUID(),action:action==='close'&&row.state==='closed'?'resume':action,session_id:row.id,revision:row.revision});sessionNotice('指令已保存。');await loadSessions();}catch(error){sessionNotice(error.message);}
};
document.querySelector('#session-retry').onclick=async()=>{try{const raw=localStorage.getItem('oneai.session.pending');if(!raw)return;const command=JSON.parse(raw);const result=await sessionCommand(command);if(command.action==='create')selectSession(result.session_id);sessionNotice('已确认指令状态：'+result.state);await loadSessions();}catch(error){sessionNotice(error.message);}};
setInterval(()=>{if(state.page==='sessions'&&!document.hidden&&!document.querySelector('#workspace').hidden)loadSessions();},1500);
