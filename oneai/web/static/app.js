'use strict';
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const state={csrf:'',device:'',filter:'needs_review',query:'',offset:0,items:[],task:null,editing:false,page:'tasks',online:false,mailView:'inbox'};
const labels={pending:'正在准备',needs_review:'待核对',reviewed:'已核对',completed:'已归档'};
let timer,searchTimer,toastTimer;
function text(tag,value,cls){const el=document.createElement(tag);el.textContent=value;if(cls)el.className=cls;return el;}
function announce(message){const box=$('#toast');box.textContent=message;box.hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>box.hidden=true,4200);}
function alert(message){const b=$('#global-alert');b.replaceChildren(text('span',message));b.hidden=!message;}
function pendingAlert(){const raw=localStorage.getItem('oneai.pending');if(!raw)return;alert('上次提交尚未确认，内容已保存在这台设备。');const b=text('button','重试提交','text-button');b.onclick=async()=>{try{const c=JSON.parse(raw);const r=await sendCommand(c,true);if(c.action==='create'){localStorage.removeItem('oneai.new');$('#create-form').reset();$('#create-dialog').close();await selectTask(r.task_id);}if(c.action==='revise'){localStorage.removeItem('oneai.draft.'+c.task_id);}announce('已确认提交');await loadTasks();if(state.task)await selectTask(state.task.id);}catch(e){announce(e.message);}};$('#global-alert').append(b);}
async function api(path,options={}){const {quiet=false,...fetchOptions}=options;let response;try{response=await fetch('/api'+path,{...fetchOptions,headers:{'Content-Type':'application/json',...(options.method?{'X-OneAI-CSRF':state.csrf}:{}),...options.headers},credentials:'same-origin'});}catch(error){if(error.name==='AbortError')throw error;if(!quiet){state.online=false;connection(false);}throw new Error('暂时无法连接云端，内容已保留。');}let value;try{value=await response.json();}catch{throw new Error('服务返回异常，请稍后重试。');}if(!response.ok){const messages={login_required:'请重新连接这台设备。',invalid_code:'配对码无效、已使用或已过期。',too_many_attempts:'尝试次数较多，请十分钟后重试。',origin_rejected:'请从工作空间的原地址打开。',csrf_rejected:'登录状态已变化，请刷新页面。',internal_error:'服务暂时出错，请稍后重试。'};const trace=response.headers.get('X-Request-ID');const error=new Error((messages[value.detail]||value.detail||'操作失败')+(response.status>=500&&trace?'（请求编号 '+trace+'）':''));error.status=response.status;if(response.status===401&&path!='/login')showLogin();throw error;}return value;}
function connection(online){state.online=online;$('#connection-dot').classList.toggle('online',online);$('#connection-label').textContent=online?'云端已连接':'暂时离线';$('#connection-detail').textContent=online?'任务持续处理中':'恢复网络后可继续';}
function showLogin(){clearTaskLists();state.items=[];$('#task-list').replaceChildren();if(typeof mailDisplayCache!=='undefined')mailDisplayCache.clear();selectionSequence++;selectionRequest?.abort();state.task=null;$('#workspace').hidden=true;$('#login-screen').hidden=false;clearInterval(timer);}
async function start(){try{
 const key=taskListKey(),params=new URLSearchParams({q:state.query,task_status:state.filter,mail_view:state.mailView});
 let bundle;try{bundle=await api('/bootstrap?'+params);}catch(e){if(e.status!==404)throw e;const session=await api('/session');bundle={session};}
 const session=bundle.session;state.csrf=session.csrf;state.device=session.device_id;$('#login-screen').hidden=true;$('#workspace').hidden=false;connection(true);
 if(bundle.tasks){rememberTaskList(key,bundle.tasks);renderTaskList(bundle.tasks);renderedTaskListKey=key;renderStatus(bundle.status);loadMailAlerts().catch(()=>{});scheduleTaskPrefetch();}
 else await Promise.all([loadTasks(),status()]);
 pendingAlert();clearInterval(timer);timer=setInterval(poll,15000);
 }catch(e){if(e.status===401)showLogin();else{$('#login-error').textContent=e.message;showLogin();}}}
const pairDigits=$$('.pair-digit');
function syncPair(){ $('#pair-code').value=pairDigits.map(x=>x.value).join(''); }
pairDigits.forEach((input,index)=>{
 const distribute=value=>{const digits=value.normalize('NFKC').replace(/[^0-9]/g,'').slice(0,4);if(!digits){input.value='';syncPair();return;}const start=digits.length===4?0:index;if(digits.length===1)pairDigits[start].value=digits;else for(let n=start;n<4;n++)pairDigits[n].value=digits[n-start]||'';syncPair();pairDigits[Math.min(start+digits.length,3)].focus();};
 input.addEventListener('input',()=>distribute(input.value));
 input.addEventListener('paste',e=>{e.preventDefault();distribute(e.clipboardData.getData('text'));});
 input.addEventListener('focus',()=>input.select());
 input.addEventListener('keydown',e=>{if(e.key==='Backspace'&&!input.value&&index>0){e.preventDefault();pairDigits[index-1].value='';pairDigits[index-1].focus();syncPair();}if(e.key==='ArrowLeft'&&index>0){e.preventDefault();pairDigits[index-1].focus();}if(e.key==='ArrowRight'&&index<3){e.preventDefault();pairDigits[index+1].focus();}});
});
$('#login-form').onsubmit=async e=>{e.preventDefault();const button=e.target.querySelector('button');button.disabled=true;$('#login-error').textContent='';try{const s=await api('/login',{method:'POST',body:JSON.stringify({code:$('#pair-code').value,name:$('#device-name').value})});state.csrf=s.csrf;$('#pair-code').value='';pairDigits.forEach(x=>x.value='');await start();}catch(error){$('#login-error').textContent=error.message;}finally{button.disabled=false;}};
function renderStatus(s){$('#review-count').textContent=s.counts.needs_review||0;$('#pending-count').textContent=s.counts.pending||0;connection(true);if(s.worker&&s.time-s.worker.at>180)alert('后台最近没有更新。已保存的任务仍在，可稍后刷新查看。');else if(!localStorage.getItem('oneai.pending'))alert('');}
async function status(){loadMailAlerts().catch(()=>{});const s=await api('/status');renderStatus(s);return s;}
// Private, bounded in-memory cache; never persists mailbox lists across logins.
const taskLists=new Map(),taskListRequests=new Map();
let taskListSequence=0,taskListGeneration=0,renderedTaskListKey='';
const TASK_LIST_TTL=15000,TASK_LIST_LIMIT=20;
function clearTaskLists(){clearTimeout(taskPrefetchTimer);taskPrefetchController?.abort();taskPrefetchAttempts=0;taskPrefetchBytes=0;taskListGeneration++;taskListSequence++;taskLists.clear();taskListRequests.clear();renderedTaskListKey='';}
function taskListKey(offset=0){return new URLSearchParams({status:state.filter,q:state.query,offset,mail_view:state.mailView}).toString();}
function rememberTaskList(key,data){taskLists.delete(key);taskLists.set(key,{data,at:Date.now()});while(taskLists.size>TASK_LIST_LIMIT)taskLists.delete(taskLists.keys().next().value);}
async function fetchTaskList(key,options={}){
 if(taskListRequests.has(key))return taskListRequests.get(key);
 const generation=taskListGeneration;
 const request=api('/tasks?'+key,options).then(data=>{
  if(generation===taskListGeneration)rememberTaskList(key,data);
  return data;
 }).finally(()=>{if(taskListRequests.get(key)===request)taskListRequests.delete(key);});
 taskListRequests.set(key,request);return request;
}
function taskRowSignature(item){return JSON.stringify([item.id,item.title,item.status,item.revision,item.updated,item.mail_received,item.origin]);}
function renderTaskList(data,base=[]){
 const list=$('#task-list'),items=base.concat(data.items),existing=new Map([...list.children].filter(x=>x.dataset?.taskId).map(x=>[x.dataset.taskId,x]));
 if(!items.length){if(state.items.length||!list.children.length)list.replaceChildren(text('div',state.query?'没有找到匹配任务。':'这里暂时没有任务。\n新的事项准备好后会出现在这里。','empty-list'));}
 else{
  for(const child of [...list.children])if(!child.dataset?.taskId)child.remove();
  for(let i=0;i<items.length;i++){
   const item=items[i],signature=taskRowSignature(item)+'|'+state.mailView;
   let row=existing.get(item.id);
   if(!row){row=document.createElement('button');row.dataset.taskId=item.id;row.onclick=()=>selectTask(item.id);}
   if(row.dataset.signature!==signature){const meta=text('div','','row-meta');meta.append(text('span',state.mailView==='filtered'?'已筛选':labels[item.status],`pill ${item.status}`),text('time',taskDisplayDate(item)));row.replaceChildren(text('h3',item.title),meta);row.dataset.signature=signature;}
   const cls='task-row'+(state.task?.id===item.id?' selected':'');if(row.className!==cls)row.className=cls;
   if(list.children[i]!==row)list.insertBefore(row,list.children[i]||null);existing.delete(item.id);
  }
  for(const row of existing.values())row.remove();
 }
 state.items=items;state.offset=items.length;$('#more-tasks').hidden=items.length>=data.total;
}
let taskPrefetchTimer,taskPrefetchController,taskPrefetchKey='',taskPrefetchAttempts=0,taskPrefetchBytes=0;
function scheduleTaskPrefetch(){
 clearTimeout(taskPrefetchTimer);
 if(state.query||document.hidden||state.page!=='tasks'||navigator.connection?.saveData||/2g/.test(navigator.connection?.effectiveType||'')||taskPrefetchAttempts>=4||taskPrefetchBytes>=262144)return;
 taskPrefetchTimer=setTimeout(prefetchTaskList,500);
}
async function prefetchTaskList(){
 if(taskPrefetchController||state.query||document.hidden||state.page!=='tasks'||!state.csrf)return;
 const generation=taskListGeneration;
 const candidates=[['needs_review','inbox'],['','inbox'],['pending','inbox'],['completed','inbox'],['','filtered']];
 const key=candidates.map(([status,mail_view])=>new URLSearchParams({status,q:'',offset:0,mail_view}).toString()).find(k=>k!==taskListKey()&&!taskListRequests.has(k)&&(!taskLists.has(k)||Date.now()-taskLists.get(k).at>=TASK_LIST_TTL));
 if(!key)return;
 const controller=new AbortController();taskPrefetchController=controller;taskPrefetchKey=key;taskPrefetchAttempts++;
 const timeout=setTimeout(()=>controller.abort(),4000);
 try{const data=await fetchTaskList(key,{signal:controller.signal,quiet:true});if(generation===taskListGeneration)taskPrefetchBytes+=new TextEncoder().encode(JSON.stringify(data)).length;}
 catch{}finally{clearTimeout(timeout);if(taskPrefetchController===controller){taskPrefetchController=null;taskPrefetchKey='';}if(generation===taskListGeneration)scheduleTaskPrefetch();}
}

async function loadTasks(append=false,force=true){
 clearTimeout(taskPrefetchTimer);
 if(taskPrefetchController&&taskPrefetchKey!==taskListKey(append?state.offset:0)){taskListRequests.delete(taskPrefetchKey);taskPrefetchController.abort();}
 const sequence=++taskListSequence,generation=taskListGeneration;
 const baseKey=taskListKey(),refreshLoaded=!append&&force&&renderedTaskListKey===baseKey&&state.items.length>50;
 const key=taskListKey(append?state.offset:0)+(refreshLoaded?'&limit='+Math.min(state.items.length,500):'');
 const base=append?state.items.slice():[],cached=taskLists.get(key);
 const notice=$('#task-list-status'),more=$('#more-tasks');
 const current=()=>sequence===taskListSequence&&generation===taskListGeneration&&baseKey===taskListKey();
 if(append&&renderedTaskListKey!==baseKey)return;
 if(cached&&!append&&!(force&&renderedTaskListKey===baseKey)){renderTaskList(cached.data);renderedTaskListKey=baseKey;}
 else if(!append&&renderedTaskListKey!==baseKey){state.items=[];state.offset=0;$('#task-list').replaceChildren();more.hidden=true;}
 if(cached&&!append&&!force&&Date.now()-cached.at<TASK_LIST_TTL){notice.textContent='';more.disabled=false;scheduleTaskPrefetch();return;}
 notice.textContent=cached?'显示已缓存的列表，正在更新…':append?'正在加载更多…':'正在加载…';more.disabled=true;
 try{const data=await fetchTaskList(key);if(!current())return;renderTaskList(data,base);renderedTaskListKey=baseKey;notice.textContent='';}
 catch(error){if(!current())return;notice.textContent=cached?'暂时无法更新，当前为缓存列表。':error.message;}
 finally{if(current()){more.disabled=false;scheduleTaskPrefetch();}}
}
function disclosure(title,value,parent){const d=text('details','','detail-section');d.append(text('summary',title));const body=text('div','','section-content');if(typeof value==='string')body.append(text('pre',value,'plain-text'));else value(body);d.append(body);parent.append(d);return d;}
function renderBody(value,parent){for(const line of value.split('\n')){if(/^##? /.test(line))parent.append(text('h3',line.replace(/^#+ /,'')));else if(line.startsWith('> '))parent.append(text('blockquote',line.slice(2)));else if(line.trim())parent.append(text('p',line));}}
function taskSections(value){const result={main:[],material:[]};let section=[],kind='main';const flush=()=>{result[kind]?.push(...section);section=[];};for(const line of value.split('\n')){if(/^##? /.test(line)){flush();const title=line.replace(/^#+ /,'');kind=title==='回复草稿模板'?'legacy':/原始|检索|用户身份|user[-_ ]?scope|上下文|规则/.test(title)?'material':'main';}section.push(line);}flush();return {main:result.main.join('\n'),material:result.material.join('\n')};}
let selectionSequence=0,selectionRequest,selectedTaskId=null;
function showTaskLoading(id){const box=$('#task-detail');box.replaceChildren();box.className='task-detail';const back=text('button','← 返回任务列表','back-button');back.onclick=()=>{$('.task-layout').classList.remove('open');};const item=state.items.find(x=>x.id===id);box.append(back,text('h2',item?.title||'任务详情'),text('p','正在读取…','hint'));box.setAttribute('aria-busy','true');$('.task-layout').classList.add('open');}
async function selectTask(id){
 if(state.editing&&state.task?.id!==id&&!confirm('草稿已保存在此设备。先切换到另一项任务？'))return;
 const sequence=++selectionSequence;selectedTaskId=id;selectionRequest?.abort();const controller=new AbortController();selectionRequest=controller;const timeout=setTimeout(()=>controller.abort(),12000);
 state.task=null;state.editing=false;showTaskLoading(id);
 $$('.task-row').forEach((e,i)=>e.classList.toggle('selected',state.items[i]?.id===id));
 try{const task=await api('/tasks/'+id,{signal:controller.signal});if(sequence!==selectionSequence)return;state.task=task;renderTask();}
 catch(error){if(sequence!==selectionSequence)return;const box=$('#task-detail');box.replaceChildren(text('h2','暂时未能打开任务'),text('p',error.name==='AbortError'?'读取超时，请重试。':error.message));const retry=text('button','重试打开','primary');retry.onclick=()=>selectTask(id);const back=text('button','返回任务列表','secondary');back.onclick=()=>$('.task-layout').classList.remove('open');box.append(retry,back);}
 finally{clearTimeout(timeout);if(sequence===selectionSequence)$('#task-detail').removeAttribute('aria-busy');}
}
function renderTask(){
 const t=state.task;if(!t)return;const isMail=t.origin?.startsWith('mail:');const box=$('#task-detail');box.className='task-detail';box.replaceChildren();
 const back=text('button','← 返回任务列表','back-button');back.onclick=()=>$('.task-layout').classList.remove('open');box.append(back);
 if(t.verification&&(t.verification.code||t.verification.links.length)){const hints=text('section','','verification-hints');hints.append(text('p','请核对发件人与用途；历史验证码可能已失效。','hint'));renderVerification(t.verification,hints);box.append(hints);}
 const top=text('div','','detail-top');top.append(text('span',t.mail_classification?.filtered?'已筛选':labels[t.status],`pill ${t.status}`),text('small','版本 '+t.revision));box.append(top,text('h2',t.title),text('p','一级 · 处理事项','level-label'));

 if(isMail)renderMailView(t,box);
 const tools=text('div','','detail-tools');const reviewable=['needs_review','reviewed'].includes(t.status);
 if(t.mail_classification?.filtered){const restore=text('button','恢复为待处理事项','primary');restore.onclick=()=>act('restore_mail');tools.append(restore);}
 if(reviewable){if(!t.reply_generated){const generate=text('button',isMail?'需要回复':'生成回复模板','primary');generate.onclick=()=>{if(confirm('生成待编辑的回复模板？生成后需重新核对，不会发送邮件。'))act('generate_reply');};tools.append(generate);}const edit=text('button','补充信息 / 编辑草稿','secondary');edit.onclick=editDraft;const approve=text('button',t.status==='reviewed'?'✓ 已核对':'确认草稿内容','secondary');approve.disabled=t.status==='reviewed';approve.onclick=()=>act('approve');const complete=text('button','归档事项','secondary');complete.onclick=()=>{if(confirm('将这项任务归档？归档不会发送邮件，也不代表事项已经办结。'))act('complete');};if(!isMail||t.reply_generated)tools.append(edit,approve);tools.append(complete);}
 box.append(tools);const draft=text('div','','draft-body');const sections=taskSections(t.result);if(!isMail||t.reply_generated)renderBody(sections.main||'无需自动执行。请核对事项后选择上方操作。',draft);box.append(draft);
 disclosure(isMail?'二级 · 处理建议与检索依据':'二级 · 原文与检索依据',body=>{if(isMail){if(!t.reply_generated)renderBody(sections.main,body);renderBody(sections.material.replace(/## 原始内容[^\n]*[\s\S]*?(?=\n## |$)/g,''),body);}else if(sections.material)renderBody(sections.material,body);else{body.append(text('h3','原始内容'),text('pre',t.input,'plain-text'));}if(t.sources.length){body.append(text('h3','引用来源'));for(const source of t.sources){const button=text('button',source,'source-link');button.onclick=()=>showSource(source);body.append(button);}}},box);
 const context=disclosure('三级 · 身份、范围与分类规则',body=>{body.append(text('p','展开后读取当前身份和规则。','hint'));},box);let loaded=false,loading=false;
 const loadContext=async()=>{if(!context.open||loaded||loading)return;loading=true;const body=context.querySelector('.section-content');body.replaceChildren(text('p','正在读取当前规则…','hint'));try{const view=await api('/tasks/'+t.id+'/context');body.replaceChildren();for(const [title,value] of [['用户身份（当前资料）',view.identity.map(e=>e.text).join('\n\n')||'尚未填写身份资料。'],['User scope · 当前使用范围',view.scope],['当前规则',view.rules.map(e=>e.path+'\n'+e.text).join('\n\n')||'暂无规则。']]){body.append(text('h3',title),text('pre',value,'plain-text'));}if(t.mail_classification){const m=t.mail_classification;body.append(text('h3','邮件分类'),text('p',m.category+' · 策略分数 '+Math.round(m.confidence*100)+'% · '+m.source),text('p',m.reason));if(m.evidence){body.append(text('p','模型：'+m.evidence.model+' · 模型置信度 '+Math.round(m.evidence.model_confidence*100)+'% · 需处理概率 '+Math.round(m.evidence.attention_probability*100)+'%'),text('p','策略分数不等于实际分类准确率。','hint'));}}loaded=true;}catch(error){body.replaceChildren(text('p',error.message));const retry=text('button','重试读取规则','secondary');retry.onclick=loadContext;body.append(retry);}finally{loading=false;}};
 context.addEventListener('toggle',loadContext);
 if(localStorage.getItem('oneai.draft.'+t.id)&&reviewable){const restore=text('button','继续编辑本机未提交草稿','text-button');restore.onclick=editDraft;tools.prepend(restore);}
}

function editDraft(){if(state.editing)return;const t=state.task;state.editing=true;const box=$('#task-detail');box.querySelector('.draft-body').hidden=true;box.querySelector('.detail-tools').hidden=true;const editor=text('div','','edit-panel');const info=text('p','修改会生成新版本，需要重新核对。','hint');const area=document.createElement('textarea');area.className='draft-edit';area.setAttribute('aria-label','编辑草稿正文');area.maxLength=50000;const stored=localStorage.getItem('oneai.draft.'+t.id);let local;try{local=stored?JSON.parse(stored):null;}catch{}area.value=local?.text??t.result;let baseRevision=local?.revision??t.revision;localStorage.setItem('oneai.draft.'+t.id,JSON.stringify({text:area.value,revision:baseRevision}));area.oninput=()=>{localStorage.setItem('oneai.draft.'+t.id,JSON.stringify({text:area.value,revision:baseRevision}));info.textContent='已保存在这台设备，尚未提交到云端。';};const controls=text('div','','detail-tools');const save=text('button','保存新版本','primary');const cancel=text('button','返回预览','secondary');save.onclick=async()=>{save.disabled=true;try{await sendCommand({id:crypto.randomUUID(),action:'revise',task_id:t.id,revision:baseRevision,body:area.value});localStorage.removeItem('oneai.draft.'+t.id);state.editing=false;await selectTask(t.id);await loadTasks();announce('新版本已保存，请重新核对。');}catch(e){info.textContent=e.status===409?'云端版本已变化。你的修改仍保存在此设备，请先比较最新内容再提交。':e.message;if(e.status===409){const reload=text('button','查看云端最新版本','text-button');reload.onclick=()=>{state.editing=false;selectTask(t.id);};editor.append(reload);}}finally{save.disabled=false;}};cancel.onclick=()=>{state.editing=false;renderTask();};controls.append(save,cancel);if(baseRevision!==t.revision){save.disabled=true;info.textContent='这份本机草稿基于旧版本。请先返回预览比较云端内容，再决定是否用你的修改保存新版本。';const rebase=text('button','已比较，基于当前版本保存我的修改','secondary');rebase.onclick=()=>{if(!confirm('确认已比较云端最新内容，并以这份修改创建新版本？'))return;baseRevision=t.revision;localStorage.setItem('oneai.draft.'+t.id,JSON.stringify({text:area.value,revision:baseRevision}));save.disabled=false;rebase.remove();};controls.append(rebase);}editor.append(info,area,controls);box.querySelector('.draft-body').before(editor);area.focus();}
async function sendCommand(command,retry=false){const pending=localStorage.getItem('oneai.pending');if(pending&&!retry)throw new Error('上一项提交尚未确认，请先点击上方的重试。');clearTaskLists();localStorage.setItem('oneai.pending',JSON.stringify(command));try{const r=await api('/commands',{method:'POST',body:JSON.stringify(command)});clearTaskLists();localStorage.removeItem('oneai.pending');alert('');return r;}catch(e){if(e.status&&e.status<500){localStorage.removeItem('oneai.pending');}else pendingAlert();throw e;}}
async function act(action){try{await sendCommand({id:crypto.randomUUID(),action,task_id:state.task.id,revision:state.task.revision});await selectTask(state.task.id);await Promise.all([loadTasks(),status()]);announce(({approve:'当前版本已核对。',complete:'任务已归档。',restore_mail:'已恢复到任务列表。',generate_reply:'回复模板已生成，请编辑并核对。'})[action]||'已更新');}catch(e){announce(e.status===409?'版本已变化，请刷新后重新核对。':e.message);}}
$$('[data-filter]').forEach(b=>b.onclick=async()=>{state.filter=b.dataset.filter;$$('[data-filter]').forEach(x=>x.classList.toggle('selected',x===b));try{await loadTasks(false,false);}catch(e){announce(e.message);}});
$('#task-search').oninput=e=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.query=e.target.value;loadTasks(false,false).catch(e=>announce(e.message));},250);};
$('#more-tasks').onclick=()=>loadTasks(true).catch(e=>announce(e.message));
$$('[data-page]').forEach(b=>b.onclick=async()=>{state.page=b.dataset.page;document.body.dataset.page=state.page;$$('[data-page]').forEach(x=>x.classList.toggle('active',x===b));$$('.page').forEach(x=>x.hidden=x.id!==state.page+'-page');$('#page-title').textContent={tasks:'把事情，一件件做好。',knowledge:'你的资料，随时可用。',devices:'一个空间，随处继续。',sessions:'从上次停下的地方，继续。'}[state.page];$('#new-task').hidden=state.page!=='tasks';if(state.page==='sessions')loadSessions();if(state.page==='devices')try{await loadDevices();}catch(e){announce(e.message);}});
$('#new-task').onclick=()=>{$('#create-error').textContent='';$('#create-dialog').showModal();$('#create-title').focus();};$('#close-create').onclick=()=>$('#create-dialog').close();
const savedNew=localStorage.getItem('oneai.new');if(savedNew){try{const d=JSON.parse(savedNew);$('#create-title').value=d.title;$('#create-body').value=d.body;}catch{}}
for(const id of ['create-title','create-body'])$('#'+id).oninput=()=>localStorage.setItem('oneai.new',JSON.stringify({title:$('#create-title').value,body:$('#create-body').value}));
$('#create-form').onsubmit=async e=>{e.preventDefault();const button=e.target.querySelector('button[type=submit]');button.disabled=true;try{const r=await sendCommand({id:crypto.randomUUID(),action:'create',title:$('#create-title').value,body:$('#create-body').value});localStorage.removeItem('oneai.new');e.target.reset();$('#create-dialog').close();state.filter='';$$('[data-filter]').forEach(b=>b.classList.toggle('selected',b.dataset.filter===''));await loadTasks();await selectTask(r.task_id);await status();announce('任务已交给云端，你可以安心离开。');}catch(error){$('#create-error').textContent=error.message;}finally{button.disabled=false;}};
async function showSource(citation){$('#source-title').textContent=citation;$('#source-content').textContent='正在读取原文…';$('#source-dialog').showModal();try{const r=await api('/source?'+new URLSearchParams({citation}));$('#source-content').textContent=r.text;}catch(e){$('#source-content').textContent=e.message;}}$('#close-source').onclick=()=>$('#source-dialog').close();
$('#knowledge-form').onsubmit=async e=>{e.preventDefault();const box=$('#knowledge-results');box.replaceChildren(text('p','正在检索…','hint'));try{const r=await api('/search?'+new URLSearchParams({q:$('#knowledge-query').value}));box.replaceChildren();for(const hit of r.items){const c=text('article','','knowledge-card');c.append(text('h3',hit.heading||'资料片段'),text('p',hit.text));const b=text('button','查看来源与页码 ↗','source-link');b.onclick=()=>showSource(hit.citation);c.append(b);box.append(c);}if(!r.items.length)box.append(text('p','暂时没有匹配资料。可以换一个关键词，或在 Mac 添加 Markdown 资料。','hint'));}catch(error){box.replaceChildren(text('p',error.message,'error'));}};
async function loadDevices(){loadPushState().catch(e=>$('#push-status').textContent=e.message);const r=await api('/devices');const list=$('#device-list');list.replaceChildren();for(const device of r.devices){const row=text('div','','device-row'),info=text('div','');info.append(text('h3',device.name+(device.id===state.device?' · 当前设备':'')),text('p','连接于 '+new Date(device.created*1000).toLocaleDateString('zh-CN')));row.append(info);if(device.id!==state.device){const revoke=text('button','断开连接','text-button');revoke.onclick=async()=>{if(!confirm('断开这台设备？它需要新的配对码才能重新登录。'))return;try{await api('/devices/revoke',{method:'POST',body:JSON.stringify({id:device.id})});await loadDevices();}catch(e){announce(e.message);}};row.append(revoke);}list.append(row);}}
$('#pair-device').onclick=async()=>{const button=$('#pair-device');button.disabled=true;button.textContent='正在生成…';try{const r=await api('/pair',{method:'POST',body:'{}'});const box=$('#pair-result');box.replaceChildren(pairCodeDisplay(r.code),text('p','在另一台设备输入此码。十分钟内有效，仅可使用一次；生成新码后旧码失效。'));box.hidden=false;box.scrollIntoView({block:'nearest',behavior:'smooth'});}catch(e){announce(e.message);}finally{button.disabled=false;button.textContent='生成四位配对码';}};
$('#logout').onclick=async()=>{if(localStorage.getItem('oneai.pending')){announce('还有未确认的提交，请先处理后再退出。');return;}if(!confirm('退出这台设备？本机尚未提交的草稿将继续保留。'))return;try{await api('/logout',{method:'POST',body:'{}'});showLogin();}catch(e){announce(e.message);}};
async function poll(){if(document.hidden)return;try{await Promise.all([status(),state.page==='tasks'?loadTasks():Promise.resolve()]);if(state.page==='tasks'){if(state.task&&!state.editing){const id=state.task.id,sequence=selectionSequence;const latest=await api('/tasks/'+id);if(sequence===selectionSequence&&state.task?.id===id&&!state.editing&&(latest.revision!==state.task.revision||latest.updated!==state.task.updated||latest.status!==state.task.status)){state.task=latest;renderTask();}}}}catch(e){connection(false);if(e.status!==401)alert('连接暂时中断。云端仍会继续处理；本机未提交的内容会保留。');}}
$('#refresh').onclick=async()=>{try{await poll();if(state.online)announce('已刷新。');}catch(e){announce(e.message);}};
window.addEventListener('online',()=>{poll();pendingAlert();});window.addEventListener('offline',()=>{connection(false);alert('你已离线。可以编辑草稿，恢复网络后再提交。');});
if('serviceWorker'in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});
start();

$('#filtered-mail').onclick=async()=>{state.mailView=state.mailView==='filtered'?'inbox':'filtered';state.filter='';$$('[data-filter]').forEach(x=>x.classList.remove('selected'));$('#filtered-mail').classList.toggle('selected',state.mailView==='filtered');await loadTasks(false,false);};
$$('[data-filter]').forEach(b=>{const previous=b.onclick;b.onclick=async()=>{state.mailView='inbox';$('#filtered-mail').classList.remove('selected');await previous();};});

function renderVerification(value,box){
 if(value.code){const row=text('div','','verification-code');row.append(text('strong',value.code));const copy=text('button','复制验证码','secondary');copy.onclick=async()=>{try{await navigator.clipboard.writeText(value.code);announce('验证码已复制');}catch{announce('请长按验证码复制');}};row.append(copy);box.append(row);}
 for(const link of value.links||[]){const a=text('a','打开验证页面 · '+link.domain,'verification-link');a.href=link.url;a.target='_blank';a.rel='noopener noreferrer';a.referrerPolicy='no-referrer';a.onclick=e=>{if(!confirm('打开外部验证页面：'+link.domain+'？请先核对发件人。HTTPS 不代表链接可信。'))e.preventDefault();};box.append(a);}
}
const dismissedMailAlerts=new Set();
async function loadMailAlerts(){const data=await api('/mail/alerts');const box=$('#mail-alerts');box.replaceChildren();for(const item of data.items){if(dismissedMailAlerts.has(item.id))continue;const card=text('article','','mail-alert');card.append(text('h3',item.subject),text('p',item.sender,'hint'));renderVerification(item,card);if(!item.code&&!item.links.length)card.append(text('p','收到验证邮件，未识别到可安全展示的 HTTPS 链接。请在邮箱查看。'));const dismiss=text('button','收起','text-button');dismiss.onclick=()=>{dismissedMailAlerts.add(item.id);card.remove();box.hidden=!box.children.length;};card.append(dismiss);box.append(card);}box.hidden=!box.children.length;}

function pairCodeDisplay(code){const box=text('div','','code-cells');box.setAttribute('aria-label','配对码 '+code.split('').join(' '));for(const digit of code){const cell=text('span',digit);cell.setAttribute('aria-hidden','true');box.append(cell);}return box;}

let pushConfig,pushRegistration;
async function loadPushState(){
 const status=$('#push-status');
 if(!('serviceWorker' in navigator)||!('PushManager' in window)||!('Notification' in window)){status.textContent='当前客户端不支持 Web Push。iPhone 请用 Safari 添加到主屏幕后打开；原生客户端的 APNs 通道尚未配置。';return;}
 pushConfig=await api('/push/config');pushRegistration=await navigator.serviceWorker.ready;
 const subscription=await pushRegistration.pushManager.getSubscription();
 const enabled=pushConfig.subscribed&&!!subscription&&Notification.permission==='granted';
 $('#push-enable').disabled=!pushConfig.configured||enabled;$('#push-test').disabled=!enabled;$('#push-disable').disabled=!subscription&&!pushConfig.subscribed;
 status.textContent=!pushConfig.configured?'云端推送尚未配置。':enabled?'通知订阅已开启。请发送测试通知并检查锁屏实际显示。':Notification.permission==='denied'?'通知被系统拒绝，请到浏览器或系统设置允许后重试。':'点击开启，系统会请求通知权限。';
 if(pushConfig.last)status.textContent+=' 最近推送：'+({sent:'服务商已接收（不代表设备已显示）',pending:'等待发送',sending:'正在发送',failed:'发送失败',expired:'已过期',cancelled:'已取消'}[pushConfig.last.status]||pushConfig.last.status);
}
$('#push-enable').onclick=async()=>{try{const key=Uint8Array.from(atob(pushConfig.public_key.replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0));const subscription=await pushRegistration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:key});await api('/push/subscribe',{method:'POST',body:JSON.stringify(subscription.toJSON())});await loadPushState();}catch(e){$('#push-status').textContent='未能开启通知：'+e.message;}};
$('#push-disable').onclick=async()=>{try{await api('/push/unsubscribe',{method:'POST',body:'{}'});const subscription=await pushRegistration.pushManager.getSubscription();if(subscription)await subscription.unsubscribe();await loadPushState();}catch(e){$('#push-status').textContent=e.message;}};
$('#push-test').onclick=async()=>{try{await api('/push/test',{method:'POST',body:'{}'});$('#push-status').textContent='测试通知已进入云端队列，请锁屏检查。通常在下一轮推送任务中发送。';}catch(e){$('#push-status').textContent=e.message;}};

function taskDisplayDate(item){const value=item.origin?.startsWith('mail:')?item.mail_received:item.updated;if(!value)return '收信时间未知';return new Date(value).toLocaleDateString('zh-CN',{year:'numeric',month:'short',day:'numeric'});}
