const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync('oneai/web/static/sessions.js','utf8');
function fixture(){
 const nodes=new Map(),storage=new Map(),pending=[],notices=[];
 class Element{
  constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.attrs={};this.textContent='';this.value='';this.className='';this.scrollTop=0;this.scrollHeight=0;this.clientHeight=0;this.mutations=0;this.classList={contains:c=>this.className.split(' ').includes(c),toggle:(c,on)=>{const set=new Set(this.className.split(' ').filter(Boolean));(on??!set.has(c))?set.add(c):set.delete(c);this.className=[...set].join(' ');},add:c=>this.classList.toggle(c,true),remove:c=>this.classList.toggle(c,false)};}
  append(...nodes){for(const n of nodes)this.insertBefore(n,null);}
  insertBefore(n,before){if(n.parentNode)n.remove();const at=before?this.children.indexOf(before):this.children.length;this.children.splice(at,0,n);n.parentNode=this;this.mutations++;}
  remove(){if(this.parentNode){const p=this.parentNode;p.children.splice(p.children.indexOf(this),1);p.mutations++;this.parentNode=null;}}
  replaceChildren(...nodes){for(const n of [...this.children])n.remove();this.append(...nodes);}
  setAttribute(k,v){this.attrs[k]=v;}
  getAttribute(k){return this.attrs[k];}
  focus(){this.focused=true;}
  scrollIntoView(){this.scrolled=true;}
  querySelectorAll(){return this.children.filter(x=>x.dataset.item);}
  get lastElementChild(){return this.children.at(-1);}
 }
 const get=k=>{if(!nodes.has(k))nodes.set(k,new Element());return nodes.get(k);};
 const rows=[{id:'a',title:'A',host_id:'h',state:'idle',provider:'codex'},{id:'b',title:'B',host_id:'h',state:'observing',mode:'observe',provider:'pi'}];
 const hosts=[{id:'h',name:'Host',online:true,capabilities:{providers:['codex','pi'],workspaces:['demo']}}];
 const ctx=vm.createContext({document:{querySelector:get,createElement:tag=>new Element(tag)},localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},setInterval(){},state:{page:'sessions'},crypto:{randomUUID:()=> 'test'},text:(tag,value,cls)=>{const e=new Element(tag);e.textContent=value;e.className=cls||'';return e;},api:path=>{
   if(path==='/agent/hosts')return Promise.resolve({items:hosts});if(path==='/agent/sessions')return Promise.resolve({items:rows});
   return new Promise((resolve,reject)=>pending.push({path,resolve,reject}));
 }});
 vm.runInContext(source+'\nglobalThis.view=sessionView;globalThis.page=sessionPage;',ctx);
 ctx.view.items=rows;ctx.view.hosts=hosts;ctx.renderSessionList();
 return {ctx,rows,hosts,get,storage,pending,notices,list:get('#session-list'),row:id=>get('#session-list').children.find(x=>x.dataset.sessionId===id)};
}
const flush=()=>new Promise(r=>setImmediate(r));
const reply=(p,value,cursor=1)=>p.resolve({items:[{kind:'command',payload:{action:'prompt',text:value}}],cursor});
test('unchanged polling preserves row objects, focus, scroll and children; changes reconcile by ID',async()=>{
 const f=fixture(),a=f.row('a'),b=f.row('b'),title=a.children[0];a.focus();f.list.scrollTop=70;const changes=f.list.mutations;
 for(let i=0;i<3;i++)await f.ctx.loadSessions();
 assert.equal(f.row('a'),a);assert.equal(f.row('b'),b);assert.equal(f.list.mutations,changes);assert.equal(f.list.scrollTop,70);assert.equal(a.focused,true);
 f.rows[0].title='A updated';f.rows[0].revision=20;f.ctx.renderSessionList();assert.equal(a.children[0],title);assert.equal(title.textContent,'A updated');assert.equal(f.list.mutations,changes);
 f.rows.reverse();f.ctx.renderSessionList();assert.equal(f.list.children[0],b);assert.equal(f.row('a'),a);
 f.rows.pop();f.ctx.renderSessionList();assert.equal(a.parentNode,null);assert.equal(f.row('b'),b);
 f.rows.push({id:'c',title:'C',host_id:'h',state:'idle',provider:'codex'});f.ctx.renderSessionList();f.row('c').onclick();assert.equal(f.ctx.view.selected,'c');
});
test('A-B-A selection is immediate while loading; only latest events and matching draft/permissions survive',async()=>{
 const f=fixture();f.storage.set('oneai.session.draft.a','draft A');f.storage.set('oneai.session.draft.b','draft B');f.ctx.view.loading=true;
 f.row('a').onclick();assert.equal(f.get('#session-title').textContent,'A');
 f.row('b').onclick();assert.equal(f.get('#session-title').textContent,'B');assert.equal(f.get('#session-message').value,'draft B');assert.equal(f.get('#session-message').disabled,true);assert.equal(f.row('b').attrs['aria-pressed'],'true');assert.equal(f.row('a').attrs['aria-pressed'],'false');
 f.row('a').onclick();assert.equal(f.get('#session-message').value,'draft A');assert.equal(f.get('#session-message').disabled,false);assert.equal(f.storage.get('oneai.session.selected'),'a');
 reply(f.pending[2],'new A',3);await flush();reply(f.pending[1],'old B',2);reply(f.pending[0],'old A',99);await flush();
 assert.deepEqual(f.get('#session-transcript').children.map(x=>x.textContent),['new A']);assert.equal(f.ctx.view.cursor,3);
 f.get('#session-message').value='in progress';const requests=f.pending.length;f.row('a').onclick();assert.equal(f.pending.length,requests);assert.equal(f.get('#session-message').value,'in progress');assert.equal(f.get('#session-transcript').children.length,1);
});
test('same ID concurrent replay cannot append an older response or regress cursor',async()=>{
 const f=fixture();f.row('a').onclick();const second=f.ctx.renderSession();reply(f.pending[1],'latest',20);await second;reply(f.pending[0],'old',1);await flush();assert.equal(f.ctx.view.cursor,20);assert.deepEqual(f.get('#session-transcript').children.map(x=>x.textContent),['latest']);
});
test('stale event errors are ignored but current event failures remain visible',async()=>{
 const f=fixture();f.row('a').onclick();f.row('b').onclick();f.pending[0].reject(Error('old error'));await flush();assert.equal(f.get('#session-notice').textContent,'');f.pending[1].reject(Error('current error'));await flush();assert.equal(f.get('#session-notice').textContent,'current error');
});
test('removing selected session invalidates pending replay and clears actionable details',async()=>{
 const f=fixture();f.row('a').onclick();f.rows.splice(0,1);await f.ctx.loadSessions();reply(f.pending[0],'removed A',8);await flush();assert.equal(f.ctx.view.selected,null);assert.equal(f.ctx.page.classList.contains('has-session'),false);assert.equal(f.get('#session-transcript').children.length,0);assert.equal(f.get('#session-send').disabled,true);assert.equal(f.get('#session-state').textContent,'');
 f.rows.length=0;await f.ctx.loadSessions();assert.equal(f.list.children.length,1);assert.equal(f.storage.has('oneai.session.selected'),false);
});
test('switch control expands full list, survives refresh, and selection returns focus without commands',async()=>{
 const f=fixture();f.row('a').onclick();reply(f.pending[0],'A');await flush();f.get('#session-switch').onclick();assert.equal(f.ctx.page.classList.contains('session-list-expanded'),true);assert.equal(f.get('#session-switch').attrs['aria-expanded'],'true');assert.equal(f.list.focused,true);assert.equal(f.list.scrolled,true);
 const refresh=f.ctx.loadSessions();await flush();reply(f.pending[1],'',1);await refresh;assert.equal(f.ctx.page.classList.contains('session-list-expanded'),true);
 f.row('b').onclick();assert.equal(f.ctx.page.classList.contains('session-list-expanded'),false);assert.equal(f.get('#session-title').focused,true);assert.equal(f.get('#session-switch').attrs['aria-expanded'],'false');assert.ok(f.pending.every(x=>x.path.includes('/events?')));
});
test('persisted and newly created not-yet-listed IDs render when list becomes available',async()=>{
 const f=fixture();f.ctx.view.selected='a';const first=f.ctx.loadSessions();await flush();reply(f.pending[0],'restored');await first;assert.equal(f.ctx.view.rendered,'a');
 f.ctx.selectSession('new');assert.equal(f.ctx.view.rendered,null);f.rows.push({id:'new',title:'New',host_id:'h',state:'idle',provider:'pi'});const next=f.ctx.loadSessions();await flush();reply(f.pending[1],'new');await next;assert.equal(f.ctx.view.rendered,'new');assert.equal(f.get('#session-title').textContent,'New');
});

test('unselected session list stays expanded across background polling',async()=>{
 const f=fixture();f.get('#session-switch').onclick();assert.equal(f.get('#session-switch').attrs['aria-expanded'],'true');
 for(let i=0;i<3;i++)await f.ctx.loadSessions();
 assert.equal(f.ctx.view.selected,null);assert.equal(f.ctx.page.classList.contains('session-list-expanded'),true);assert.equal(f.get('#session-switch').attrs['aria-expanded'],'true');
});
