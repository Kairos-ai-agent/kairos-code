/**
 * Vitest setup — runs before every test file.
 *
 * Wires up @testing-library/jest-dom's matchers (toBeInTheDocument etc.)
 * and stubs out a few things that don't exist in jsdom.
 */
import '@testing-library/jest-dom/vitest';
import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// jsdom ships a stub matchMedia that returns `matches: undefined` for
// some queries; always replace it with a deterministic mock so the
// theme store's OS-preference fallback is testable. Use a plain
// function (not vi.fn) so the polyfill is set even before the vitest
// mock system is initialised.
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  configurable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});
