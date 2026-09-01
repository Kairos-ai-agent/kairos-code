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
 * R38.6 §28.2: the model list is NO LONGER hardcoded per
 * preset. Each provider's available models change weekly
 * (DeepSeek adds new ones, OpenRouter adds 10/day, etc.) and
 * a static list goes stale immediately. The user now:
 *   1. picks a preset → endpoint URL is filled
 *   2. clicks "Fetch models" → backend hits <base>/models
 *      and the live list populates the dropdown
 *   3. picks a model from the live list (or types a custom
 *      one if the fetch failed / the model isn't listed)
 *
 * The `defaultModel` field below is the one we suggest if
 * the user clicks "Fetch" — but it's just a hint, the user
 * always picks.
 */

export interface LLMPreset {
  /** Short id used in the dropdown value (e.g. "deepseek"). */
  id: string;
  /** Human label shown in the dropdown. */
  label: string;
  /** One-line hint shown under the label in the options panel. */
  hint: string;
  /** The full chat completions URL the backend will POST to. */
  endpointUrl: string;
  /** Default model the preset is built around. Pre-fills the
   *  Model field when the user picks this preset (before they
   *  hit "Fetch"). After Fetch, this is just one option among
   *  however many the live API returned. */
  defaultModel: string;
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
    hint: 'GPT-4o / GPT-4.1 / o1 / o3 / o4',
    endpointUrl: 'https://api.openai.com/v1/chat/completions',
    defaultModel: 'gpt-4o',
    signupUrl: 'https://platform.openai.com/',
    docsUrl: 'https://platform.openai.com/docs',
    recommended: true,
  },
  {
    id: 'deepseek',
    label: 'DeepSeek',
    hint: '国内首选 · 极致性价比',
    endpointUrl: 'https://api.deepseek.com/v1/chat/completions',
    defaultModel: 'deepseek-chat',
    signupUrl: 'https://platform.deepseek.com/',
    docsUrl: 'https://api-docs.deepseek.com/',
    recommended: true,
  },
  {
    id: 'qwen',
    label: 'Qwen (Dashscope)',
    hint: 'qwen-max / qwen-plus / qwen-coder — 阿里云',
    endpointUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions',
    defaultModel: 'qwen-max',
    signupUrl: 'https://dashscope.console.aliyun.com/',
    docsUrl: 'https://help.aliyun.com/zh/model-studio/',
    recommended: true,
  },
  {
    id: 'glm',
    label: 'GLM (Zhipu)',
    hint: 'glm-4-plus / glm-4-flash — 智谱 AI',
    endpointUrl: 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
    defaultModel: 'glm-4-plus',
    signupUrl: 'https://open.bigmodel.cn/',
    docsUrl: 'https://open.bigmodel.cn/dev/api',
  },
  {
    id: 'moonshot',
    label: 'Moonshot (Kimi)',
    hint: 'moonshot-v1 / kimi-k2 — 长上下文',
    endpointUrl: 'https://api.moonshot.cn/v1/chat/completions',
    defaultModel: 'moonshot-v1-128k',
    signupUrl: 'https://platform.moonshot.cn/',
    docsUrl: 'https://platform.moonshot.cn/docs/api-reference',
  },
  {
    id: 'doubao',
    label: 'Doubao (火山方舟)',
    hint: '豆包 — 字节跳动',
    endpointUrl: 'https://ark.cn-beijing.volces.com/api/v3/chat/completions',
    defaultModel: 'doubao-pro-32k',
    signupUrl: 'https://www.volcengine.com/product/doubao',
    docsUrl: 'https://www.volcengine.com/docs/82379',
  },
  {
    id: 'ollama-cloud',
    label: 'Ollama (local)',
    hint: 'localhost:11434 — 本地离线',
    endpointUrl: 'http://localhost:11434/v1/chat/completions',
    defaultModel: 'qwen2.5-coder:7b',
    signupUrl: 'https://ollama.com/',
    docsUrl: 'https://github.com/ollama/ollama',
  },
  {
    id: 'openrouter',
    label: 'OpenRouter',
    hint: '统一接口访问 200+ 模型',
    endpointUrl: 'https://openrouter.ai/api/v1/chat/completions',
    defaultModel: 'anthropic/claude-sonnet-4-20250514',
    signupUrl: 'https://openrouter.ai/',
    docsUrl: 'https://openrouter.ai/docs',
  },
  {
    id: 'custom',
    label: 'Custom (任意 OpenAI 兼容 API)',
    hint: '自填 endpoint URL 和 model',
    endpointUrl: '',  // blank — user types
    defaultModel: '',
    signupUrl: '',
    docsUrl: '',
  },
  // R38.6 §34: borrowed from OpenCode's 75+ provider coverage.
  // These presets populate the model dropdown + auto-detect
  // via URL / model-name matching.
  {
    id: 'ollama-local',
    label: 'Ollama (local)',
    hint: 'localhost:11434 — 本地离线',
    endpointUrl: 'http://localhost:11434/v1/chat/completions',
    defaultModel: 'qwen2.5-coder:7b',
    signupUrl: 'https://ollama.com/',
    docsUrl: 'https://github.com/ollama/ollama',
  },
  {
    id: 'groq',
    label: 'Groq',
    hint: 'LPU 极速推理 · 免费额度',
    endpointUrl: 'https://api.groq.com/openai/v1/chat/completions',
    defaultModel: 'llama-3.3-70b-versatile',
    signupUrl: 'https://console.groq.com/',
    docsUrl: 'https://console.groq.com/docs',
  },
  {
    id: 'together',
    label: 'Together.ai',
    hint: '开源模型 API · 大免费额度',
    endpointUrl: 'https://api.together.xyz/v1/chat/completions',
    defaultModel: 'meta-llama/Llama-3.3-70B-Instruct-Turbo',
    signupUrl: 'https://api.together.xyz/',
    docsUrl: 'https://docs.together.ai/',
  },
  {
    id: 'fireworks',
    label: 'Fireworks.ai',
    hint: 'Fast 开源推理 · $1 free',
    endpointUrl: 'https://api.fireworks.ai/inference/v1/chat/completions',
    defaultModel: 'accounts/fireworks/models/llama-v3p3-70b-instruct',
    signupUrl: 'https://fireworks.ai/',
    docsUrl: 'https://docs.fireworks.ai/',
  },
  {
    id: 'mistral',
    label: 'Mistral AI',
    hint: 'Codestral / Mistral-Large',
    endpointUrl: 'https://api.mistral.ai/v1/chat/completions',
    defaultModel: 'mistral-large-latest',
    signupUrl: 'https://console.mistral.ai/',
    docsUrl: 'https://docs.mistral.ai/',
  },
  {
    id: 'xai',
    label: 'xAI (Grok)',
    hint: 'Grok-2 / Grok-2-mini',
    endpointUrl: 'https://api.x.ai/v1/chat/completions',
    defaultModel: 'grok-2-latest',
    signupUrl: 'https://console.x.ai/',
    docsUrl: 'https://docs.x.ai/',
  },
  {
    id: 'perplexity',
    label: 'Perplexity',
    hint: '联网增强搜索 · 实时',
    endpointUrl: 'https://api.perplexity.ai/v1/chat/completions',
    defaultModel: 'llama-3.1-sonar-large-128k-online',
    signupUrl: 'https://www.perplexity.ai/settings/api',
    docsUrl: 'https://docs.perplexity.ai/',
  },
  {
    id: 'cohere',
    label: 'Cohere',
    hint: 'Command-R / Command-R+',
    endpointUrl: 'https://api.cohere.ai/compatibility/v1/chat/completions',
    defaultModel: 'command-r-plus',
    signupUrl: 'https://dashboard.cohere.com/',
    docsUrl: 'https://docs.cohere.com/',
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
    if (model.startsWith('moonshot') || model.startsWith('kimi'))
      return 'moonshot';
    if (model.startsWith('doubao')) return 'doubao';
  }
  return 'custom';
}


/** Return the preset for a given id, or the OpenAI default. */
export function getPreset(id: string): LLMPreset {
  return LLM_PRESETS.find((p) => p.id === id) || LLM_PRESETS[0];
}


/** Sentinel value used in the Model Select for "the user typed
 *  a model not in the fetched list". Selecting it shows a
 *  free-text input below the Select. */
export const CUSTOM_MODEL = '__custom__';
