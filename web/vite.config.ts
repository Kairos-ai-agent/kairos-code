/// <reference types="vitest" />
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'path'
import http from 'http'

// ---------------------------------------------------------------------------
// Dev proxy → backend port. Resolved PER REQUEST (self-healing).
//
// Why not a one-shot startup probe any more: the backend default is 8900,
// start_silent.bat pins KAIROS_PORT=9527, and earlier launcher runs have used
// 8964 / 8909. When vite probed a single port once at startup and missed it
// (backend still booting, or listening somewhere else), the proxy pointed at a
// dead port and EVERY /api call came back as a bare `500 text/plain` — the UI
// showed "[HTTP 500] [upstream returned text/plain, not JSON] ..." in the
// Settings drawer, which looks exactly like "LLM 设置连接不上" even though the
// provider key/endpoint were perfectly fine.
//
// Now the target is re-resolved on every proxied request (cached ~1.5s), so the
// dev server recovers by itself when the backend starts late, restarts on a
// different port, or crashes and comes back.
// ---------------------------------------------------------------------------
const CANDIDATE_PORTS = ['8900', '9527', '8964', '8966', '8909', '8000']
const PORT_CACHE_MS = 1500
const PROBE_TIMEOUT_MS = 700

let cachedPort: string | null = null
let cachedAt = 0
let warnedAt = 0

function isBackendAlive(port: string, timeoutMs = PROBE_TIMEOUT_MS): Promise<boolean> {
  return new Promise((resolve) => {
    const req = http.get(
      { host: '127.0.0.1', port, path: '/api/health', timeout: timeoutMs },
      (res) => {
        res.resume();
        resolve(res.statusCode === 200);
      },
    );
    req.on('error', () => resolve(false));
    req.on('timeout', () => {
      req.destroy();
      resolve(false);
    });
  });
}

function candidatePorts(): string[] {
  // An explicit KAIROS_PORT is tried first, but never trusted blindly — if
  // nothing is listening there we keep probing the known ports.
  const envPort = (process.env.KAIROS_PORT || '').trim()
  return [...new Set([envPort, ...CANDIDATE_PORTS].filter(Boolean))]
}

async function resolveBackendPort(): Promise<string | null> {
  if (cachedPort && Date.now() - cachedAt < PORT_CACHE_MS) return cachedPort

  const ports = candidatePorts()
  const alive = await Promise.all(ports.map((p) => isBackendAlive(p)))
  const hit = ports.find((_, i) => alive[i])

  if (hit) {
    if (cachedPort !== hit) {
      console.log(`[kairos] dev proxy → http://127.0.0.1:${hit}`)
    }
    cachedPort = hit
    cachedAt = Date.now()
    return hit
  }

  // Nothing answered. Do NOT cache this, so the very next request re-probes and
  // the proxy heals itself the moment the backend is up.
  cachedPort = null
  if (Date.now() - warnedAt > 10_000) {
    warnedAt = Date.now()
    console.warn(
      `[kairos] no backend on ${ports.map((p) => `:${p}`).join(', ')} — start it with ` +
        '`python -m kairos.main` (or start_silent.bat); /api returns 503 until it is up.',
    )
  }
  return null
}

/**
 * When no backend is listening we answer with JSON, not the bare
 * `500 text/plain` http-proxy emits for a dead target. The UI's
 * formatError() renders `detail` verbatim, so the drawer shows an
 * actionable Chinese sentence instead of the opaque
 * "[HTTP 500] [upstream returned text/plain, not JSON]".
 */
function sendNoBackend(res: any) {
  const ports = candidatePorts().map((p) => `:${p}`).join(' / ')
  const body = JSON.stringify({
    detail:
      `kairos 后端未启动（${ports} 都没有响应）。` +
      '请先运行 start_silent.bat（或 python -m kairos.main），' +
      '后端起来后本页会自动重连，不需要重启 vite。',
  })
  res.statusCode = 503
  res.setHeader('Content-Type', 'application/json; charset=utf-8')
  res.setHeader('Cache-Control', 'no-store')
  res.end(body)
}

/**
 * Proxy options whose target is swapped per request, so the dev server never
 * gets stuck pointing at a dead port. Uses the documented `configure` hook to
 * wrap http-proxy's own web()/ws() (Vite 6 bundles http-proxy without the
 * `router` option).
 */
function kairosProxy(ws = false) {
  return {
    target: 'http://127.0.0.1:8900', // placeholder — replaced per request
    changeOrigin: true,
    ...(ws ? { ws: true } : {}),
    configure: (proxy: any) => {
      const origWeb = proxy.web.bind(proxy)
      proxy.web = (req: any, res: any, opts: any) =>
        resolveBackendPort().then((port) => {
          if (!port) return sendNoBackend(res)
          origWeb(req, res, { ...(opts || {}), target: `http://127.0.0.1:${port}` })
        })

      const origWs = proxy.ws.bind(proxy)
      proxy.ws = (req: any, socket: any, head: any, opts: any) =>
        resolveBackendPort().then((port) => {
          if (!port) {
            try {
              socket.destroy()
            } catch {
              /* socket already gone */
            }
            return
          }
          origWs(req, socket, head, { ...(opts || {}), target: `http://127.0.0.1:${port}` })
        })
    },
  }
}

export default defineConfig(async () => {
  await resolveBackendPort() // log the live port at boot

  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      port: 3000,
      proxy: {
        '/api': kairosProxy(),
        '/ws': kairosProxy(true),
      },
    },
    test: {
      environment: 'jsdom',
      globals: true,
      setupFiles: ['./src/test/setup.ts'],
      include: ['src/**/*.test.tsx', 'src/**/*.test.ts'],
      css: false,  // we don't need CSS processing in unit tests
      coverage: {
        provider: 'v8',
        reporter: ['text', 'html'],
        include: ['src/**/*.{ts,tsx}'],
        exclude: ['src/**/*.test.{ts,tsx}', 'src/main.tsx', 'src/test/**'],
      },
    },
  };
})
