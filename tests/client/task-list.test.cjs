const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync('oneai/web/static/app.js','utf8');
function setup(){
 let now=1000;const pending=[],renders=[],elements={};
 const state={filter:'needs_review',query:'',mailView:'inbox',offset:0,items:[]};
 const context=vm.createContext({state,URLSearchParams,Date:{now:()=>now},
 api:key=>new Promise((resolve,reject)=>pending.push({key,resolve,reject})),
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
