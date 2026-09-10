import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

export type CoderMode = 'default' | 'read_only' | 'sandbox';

export type TtsProvider = 'mock' | 'edge';
export type SttProvider = 'mock' | 'whisper';

// R37: LLM provider is now exclusively one of two custom endpoints
// (OpenAI-compatible or Anthropic-compatible). The "ollama /
// deepseek / custom" options from R8 are gone — the user wanted a
// focused, simple UI for the two providers that matter.
export type LlmProvider = 'openai' | 'anthropic';

export interface VoiceSettings {
  ttsProvider: TtsProvider;
  ttsVoice: string;
  sttProvider: SttProvider;
  sttLanguage: string;
  autoPlay: boolean;
}

export interface McpSettings {
  enabledServers: string[];
  permissionPrompt: boolean;
}

export interface CloudSettings {
  s3Bucket: string;
  s3Region: string;
  s3Endpoint: string;
  addressingStyle: 'auto' | 'virtual' | 'path';
}

export interface MetricsSettings {
  showInFooter: boolean;
}

export interface OpenAIConfig {
  /**
   * Full endpoint URL the test probe will hit (e.g.
   * `https://api.openai.com/v1/chat/completions` or
   * `https://api.example.com/v1/chat/completions`).
   * When set, this overrides the auto-construct logic entirely —
   * the probe just POSTs to this URL as-is. The user owns the path.
   * R38: replaces the brittle "Base URL + /v1/chat/completions"
   * auto-append that broke on proxies with non-standard layouts.
   */
  endpointUrl: string;
  /**
   * Base URL the orchestrator uses for real chat calls. Usually
   * derived from `endpointUrl` (strip the last path segment), but
   * the user can override if the orchestrator's chat path differs
   * from the test probe path. Kept for backward compat with the
   * R37 field name.
   */
  baseUrl: string;
  apiKey: string;         // user-supplied; never persisted server-side
  model: string;          // e.g. gpt-4o
}

export interface AnthropicConfig {
  /**
   * Full endpoint URL the test probe will hit (e.g.
   * `https://api.anthropic.com/v1/messages`).
   * When set, the probe POSTs to this URL as-is. See OpenAIConfig
   * for the rationale.
   */
  endpointUrl: string;
  baseUrl: string;
  apiKey: string;
  model: string;          // e.g. claude-3-5-sonnet-latest
}

export interface ProviderSettings {
  active: LlmProvider;
  openai: OpenAIConfig;
  anthropic: AnthropicConfig;
}

export interface SettingsState {
  drawerOpen: boolean;
  coderMode: CoderMode;
  voice: VoiceSettings;
  mcp: McpSettings;
  cloud: CloudSettings;
  metrics: MetricsSettings;
  provider: ProviderSettings;

  openDrawer: () => void;
  closeDrawer: () => void;
  setCoderMode: (m: CoderMode) => void;
  setVoice: (patch: Partial<VoiceSettings>) => void;
  setMcp: (patch: Partial<McpSettings>) => void;
  setCloud: (patch: Partial<CloudSettings>) => void;
  setMetrics: (patch: Partial<MetricsSettings>) => void;
  setProvider: (patch: Partial<ProviderSettings>) => void;
}

const DEFAULT: Omit<SettingsState,
  'openDrawer' | 'closeDrawer' | 'setCoderMode' | 'setVoice' | 'setMcp' |
  'setCloud' | 'setMetrics' | 'setProvider'
> = {
  drawerOpen: false,
  coderMode: 'default',
  voice: {
    ttsProvider: 'edge',
    ttsVoice: 'en-US-AriaNeural',
    sttProvider: 'mock',
    sttLanguage: 'en',
    autoPlay: false,
  },
  mcp: {
    enabledServers: ['filesystem'],
    permissionPrompt: true,
  },
  cloud: {
    s3Bucket: '',
    s3Region: 'us-east-1',
    s3Endpoint: '',
    addressingStyle: 'auto',
  },
  metrics: {
    showInFooter: true,
  },
  provider: {
    active: 'openai',
    openai: {
      endpointUrl: 'https://api.openai.com/v1/chat/completions',
      baseUrl: 'https://api.openai.com/v1',
      apiKey: '',
      model: 'gpt-4o',
    },
    anthropic: {
      endpointUrl: 'https://api.anthropic.com/v1/messages',
      baseUrl: 'https://api.anthropic.com',
      apiKey: '',
      model: 'claude-3-5-sonnet-latest',
    },
  },
};

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      ...DEFAULT,
      openDrawer: () => set({ drawerOpen: true }),
      closeDrawer: () => set({ drawerOpen: false }),
      setCoderMode: (m) => set({ coderMode: m }),
      setVoice: (patch) => set((s) => ({ voice: { ...s.voice, ...patch } })),
      setMcp: (patch) => set((s) => ({ mcp: { ...s.mcp, ...patch } })),
      setCloud: (patch) => set((s) => ({ cloud: { ...s.cloud, ...patch } })),
      setMetrics: (patch) => set((s) => ({ metrics: { ...s.metrics, ...patch } })),
      setProvider: (patch) => set((s) => ({ provider: { ...s.provider, ...patch } })),
    }),
    {
      // R38.6 §21: Persist the user's settings to localStorage so
      // they survive a browser refresh. The user reported
      // "llm model设置又丢失了" (LLM model lost again after refresh)
      // — the backend had it (R38.6 §14), the AppLayout fetched it
      // on mount (R38.6 §16), but if the backend is stale (pre-§14
      // code, or in offline / first-paint) the store would re-mount
      // with DEFAULT and overwrite the in-flight fetch. localStorage
      // gives us a stable baseline that's the user's most recent
      // explicit state.
      name: 'kairos-settings',
      storage: createJSONStorage(() => localStorage),
      version: 1,
      // partialize: only persist the user-configurable sections.
      // Live UI state (`drawerOpen`) is excluded — that should
      // always start closed on reload. `coderMode` is also
      // excluded — it's a runtime sandbox hint, not a user
      // preference, and persisting it could leave the user in
      // read_only after a refresh.
      partialize: (s) => ({
        provider: s.provider,
        voice: s.voice,
        mcp: s.mcp,
        cloud: s.cloud,
        metrics: s.metrics,
      }),
    },
  ),
);
