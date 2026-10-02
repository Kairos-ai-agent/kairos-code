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

// ---------------------------------------------------------------------------
// Bots (IM connectors: WeChat and friends)
// ---------------------------------------------------------------------------
// These are the calls the *operator* of a connector makes: manage accounts,
// see which conversation maps to which workspace, and inspect the reply
// queue. The connector itself lives outside this app (it holds a WeChat
// login), and talks to the signed /api/im/{account}/... endpoints directly.
export interface ImAccount {
  account_id: string;
  name: string;
  enabled: boolean;
  created_at: number;
  pending: number;
  conversations: number;
}

export interface ImBinding {
  account_id: string;
  chat_id: string;
  project_id: string;
  updated_at: number;
}

/** A pairing: the click-to-scan way to connect a chat client.
 *
 * The UI only ever *reads* one of these. The pairing's secret goes to whoever
 * claimed the code (the connector on this machine), and the account's real
 * secret is handed over in the same handshake, so neither is ever typed,
 * copied, or displayed here.
 */
export interface ImPairing {
  pairing_id: string;
  status: 'waiting' | 'claimed' | 'qr' | 'scanned' | 'bound' | 'expired';
  expires_at: number;
  account_id: string;
  display_name: string;
  has_qr: boolean;
  expired: boolean;
}

export async function createImPairing(): Promise<ImPairing> {
  return (await api.post('/im/pairings')).data;
}

export async function getImPairing(pairingId: string): Promise<ImPairing> {
  return (await api.get(`/im/pairings/${pairingId}`)).data;
}

export async function cancelImPairing(pairingId: string): Promise<void> {
  await api.delete(`/im/pairings/${pairingId}`);
}

/** The QR is served as an image, so the panel can point an <img> at it. */
export function imPairingQrUrl(pairingId: string): string {
  const origin = typeof window === 'undefined' ? '' : window.location.origin;
  return `${origin}/api/im/pairings/${pairingId}/qr.png`;
}

export async function listImAccounts(): Promise<{ accounts: ImAccount[] }> {
  const r = await api.get('/im/accounts');
  return r.data;
}

export async function upsertImAccount(body: {
  account_id: string;
  name?: string;
  secret?: string;
  enabled?: boolean;
}): Promise<{ ok: boolean }> {
  const r = await api.post('/im/accounts', body);
  return r.data;
}

export async function deleteImAccount(accountId: string): Promise<any> {
  const r = await api.delete(`/im/accounts/${encodeURIComponent(accountId)}`);
  return r.data;
}

export async function listImBindings(
  accountId?: string,
): Promise<{ bindings: ImBinding[] }> {
  const r = await api.get('/im/bindings', {
    params: accountId ? { account_id: accountId } : {},
  });
  return r.data;
}

export async function deleteImBinding(
  accountId: string,
  chatId: string,
): Promise<any> {
  const r = await api.delete(
    `/im/bindings/${encodeURIComponent(accountId)}/${encodeURIComponent(chatId)}`,
  );
  return r.data;
}
