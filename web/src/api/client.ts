import axios from 'axios';

const api = axios.create({
  baseURL: '/api',
  timeout: 120000,
});

export default api;

// WebSocket connection
type WsListener = (data: any) => void;
let ws: WebSocket | null = null;
let wsListeners: WsListener[] = [];
let wsStateListeners: ((state: 'connecting' | 'open' | 'closed') => void)[] = [];
let wsRetryCount = 0;
const WS_MAX_RETRIES = 20;
const WS_BASE_DELAY = 3000;

function setState(s: 'connecting' | 'open' | 'closed') {
  wsStateListeners.forEach((l) => l(s));
}

export function connectWebSocket(): WebSocket {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return ws;
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${protocol}//${window.location.host}/ws/collaboration`);
  setState('connecting');

  ws.onopen = () => { wsRetryCount = 0; setState('open'); };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      wsListeners.forEach((listener) => listener(data));
    } catch {
      // Drop malformed frame rather than crash the page.
    }
  };

  ws.onclose = () => {
    setState('closed');
    if (wsRetryCount < WS_MAX_RETRIES) {
      const delay = Math.min(WS_BASE_DELAY * Math.pow(1.5, wsRetryCount), 30000);
      wsRetryCount++;
      setTimeout(connectWebSocket, delay);
    }
  };

  return ws;
}

export function onWebSocketMessage(listener: WsListener) {
  wsListeners.push(listener);
  return () => {
    wsListeners = wsListeners.filter((l) => l !== listener);
  };
}

export function onWebSocketState(listener: (s: 'connecting' | 'open' | 'closed') => void) {
  wsStateListeners.push(listener);
  return () => {
    wsStateListeners = wsStateListeners.filter((l) => l !== listener);
  };
}

export const revertFile = (projectId: string, sha: string, path: string) =>
  api.post(`/projects/${projectId}/checkpoint/revert_file`, { sha, path });

// ---------------------------------------------------------------------------
// Voice mode
//
// The synthesis engine lives on the server (Microsoft Edge's neural voices, no
// key needed). The browser's own voices are the fallback, so a reply is still
// spoken when the server has no engine or no network.
// ---------------------------------------------------------------------------

export interface VoiceOption {
  name: string;
  locale: string;
  gender: string;
  friendly: string;
}

export interface VoiceList {
  engine: 'edge' | 'mock';
  source: 'live' | 'bundled';
  count: number;
  voices: VoiceOption[];
}

export interface VoiceStatus {
  engine: 'edge' | 'mock';
  available: boolean;
  online: boolean;
  voice_count: number;
  voices_source: string;
  detail: string;
}

export interface SpeakOptions {
  voice?: string;
  /** Percent. 0 is the engine's own default. */
  rate?: number;
  /** Hertz. 0 is the engine's own default. */
  pitch?: number;
  volume?: number;
  max_chars?: number;
}

export const getVoiceStatus = async (): Promise<VoiceStatus> =>
  (await api.get('/voice/status')).data;

export const getVoices = async (locale?: string): Promise<VoiceList> =>
  (await api.get('/voice/voices', { params: locale ? { locale } : undefined })).data;

/** The spoken form of a reply — used by the browser-voice fallback path. */
export const distillForSpeech = async (
  text: string,
  maxChars?: number,
): Promise<{ spoken: string; original_chars: number; spoken_chars: number }> =>
  (await api.post('/voice/distill', { text, max_chars: maxChars })).data;

/**
 * Synthesize `text` server-side.
 *
 * Resolves to `null` when there is nothing speakable (HTTP 204) and to a Blob
 * plus the engine that produced it otherwise, so the caller can tell a real
 * engine from the silent placeholder and fall back to the browser's voices.
 */
export const synthesizeSpeech = async (
  text: string,
  opts: SpeakOptions = {},
): Promise<{ blob: Blob; engine: string; spokenChars: number } | null> => {
  const resp = await api.post(
    '/voice/speak',
    {
      text,
      voice: opts.voice ?? '',
      rate: opts.rate ?? 0,
      pitch: opts.pitch ?? 0,
      volume: opts.volume ?? 0,
      max_chars: opts.max_chars,
    },
    { responseType: 'blob', validateStatus: (s) => s === 200 || s === 204 },
  );
  if (resp.status === 204) return null;
  return {
    blob: resp.data as Blob,
    engine: String(resp.headers['x-voice-engine'] ?? ''),
    spokenChars: Number(resp.headers['x-spoken-chars'] ?? 0),
  };
};