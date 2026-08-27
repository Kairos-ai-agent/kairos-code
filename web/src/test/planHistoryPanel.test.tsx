/**
 * PlanHistoryPanel — vitest + RTL tests.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';

vi.mock('../hooks/useThemeTokens', () => ({
  useThemeTokens: () => ({
    brand: '#1677ff',
    border: '#d9d9d9',
    labelPrimary: '#000',
    labelSecondary: '#666',
    bgLay1: '#fafafa',
  }),
}));

import PlanHistoryPanel, { HistoryEntry } from '../components/PlanHistoryPanel';

function entry(round: number, todos: any[]): HistoryEntry {
  return { round, plan: { todos, updated_at: 1 } };
}

describe('PlanHistoryPanel', () => {
  it('renders empty state when history has no plans', () => {
    render(<PlanHistoryPanel history={[]} />);
    expect(screen.getByText(/No plan history yet/i)).toBeTruthy();
  });

  it('renders one row per round with completion percent', () => {
    const history: HistoryEntry[] = [
      entry(1, [
        { status: 'pending', content: 'A' },
        { status: 'pending', content: 'B' },
      ]),
      entry(2, [
        { status: 'completed', content: 'A' },
        { status: 'pending', content: 'B' },
      ]),
      entry(3, [
        { status: 'completed', content: 'A' },
        { status: 'completed', content: 'B' },
      ]),
    ];
    render(<PlanHistoryPanel history={history} />);
    // All 3 round labels visible
    expect(screen.getByText('R1')).toBeTruthy();
    expect(screen.getByText('R2')).toBeTruthy();
    expect(screen.getByText('R3')).toBeTruthy();
    // Completion percentages
    expect(screen.getByText('0% done')).toBeTruthy();
    expect(screen.getByText('50% done')).toBeTruthy();
    expect(screen.getByText('100% done')).toBeTruthy();
  });

  it('skips rounds without a plan field', () => {
    const history: HistoryEntry[] = [
      { round: 1 }, // no plan
      entry(2, [{ status: 'pending', content: 'A' }]),
      { round: 3 }, // no plan
    ];
    render(<PlanHistoryPanel history={history} />);
    expect(screen.queryByText('R1')).toBeNull();
    expect(screen.getByText('R2')).toBeTruthy();
    expect(screen.queryByText('R3')).toBeNull();
  });

  it('handles corrupt plan dicts gracefully', () => {
    const history: HistoryEntry[] = [
      { round: 1, plan: { todos: 'not an array' as any } },
      entry(2, [{ status: 'pending', content: 'A' }]),
    ];
    render(<PlanHistoryPanel history={history} />);
    // Round 1 is dropped; round 2 appears
    expect(screen.queryByText('R1')).toBeNull();
    expect(screen.getByText('R2')).toBeTruthy();
  });

  it('expands to show per-round diff on click', () => {
    const history: HistoryEntry[] = [
      entry(1, [{ status: 'pending', content: 'A' }]),
      entry(2, [
        { status: 'completed', content: 'A' },
        { status: 'pending', content: 'B' },
      ]),
    ];
    render(<PlanHistoryPanel history={history} />);
    // Find R2's header
    const r2 = screen.getByText('R2');
    // Click to expand — the Collapse panel headers are clickable
    fireEvent.click(r2);
    // The diff for R2 should now be visible
    // (A marked completed, B added)
    expect(screen.getAllByText('A').length).toBeGreaterThan(0);
    expect(screen.getAllByText('B').length).toBeGreaterThan(0);
  });

  it('compact mode collapses to a tag', () => {
    const history: HistoryEntry[] = [
      entry(1, [{ status: 'pending', content: 'A' }]),
      entry(2, [{ status: 'completed', content: 'A' }]),
    ];
    render(<PlanHistoryPanel history={history} />);
    const compactLink = screen.getByText('compact');
    fireEvent.click(compactLink);
    expect(screen.getByText(/Plan history: 2 rounds/)).toBeTruthy();
  });
});
