const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync('oneai/web/static/session-controls.js','utf8');
function context(){const ctx=vm.createContext({Date});vm.runInContext(source.slice(source.indexOf('function displayNumber('),source.indexOf('function renderSessionControls(')),ctx);return ctx;}
test('usage distinguishes missing information, shared quota and estimated cost',()=>{
 const c=context();assert.match(c.usageLines({}).join(' '),/等待运行时报告/);
 const text=c.usageLines({usage:{total:100,input:50,output:50,cost_usd:0,cost_kind:'runtime_estimate_not_invoice',observed_at:1},quota:{observed_at:1,windows:[{bucket:'codex',remaining_percent:60,window_minutes:300}]}}).join(' ');
 assert.match(text,/不是订阅账单/);assert.match(text,/账号共享/);assert.match(text,/未知/);
});
test('review prompt includes purpose, exact commits and per-file evidence without implying deployment approval',()=>{
 const c=context();const value=c.reviewText({purpose:'Fix switch',branch:'codex/test',base_sha:'a'.repeat(40),head_sha:'b'.repeat(40),dirty:true,workspace_digest:'digest',files:[{path:'oneai/web/a.js',status:'changed',purpose:'Switch safely'}],tests:'not run',decision:'只审查'});
 assert.match(value,/Fix switch/);assert.match(value,/oneai\/web\/a.js/);assert.match(value,/Switch safely/);assert.match(value,/含未提交改动/);assert.match(value,/not run/);assert.match(value,/重新生成/);
});
