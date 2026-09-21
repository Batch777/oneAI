import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { realpathSync } from 'node:fs';
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

let packageRoot;
try { packageRoot = resolve(dirname(realpathSync(execFileSync('which', ['pi'], {encoding:'utf8'}).trim())), '../..'); }
catch { /* Optional host integration; pure bridge tests still run without pi. */ }

test('installed pi loads and executes the real extension against an isolated vault', {skip: !packageRoot}, async () => {
  const root = await mkdtemp(join(tmpdir(), 'oneai-pi-'));
  const saved = {...process.env};
  try {
    process.env.ONEAI_PYTHON = process.env.ONEAI_TEST_PYTHON || join(process.cwd(), '.venv/bin/python');
    delete process.env.ONEAI_CLI;
    process.env.ONEAI_VAULT_PATH = join(root,'vault');
    process.env.ONEAI_STATE_PATH = join(root,'state');
    process.env.DEEPSEEK_API_KEY = '';
    await mkdir(join(root,'vault'));
    await writeFile(join(root,'vault','source.md'), 'deadline: October 1; start: unknown');
    const { loadExtensions } = await import(pathToFileURL(join(packageRoot,'dist/core/extensions/loader.js')));
    const loaded = await loadExtensions([join(process.cwd(),'extension/oneai/index.ts')], root);
    assert.deepEqual(loaded.errors, []);
    const extension = loaded.extensions[0];
    const execute = (name, params) => extension.tools.get(name).definition.execute('test', params, undefined, undefined, {hasUI:false});
    const search = await execute('vault_search', {query:'deadline'});
    const evidence = JSON.parse(search.content[0].text)[0];
    assert.ok(evidence.source_version);
    await writeFile(join(root,'vault','source.md'), 'changed');
    const read = await execute('vault_read', {path:'source.md',version:evidence.source_version,lines:'1'});
    assert.match(read.content[0].text, /start: unknown/);
    const draft = await execute('draft_create', {title:'Application',body:'活动开始时间未知',sources:[{path:'source.md',version:evidence.source_version,lines:'1'}]});
    const result = JSON.parse(draft.content[0].text);
    assert.ok((await readFile(join(root,'vault',result.path),'utf8')).includes('活动开始时间未知'));
    let sent;
    loaded.runtime.sendUserMessage = (message) => { sent=message; };
    await extension.commands.get('draft').handler('回复通知', {ui:{notify() {}}});
    assert.match(sent, /当前对话/);
  } finally {
    process.env = saved;
    await rm(root,{recursive:true,force:true});
  }
});
