/**
 * i18n core tests + the "no partial translation" guard.
 *
 * The parity test at the bottom is the one that matters most: every key in
 * one language must exist in the other, otherwise the UI silently mixes
 * languages — exactly the "完全切换，不是部分" failure the user called out.
 */
import React from 'react';
import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { I18nProvider, useI18n, detectLang, translate, LANGS } from '../i18n';
import { dict as zhCN } from '../i18n/locales/zh-CN';
import { dict as enUS } from '../i18n/locales/en-US';

const Probe: React.FC = () => {
  const { t, lang, setLang } = useI18n();
  return (
    <div>
      <span data-testid="lang">{lang}</span>
      <span data-testid="label">{t('language.label')}</span>
      <span data-testid="switch">{t('language.switched', { lang: 'English' })}</span>
      <button onClick={() => setLang('zh-CN')}>to-zh</button>
      <button onClick={() => setLang('en-US')}>to-en</button>
    </div>
  );
};

describe('i18n core', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('detects zh-* as zh-CN and anything else as en-US', () => {
    expect(detectLang('zh-CN')).toBe('zh-CN');
    expect(detectLang('zh-Hans')).toBe('zh-CN');
    // shipped languages match exactly, dropped variants collapse to their
    // primary locale, unknown tags fall back to English
    expect(detectLang('fr-FR')).toBe('fr-FR');
    expect(detectLang('ja-JP')).toBe('ja-JP');
    expect(detectLang('en-GB')).toBe('en-US');   // variant removed
    expect(detectLang('zh-TW')).toBe('zh-CN');   // variant removed -> 简体
    expect(detectLang('xx-YY')).toBe('en-US');
  });

  it('falls back to zh-CN and then to a humanized label', () => {
    // unknown key → readable words, never a raw dotted key and never blank
    expect(translate('en-US', 'does.not.exist')).toBe('Exist');
    expect(translate('en-US', 'loop.planApproved')).toBe('Plan approved');
    expect(translate('zh-CN', 'language.label')).toBe(zhCN['language.label']);
    expect(translate('en-US', 'language.label')).toBe(enUS['language.label']);
  });

  it('interpolates {params} and leaves unknown placeholders alone', () => {
    expect(translate('en-US', 'language.switched', { lang: '中文' }))
      .toContain('中文');
    expect(translate('en-US', 'language.switched', {})).toContain('{lang}');
  });

  it('switching language re-renders the tree and persists', () => {
    render(<I18nProvider lang="en-US"><Probe /></I18nProvider>);
    expect(screen.getByTestId('label').textContent).toBe(enUS['language.label']);

    fireEvent.click(screen.getByText('to-zh'));
    expect(screen.getByTestId('lang').textContent).toBe('zh-CN');
    expect(screen.getByTestId('label').textContent).toBe(zhCN['language.label']);
    expect(localStorage.getItem('kairos-lang')).toBe('zh-CN');

    fireEvent.click(screen.getByText('to-en'));
    expect(screen.getByTestId('label').textContent).toBe(enUS['language.label']);
  });

  it('renders without a provider (standalone widget / unit test)', () => {
    render(<Probe />);
    expect(screen.getByTestId('label').textContent)
      .toBe(translate('en-US', 'language.label'));
  });

  it('offers both languages', () => {
    // one row per language (variants like en-GB/fr-CA/zh-TW are dropped on
    // purpose — see scripts/gen_languages.mjs), all lazily loaded
    expect(LANGS.length).toBeGreaterThan(50);
    const values = LANGS.map((l) => l.value);
    expect(values).toContain('zh-CN');
    expect(values).toContain('en-US');
    expect(values).toContain('ja-JP');
    expect(values).toContain('ar-EG');
    expect(values).not.toContain('en-GB');
    expect(values).not.toContain('zh-TW');
    expect(LANGS.every((l) => typeof l.load === 'function')).toBe(true);
    // invariant: exactly one locale per language subtag
    const bases = values.map((v) => v.split('-')[0]);
    expect(new Set(bases).size).toBe(bases.length);
  });

  it('has no missing translation in either language (no partial switch)', () => {
    const zhKeys = Object.keys(zhCN).sort();
    const enKeys = Object.keys(enUS).sort();
    const missingInEn = zhKeys.filter((k) => !(k in enUS));
    const missingInZh = enKeys.filter((k) => !(k in zhCN));
    expect(missingInEn).toEqual([]);
    expect(missingInZh).toEqual([]);

    for (const [key, zh] of Object.entries(zhCN)) {
      if (key === 'language.zhCN' || key === 'language.enUS') continue; // native labels
      expect(zh.trim(), `zh text for ${key} is empty`).not.toBe('');
      expect(enUS[key].trim(), `en text for ${key} is empty`).not.toBe('');
    }
  });
});
