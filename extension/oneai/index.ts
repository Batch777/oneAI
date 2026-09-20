/**
 * oneAI — personal assistant extension for pi.
 *
 * Bridges pi's agent/TUI to the oneAI Python core via the `oneai` CLI.
 * - Tools for the LLM: vault_search, vault_read, draft_create (confirm-gated)
 * - Slash commands: /draft /inbox /reindex /vault
 * - System-prompt guidelines: Chinese replies, mandatory [[path#Lx-Ly]] citations
 *
 * Install: symlink this directory into ~/.pi/agent/extensions/oneai
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

// Resolve the CLI: env override, else the repo venv, else PATH.
const ONEAI =
	process.env.ONEAI_CLI ?? "/Users/henrypotter/oneAI/.venv/bin/oneai";

async function run(
	pi: ExtensionAPI,
	args: string[],
	signal?: AbortSignal,
): Promise<string> {
	const res = await pi.exec(ONEAI, args, { signal, timeout: 120_000 });
	if (res.code !== 0) {
		throw new Error(`oneai ${args[0]} failed: ${res.stderr || res.stdout}`);
	}
	return res.stdout.trim();
}

export default function (pi: ExtensionAPI) {
	// --- LLM tools -------------------------------------------------------

	pi.registerTool({
		name: "vault_search",
		label: "Vault Search",
		description:
			"Search the user's personal knowledge vault (Markdown notes). " +
			"Returns JSON array: path, start_line, end_line, heading, citation, text.",
		promptSnippet: "Search the user's personal knowledge vault",
		promptGuidelines: [
			"Use vault_search before answering anything about the user's life, background, contacts, or preferences.",
			"Cite every claim from the vault as [[path#Lstart-Lend]] using the citation field.",
		],
		parameters: Type.Object({
			query: Type.String({ description: "Search keywords (Chinese OK)" }),
			k: Type.Optional(Type.Number({ description: "Max results, default 8" })),
		}),
		async execute(_id, params, signal) {
			const args = ["search", "--json", "-k", String(params.k ?? 8), params.query];
			return { content: [{ type: "text" as const, text: await run(pi, args, signal) }], details: {} };
		},
	});

	pi.registerTool({
		name: "vault_read",
		label: "Vault Read",
		description:
			"Read a vault note (JSON: metadata + body). Optionally pass a line " +
			"range from a vault_search citation, e.g. '8-16'.",
		promptGuidelines: ["Use vault_read to see full note content beyond vault_search chunks."],
		parameters: Type.Object({
			path: Type.String({ description: "Vault-relative path, e.g. facts/education.md" }),
			lines: Type.Optional(Type.String({ description: "Line range like '8-16'" })),
		}),
		async execute(_id, params, signal) {
			const args = ["read", params.path];
			if (params.lines) args.push("--lines", params.lines);
			return { content: [{ type: "text" as const, text: await run(pi, args, signal) }], details: {} };
		},
	});

	pi.registerTool({
		name: "draft_create",
		label: "Draft Manuscript",
		description:
			"Draft a manuscript (email, intro, etc.) grounded in vault context. " +
			"Saved to inbox/drafts/ with status 'drafted'; the user reviews and " +
			"approves manually. NEVER sends anything.",
		promptGuidelines: [
			"Only use draft_create when the user explicitly asks to draft something; it requires user confirmation.",
		],
		parameters: Type.Object({
			instruction: Type.String({ description: "What to draft" }),
		}),
		async execute(_id, params, signal) {
			const text = await run(pi, ["draft", "--json", params.instruction], signal);
			return { content: [{ type: "text" as const, text }], details: {} };
		},
	});

	// Drafts require explicit user confirmation (per project decision:
	// drafting happens if and only if the user asks — never automatically).
	pi.on("tool_call", async (event, ctx) => {
		if (event.toolName !== "draft_create" || !ctx.hasUI) return;
		const ok = await ctx.ui.confirm(
			"AI Draft",
			`允许起草手稿？\n\n指令: ${(event.input as { instruction?: string }).instruction}`,
		);
		if (!ok) return { block: true, reason: "用户拒绝了本次起草" };
	});

	// --- Slash commands ----------------------------------------------------

	pi.registerCommand("draft", {
		description: "起草手稿到 inbox/drafts/（唯一入口，绝不自动）",
		handler: async (args, ctx) => {
			if (!args.trim()) {
				ctx.ui.notify("用法: /draft <起草指令>", "warning");
				return;
			}
			ctx.ui.notify("起草中…", "info");
			try {
				const out = await run(pi, ["draft", "--json", args]);
				const { path } = JSON.parse(out);
				ctx.ui.notify(`✔ 手稿已保存: ${path}（编辑后把 status 改为 approved）`, "info");
			} catch (e) {
				ctx.ui.notify(`起草失败: ${e}`, "error");
			}
		},
	});

	pi.registerCommand("inbox", {
		description: "列出 inbox 中的捕获与手稿",
		handler: async (_args, ctx) => {
			const files: string[] = JSON.parse(await run(pi, ["inbox"]));
			ctx.ui.notify(files.length ? files.join("\n") : "inbox 为空", "info");
		},
	});

	pi.registerCommand("reindex", {
		description: "重建 vault 检索索引",
		handler: async (_args, ctx) => {
			ctx.ui.notify("重建索引中…", "info");
			await run(pi, ["index"]);
			ctx.ui.notify("✔ 索引已重建", "info");
		},
	});

	// --- System prompt guidelines ------------------------------------------

	pi.on("before_agent_start", async (event) => ({
		systemPrompt:
			event.systemPrompt +
			"\n\n# oneAI 个人助理准则\n" +
			"- 默认用中文回复。\n" +
			"- 涉及用户的个人问题，先用 vault_search 检索，不要凭记忆回答。\n" +
			"- 来自 vault 的每条信息都要标注引用 [[path#Lstart-Lend]]。\n" +
			"- 起草类请求用 draft_create（会弹窗经用户确认）；绝不自动发送任何内容。",
	}));
}
