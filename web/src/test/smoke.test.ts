/** Quick smoke test for the ts-only path (no JSX). */
import { describe, it, expect } from 'vitest';

describe('ts smoke', () => {
  it('1 + 1 = 2', () => {
    expect(1 + 1).toBe(2);
  });
});
