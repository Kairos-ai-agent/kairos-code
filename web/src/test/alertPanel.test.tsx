/**
 * AlertPanel — vitest + RTL tests (Round 30).
 *
 * Mocks the three API endpoints (recent / summary / mutes) and the
 * mute POST. Verifies:
 *   - Empty state
 *   - Renders severity counts from summary
 *   - Renders entries with the right severity tag + status icon
 *   - Mute button click calls POST /alerts/mute with the right key
 *   - Muted entries are dimmed (opacity < 1) and show a "muted" tag
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';

// Mock the theme hook so the component doesn't depend on the
// real antd theme provider.
vi.mock('../hooks/useThemeTokens', () => ({
  useThemeTokens: () => ({
    brand: '#1677ff',
    border: '#d9d9d9',
    labelPrimary: '#000',
    labelSecondary: '#666',
    labelTertiary: '#999',
    bgBase: '#fff',
    bgLay1: '#fafafa',
    bgElevated: '#fff',
  }),
}));

// Mock the API client. The component uses 4 endpoints + 1 POST.
const mockGet = vi.fn();
const mockPost = vi.fn();
vi.mock('../api/client', () => ({
  default: {
    get: (...args: any[]) => mockGet(...args),
    post: (...args: any[]) => mockPost(...args),
  },
}));

// Mock antd's message so the success/error toasts don't crash jsdom.
vi.mock('antd', async () => {
  const actual = await vi.importActual<any>('antd');
  return {
    ...actual,
    message: {
      success: vi.fn(),
      error: vi.fn(),
      info: vi.fn(),
    },
  };
});

import AlertPanel from '../components/AlertPanel';

const NOW = 1_700_000_000;

const SAMPLE_ENTRY_CRITICAL = {
  timestamp: NOW - 60,
  severity: 'critical',
  kind: 'cost_spike',
  message: 'Total cost spiked 400%',
  metric: 'cost_usd',
  baseline: 1.0,
  current: 5.0,
  delta_pct: 400.0,
  channel: 'slack',
  channel_url: 'https://hooks.example',
  status: 'sent',
  error: '',
};

const SAMPLE_ENTRY_WARNING = {
  timestamp: NOW - 30,
  severity: 'warning',
  kind: 'call_spike',
  message: 'Per-call p95 up 250%',
  metric: 'per_call',
  baseline: 0.01,
  current: 0.035,
  delta_pct: 250.0,
  channel: 'slack',
  channel_url: 'https://hooks.example',
  status: 'failed',
  error: 'webhook returned non-2xx',
};

const SAMPLE_SUMMARY = {
  total: 2,
  by_severity: { info: 0, warning: 1, critical: 1 },
  by_status: { sent: 1, failed: 1, skipped: 0 },
  by_kind: { cost_spike: 1, call_spike: 1 },
  last_critical_at: NOW - 60,
  active_mutes: 0,
};

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
});

function setupMocks(opts: {
  entries?: any[];
  summary?: any;
  mutes?: Record<string, number>;
  postResponse?: any;
} = {}) {
  const entries = opts.entries ?? [SAMPLE_ENTRY_CRITICAL, SAMPLE_ENTRY_WARNING];
  const summary = opts.summary ?? SAMPLE_SUMMARY;
  const mutes = opts.mutes ?? {};
  const postResponse = opts.postResponse ?? {
    data: {
      key: 'cost_spike:cost_usd',
      expires_at: NOW + 3600,
      duration_s: 3600,
    },
  };

  mockGet.mockImplementation((url: string) => {
    if (url.startsWith('/alerts/recent')) {
      return Promise.resolve({ data: { entries, count: entries.length } });
    }
    if (url.startsWith('/alerts/summary')) {
      return Promise.resolve({ data: summary });
    }
    if (url.startsWith('/alerts/mutes')) {
      return Promise.resolve({ data: { mutes, count: Object.keys(mutes).length } });
    }
    return Promise.reject(new Error(`Unexpected GET ${url}`));
  });
  mockPost.mockResolvedValue(postResponse);
}

describe('AlertPanel', () => {
  it('renders empty state when no alerts are present', async () => {
    setupMocks({ entries: [], summary: { ...SAMPLE_SUMMARY, total: 0 } });
    render(<AlertPanel />);
    await waitFor(() => {
      expect(screen.getByText(/No alerts yet/i)).toBeTruthy();
    });
  });

  it('renders severity counts from the summary', async () => {
    setupMocks();
    render(<AlertPanel />);
    await waitFor(() => {
      // Both Statistic titles render; check the active mute count
      // (which is 0) is NOT shown and that the entries are visible.
      // The Statistic component renders values in elements with
      // class ant-statistic-content-value.
      const values = document.querySelectorAll('.ant-statistic-content-value');
      expect(values.length).toBe(3);
      // First value (critical) should be "1"
      expect(values[0].textContent).toBe('1');
      // Second value (warning) should be "1"
      expect(values[1].textContent).toBe('1');
      // Third value (info) should be "0"
      expect(values[2].textContent).toBe('0');
    });
  });

  it('renders entries with severity tag and status icon', async () => {
    setupMocks();
    render(<AlertPanel />);
    await waitFor(() => {
      // Severity tags
      expect(screen.getByText('CRITICAL')).toBeTruthy();
      expect(screen.getByText('WARNING')).toBeTruthy();
      // The kind label appears (lowercase in the meta line)
      expect(screen.getByText(/cost_spike/)).toBeTruthy();
      expect(screen.getByText(/call_spike/)).toBeTruthy();
    });
  });

  it('calls POST /alerts/mute when mute button clicked', async () => {
    setupMocks();
    render(<AlertPanel />);
    // Wait for the panel to load entries
    await waitFor(() => {
      expect(screen.getByTestId('mute-cost_spike')).toBeTruthy();
    });
    // Click the mute button on the cost_spike row
    fireEvent.click(screen.getByTestId('mute-cost_spike'));
    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith('/alerts/mute', {
        key: 'cost_spike:cost_usd',
        duration_s: 3600,
      });
    });
  });

  it('shows muted tag and dims muted entries', async () => {
    // Plant a mute that will cover the critical entry
    const futureExp = Math.floor(Date.now() / 1000) + 3600;
    setupMocks({
      mutes: { 'cost_spike:cost_usd': futureExp },
    });
    const { container } = render(<AlertPanel />);
    await waitFor(() => {
      // The critical row's mute button is gone (because muted)
      expect(screen.queryByTestId('mute-cost_spike')).toBeNull();
      // A "muted" tag is present
      expect(screen.getByText('muted')).toBeTruthy();
    });
    // The muted row's wrapper div has opacity < 1
    const mutedRow = container.querySelector('[data-testid="alert-row-cost_spike"]');
    expect(mutedRow).toBeTruthy();
    const opacity = (mutedRow as HTMLElement).style.opacity;
    expect(parseFloat(opacity)).toBeLessThan(1);
  });

  it('does not show muted tag for non-muted entries', async () => {
    setupMocks();
    render(<AlertPanel />);
    await waitFor(() => {
      expect(screen.getByTestId('mute-cost_spike')).toBeTruthy();
    });
    // No "muted" tag yet
    expect(screen.queryByText('muted')).toBeNull();
  });

  it('shows active mute count when mutes exist', async () => {
    const futureExp = Math.floor(Date.now() / 1000) + 3600;
    setupMocks({
      mutes: { 'a:b': futureExp, 'c:d': futureExp },
      summary: { ...SAMPLE_SUMMARY, active_mutes: 2 },
    });
    render(<AlertPanel />);
    await waitFor(() => {
      expect(screen.getByText(/2 active mute/i)).toBeTruthy();
    });
  });

  it('shows error state when API fails', async () => {
    mockGet.mockRejectedValue(new Error('network error'));
    render(<AlertPanel />);
    await waitFor(() => {
      // The error message and retry button appear
      expect(screen.getByText(/network error|failed/i)).toBeTruthy();
      expect(screen.getByText(/retry/i)).toBeTruthy();
    });
  });
});
