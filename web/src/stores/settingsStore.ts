import { create } from 'zustand';

export type CoderMode = 'default' | 'read_only' | 'sandbox';

export type TtsProvider = 'mock' | 'edge';
export type SttProvider = 'mock' | 'whisper';

// LLM provider the Coder/Reviewer agents use. Each value maps to a
// concrete kairos.llm.providers.* implementation on the backend.
export type LlmProvider = 'openai' | 'anthropic' | 'ollama' | 'deepseek' | 'custom';

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

export interface ProviderSettings {
  // Active provider — drives ModelRouter.get_provider_for_role()
  active: LlmProvider;
  // Per-provider detail
  ollamaBaseUrl: string;
  ollamaModel: string;
  // Free-form env-var name for the active provider's API key.
  apiKeyEnv: string;
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
    ollamaBaseUrl: 'http://127.0.0.1:11434',
    ollamaModel: 'qwen2.5-coder:7b',
    apiKeyEnv: 'OPENAI_API_KEY',
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
