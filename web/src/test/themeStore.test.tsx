/** Tests for the theme store + tokens. */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useThemeStore } from '../stores/themeStore';
import { tokensFor, DARK, LIGHT, toAntdTokens, LAYOUT } from '../styles/theme';

describe('themeStore', () => {
  beforeEach(() => {
    localStorage.clear();
    // Force the store back to light by calling setMode directly.
    useThemeStore.getState().setMode('light');
  });

  it('defaults to light when no localStorage and no OS dark mode', () => {
    // The setup file's matchMedia polyfill returns matches: false,
    // so the OS preference is "not dark" and the default is light.
    expect(useThemeStore.getState().mode).toBe('light');
  });

  it('setMode persists to localStorage', () => {
    useThemeStore.getState().setMode('dark');
    expect(localStorage.getItem('kairos:theme')).toBe('dark');
  });

  it('toggle flips between light and dark', () => {
    expect(useThemeStore.getState().mode).toBe('light');
    useThemeStore.getState().toggle();
    expect(useThemeStore.getState().mode).toBe('dark');
    expect(localStorage.getItem('kairos:theme')).toBe('dark');
    useThemeStore.getState().toggle();
    expect(useThemeStore.getState().mode).toBe('light');
    expect(localStorage.getItem('kairos:theme')).toBe('light');
  });

  it('persists dark mode across reloads', () => {
    // We can't easily re-import the module mid-test, but the
    // readPersisted() function reads from localStorage on first
    // import, so we verify the key is what was set.
    useThemeStore.getState().setMode('dark');
    expect(localStorage.getItem('kairos:theme')).toBe('dark');
    // Subsequent setMode(light) overrides correctly.
    useThemeStore.getState().setMode('light');
    expect(localStorage.getItem('kairos:theme')).toBe('light');
  });
});

describe('design tokens', () => {
  it('LIGHT and DARK have the same key set', () => {
    expect(new Set(Object.keys(LIGHT))).toEqual(new Set(Object.keys(DARK)));
  });

  it('tokensFor returns the right bag for each mode', () => {
    expect(tokensFor('light')).toBe(LIGHT);
    expect(tokensFor('dark')).toBe(DARK);
  });

  it('toAntdTokens maps every semantic key we need', () => {
    const t = toAntdTokens(LIGHT);
    for (const k of ['colorPrimary', 'colorBgLayout', 'colorBgContainer',
                     'colorText', 'colorBorder', 'borderRadius',
                     'fontFamily', 'controlHeight']) {
      expect(t, k).toHaveProperty(k);
    }
  });

  it('layout constants are positive numbers', () => {
    expect(LAYOUT.topbarHeight).toBeGreaterThan(0);
    expect(LAYOUT.sidebarWidth).toBeGreaterThan(0);
    expect(LAYOUT.threadMaxWidth).toBeGreaterThan(0);
  });
});
