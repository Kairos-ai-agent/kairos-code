import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

export type CoderMode = 'default' | 'read_only' | 'sandbox';

export type TtsProvider = 'mock' | 'edge';
export type SttProvider = 'mock' | 'whisper';
/** Where speech comes from: the app's engine, the browser's, or try then fall back. */
export type VoiceEngine = 'auto' | 'server' | 'browser';

// R37: LLM provider is now exclusively one of two custom endpoints
// (OpenAI-compatible or Anthropic-compatible). The "ollama /
// deepseek / custom" options from R8 are gone — the user wanted a
// focused, simple UI for the two providers that matter.
export type LlmProvider = 'openai' | 'anthropic';

export interface VoiceSettings {
  ttsProvider: TtsProvider;
  /** Engine voice name. Empty means "pick one that matches the reply's language". */
  ttsVoice: string;
  sttProvider: SttProvider;
  sttLanguage: string;
  /** Read new replies aloud. Voice mode turns this on. */
  autoPlay: boolean;
  /**
   * Voice mode: the agent writes for the ear — short, no Markdown — and its
   * replies are spoken. The flag is sent with each message, so the agent knows
   * to answer briefly instead of the interface trimming a wall of text.
   */
  voiceMode: boolean;
  /** Percent, -90..200. 0 keeps the engine's own pace. */
  rate: number;
  /** Hertz, -100..100. 0 keeps the engine's own pitch. */
  pitch: number;
  /** Percent, -100..100. */
  volume: number;
  engine: VoiceEngine;
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
    // Empty on purpose: the server then picks a voice that matches the reply's
    // own language, so a Chinese answer is not read by an English voice. The
    // voice picker overrides it.
    ttsVoice: '',
    sttProvider: 'mock',
    sttLanguage: 'en',
    autoPlay: false,
    voiceMode: false,
    rate: 0,
    pitch: 0,
    volume: 0,
    engine: 'auto',
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
      setVoice: (patch) => set((s) => {
        const next = { ...s.voice, ...patch };
        // Voice mode means "write for the ear *and* speak it". Switching it on
        // sets the speaking switch too, so the two cannot disagree.
        if (patch.voiceMode) next.autoPlay = true;
        return { voice: next };
      }),
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
      version: 2,
      // v1 persisted a hard-coded English default voice, which reads a Chinese
      // answer in English. Empty now means "match the reply's language", so
      // only the untouched default is migrated — a voice the user chose stays.
      migrate: (persisted: any, from: number) => {
        if (from < 2 && persisted?.voice?.ttsVoice === 'en-US-AriaNeural') {
          persisted.voice.ttsVoice = '';
        }
        return persisted;
      },
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
