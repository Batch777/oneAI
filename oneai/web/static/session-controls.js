'use strict';
const sessionCatalog={items:[],at:0},sessionControls={key:null,auditKey:null};
const control=id=>document.querySelector('#'+id);
async function loadSessionCatalog(){
 if(Date.now()-sessionCatalog.at<60000)return;
 try{sessionCatalog.items=(await api('/agent/catalog')).items;sessionCatalog.at=Date.now();}catch{sessionCatalog.items=[];}
}
function catalogFor(host){return sessionCatalog.items.find(x=>x.host_id===host)||{models:[],profiles:[]};}
function options(select,items,preferred){
 const before=preferred??select.value,signature=JSON.stringify(items.map(x=>[x.id,x.name]));
 if(select.dataset.options!==signature){select.dataset.options=signature;select.replaceChildren();for(const item of items){const el=text('option',item.name||item.id);el.value=item.id;select.append(el);}}
 if(items.some(x=>x.id===before))select.value=before;
}
function effortOptions(modelSelect,effortSelect,models,preferred){
 const m=models.find(x=>x.id===modelSelect.value);options(effortSelect,(m?.efforts||[]).map(id=>({id,name:id})),preferred??m?.default_effort);
}
function refreshSessionModelOptions(){
 const c=catalogFor(control('session-host').value),provider=control('session-provider').value,workspace=control('session-workspace').value;
 const models=c.models.filter(m=>m.provider===provider),profiles=c.profiles.filter(p=>p.workspaces.includes(workspace));
 options(control('session-profile'),profiles);options(control('session-new-model'),models);
 effortOptions(control('session-new-model'),control('session-new-effort'),models,control('session-new-effort').value||undefined);
 control('session-create').disabled=!models.length||!profiles.length;
 control('session-profile-preview').textContent=profiles.find(p=>p.id===control('session-profile').value)?.prompt||'普通会话：由运行时和仓库 AGENTS.md 提供基础指令。';
}
function newSessionSettings(){return {model:control('session-new-model').value,effort:control('session-new-effort').value||null,profile:control('session-profile').value||'general'};}
function displayNumber(value){return typeof value==='number'&&Number.isFinite(value)?value.toLocaleString('zh-CN',{maximumFractionDigits:2}):'未知';}
function usageLines(metrics){
 const u=metrics?.usage,q=metrics?.quota,lines=[];
 if(u){lines.push('会话累计 '+displayNumber(u.total)+' tokens','输入 '+displayNumber(u.input)+' · 输出 '+displayNumber(u.output)+' · 缓存读取 '+displayNumber(u.cached_input));lines.push('当前上下文 '+displayNumber(u.context_tokens)+' / '+displayNumber(u.context_window));
  if(u.cost_kind==='runtime_estimate_not_invoice')lines.push('运行时估算 $'+displayNumber(u.cost_usd)+'；不是订阅账单，0 不代表免费');
  else lines.push('费用：服务商未提供账单金额');
  lines.push('用量采样 '+new Date((u.observed_at||u.reported_at)*1000).toLocaleString());
 }else lines.push('会话用量：等待运行时报告');
 if(q?.windows?.length){for(const w of q.windows)lines.push(`${w.bucket} · ${displayNumber(w.window_minutes)} 分钟窗口剩余 ${displayNumber(w.remaining_percent)}%`+(w.resets_at?' · '+new Date(w.resets_at*1000).toLocaleString()+' 重置':''));lines.push('账号共享额度，非本会话专属 · 采样 '+new Date(q.observed_at*1000).toLocaleTimeString());}
 else lines.push('账号剩余额度：未提供');
 return lines;
}
function reviewText(a){
 return `请审查这次变更。\n目的：${a.purpose}\n分支：${a.branch}\n基线：${a.base_sha}\n当前提交：${a.head_sha}\n工作区：${a.dirty?'含未提交改动':'干净'}\n快照摘要：${a.workspace_digest}\n\n实际变更文件：\n${a.files.length?a.files.map(f=>`- ${f.path}（${f.status}）：${f.purpose}`).join('\n'):'- 无文件变更；目前没有代码可批准发布。'}\n\n测试证据：${a.tests}\n请逐文件核对作用、兼容性和风险，补充真实测试命令与结果、回滚方案。\n需要你的决定：${a.decision}\n此快照生成后若提交或工作区改变，请重新生成。`;
}
function renderSessionControls(row){
 control('session-controls').hidden=false;
 const c=catalogFor(row.host_id),models=c.models.filter(m=>m.provider===row.provider),settings=row.settings||{},actual=row.metrics?.model;
 control('session-model-state').textContent=(actual?.model||settings.model||'运行时默认模型')+' · '+(actual?.effort||settings.effort||'默认强度')+(actual?.effective_from==='next_turn'?' · 下一轮生效':'');
 const key=JSON.stringify([row.id,settings,c.updated]);
 if(sessionControls.key!==key){sessionControls.key=key;options(control('session-model'),models,settings.model||actual?.model);effortOptions(control('session-model'),control('session-effort'),models,settings.effort||actual?.effort);control('session-audit-purpose').value=row.title+'：核对变更目的、文件与测试';}
 control('session-model-apply').disabled=row.mode==='observe'||row.state!=='idle'||!models.length;
 control('session-audit').disabled=row.mode==='observe'||row.state!=='idle';
 control('session-remove').hidden=row.state!=='closed'||row.mode==='observe';
 control('session-role-prompt').textContent=c.profiles.find(p=>p.id===(settings.profile||'general'))?.prompt||'普通会话：由运行时和仓库 AGENTS.md 提供基础指令。';
 control('session-usage').replaceChildren(...usageLines(row.metrics).map(line=>text('p',line,'hint')));
 const a=row.metrics?.audit,auditKey=JSON.stringify([row.id,a]);
 if(sessionControls.auditKey!==auditKey){sessionControls.auditKey=auditKey;const box=control('session-audit-result');box.replaceChildren();if(a){const label=text('label','可复制给审查者的提示词'),area=document.createElement('textarea');area.readOnly=true;area.rows=12;area.value=reviewText(a);label.append(area);box.append(label);const copy=text('button','复制审计提示词','secondary');copy.type='button';copy.onclick=async()=>{try{await navigator.clipboard.writeText(area.value);announce('已复制审计提示词');}catch{area.select();announce('已选中，请复制。');}};box.append(copy);}}
}
for(const id of ['session-provider','session-workspace'])control(id).onchange=refreshSessionModelOptions;
control('session-profile').onchange=()=>{const profile=control('session-profile').value,c=catalogFor(control('session-host').value);const wanted=profile==='supervisor'?'gpt-6-astra':profile==='implementer'?'kimi-coding/k3-256k':null;if(wanted&&c.models.some(m=>m.id===wanted))control('session-new-model').value=wanted;refreshSessionModelOptions();};
control('session-new-model').onchange=()=>effortOptions(control('session-new-model'),control('session-new-effort'),catalogFor(control('session-host').value).models);
control('session-model').onchange=()=>{const row=sessionView.items.find(s=>s.id===sessionView.selected);if(row)effortOptions(control('session-model'),control('session-effort'),catalogFor(row.host_id).models);};
async function controlCommand(action,extra={}){
 const row=sessionView.items.find(s=>s.id===sessionView.selected);if(!row)return;
 await sessionCommand({id:crypto.randomUUID(),action,session_id:row.id,revision:row.revision,...extra});
 if(action==='remove'){sessionView.selected=null;sessionView.rendered=null;localStorage.removeItem('oneai.session.selected');control('session-controls').hidden=true;control('session-transcript').replaceChildren();control('session-title').textContent='选择会话';control('session-message').value='';control('session-send').disabled=true;control('session-close').disabled=true;}
 sessionNotice(action==='remove'?'已从列表移除，审计历史保留。':'指令已保存，等待执行主机确认。');await loadSessions();
}
control('session-model-form').onsubmit=async e=>{e.preventDefault();control('session-model-apply').disabled=true;try{const row=sessionView.items.find(s=>s.id===sessionView.selected);await controlCommand('configure',{settings:{model:control('session-model').value,effort:control('session-effort').value||null,profile:row.settings?.profile||'general'}});}catch(error){sessionNotice(error.message);await loadSessions();}};
control('session-audit-form').onsubmit=async e=>{e.preventDefault();control('session-audit').disabled=true;try{await controlCommand('audit',{purpose:control('session-audit-purpose').value});}catch(error){sessionNotice(error.message);await loadSessions();}};
control('session-remove').onclick=async()=>{if(!confirm('从列表移除这个已关闭会话？审计历史与 Linux 原始记录仍保留。'))return;try{await controlCommand('remove');}catch(e){sessionNotice(e.message);}};
