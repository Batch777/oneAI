import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, writeFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { run, draftPayload, draftRequest, registerContext } from '../../extension/oneai/bridge.mjs';

test('bridge preserves exact current-session draft and invokes real CLI over stdin', async () => {
  const root = await mkdtemp(join(tmpdir(), 'oneai-bridge-'));
  const saved = { ...process.env };
  try {
    process.env.ONEAI_PYTHON = process.env.ONEAI_TEST_PYTHON || join(process.cwd(), '.venv/bin/python');
    delete process.env.ONEAI_CLI;
    process.env.ONEAI_VAULT_PATH = join(root, 'vault');
    process.env.ONEAI_STATE_PATH = join(root, 'state');
    process.env.DEEPSEEK_API_KEY = '';
    await mkdir(join(root, 'vault'));
    const body = '保持当前对话的纠正：截止不是开始。 $HOME `echo test`';
    const result = JSON.parse(await run(['draft-save'], draftPayload({title: 'Test', body})));
    assert.equal(result.status, 'drafted');
    assert.ok((await readFile(join(root, 'vault', result.path), 'utf8')).includes(body));
    await assert.rejects(run(['read', '../outside.md']));
  } finally {
    process.env = saved;
    await rm(root, { recursive: true, force: true });
  }
});

test('draft command stays in the current session', () => {
  assert.ok(draftRequest('通知回复').includes('当前对话'));
  assert.equal(draftPayload({title:'t',body:'user correction'}).body, 'user correction');
});

test('context hook reloads rules every turn, including headless turns', async () => {
  let handler, version = 0;
  registerContext({on(event, callback) { assert.equal(event, 'before_agent_start'); handler = callback; }},
    async () => JSON.stringify({rules: `version-${++version}`}));
  assert.match((await handler({systemPrompt:'base'}, {hasUI:false})).systemPrompt, /version-1/);
  assert.match((await handler({systemPrompt:'base'}, {hasUI:false})).systemPrompt, /version-2/);
});

test('rule loading failure is not silently ignored', async () => {
  let handler;
  registerContext({on(_, cb) { handler=cb; }}, async () => { throw Error('unavailable'); });
  await assert.rejects(handler({systemPrompt:'base'}), /unavailable/);
});
