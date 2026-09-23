const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function setup(){
 const nodes=[],events={},refresh={},state={reloads:0,allow:false,calls:0,version:'a'.repeat(40)};
 const document={hidden:false,createElement:()=>{const n={hidden:false,append(){},addEventListener:(k,v)=>n[k]=v};nodes.push(n);return n;},body:{append(){}},querySelector:s=>s.startsWith('meta')?{content:'a'.repeat(40)}:{addEventListener:(k,v)=>refresh[k]=v},addEventListener:(k,v)=>events[k]=v};
 vm.runInNewContext(fs.readFileSync('oneai/web/static/updates.js','utf8'),{document,window:{addEventListener(){}},fetch:async()=>{state.calls++;return {ok:true,json:async()=>({commit:state.version})};},confirm:()=>state.allow,location:{reload:()=>state.reloads++}});
 return {nodes,events,refresh,state,document};
}
const flush=()=>new Promise(r=>setImmediate(r));
test('new version requires explicit acceptance and never auto reloads',async()=>{
 const x=setup();await flush();assert.equal(x.nodes[2].hidden,true);
 x.state.version='b'.repeat(40);await x.refresh.click();assert.equal(x.nodes[2].hidden,false);assert.equal(x.state.reloads,0);
 x.nodes[2].click();assert.equal(x.state.reloads,0);
 x.state.allow=true;x.nodes[2].click();assert.equal(x.state.reloads,1);
});
test('returning to same version clears update prompt; invalid version ignored',async()=>{
 const x=setup();await flush();x.state.version='b'.repeat(40);await x.refresh.click();
 x.state.version='a'.repeat(40);await x.refresh.click();assert.equal(x.nodes[2].hidden,true);
 x.state.version='<script>';await x.refresh.click();assert.equal(x.nodes[2].hidden,true);
});
test('hidden page does not check and concurrent checks deduplicate',async()=>{
 const x=setup();await flush();const before=x.state.calls;x.document.hidden=true;await x.refresh.click();assert.equal(x.state.calls,before);
 x.document.hidden=false;await Promise.all([x.refresh.click(),x.refresh.click()]);assert.equal(x.state.calls,before+1);
});
test('settings check button gives feedback without reloading',async()=>{
 const x=setup();await flush();
 assert.equal(x.nodes[3].textContent,'检查更新');
 x.state.version='b'.repeat(40);await x.nodes[3].click();
 assert.match(x.nodes[4].textContent,/新版已部署/);
 assert.equal(x.nodes[3].disabled,false);assert.equal(x.state.reloads,0);
});
