// Trivial test that just checks the matchMedia polyfill works
// (jsdom's default returns undefined.matches for unknown queries).
import { describe, it, expect } from 'vitest';

describe('matchMedia polyfill', () => {
  it('window.matchMedia returns an object with matches: false', () => {
    const mql = window.matchMedia('(prefers-color-scheme: dark)');
    expect(mql).toBeDefined();
    expect(mql.matches).toBe(false);
  });
});
