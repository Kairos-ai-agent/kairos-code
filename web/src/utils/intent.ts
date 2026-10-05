/**
 * Intent classification — does this message go to the chat endpoint
 * (single-turn reply, every tool available) or the task endpoint (the
 * full Coder ↔ Reviewer loop)?
 *
 * R40: the loop is now the **exception**, not the default.
 *
 * The loop runs several rounds — each one a Coder call, a tool batch and a
 * Reviewer call — so a request that lands there costs minutes and ends in a
 * reviewed diff. That is the right shape for a genuinely long job, and the
 * wrong shape for "fix this line": for everything else, one turn with tools
 * answers faster and just as well. (The single-turn path is not crippled —
 * it carries the same tool set, `file_edit` and `terminal` included; it just
 * skips the multi-round review.)
 *
 * So:
 *   - **chat** (default): questions, discussion, small edits, single-file
 *     work, anything that fits in a turn.
 *   - **task** (loop): explicitly long work only — a refactor / migration /
 *     rewrite, building a whole app or service, multi-file or batch jobs,
 *     end-to-end pipelines, a brief that spells out several requirements at
 *     once, or the user simply asking for the loop ("走 loop").
 *
 * The heuristic stays rule-based and synchronous — no LLM call, no latency
 * before the message is sent. The cases are pinned in
 * tests/test_r37_ui_source.py, which runs this module through Node.
 */

export type Intent = 'chat' | 'task';

// Asking for the loop outright always wins.
const LOOP_PHRASES: readonly string[] = [
  '走 loop', '走loop', '用 loop', '用loop', '跑 loop', '跑loop',
  '完整闭环', '闭环迭代', '迭代到通过', '自己评审', '多轮迭代', '循环迭代',
  'run the loop', 'use the loop', 'with the loop', 'loop it',
];

// Scope words and phrases that imply several files or several steps. These
// are the only keywords that still escalate on their own — the old list
// escalated on any imperative ("帮我", "fix ", "add "), which sent almost
// every message through the loop.
const LONG_TASK_PATTERNS: readonly RegExp[] = [
  // Chinese: multi-step / whole-project scope
  /重构|重写|迁移|从零(开始)?|整个项目|全项目|整个仓库|所有文件|多个文件|批量|端到端|流水线/,
  // Chinese: "实现/搭建/开发 … (系统|平台|服务|应用|框架|模块|API)"
  /(实现|搭建|开发|设计并实现|做一个|做一套|搭一个|写一套)[^。\n]{0,14}(系统|平台|服务|应用|app|框架|流水线|完整功能|模块|接口|api)/i,
  // Chinese: "把所有/全部 … 都 …"
  /(把|将)[^。\n]{0,24}(全部|所有|都)[^。\n]{0,12}(改|换|删|加|迁移|升级)/,
  // English: multi-step scope
  /\b(refactor|refactoring|migrat\w*|rewrite|rewriting|scaffold\w*|end-to-end|entire (project|repo|codebase)|whole (project|repo|codebase)|multiple files|across the codebase|batch|pipelines?)\b/i,
  // English: "build/implement/create a … (app|system|service|platform|api)"
  /\b(build|implement|create|develop|design|write)\b[^.\n]{0,24}\b(app|application|system|service|platform|framework|pipeline|module|api|backend|frontend|cli)\b/i,
];

// A brief that spells out several requirements is a long job even with no
// scope word at all.
function looksLikeBrief(raw: string): boolean {
  const lines = raw.split(/\r?\n/);
  const items = lines.filter((l) => /^\s*(\d[).、]|[-*•])\s+\S/.test(l));
  if (items.length >= 3) return true;
  return items.length >= 2 && raw.length > 160;
}

// Question patterns. Checked *after* the long-task signals, so
// "能帮我重构整个项目吗？" is still a refactor request.
const CHAT_QUESTION_STARTS: readonly RegExp[] = [
  /^(what|why|how|when|where|which|who|can you tell|could you tell|do you|does|is|are|was|were|will|would|should|tell me|explain|describe|show me)\b/i,
  /^(什么|为什么|咋|怎么|哪里|哪个|谁|解释|说明|告诉我|给我讲|是不是|会不会)/i,
];

// "能帮我…吗？" / "可以帮我…吗？" are requests wearing a question mark, not
// questions, so they are not short-circuited by ENDS_WITH_QUESTION.
const REQUEST_OPENER = /^(能|可以|能否|可不可以|帮我|帮忙|麻烦|请|给我)/;

// Single-line "?" at the end is a strong chat signal.
const ENDS_WITH_QUESTION = /[？?]\s*$/;

/**
 * Classify a user message as either a "chat" (single-turn, one call) or a
 * "task" (the full Coder ↔ Reviewer loop).
 *
 * Defaults to 'chat'. Only explicit long-job signals — a scope word, a
 * many-requirement brief, or the user asking for the loop — return 'task'.
 */
export function classifyIntent(text: string): Intent {
  const raw = (text || '').trim();
  if (!raw) return 'chat';

  const lower = raw.toLowerCase();

  // 1. The user asked for the loop by name.
  if (LOOP_PHRASES.some((p) => lower.includes(p))) {
    return 'task';
  }

  // 2. Scope that only makes sense as a long job.
  if (LONG_TASK_PATTERNS.some((p) => p.test(raw))) {
    return 'task';
  }

  // 3. A brief with several requirements listed out.
  if (looksLikeBrief(raw)) {
    return 'task';
  }

  // 4. Everything below is chat — questions, discussion, small edits,
  //    and anything we are not sure about. The single-turn path can still
  //    use tools, so an edit that lands here is not lost work.
  if (ENDS_WITH_QUESTION.test(raw) && !REQUEST_OPENER.test(raw)) {
    return 'chat';
  }
  if (CHAT_QUESTION_STARTS.some((p) => p.test(lower))) {
    return 'chat';
  }
  return 'chat';
}
