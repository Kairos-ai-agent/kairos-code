/**
 * Intent classification — should this message go to the chat
 * endpoint (single-turn LLM reply) or the task endpoint (full
 * Coder ↔ Reviewer loop)?
 *
 * R38.6: the user removed the manual Chat/Task toggle from the
 * composer. The frontend now classifies intent automatically
 * using a small rule-based heuristic:
 *
 *   - **chat** (single-turn): the user is asking a question, having
 *     a casual conversation, or asking for an explanation. No
 *     code is being changed. The fastest path: one LLM call, no
 *     Reviewer, no plan.
 *   - **task** (full loop): the user is asking the agent to do
 *     something that produces or modifies code. The full
 *     Coder ↔ Reviewer loop runs.
 *
 * The heuristic is intentionally simple — no LLM call. We look
 * for:
 *   - **Imperative keywords** (English + Chinese): "build",
 *     "create", "fix", "implement", "修", "实现", etc. → task.
 *   - **Question patterns**: starts with what/why/how/什么/
 *     为什么, ends with "?", has a question mark followed by
 *     a question word → chat.
 *   - **Code blocks** (triple-backtick, "function", "def "): the
 *     user is sharing code, usually for review or analysis →
 *     task (the agent will edit the file).
 *   - **Long technical messages** (>120 chars with technical
 *     tokens like "import", "class", "async"): task.
 *   - **Short casual** (<20 chars, no imperative keywords):
 *     chat ("hi", "ok", "thanks").
 *   - **Default**: chat (faster, safer, less likely to trigger
 *     a long loop the user didn't ask for).
 *
 * Tests live in tests/test_r37_ui_source.py and pin each
 * category so we can iterate on the heuristic without surprises.
 */

export type Intent = 'chat' | 'task';

// Imperative verbs / task-ish keywords (English + Chinese).
// We match these anywhere in the message (case-insensitive, no
// word boundary) because users often drop punctuation ("fix
// the bug" → "fix the bug").
const TASK_KEYWORDS: readonly string[] = [
  // English imperatives
  'build', 'create', 'make ', 'fix ', 'implement', 'add ',
  'remove ', 'delete ', 'update ', 'change ', 'modify ',
  'rewrite', 'refactor', 'optimi', 'replac', 'patch ',
  'deploy', 'execute', 'run ', 'test ', 'debug', 'find ',
  'search ', 'add a ', 'add the ', 'create a ', 'create the ',
  'build a ', 'build the ', 'fix a ', 'fix the ', 'implement a ',
  // Chinese imperatives
  '帮我', '实现', '写一下', '写个', '改一下', '改一', '改成', '删了',
  '删掉', '添加', '创建', '创建一', '优化', '部署', '调试', '修一下',
  '修一', '做个', '加一', '加个', '去掉', '重写', '重构',
  '删除', '改动', '修改', '改写',
];

// Question patterns (English + Chinese). Match at the start of
// the message (after trimming whitespace) or as a question mark
// followed by a question word.
const CHAT_QUESTION_STARTS: readonly RegExp[] = [
  /^(what|why|how|when|where|which|who|can you|could you|do you|does|is|are|was|were|will|would|should|tell me|explain|describe|show me)\b/i,
  /^(什么|为什么|咋|怎么|哪里|哪个|谁|解释|说明|告诉我|给我讲|能|可不可以|是不是|会不会|能否)/i,
];

// Code-like patterns. A message with one of these is almost
// always a task (the user is sharing code, usually asking the
// agent to fix or extend it).
const CODE_PATTERNS: readonly RegExp[] = [
  /```[\s\S]+```/,                    // fenced code block
  /(^|\n)\s*(import |from |function |class |def |const |let |var |async |await |return )/,
  /\.(py|js|ts|tsx|jsx|go|rs|java|c|cpp|cs|rb|php|sh|yaml|json|toml|sql|html|css)\b/,
  /^\s*<\w+[\s>]/,                    // JSX / HTML
];

// Single-line "?" at the end is a strong chat signal.
const ENDS_WITH_QUESTION = /[？?]\s*$/;

/**
 * Classify a user message as either a "chat" (single-turn) or a
 * "task" (full Coder ↔ Reviewer loop).
 *
 * Returns 'task' if the message looks like an imperative request
 * with code work, 'chat' otherwise. The default is 'chat' to
 * avoid spinning up the loop for casual conversation.
 */
export function classifyIntent(text: string): Intent {
  const raw = (text || '').trim();
  if (!raw) return 'chat';

  const lower = raw.toLowerCase();

  // 1. Code blocks are almost always a task — the user is
  //    asking the agent to do something with code.
  if (CODE_PATTERNS.some((p) => p.test(raw))) {
    return 'task';
  }

  // 2. Question pattern (chat signal). Checked BEFORE the
  //    imperative keyword scan because a question like
  //    "what does this function do?" should be chat, not task,
  //    even though "function" matches CODE_PATTERNS.
  if (ENDS_WITH_QUESTION.test(raw)) {
    return 'chat';
  }
  if (CHAT_QUESTION_STARTS.some((p) => p.test(lower))) {
    return 'chat';
  }

  // 3. Imperative keyword (task signal).
  if (TASK_KEYWORDS.some((k) => lower.includes(k))) {
    return 'task';
  }

  // 4. Long, technical-looking messages default to task.
  //    "technical" = has at least 2 of: import / class / function
  //    / async / return / = / ().
  if (raw.length > 120) {
    const technical = ['= ', '(', ')', 'import ', 'class ',
                        'function ', 'async ', 'return '];
    const hits = technical.reduce(
      (n, tok) => n + (raw.includes(tok) ? 1 : 0), 0);
    if (hits >= 2) return 'task';
  }

  // 5. Short casual messages default to chat.
  if (raw.length < 20) return 'chat';

  // 6. Final fallback: chat (faster, less risky).
  return 'chat';
}
