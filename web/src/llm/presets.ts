/**
 * LLM provider presets for the OpenAI-compatible form.
 *
 * R38.6 §28: most Chinese / open-source LLMs expose an
 * OpenAI-compatible API at a different endpoint URL with a
 * different default model. Rather than force the user to type
 * the URL from scratch, we let them pick a preset that
 * pre-fills the endpoint URL and default model. They can
 * always override either field manually.
 *
 * Each preset is a single source of truth. The endpointUrl
 * is the full path the backend will POST to (chat
 * completions). The baseUrl is auto-derived by stripping the
 * last path segment, so the orchestrator's chat calls can
 * use it (some providers need a different base for streaming
 * vs one-shot).
 */

export interface LLMPreset {
  /** Short id used in the dropdown value (e.g. "deepseek"). */
  id: string;
  /** Human label shown in the dropdown. */
  label: string;
  /** One-line hint shown under the label. */
  hint: string;
  /** The full chat completions URL the backend will POST to. */
  endpointUrl: string;
  /** Default model the preset is built around. */
  model: string;
  /** Where to get an API key. */
  signupUrl: string;
  /** Where to look for documentation. */
  docsUrl: string;
  /** If true, this is the "default" / most-used option
   * (rendered at the top of the dropdown). */
  recommended?: boolean;
}

export const LLM_PRESETS: LLMPreset[] = [
  {
    id: 'openai',
    label: 'OpenAI',
    hint: 'GPT-4o / GPT-4.1 / o3 — the default',
    endpointUrl: 'https://api.openai.com/v1/chat/completions',
    model: 'gpt-4o',
    signupUrl: 'https://platform.openai.com/',
    docsUrl: 'https://platform.openai.com/docs',
    recommended: true,
  },
  {
    id: 'deepseek',
    label: 'DeepSeek',
    hint: 'deepseek-chat / deepseek-reasoner — 国内首选',
    endpointUrl: 'https://api.deepseek.com/v1/chat/completions',
    model: 'deepseek-chat',
    signupUrl: 'https://platform.deepseek.com/',
    docsUrl: 'https://api-docs.deepseek.com/',
    recommended: true,
  },
  {
    id: 'qwen',
    label: 'Qwen (Dashscope)',
    hint: 'qwen-max / qwen-plus / qwen-coder — 阿里云',
    endpointUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
    model: 'qwen-max',
    signupUrl: 'https://dashscope.console.aliyun.com/',
    docsUrl: 'https://help.aliyun.com/zh/model-studio/',
    recommended: true,
  },
  {
    id: 'glm',
    label: 'GLM (Zhipuai)',
    hint: 'glm-4 / glm-4-plus — 智谱 AI',
    endpointUrl: 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
    model: 'glm-4-plus',
    signupUrl: 'https://open.bigmodel.cn/',
    docsUrl: 'https://open.bigmodel.cn/dev/api',
  },
  {
    id: 'moonshot',
    label: 'Moonshot (Kimi)',
    hint: 'moonshot-v1-128k — 长上下文',
    endpointUrl: 'https://api.moonshot.cn/v1/chat/completions',
    model: 'moonshot-v1-128k',
    signupUrl: 'https://platform.moonshot.cn/',
    docsUrl: 'https://platform.moonshot.cn/docs/api-reference',
  },
  {
    id: 'ollama-cloud',
    label: 'Ollama (local)',
    hint: 'localhost:11434 / qwen2.5 / llama3 — 本地离线',
    endpointUrl: 'http://localhost:11434/v1/chat/completions',
    model: 'qwen2.5-coder:7b',
    signupUrl: 'https://ollama.com/',
    docsUrl: 'https://github.com/ollama/ollama',
  },
  {
    id: 'openrouter',
    label: 'OpenRouter',
    hint: 'OpenAI 兼容，统一接口访问 200+ 模型',
    endpointUrl: 'https://openrouter.ai/api/v1/chat/completions',
    model: 'anthropic/claude-sonnet-4-20250514',
    signupUrl: 'https://openrouter.ai/',
    docsUrl: 'https://openrouter.ai/docs',
  },
  {
    id: 'custom',
    label: 'Custom (任意 OpenAI 兼容 API)',
    hint: '自填 endpoint URL 和 model — 默认 OpenAI 字段',
    endpointUrl: '',  // blank — user types
    model: '',
    signupUrl: '',
    docsUrl: '',
  },
];


/** Guess which preset a given (endpointUrl, model) pair matches.
 * Used to auto-select the dropdown when the user loads existing
 * settings. Returns the first matching preset id, or 'custom'
 * if no preset matches.
 */
export function matchPreset(endpointUrl: string, model: string): string {
  if (!endpointUrl) return 'openai';
  const lower = endpointUrl.toLowerCase();
  for (const p of LLM_PRESETS) {
    if (p.id === 'custom') continue;
    if (p.endpointUrl && lower.includes(
        p.endpointUrl.replace(/^https?:\/\//, '').split('/')[0])) {
      return p.id;
    }
  }
  // Model name hint
  if (model) {
    if (model.startsWith('deepseek')) return 'deepseek';
    if (model.startsWith('qwen')) return 'qwen';
    if (model.startsWith('glm-')) return 'glm';
    if (model.startsWith('moonshot')) return 'moonshot';
  }
  return 'custom';
}


/** Return the preset for a given id, or the OpenAI default. */
export function getPreset(id: string): LLMPreset {
  return LLM_PRESETS.find((p) => p.id === id) || LLM_PRESETS[0];
}
