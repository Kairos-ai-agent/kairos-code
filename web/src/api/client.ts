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