const test=require('node:test');const assert=require('node:assert/strict');const vm=require('node:vm');const fs=require('node:fs');
const source=fs.readFileSync('oneai/web/static/app.js','utf8');
test('task tiers group all evidence while leaving reply actions on the first level',()=>{
 const context=vm.createContext({});vm.runInContext(source.slice(source.indexOf('function taskSections('),source.indexOf('let selectionSequence=')),context);
 const result=context.taskSections('## 下一步建议\n核对日期\n## 原始内容\nmail\n## 检索材料\nevidence\n## 回复草稿模板\nlegacy\n## 回复草稿\nnew reply');
 assert.match(result.main,/new reply/);assert.doesNotMatch(result.main,/mail|evidence|legacy/);
 assert.match(result.material,/mail/);assert.match(result.material,/evidence/);
});
test('quick task switching immediately shows loading and ignores stale responses',async()=>{
 const pending={},loading=[],rendered=[];const state={editing:false,task:null,items:[]};
 const context=vm.createContext({state,AbortController,setTimeout:()=>1,clearTimeout:()=>{},
 api:path=>new Promise(resolve=>pending[path]=resolve),showTaskLoading:id=>loading.push(id),
 $$:()=>[],$:()=>({removeAttribute:()=>{}}),renderTask:()=>rendered.push(state.task.id),confirm:()=>true});
 vm.runInContext('let selectionSequence=0,selectionRequest,selectedTaskId=null;'+source.slice(source.indexOf('async function selectTask('),source.indexOf('function renderTask(')),context);
 const a=context.selectTask('a');const b=context.selectTask('b');assert.deepEqual(loading,['a','b']);
 pending['/tasks/b']({id:'b'});await b;pending['/tasks/a']({id:'a'});await a;
 assert.deepEqual(rendered,['b']);assert.equal(state.task.id,'b');
});
