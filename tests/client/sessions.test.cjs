const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync('oneai/web/static/sessions.js','utf8');
test('switching sessions immediately updates identity and ignores an older in-flight replay',async()=>{
 const nodes=new Map();const get=k=>{if(!nodes.has(k))nodes.set(k,{textContent:'',value:'',children:[],classList:{add(){}},replaceChildren(){this.children=[];},append(n){this.children.push(n);},scrollTop:0,scrollHeight:0,clientHeight:0});return nodes.get(k);};
 const pending=[];const view={selected:'a',items:[{id:'a',title:'A',host_id:'h',state:'idle',provider:'codex'},{id:'b',title:'B',host_id:'h',state:'idle',provider:'pi'}],hosts:[{id:'h',online:true}],cursor:0,rendered:null,renderSeq:0,loading:true};
 const ctx=vm.createContext({sessionView:view,sessionPage:get('page'),sessionLabels:{idle:'Idle'},document:{querySelector:get},localStorage:{getItem:()=>'',setItem(){}},api:()=>new Promise(resolve=>pending.push(resolve)),sessionNotice(){},text:(tag,value)=>({textContent:value})});
 vm.runInContext(source.slice(source.indexOf('function selectSession('),source.indexOf("document.querySelector('#session-host').onchange")),ctx);
 const old=ctx.renderSession();ctx.selectSession('b');assert.equal(get('#session-title').textContent,'B');assert.equal(view.rendered,'b');
 pending[1]({items:[{kind:'command',payload:{action:'prompt',text:'new'}}],cursor:2});await new Promise(r=>setImmediate(r));
 pending[0]({items:[{kind:'command',payload:{action:'prompt',text:'old'}}],cursor:10});await old;
 assert.deepEqual(get('#session-transcript').children.map(n=>n.textContent),['new']);assert.equal(view.cursor,2);
});
