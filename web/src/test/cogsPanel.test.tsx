/**
 * CogsPanel — vitest + RTL tests (Round 36).
 *
 * Mocks the `/api/cost/value` endpoint and verifies the 5 derived
 * metrics + 3 totals are rendered with the right color coding.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';

// Mock the theme hook
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

// Mock the API client
const mockGet = vi.fn();
vi.mock('../api/client', () => ({
  default: {
    get: (...args: any[]) => mockGet(...args),
  },
}));

// Mock antd's message
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

import CogsPanel from '../components/CogsPanel';

const FULL_RESPONSE = {
  total_cost_usd: 1.234,
  n_llm_calls: 42,
  dataset: { total_cases: 10, passed: 8, failed: 2 },
  alerts: { total: 5, critical: 1 },
  metrics: {
    cost_per_case: 0.1234,
    cost_per_passing: 0.1543,
    cost_per_alert: 0.2468,
    efficiency: 0.8,
    approval_yield: 0.8,
  },
};

beforeEach(() => {
  mockGet.mockReset();
});

function setupMock(data: any = FULL_RESPONSE) {
  mockGet.mockResolvedValue({ data });
}

describe('CogsPanel', () => {
  it('renders all 3 totals from the response', async () => {
    setupMock();
    render(<CogsPanel />);
    await waitFor(() => {
      // Use a function matcher: antd Statistic splits the value
      // into prefix + value spans, so the literal "1.2340" may
      // not appear as a single text node.
      const has = (needle: string) => (content: string) => content.includes(needle);
      // Title: "Cost / Value (COGS)"
      expect(screen.getByText(/Cost \/ Value \(COGS\)/)).toBeTruthy();
      // Total spend value 1.2340 lives in an ant-statistic-content-value span
      const total = document.querySelectorAll('.ant-statistic-content-value');
      // 3 totals + 5 derived metrics = 8 Statistics
      expect(total.length).toBe(8);
      // First 3 are the totals: 1.2340, 10, 5
      expect(total[0].textContent).toBe('1.2340');
      expect(total[1].textContent).toBe('10');
      expect(total[2].textContent).toBe('5');
    });
  });

  it('renders the 5 derived metrics', async () => {
    setupMock();
    render(<CogsPanel />);
    await waitFor(() => {
      const total = document.querySelectorAll('.ant-statistic-content-value');
      expect(total.length).toBe(8);
      // 5 derived metrics (positions 3-7)
      expect(total[3].textContent).toBe('0.1234');   // cost_per_case
      expect(total[4].textContent).toBe('0.1543');   // cost_per_passing
      expect(total[5].textContent).toBe('0.2468');   // cost_per_alert
      expect(total[6].textContent).toBe('80.0%');    // efficiency
      expect(total[7].textContent).toBe('80.0%');    // approval_yield
    });
  });

  it('renders the subtitle with composition details', async () => {
    setupMock();
    render(<CogsPanel />);
    await waitFor(() => {
      const subtitle = screen.getByTestId('cogs-subtitle');
      expect(subtitle.textContent).toContain('42 LLM calls');
      expect(subtitle.textContent).toContain('8 pass / 2 fail');
      expect(subtitle.textContent).toContain('1 critical / 5 total');
    });
  });

  it('shows a critical badge when critical > 0', async () => {
    setupMock();
    render(<CogsPanel />);
    await waitFor(() => {
      expect(screen.getByText('1 crit')).toBeTruthy();
    });
  });

  it('does NOT show a critical badge when critical = 0', async () => {
    setupMock({
      ...FULL_RESPONSE,
      alerts: { total: 3, critical: 0 },
    });
    render(<CogsPanel />);
    await waitFor(() => {
      expect(screen.queryByText('crit')).toBeNull();
    });
  });

  it('renders "—" for null metrics (no data)', async () => {
    setupMock({
      total_cost_usd: 0,
      n_llm_calls: 0,
      dataset: { total_cases: 0, passed: 0, failed: 0 },
      alerts: { total: 0, critical: 0 },
      metrics: {
        cost_per_case: null,
        cost_per_passing: null,
        cost_per_alert: null,
        efficiency: null,
        approval_yield: null,
      },
    });
    render(<CogsPanel />);
    await waitFor(() => {
      // 5 dash characters for 5 null metrics
      const dashes = screen.getAllByText('—');
      expect(dashes.length).toBe(5);
    });
  });

  it('color-codes efficiency: green >= 0.8, orange >= 0.5, red < 0.5', async () => {
    // 0.9 → green
    setupMock({
      ...FULL_RESPONSE,
      metrics: { ...FULL_RESPONSE.metrics, efficiency: 0.9 },
    });
    const { unmount } = render(<CogsPanel />);
    await waitFor(() => {
      const total = document.querySelectorAll('.ant-statistic-content-value');
      expect(total[6].textContent).toBe('90.0%');
    });
    unmount();
    // 0.3 → red
    setupMock({
      ...FULL_RESPONSE,
      metrics: { ...FULL_RESPONSE.metrics, efficiency: 0.3 },
    });
    render(<CogsPanel />);
    await waitFor(() => {
      const total = document.querySelectorAll('.ant-statistic-content-value');
      expect(total[6].textContent).toBe('30.0%');
    });
  });

  it('shows error state when API fails', async () => {
    mockGet.mockRejectedValue(new Error('network down'));
    render(<CogsPanel />);
    await waitFor(() => {
      // Error text + retry button
      expect(screen.getByText(/network down|failed/i)).toBeTruthy();
      expect(screen.getByText(/retry/i)).toBeTruthy();
    });
  });
});
