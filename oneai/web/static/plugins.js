/* Owner-trusted Linux extensions. The relay never receives executable packages. */
(()=>{
 const node=(tag,value,cls)=>{const n=document.createElement(tag);n.textContent=value||'';if(cls)n.className=cls;return n;};
 let loading=false,timer;
 const statuses={queued:'等待执行',running:'执行中',completed:'已完成',failed:'执行失败',unknown:'结果待核对',cancelled:'已取消',expired:'已过期'};
 window.loadPlugins=async(onlyRuns=false)=>{
  onlyRuns=onlyRuns===true;
  if(loading)return;loading=true;clearTimeout(timer);
  const list=document.querySelector('#plugins-list'),runs=document.querySelector('#plugin-runs');
  try{
   const data=await api('/plugins');runs.replaceChildren();if(!onlyRuns){list.replaceChildren();
   if(!data.items.length)list.append(node('p','尚无已安装扩展。请在 Linux 安装经过审阅的插件。','hint'));
   for(const p of data.items){
    const box=node('article','','plugin-item'),m=p.manifest;
    box.append(node('h4',m.display_name),node('p',m.description),node('p',`${m.version} · ${p.host_name} · ${p.online?'在线':'离线'} · ${p.digest.slice(0,12)}`,'hint'));
    const toggle=node('button',p.enabled?'停用':'启用','secondary');toggle.disabled=!p.online&&!p.enabled;
    toggle.onclick=async()=>{toggle.disabled=true;try{await api(`/plugins/${p.key}/state`,{method:'POST',body:JSON.stringify({revision:p.revision,enabled:!p.enabled})});await window.loadPlugins();}catch(e){announce(e.message);toggle.disabled=false;}};box.append(toggle);
    if(p.enabled){
     const form=node('form','','plugin-form'),fields={};const workspace=node('select');
     for(const w of p.workspaces){const o=node('option',w);o.value=w;workspace.append(o);}const wl=node('label','项目');wl.append(workspace);form.append(wl);
     for(const [name,s] of Object.entries(m.input_schema.properties)){
      const label=node('label',s.title||name);let input;
      if(s.enum){input=node('select');for(const value of s.enum){const o=node('option',String(value));o.value=String(value);input.append(o);}}
      else{input=node('input');input.type=s.type==='integer'?'number':s.type==='boolean'?'checkbox':'text';if(s.minimum!==undefined)input.min=s.minimum;if(s.maximum!==undefined)input.max=s.maximum;if(s.maxLength)input.maxLength=s.maxLength;if(s.type==='integer')input.value=s.minimum??1;}
      input.required=s.type!=='boolean'&&m.input_schema.required.includes(name);fields[name]=[input,s];label.append(input);form.append(label);
     }
     const run=node('button','执行扩展','primary');run.type='submit';run.disabled=!p.online;form.append(run);let pending;
     form.onsubmit=async e=>{e.preventDefault();run.disabled=true;const value={};for(const [name,[input,s]] of Object.entries(fields)){if(!input.required&&input.value===''&&s.type!=='boolean')continue;value[name]=s.type==='boolean'?(s.enum?input.value==='true':input.checked):s.type==='integer'?Number(input.value):input.value;}
      const body={plugin_key:p.key,revision:p.revision,workspace:workspace.value,input:value};const fingerprint=JSON.stringify(body);if(!pending||pending.fingerprint!==fingerprint)pending={fingerprint,id:crypto.randomUUID()};
      try{await api('/plugin-actions',{method:'POST',body:JSON.stringify({...body,id:pending.id})});pending=null;await window.loadPlugins();}catch(error){announce(error.message);run.disabled=false;}
     };box.append(form);
    }list.append(box);
   }
   }
   for(const r of data.runs){const detail=node('details','','plugin-item');detail.append(node('summary',`${statuses[r.status]||r.status} · ${r.workspace} · ${new Date(r.created*1000).toLocaleTimeString()}`));if(r.result)detail.append(node('pre',JSON.stringify(r.result,null,2),'plugin-result'));runs.append(detail);}
   if(data.runs.some(r=>['queued','running'].includes(r.status))&&state.page==='devices')timer=setTimeout(()=>window.loadPlugins(true),2000);
  }catch(e){list.replaceChildren(node('p',e.message,'hint'));}finally{loading=false;}
 };
 document.querySelector('#plugins-refresh').onclick=window.loadPlugins;
})();
