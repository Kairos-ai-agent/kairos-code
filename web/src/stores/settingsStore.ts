import { create } from 'zustand';

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
  baseUrl: string;        // e.g. https://api.openai.com/v1
  apiKey: string;         // user-supplied; never persisted server-side
  model: string;          // e.g. gpt-4o
}

export interface AnthropicConfig {
  baseUrl: string;        // e.g. https://api.anthropic.com
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
      baseUrl: 'https://api.openai.com/v1',
      apiKey: '',
      model: 'gpt-4o',
    },
    anthropic: {
      baseUrl: 'https://api.anthropic.com',
      apiKey: '',
      model: 'claude-3-5-sonnet-latest',
    },
  },
};

export const useSettingsStore = create<SettingsState>((set) => ({
  ...DEFAULT,
  openDrawer: () => set({ drawerOpen: true }),
  closeDrawer: () => set({ drawerOpen: false }),
  setCoderMode: (m) => set({ coderMode: m }),
  setVoice: (patch) => set((s) => ({ voice: { ...s.voice, ...patch } })),
  setMcp: (patch) => set((s) => ({ mcp: { ...s.mcp, ...patch } })),
  setCloud: (patch) => set((s) => ({ cloud: { ...s.cloud, ...patch } })),
  setMetrics: (patch) => set((s) => ({ metrics: { ...s.metrics, ...patch } })),
  setProvider: (patch) => set((s) => ({ provider: { ...s.provider, ...patch } })),
}));
