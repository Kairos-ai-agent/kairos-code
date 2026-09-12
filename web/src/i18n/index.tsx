/**
 * i18n — dependency-free internationalization for the Kairos UI.
 *
 * 70 languages, all served from the same flat `Record<string, string>` shape.
 * Ant Design's locale object, the dayjs locale and the language's dictionary
 * are loaded through dynamic imports (`languages.ts`), so each language is its
 * own bundle chunk and only the active language plus the English/Chinese
 * fallbacks are ever fetched. Adding a language is: translate
 * `catalog/<lang>.json`, run `scripts/merge_i18n.py` — no code change.
 *
 * Rules for the rest of the codebase:
 *   1. NO user-visible string literals in components — call `t('key')`.
 *   2. Reuse `common.*` for shared vocabulary (Save/Cancel/Edit/…).
 *   3. Keys are flat and namespaced: `<area>.<component>.<slug>`.
 *   4. `t()` interpolates `{name}` params; a key missing from the active
 *      catalog falls back to zh-CN (Chinese variants) or en-US, and only then
 *      degrades to a humanised key — never a raw dotted key on screen.
 *
 * Persistence: `localStorage['kairos-lang']`; first run follows the browser
 * via `matchLang()`. `<html lang>` and `<html dir>` follow the switch, so RTL
 * locales (ar/fa/he/ku/ur) mirror the layout.
 */
import React, {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from 'react';
import dayjs from 'dayjs';
import type { Locale } from 'antd/es/locale';
import antdEnUS from 'antd/locale/en_US';

import { LANGS, DEFAULT_LANG, matchLang, isRTL } from './languages';
import type { LangOption } from './languages';
import { dict as enDict } from './locales/en-US';
import { dict as zhDict } from './locales/zh-CN';

export { LANGS, DEFAULT_LANG, matchLang, isRTL };
export type { LangOption };

/** Locale tag, e.g. `zh-CN`, `ja-JP`. Kept as a plain string: 70 values. */
export type Lang = string;

const STORAGE_KEY = 'kairos-lang';

/**
 * Vite injects `import.meta.env`, but its type is not part of this tsconfig,
 * so read it defensively (and never blow up under vitest/jsdom).
 */
const IS_DEV = (() => {
  try {
    return Boolean((import.meta as unknown as { env?: { DEV?: boolean } }).env?.DEV);
  } catch {
    return false;
  }
})();

/** Dictionary cache. The two authored columns are always resident. */
const DICTS: Record<string, Record<string, string>> = {
  'en-US': enDict,
  'zh-CN': zhDict,
};
const ANTD_CACHE: Record<string, Locale> = {};
const LOADED = new Set<string>(['en-US', 'zh-CN']);
const LOADING = new Map<string, Promise<void>>();

const findOption = (value: string): LangOption =>
  LANGS.find((l) => l.value === value)
  ?? LANGS.find((l) => l.value === DEFAULT_LANG)
  ?? LANGS[0];

/** Load antd + dayjs + dictionary for a language (idempotent, cached). */
async function ensureLang(value: string): Promise<void> {
  const option = findOption(value);
  if (LOADED.has(option.value)) return;
  const running = LOADING.get(option.value);
  if (running) return running;
  const job = (async () => {
    const { antd, dict } = await option.load();
    DICTS[option.value] = dict;
    ANTD_CACHE[option.value] = antd;
    try {
      await option.dayjsLoad();
      dayjs.locale(option.dayjs);
    } catch {
      // a missing dayjs locale only affects date formatting
    }
    LOADED.add(option.value);
    LOADING.delete(option.value);
  })();
  LOADING.set(option.value, job);
  return job;
}

export type TFunc = (key: string, params?: Record<string, string | number>) => string;

/** Browser language → supported locale. Pure, exported for tests. */
export function detectLang(nav?: string): Lang {
  const raw = nav ?? (typeof navigator !== 'undefined' ? navigator.language : '');
  return matchLang(raw) ?? DEFAULT_LANG;
}

export function readStoredLang(): Lang | null {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v && LANGS.some((l) => l.value === v)) return v;
  } catch {
    // private mode / storage disabled — fall back to detection
  }
  return null;
}

function interpolate(text: string, params?: Record<string, string | number>): string {
  if (!params) return text;
  return text.replace(/\{(\w+)\}/g, (match, name) =>
    Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match);
}

/**
 * Last-resort label for a key with no dictionary entry, so a gap renders as
 * "Plan approved" rather than the raw `loop.planApproved`.
 */
export function humanizeKey(key: string): string {
  const last = key.split('.').pop() || key;
  const words = last
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .trim()
    .toLowerCase();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : key;
}

/**
 * Dictionary lookup with a language-family fallback:
 * active → zh-CN (for zh-* variants) or en-US → humanised key.
 * Pure, exported for tests.
 */
export function translate(
  lang: Lang, key: string, params?: Record<string, string | number>,
): string {
  const primary = DICTS[lang]?.[key];
  if (primary !== undefined) return interpolate(primary, params);
  const back = lang.startsWith('zh') ? 'zh-CN' : 'en-US';
  const fallback = DICTS[back]?.[key];
  if (fallback !== undefined) {
    if (IS_DEV && LOADED.has(lang)) {
      // Loud in dev, but only once the language is actually loaded: while a
      // chunk is in flight the fallback is expected, not a bug.
      // eslint-disable-next-line no-console
      console.warn(`[i18n] missing key in ${lang}: ${key}`);
    }
    return interpolate(fallback, params);
  }
  return humanizeKey(key);
}

/** Register a dictionary loaded elsewhere (tests, preloads). */
export function registerDict(lang: Lang, dict: Record<string, string>): void {
  DICTS[lang] = dict;
  LOADED.add(lang);
}

export interface I18nContextValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: TFunc;
  /** AntD locale object for <ConfigProvider locale={...}>. */
  antdLocale: Locale;
  dir: 'ltr' | 'rtl';
  /** Full language row (native label, english name, dayjs code). */
  option: LangOption;
  /** True while the language chunk is being fetched. */
  loading: boolean;
}

const I18nContext = createContext<I18nContextValue | null>(null);

export const I18nProvider: React.FC<{ children: React.ReactNode; lang?: Lang }> = ({
  children, lang: forced,
}) => {
  const [lang, setLangState] = useState<Lang>(() => forced ?? readStoredLang() ?? detectLang());
  const [antdLocale, setAntdLocale] = useState<Locale>(
    () => ANTD_CACHE[lang] ?? antdEnUS);
  const [loading, setLoading] = useState(!LOADED.has(lang));
  // Bump to re-render once a dictionary finishes loading (translate() reads
  // the module-level cache, so React needs a nudge).
  const [, setVersion] = useState(0);

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // non-fatal: the choice just won't survive a reload
    }
  }, []);

  useEffect(() => {
    let alive = true;
    const option = findOption(lang);
    setAntdLocale(ANTD_CACHE[option.value] ?? antdEnUS);
    setLoading(!LOADED.has(option.value));
    ensureLang(option.value).then(() => {
      if (!alive) return;
      setAntdLocale(ANTD_CACHE[option.value] ?? antdEnUS);
      setLoading(false);
      setVersion((v) => v + 1);
    });
    if (typeof document !== 'undefined') {
      document.documentElement.lang = option.value;
      document.documentElement.dir = isRTL(option.value) ? 'rtl' : 'ltr';
    }
    return () => { alive = false; };
  }, [lang]);

  const value = useMemo<I18nContextValue>(() => ({
    lang,
    setLang,
    t: (key, params) => translate(lang, key, params),
    antdLocale,
    dir: isRTL(lang) ? 'rtl' : 'ltr',
    option: findOption(lang),
    loading,
  }), [lang, setLang, antdLocale, loading]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
};

/**
 * Outside a provider (unit tests, a widget mounted standalone) fall back to
 * the stored/detected language instead of throwing — a missing provider must
 * never take the whole screen down.
 */
function standaloneContext(): I18nContextValue {
  const lang = readStoredLang() ?? detectLang();
  const option = findOption(lang);
  return {
    lang,
    setLang: (next: Lang) => {
      try {
        localStorage.setItem(STORAGE_KEY, next);
      } catch {
        // ignore
      }
    },
    t: (key, params) => translate(lang, key, params),
    antdLocale: ANTD_CACHE[lang] ?? antdEnUS,
    dir: isRTL(lang) ? 'rtl' : 'ltr',
    option,
    loading: false,
  };
}

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext);
  return ctx ?? standaloneContext();
}

/** Shorthand for the common case: `const t = useT()`. */
export function useT(): TFunc {
  return useI18n().t;
}

/** Non-hook accessor for the rare call site outside React (e.g. stores). */
export function tGlobal(key: string, params?: Record<string, string | number>): string {
  return translate(readStoredLang() ?? detectLang(), key, params);
}
