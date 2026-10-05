import { describe, expect, it } from 'vitest';
import { keepIfSame } from '../utils/equal';

describe('keepIfSame', () => {
  it('keeps the previous object when the payload is unchanged', () => {
    const prev = { pending: false, text: '', decision: null, round: 1 };
    const next = { pending: false, text: '', decision: null, round: 1 };
    // Identity, not deep equality: React bails out on the old object.
    expect(keepIfSame(prev, next)).toBe(prev);
  });

  it('takes the new object when anything changed', () => {
    const prev = { pending: false, round: 1 };
    const next = { pending: true, round: 2 };
    expect(keepIfSame(prev, next)).toBe(next);
  });

  it('handles nulls without inventing an update', () => {
    expect(keepIfSame(null, null)).toBe(null);
    expect(keepIfSame({ a: 1 }, null)).toBe(null);
    expect(keepIfSame(null, { a: 1 })).toEqual({ a: 1 });
  });

  it('treats an unserialisable payload as changed', () => {
    const cyclic: Record<string, unknown> = {};
    cyclic.self = cyclic;
    const next = { a: 1 };
    expect(keepIfSame(cyclic, next)).toBe(next);
  });
});
