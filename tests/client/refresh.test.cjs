const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync('oneai/web/static/app.js','utf8');
function setup(){
 const listeners={},indicator={hidden:true,style:{setProperty(){}},classList:{add(){},remove(){},toggle(){}},setAttribute(){}},state={page:'tasks',editing:false};let count=0,resolve;
 const c=vm.createContext({state,refreshRequest:null,matchMedia:()=>({matches:true}),text:()=>indicator,
 document:{body:{append(){}},addEventListener:(n,f)=>listeners[n]=f,querySelector:()=>null},$:()=>({hidden:false}),
 refreshWorkspace:()=>{count++;return new Promise(r=>resolve=r);}});
 vm.runInContext(source.slice(source.indexOf('function installPullRefresh(){'),source.indexOf("if('serviceWorker'in navigator)")),c);
 const target={scrollTop:0,parentElement:null,closest:s=>s==='#tasks-page'?{}:null};
 const event=(x,y,n=1)=>({target,touches:Array.from({length:n},()=>({clientX:x,clientY:y})),cancelable:true,preventDefault(){this.prevented=true;}});
 return {listeners,indicator,state,target,event,count:()=>count,finish:()=>resolve()};
}
test('top-edge pull refresh triggers only on release and deduplicates through refresh coordinator',async()=>{
 const t=setup();t.listeners.touchstart(t.event(0,0));t.listeners.touchmove(t.event(2,100));assert.equal(t.count(),0);assert.equal(t.indicator.textContent,'松开刷新');
 const done=t.listeners.touchend(t.event(2,100));assert.equal(t.count(),1);t.finish();await done;assert.equal(t.indicator.hidden,true);
});
test('short pull, horizontal swipe, scrolled content, multi-touch and editing do not refresh',async()=>{
 for(const kind of ['short','horizontal','scrolled','multi','editing','cancel']){
  const t=setup();if(kind==='scrolled')t.target.scrollTop=10;if(kind==='editing')t.state.editing=true;
  t.listeners.touchstart(t.event(0,0,kind==='multi'?2:1));t.listeners.touchmove(t.event(kind==='horizontal'?130:0,kind==='short'?40:100));
  if(kind==='cancel')t.listeners.touchcancel();await t.listeners.touchend(t.event(0,100));assert.equal(t.count(),0,kind);
 }
});
test('refresh does not overwrite an editing draft and concurrent requests share one operation',async()=>{
 let requests=0,finish;const button={},state={page:'tasks',task:{id:'draft'},editing:true,online:true};
 const c=vm.createContext({state,document:{hidden:false},$:s=>s==='#refresh'?button:{hidden:false},clearTaskLists:()=>{},mailDisplayCache:new Map(),
 status:()=>{requests++;return new Promise(r=>finish=r);},loadTasks:async()=>{},api:()=>{throw Error('must not fetch editing draft');},renderTask:()=>{throw Error('must not render draft');},announce:()=>{},connection:()=>{},alert:()=>{}});
 vm.runInContext(source.slice(source.indexOf('let refreshRequest;'),source.indexOf("$('#refresh').onclick=")),c);
 const a=c.refreshWorkspace(),b=c.refreshWorkspace();assert.equal(requests,1);finish();await Promise.all([a,b]);assert.equal(button.disabled,false);
});
