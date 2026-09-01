/// <reference types="vitest" />
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'path'
import http from 'http'

// ---------------------------------------------------------------------------
// Backend port for the dev proxy.
//
// Kairos' backend default is 8900 (kairos/config/settings.py), but the
// user's launcher frequently starts it on another port (8964 has been
// the live port). Instead of hardcoding either value — which silently
// breaks every /api call the moment the ports disagree (vite returns a
// bare 500, the UI shows "Save failed: [HTTP 500] Request failed with
// status code 500") — we probe the running backend at startup and use
// whichever port answers /api/health. An explicit KAIROS_PORT env var
// always wins.
// ---------------------------------------------------------------------------
const CANDIDATE_PORTS = ['8900', '8964', '8966'];

function isBackendAlive(port: string, timeoutMs = 800): Promise<boolean> {
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

async function resolveBackendPort(): Promise<string> {
  if (process.env.KAIROS_PORT) return process.env.KAIROS_PORT;
  for (const port of CANDIDATE_PORTS) {
    // eslint-disable-next-line no-await-in-loop
    if (await isBackendAlive(port)) return port;
  }
  return '8900'; // fall back to the backend's own default
}

export default defineConfig(async () => {
  const kairosPort = await resolveBackendPort();

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
        '/api': {
          target: `http://localhost:${kairosPort}`,
          changeOrigin: true,
        },
        '/ws': {
          target: `ws://localhost:${kairosPort}`,
          ws: true,
        },
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
