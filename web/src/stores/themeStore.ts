/**
 * Theme store — dark/light mode with localStorage persistence.
 *
 * Why a store (and not just ConfigProvider state)?
 *   - Several components need to read the current mode at render time
 *     (sidebar background, message bubble borders, etc.) — they consume
 *     `mode` from this store via Zustand's `useStore` selector.
 *   - We persist the user's choice in localStorage under a single key.
 *   - The default falls back to `prefers-color-scheme` for first-time
 *     visitors — DSH ships dark by default but a Mac user might want
 *     the OS preference respected.
 */
import { create } from 'zustand';
import type { ThemeMode } from '../styles/theme';

const STORAGE_KEY = 'kairos:theme';

function readPersisted(): ThemeMode {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === 'light' || v === 'dark') return v;
  } catch {
    // localStorage may be unavailable (private mode, SSR, tests).
  }
  if (typeof window !== 'undefined'
      && window.matchMedia
      && window.matchMedia('(prefers-color-scheme: dark)').matches) {
    return 'dark';
  }
  return 'light';
}

function persist(mode: ThemeMode): void {
  try { localStorage.setItem(STORAGE_KEY, mode); } catch { /* ignore */ }
}

interface ThemeStore {
  mode: ThemeMode;
  setMode: (mode: ThemeMode) => void;
  toggle: () => void;
}

export const useThemeStore = create<ThemeStore>((set, get) => ({
  mode: readPersisted(),
  setMode: (mode) => {
    persist(mode);
    set({ mode });
  },
  toggle: () => {
    const next: ThemeMode = get().mode === 'dark' ? 'light' : 'dark';
    persist(next);
    set({ mode: next });
  },
}));
