const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync('oneai/web/static/app.js','utf8');
function setup(){
 let now=1000;const pending=[],renders=[],elements={};
 const state={filter:'needs_review',query:'',mailView:'inbox',offset:0,items:[]};
 const context=vm.createContext({state,URLSearchParams,AbortController,TextEncoder,setTimeout:()=>1,clearTimeout:()=>{},document:{hidden:false},navigator:{},Date:{now:()=>now},
 api:(key,options)=>new Promise((resolve,reject)=>pending.push({key,options,resolve,reject})),
 $:s=>elements[s]??=({textContent:'',disabled:false,replaceChildren(){}})});
 vm.runInContext(source.slice(source.indexOf('// Private, bounded'),source.indexOf('function disclosure(')),context);
 context.renderTaskList=(data,base=[])=>{state.items=base.concat(data.items);state.offset=state.items.length;renders.push(Array.from(state.items,x=>x.id));};
 return {context,state,pending,renders,elements,tick:()=>now+=16000};
}
const data=id=>({items:[{id}],total:2});
test('visited filter returns immediately without another request within TTL',async()=>{
 const t=setup();let p=t.context.loadTasks(false,false);t.pending[0].resolve(data('review'));await p;
 t.state.filter='pending';p=t.context.loadTasks(false,false);t.pending[1].resolve(data('pending'));await p;
 t.state.filter='needs_review';await t.context.loadTasks(false,false);
 assert.equal(t.pending.length,2);assert.deepEqual(t.renders.at(-1),['review']);
});
test('out-of-order responses do not replace the selected filter; stale cache refreshes',async()=>{
 const t=setup();const a=t.context.loadTasks(false,false);t.state.filter='pending';const b=t.context.loadTasks(false,false);
 t.pending[1].resolve(data('pending'));await b;t.pending[0].resolve(data('review'));await a;
 assert.deepEqual(t.renders,[['pending']]);t.state.filter='needs_review';t.tick();const refresh=t.context.loadTasks(false,false);
 assert.deepEqual(t.renders.at(-1),['review']);t.pending[2].resolve(data('new'));await refresh;assert.deepEqual(t.renders.at(-1),['new']);
});
test('simultaneous same-page requests deduplicate and append only once',async()=>{
 const t=setup();let p=t.context.loadTasks();t.pending[0].resolve(data('first'));await p;
 const a=t.context.loadTasks(true),b=t.context.loadTasks(true);assert.equal(t.pending.length,2);
 t.pending[1].resolve(data('second'));await Promise.all([a,b]);assert.deepEqual(t.renders.at(-1),['first','second']);
});
test('logout or mutation invalidates in-flight cache writes and renders',async()=>{
 const t=setup();const p=t.context.loadTasks(false,false);t.context.clearTaskLists();t.pending[0].resolve(data('old'));await p;
 assert.equal(t.renders.length,0);const fresh=t.context.loadTasks(false,false);assert.equal(t.pending.length,2);t.pending[1].resolve(data('new'));await fresh;
 assert.deepEqual(t.renders.at(-1),['new']);
});
test('filtered mail and search use different cache keys; failed stale refresh remains labelled',async()=>{
 const t=setup();let p=t.context.loadTasks(false,false);t.pending[0].resolve(data('inbox'));await p;
 t.state.mailView='filtered';p=t.context.loadTasks(false,false);assert.equal(t.pending.length,2);t.pending[1].resolve(data('filtered'));await p;
 t.tick();p=t.context.loadTasks(false,false);t.pending[2].reject(new Error('offline'));await p;
 assert.match(t.elements['#task-list-status'].textContent,/缓存/);assert.deepEqual(t.renders.at(-1),['filtered']);
 t.state.query='paper';p=t.context.loadTasks(false,false);assert.equal(t.pending.length,4);t.pending[3].resolve(data('query'));await p;
});
test('prefetch is read-only, populates another filter, and is invalidated on logout',async()=>{
 const t=setup();t.state.csrf='test';t.state.page='tasks';
 const p=t.context.prefetchTaskList();assert.equal(t.pending.length,1);assert.match(t.pending[0].key,/^\/tasks\?/);
 t.pending[0].resolve(data('prefetched'));await p;
 t.state.filter='';await t.context.loadTasks(false,false);assert.equal(t.pending.length,1);assert.deepEqual(t.renders.at(-1),['prefetched']);
 const pending=t.context.prefetchTaskList();t.context.clearTaskLists();t.pending[1].resolve(data('old'));await pending;
 t.state.filter='needs_review';const current=t.context.loadTasks(false,false);assert.equal(t.pending.length,3);t.pending[2].resolve(data('fresh'));await current;
});
test('loaded pages refresh as one bounded list request',async()=>{
 const t=setup();const rows=Array.from({length:50},(_,i)=>({id:String(i)}));
 let p=t.context.loadTasks();t.pending[0].resolve({items:rows,total:200});await p;
 p=t.context.loadTasks(true);t.pending[1].resolve({items:rows.map(x=>({id:'next'+x.id})),total:200});await p;
 p=t.context.loadTasks();assert.match(t.pending[2].key,/limit=100/);t.pending[2].resolve({items:t.state.items,total:200});await p;assert.equal(t.state.items.length,100);
});
test('unchanged rows retain DOM identity and perform no child mutations',()=>{
 let mutations=0;
 class Node{
  constructor(){this.children=[];this.dataset={};this.className='';}
  append(...nodes){for(const n of nodes){n.parent=this;this.children.push(n);}mutations++;}
  replaceChildren(...nodes){this.children=[];this.append(...nodes);}
  insertBefore(node,before){if(node.parent)node.remove();const at=before?this.children.indexOf(before):this.children.length;this.children.splice(at,0,node);node.parent=this;mutations++;}
  remove(){if(this.parent){this.parent.children.splice(this.parent.children.indexOf(this),1);this.parent=null;mutations++;}}
 }
 const list=new Node(),more=new Node(),state={items:[],mailView:'inbox',query:'',task:null};
 const c=vm.createContext({state,labels:{pending:'pending'},document:{createElement:()=>new Node()},text:()=>new Node(),taskDisplayDate:()=>'',selectTask:()=>{},$:id=>id==='#task-list'?list:more});
 vm.runInContext(source.slice(source.indexOf('function taskRowSignature('),source.indexOf('let taskPrefetchTimer')),c);
 const data={items:[{id:'a',title:'A',status:'pending'},{id:'b',title:'B',status:'pending'}],total:2};c.renderTaskList(data);const row=list.children[0];mutations=0;
 c.renderTaskList(data);assert.equal(mutations,0);assert.equal(list.children[0],row);
 c.renderTaskList({...data,items:[data.items[0],{...data.items[1],title:'changed'}]});assert.equal(list.children[0],row);assert.ok(mutations>0);
});

test('foreground switch never joins a previously aborted prefetch',async()=>{
 const t=setup();t.state.csrf='test';t.state.page='tasks';
 const prefetch=t.context.prefetchTaskList();
 const review=t.context.loadTasks(false,false);
 assert.equal(t.pending[0].options.signal.aborted,true);
 t.state.filter='';const all=t.context.loadTasks(false,false);
 assert.equal(t.pending.length,3);
 t.pending[0].reject(new Error('aborted'));t.pending[1].resolve(data('review'));t.pending[2].resolve(data('all'));
 await Promise.all([prefetch,review,all]);assert.deepEqual(t.renders.at(-1),['all']);
});
