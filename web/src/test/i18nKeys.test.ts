/// <reference types="vite/client" />
/**
 * Permanent guard: every t('key') in the source tree must exist in the
 * dictionary, and every shipped language must have the complete key set.
 *
 * Sources are pulled in with Vite's `import.meta.glob(..., { query: '?raw' })`
 * so the test needs no Node APIs (the app's tsconfig has no @types/node).
 * All 70 generated locale files are imported eagerly here — they are ~35 KB
 * strings each, which is fine for a test process.
 *
 * Translation *coverage* (how much of each catalog is actually translated
 * rather than falling back to English) is reported as a table; set
 * I18N_STRICT=1 to make a gap fail the run (used when declaring a language
 * done). `python scripts/merge_i18n.py --coverage` prints the same numbers.
 */
import { describe, expect, it } from 'vitest';

import { dict as zhCN } from '../i18n/locales/zh-CN';
import { dict as enUS } from '../i18n/locales/en-US';
import { LANGS } from '../i18n/languages';

const LOCALE_MODULES = import.meta.glob('../i18n/locales/*.ts', {
  eager: true,
}) as Record<string, { dict: Record<string, string> }>;

const CATALOGS = import.meta.glob('../i18n/catalog/*.json', {
  eager: true,
  import: 'default',
}) as Record<string, Record<string, string>>;

const RAW = import.meta.glob('../**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

const SOURCES = Object.entries(RAW).filter(([file]) =>
  !file.includes('/i18n/') && !/\.(test|spec)\./.test(file));

const STRICT = Boolean(import.meta.env?.VITE_I18N_STRICT);

describe('i18n dictionary coverage', () => {
  it('finds source files to scan', () => {
    expect(SOURCES.length).toBeGreaterThan(20);
  });

  it("every t('key') used in the source exists in both authored dictionaries", () => {
    const missing: string[] = [];
    for (const [file, source] of SOURCES) {
      for (const match of source.matchAll(/\b(?:t|tGlobal)\(\s*'([^'\\]+)'/g)) {
        const key = match[1];
        if (!key.includes('.')) continue;
        if (!(key in zhCN) || !(key in enUS)) missing.push(`${file} → ${key}`);
      }
    }
    expect([...new Set(missing)]).toEqual([]);
  });

  it('no authored entry has empty text in either language', () => {
    const empty: string[] = [];
    for (const [key, zh] of Object.entries(zhCN)) {
      if (!zh.trim()) empty.push(`zh:${key}`);
      const en = enUS[key];
      if (en === undefined || !en.trim()) empty.push(`en:${key}`);
    }
    expect(empty).toEqual([]);
  });

  it('both authored dictionaries expose exactly the same keys', () => {
    expect(Object.keys(enUS).sort()).toEqual(Object.keys(zhCN).sort());
  });

  it('every key follows the area.component.slug convention', () => {
    const bad = Object.keys(zhCN).filter(
      (key) => !/^[a-z][a-zA-Z0-9]*(\.[a-z][a-zA-Z0-9]*)+$/.test(key));
    expect(bad).toEqual([]);
  });

  it('ships one locale module per language in LANGS, with the complete key set', () => {
    const expected = Object.keys(zhCN).sort();
    const problems: string[] = [];
    for (const lang of LANGS) {
      const module = LOCALE_MODULES[`../i18n/locales/${lang.value}.ts`];
      if (!module) {
        problems.push(`${lang.value}: no generated locale module`);
        continue;
      }
      const keys = Object.keys(module.dict).sort();
      if (keys.length !== expected.length || keys.some((k, i) => k !== expected[i])) {
        problems.push(`${lang.value}: key set differs (${keys.length} vs ${expected.length})`);
        continue;
      }
      const blank = keys.filter((k) => !String(module.dict[k]).trim());
      if (blank.length) problems.push(`${lang.value}: ${blank.length} empty value(s)`);
      if (keys.some((k) => typeof module.dict[k] !== 'string')) {
        problems.push(`${lang.value}: non-string value(s)`);
      }
    }
    expect(problems).toEqual([]);
  });

  it('every locale keeps the source placeholders and stays a plain string', () => {
    // Guards the two failures seen in real catalogs: a model echoing a nested
    // object ({"en": "..."}) and duplicating/dropping {n}. A mismatch renders
    // wrong text ("1 score 1"), so it must fail the build, not the user.
    const placeholders = (text: string) => (text.match(/\{[a-zA-Z0-9_]+\}/g) ?? []).sort();
    const problems: string[] = [];
    for (const lang of LANGS) {
      const module = LOCALE_MODULES[`../i18n/locales/${lang.value}.ts`];
      if (!module) continue;
      for (const [key, value] of Object.entries(module.dict)) {
        const source = lang.value.startsWith('zh') ? zhCN[key] : enUS[key];
        if (typeof value !== 'string') {
          problems.push(`${lang.value} ${key}: not a string`);
          continue;
        }
        if (value.trim().startsWith('{') && /"(en|zh|zh_hint)"\s*:/.test(value)) {
          problems.push(`${lang.value} ${key}: value looks like a nested object`);
          continue;
        }
        if (source && placeholders(source).join() !== placeholders(value).join()) {
          problems.push(`${lang.value} ${key}: ${placeholders(source)} -> ${placeholders(value)}`);
        }
      }
    }
    expect(problems.slice(0, 20)).toEqual([]);
  });

  it('ships exactly one locale per language (no same-language variants)', () => {
    const bases = LANGS.map((l) => l.value.split('-')[0]);
    expect(new Set(bases).size).toBe(bases.length);
  });

  it('reports translation coverage per language', () => {
    const total = Object.keys(enUS).length;
    const rows = LANGS.map((lang) => {
      // zh-CN / en-US are the authored source columns: they have no catalog
      // and are complete by definition (same rule as merge_i18n.py /
      // translate_i18n.py).
      if (lang.value === 'zh-CN' || lang.value === 'en-US') {
        return { lang: lang.value, english: lang.english, done: total, pct: 100 };
      }
      const catalog = CATALOGS[`../i18n/catalog/${lang.value}.json`] ?? {};
      const done = Object.keys(enUS)
        .filter((k) => typeof catalog[k] === 'string' && catalog[k].trim()).length;
      return { lang: lang.value, english: lang.english, done, pct: (done / total) * 100 };
    }).sort((a, b) => a.pct - b.pct);

    const translated = rows.filter((r) => r.pct === 100).length;
    // eslint-disable-next-line no-console
    console.log(
      `[i18n] ${translated}/${rows.length} languages fully translated (${total} keys each);`
      + ` least complete: ${rows.slice(0, 5).map((r) => `${r.lang} ${r.pct.toFixed(0)}%`).join(', ')}`,
    );
    if (STRICT) {
      expect(rows.filter((r) => r.pct < 100).map((r) => r.lang)).toEqual([]);
    } else {
      // structural completeness is guaranteed by merge_i18n.py's fallback;
      // only the coverage number can be incomplete during a rollout
      expect(total).toBeGreaterThan(0);
    }
  });
});
