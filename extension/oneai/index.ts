/** Thin pi adapter. Business data and policy live in the Python core. */
import type { ExtensionAPI } from '@earendil-works/pi-coding-agent';
import { Type } from 'typebox';
import { run, draftPayload, draftRequest, registerContext } from './bridge.mjs';

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: 'vault_search', label: 'Vault Search',
    description: 'Search personal Markdown notes. Refreshes changed/deleted files. Returns versioned source citations.',
    parameters: Type.Object({ query: Type.String(), k: Type.Optional(Type.Integer({ minimum: 1, maximum: 100 })) }),
    async execute(_id, params, signal) {
      const text = await run(['search', '--json', '-k', String(params.k ?? 8), params.query], undefined, signal);
      return { content: [{ type: 'text' as const, text }], details: {} };
    },
  });
  pi.registerTool({
    name: 'vault_read', label: 'Vault Read',
    description: 'Read a vault-relative Markdown note. Pass version from search for stable historical evidence.',
    parameters: Type.Object({ path: Type.String(), lines: Type.Optional(Type.String()), version: Type.Optional(Type.String()) }),
    async execute(_id, params, signal) {
      const args = ['read', params.path];
      if (params.lines) args.push('--lines', params.lines);
      if (params.version) args.push('--version', params.version);
      return { content: [{ type: 'text' as const, text: await run(args, undefined, signal) }], details: {} };
    },
  });
  pi.registerTool({
    name: 'draft_create', label: 'Save Draft',
    description: 'Save the complete manuscript you composed in THIS conversation, preserving user corrections. Local draft only; never sends. Cite verified sources. Use only for a user-requested draft.',
    parameters: Type.Object({
      title: Type.String(), body: Type.String(), instruction: Type.Optional(Type.String()),
      sources: Type.Optional(Type.Array(Type.Object({ path: Type.String(), version: Type.String(), lines: Type.String() }))),
    }),
    async execute(_id, params, signal) {
      return { content: [{ type: 'text' as const, text: await run(['draft-save'], draftPayload(params), signal) }], details: {} };
    },
  });
  pi.registerCommand('draft', {
    description: '在当前会话起草并保存手稿',
    handler: async (args, ctx) => {
      if (!args.trim()) { ctx.ui.notify('用法: /draft <起草指令>', 'warning'); return; }
      pi.sendUserMessage(draftRequest(args), { deliverAs: 'followUp' });
    },
  });
  pi.registerCommand('inbox', {
    description: '查看捕获和草稿',
    handler: async (_args, ctx) => ctx.ui.notify(JSON.parse(await run(['inbox'])).join('\n') || 'inbox 为空', 'info'),
  });
  pi.registerCommand('reindex', {
    description: '重建索引（搜索会自动同步）',
    handler: async (_args, ctx) => { await run(['index']); ctx.ui.notify('索引已更新', 'info'); },
  });
  registerContext(pi);
}
