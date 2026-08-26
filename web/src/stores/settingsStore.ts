import { create } from 'zustand';

export type CoderMode = 'default' | 'read_only' | 'sandbox';

export type TtsProvider = 'mock' | 'edge';
export type SttProvider = 'mock' | 'whisper';

export interface VoiceSettings {
  ttsProvider: TtsProvider;
  ttsVoice: string;          // e.g. "en-US-AriaNeural", "default"
  sttProvider: SttProvider;
  sttLanguage: string;       // e.g. "en", "zh"
  autoPlay: boolean;
}

export interface McpSettings {
  // local MCP server list (the user can drop in pre-configured
  // servers; the rest comes from server config in YAML)
  enabledServers: string[];   // names
  permissionPrompt: boolean;  // ask before invoking MCP tools
}

export interface CloudSettings {
  s3Bucket: string;
  s3Region: string;
  s3Endpoint: string;          // empty = AWS
  addressingStyle: 'auto' | 'virtual' | 'path';
  // credentials are never stored client-side; the backend uses IAM.
}

export interface MetricsSettings {
  showInFooter: boolean;
}

export interface SettingsState {
  drawerOpen: boolean;
  coderMode: CoderMode;
  voice: VoiceSettings;
  mcp: McpSettings;
  cloud: CloudSettings;
  metrics: MetricsSettings;

  openDrawer: () => void;
  closeDrawer: () => void;
  setCoderMode: (m: CoderMode) => void;
  setVoice: (patch: Partial<VoiceSettings>) => void;
  setMcp: (patch: Partial<McpSettings>) => void;
  setCloud: (patch: Partial<CloudSettings>) => void;
  setMetrics: (patch: Partial<MetricsSettings>) => void;
}

const DEFAULT: Omit<SettingsState,
  'openDrawer' | 'closeDrawer' | 'setCoderMode' | 'setVoice' | 'setMcp' |
  'setCloud' | 'setMetrics'
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
}));
