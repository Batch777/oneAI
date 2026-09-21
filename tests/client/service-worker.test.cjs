const test=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
function worker(clients={}){
 const handlers={},shown=[];
 const self={location:{origin:'https://oneai.example'},addEventListener:(name,fn)=>handlers[name]=fn,
 registration:{showNotification:async(title,options)=>shown.push({title,options})},clients};
 vm.runInNewContext(fs.readFileSync('oneai/web/static/sw.js','utf8'),{self,URL});
 return {handlers,shown};
}
test('push displays notification and ignores external payload navigation',async()=>{
 const {handlers,shown}=worker();let waiting;
 handlers.push({data:{json:()=>({title:'oneAI',body:'New mail',url:'https://evil.example/',tag:'mail-1'})},waitUntil:p=>waiting=p});
 await waiting;
 assert.equal(shown[0].options.data.url,'/#mail-alerts');
 assert.equal(shown[0].options.tag,'mail-1');
});
test('malformed payload still shows generic notification',async()=>{
 const {handlers,shown}=worker();let waiting;
 handlers.push({data:{json:()=>{throw Error('bad JSON');}},waitUntil:p=>waiting=p});await waiting;
 assert.equal(shown[0].title,'oneAI 新通知');
});
test('notification click focuses an existing same-origin window',async()=>{
 const actions=[];
 const client={url:'https://oneai.example/',navigate:async url=>actions.push(url),focus:async()=>actions.push('focus')};
 const {handlers}=worker({matchAll:async()=>[client],openWindow:async()=>assert.fail('duplicate window')});let waiting;
 handlers.notificationclick({notification:{close:()=>actions.push('close')},waitUntil:p=>waiting=p});await waiting;
 assert.deepEqual(actions,['close','/#mail-alerts','focus']);
});
test('notification click opens the workspace when all windows are closed',async()=>{
 const actions=[];const {handlers}=worker({matchAll:async()=>[],openWindow:async url=>actions.push(url)});let waiting;
 handlers.notificationclick({notification:{close:()=>{}},waitUntil:p=>waiting=p});await waiting;
 assert.deepEqual(actions,['/#mail-alerts']);
});
