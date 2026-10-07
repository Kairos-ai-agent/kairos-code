/**
 * SkeletonPanel — vitest + RTL.
 *
 * Covers the three things the panel must get right:
 *   (a) every criterion in the Verdict's ``evidence`` is rendered (with
 *       reported/actual when the verifier supplied them);
 *   (b) a terminal status takes "运行中" off the screen — and a *stale*
 *       in-flight poll that still says ``running: true`` cannot bring it back
 *       (the loop's ``loop.ended`` bug, guarded here the same way);
 *   (c) the Stop button is shown only while running, posts to
 *       ``/{id}/skeleton/stop``, and clears the running flag at once.
 *
 * The panel polls on a 2 s interval, so the few tests that need a *second*
 * poll use a `waitFor` timeout above it (real timers, like the rest of the
 * suite — fake timers here starve React's async scheduling).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import React from 'react';

// Deterministic tokens — no dependence on the real theme provider.
vi.mock('../hooks/useThemeTokens', () => ({
  useThemeTokens: () => ({
    bgLay1: '#1f1f21',
    border: 'rgb(0 0 0 / 8%)',
    labelPrimary: '#000',
    labelSecondary: '#666',
    labelTertiary: '#888',
    success: '#10a37f',
    danger: '#dc2626',
    brand: '#0f1115',
  }),
}));

const { getMock, postMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
}));

vi.mock('../api/client', () => ({
  default: { get: getMock, post: postMock },
}));

import SkeletonPanel, {
  mergeSkeletonState, isTerminalStatus,
} from '../components/SkeletonPanel';

const POLL_MS = 2000;

const RUNNING = {
  run_id: 'run12345',
  status: 'running',
  running: true,
  workspace_kind: 'docs',
};

const DONE = {
  run_id: 'run12345',
  status: 'done',
  running: false,
  workspace_kind: 'docs',
  passed: true,
  verdict: {
    passed: true,
    reason: 'all criteria satisfied',
    verifier: 'citations',
    evidence: [
      { criterion: 'declared total equals workspace resource count', satisfied: true, reported: 1, actual: 1 },
      { criterion: 'resource cited: notes.md', satisfied: false },
    ],
  },
};

describe('SkeletonPanel', () => {
  beforeEach(() => {
    getMock.mockReset();
    postMock.mockReset();
  });

  it('renders an empty state when no run exists for the project', async () => {
    getMock.mockResolvedValue({ data: { running: false, status: 'none' } });
    render(<SkeletonPanel projectId="p1" />);
    await waitFor(() => expect(screen.getByText(/No skeleton run recorded/i)).toBeTruthy());
    expect(screen.queryByTestId('skeleton-stop')).toBeNull();
  });

  it('(a) renders every evidence criterion with reported/actual', async () => {
    getMock.mockResolvedValue({ data: DONE });
    render(<SkeletonPanel projectId="p1" />);

    await waitFor(() => expect(
      screen.getByText('declared total equals workspace resource count'),
    ).toBeTruthy());

    expect(getMock).toHaveBeenCalledWith('/projects/p1/skeleton');
    // Both rows are on screen, in order.
    expect(screen.getByText('resource cited: notes.md')).toBeTruthy();
    // reported / actual are shown for the row that carries them.
    const first = screen.getByTestId('skeleton-evidence-0').textContent || '';
    expect(first).toContain('satisfied');
    expect(first).toContain('reported: 1');
    expect(first).toContain('actual: 1');
    // The overall verdict is a clear, single marker.
    expect(screen.getByText('Passed')).toBeTruthy();
    // ...and the run id is shown.
    expect(screen.getByTestId('skeleton-panel').textContent).toContain('run12345');
  });

  it('(b) clears 运行中 at the terminal status and never revives it', async () => {
    getMock.mockResolvedValue({ data: DONE });
    getMock
      .mockResolvedValueOnce({ data: RUNNING })          // 1st poll: live
      .mockResolvedValueOnce({ data: DONE })             // 2nd poll: terminal
      .mockResolvedValueOnce({ data: RUNNING });         // 3rd poll: stale, still "running"

    render(<SkeletonPanel projectId="p1" />);
    await waitFor(() => expect(screen.getByTestId('skeleton-running')).toBeTruthy());

    // Second poll → terminal: the running marker is gone...
    await waitFor(
      () => expect(screen.queryByTestId('skeleton-running')).toBeNull(),
      { timeout: POLL_MS + 2000 },
    );
    // ...while the verdict/evidence stays on screen.
    expect(screen.getByText('declared total equals workspace resource count')).toBeTruthy();
    expect(screen.getByText('Passed')).toBeTruthy();

    // Third poll returns a stale `running: true` for a run we have seen end;
    // wait for that poll to actually land, then assert it did not resurrect
    // the marker.
    await waitFor(
      () => expect(getMock.mock.calls.length).toBeGreaterThanOrEqual(3),
      { timeout: POLL_MS + 2000 },
    );
    expect(screen.queryByTestId('skeleton-running')).toBeNull();
  });

  it('(c) shows Stop only while running; posts the stop and hides at once', async () => {
    getMock.mockResolvedValue({ data: RUNNING });
    postMock.mockResolvedValue({ data: { status: 'stopping' } });

    render(<SkeletonPanel projectId="p1" />);
    await waitFor(() => expect(screen.getByTestId('skeleton-stop')).toBeTruthy());

    fireEvent.click(screen.getByTestId('skeleton-stop'));

    await waitFor(() => expect(postMock).toHaveBeenCalledWith('/projects/p1/skeleton/stop'));
    // Terminal state is applied synchronously — no 2 s window of 运行中.
    await waitFor(() => expect(screen.queryByTestId('skeleton-running')).toBeNull());
    expect(screen.queryByTestId('skeleton-stop')).toBeNull();
  });

  it('hides Stop when the run is already over', async () => {
    getMock.mockResolvedValue({ data: DONE });
    render(<SkeletonPanel projectId="p1" />);
    await waitFor(() => expect(
      screen.getByText('declared total equals workspace resource count'),
    ).toBeTruthy());
    expect(screen.queryByTestId('skeleton-stop')).toBeNull();
    expect(screen.queryByTestId('skeleton-running')).toBeNull();
  });

  describe('mergeSkeletonState', () => {
    it('treats done/failed/stopped as terminal and clears running', () => {
      expect(isTerminalStatus('done')).toBe(true);
      expect(isTerminalStatus('failed')).toBe(true);
      expect(isTerminalStatus('stopped')).toBe(true);
      expect(isTerminalStatus('running')).toBe(false);
      const next = mergeSkeletonState(
        { run_id: 'r1', status: 'running', running: true },
        { run_id: 'r1', status: 'failed', running: false },
        new Set(),
      );
      expect(next?.running).toBe(false);
      expect(next?.status).toBe('failed');
    });

    it('never resurrects a run id already observed terminal', () => {
      const closed = new Set(['r1']);
      const next = mergeSkeletonState(
        { run_id: 'r1', status: 'done', running: false },
        { run_id: 'r1', status: 'running', running: true },
        closed,
      );
      expect(next?.running).toBe(false);
    });

    it('lets a brand-new run id start running again', () => {
      const closed = new Set(['oldrun']);
      const next = mergeSkeletonState(
        { run_id: 'oldrun', status: 'done', running: false },
        { run_id: 'newrun', status: 'running', running: true },
        closed,
      );
      expect(next?.running).toBe(true);
      expect(next?.run_id).toBe('newrun');
    });

    it('keeps a finished run when the server later answers "none"', () => {
      // A "none" (no live state) response must not blank a run we already have
      // — that was the 未运行-after-a-finished-run bug.
      const done = { run_id: 'r1', status: 'done', running: false, passed: true };
      const next = mergeSkeletonState(done, { status: 'none', running: false }, new Set());
      expect(next).toBe(done);
    });

    it('still seeds the empty state from "none" when nothing is shown', () => {
      const next = mergeSkeletonState(null, { status: 'none', running: false }, new Set());
      expect(next).toBeNull();
    });
  });
});
