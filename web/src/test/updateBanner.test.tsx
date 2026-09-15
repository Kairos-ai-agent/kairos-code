/**
 * R38.9 — the banner only offers what this install can actually do.
 *
 * Tier B rules, as tests: silent when current; one-click when the install can
 * replace itself; release page only when it cannot; and nothing at all when the
 * check is switched off.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import React from 'react';

vi.mock('../hooks/useThemeTokens', () => ({
  useThemeTokens: () => ({
    labelPrimary: '#000', labelSecondary: '#666', labelTertiary: '#999',
    border: '#ddd', bgBase: '#fff',
  }),
}));

const get = vi.fn();
const post = vi.fn();
vi.mock('../api/client', () => ({
  default: {
    get: (...args: unknown[]) => get(...args),
    post: (...args: unknown[]) => post(...args),
  },
}));

import UpdateBanner from '../components/UpdateBanner';

const base = {
  current: '0.1.4', latest: '0.1.5', hasUpdate: true, enabled: true,
  notesUrl: 'https://example.invalid/rel', publishedAt: '2026-09-15T00:00:00Z',
  asset: null, canSelfUpdate: true, reason: 'ok', error: null,
};

describe('UpdateBanner (R38.9)', () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
  });

  it('stays completely silent when the installed build is current', async () => {
    get.mockResolvedValue({ data: { ...base, hasUpdate: false } });
    const { container } = render(<UpdateBanner />);
    await waitFor(() => expect(get).toHaveBeenCalledWith('/update/check'));
    expect(screen.queryByTestId('update-banner')).toBeNull();
    expect(container.textContent).toBe('');
  });

  it('says nothing at all when the check is disabled', async () => {
    get.mockResolvedValue({ data: { ...base, enabled: false, reason: 'disabled' } });
    render(<UpdateBanner />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(screen.queryByTestId('update-banner')).toBeNull();
  });

  it('offers one-click update when the install can replace itself', async () => {
    get.mockResolvedValue({ data: base });
    render(<UpdateBanner />);
    expect(await screen.findByTestId('update-banner')).toBeTruthy();
    expect(screen.getByTestId('update-apply')).toBeTruthy();
    expect(screen.getByTestId('update-notes')).toBeTruthy();
  });

  it('offers only the release page when it cannot replace itself', async () => {
    get.mockResolvedValue({
      data: { ...base, canSelfUpdate: false, reason: 'macos-notify-only' },
    });
    render(<UpdateBanner />);
    expect(await screen.findByTestId('update-banner')).toBeTruthy();
    expect(screen.queryByTestId('update-apply')).toBeNull();
    expect(screen.getByTestId('update-notes')).toBeTruthy();
  });

  it('never fires a download on startup', async () => {
    get.mockResolvedValue({ data: base });
    render(<UpdateBanner />);
    await screen.findByTestId('update-banner');
    expect(post).not.toHaveBeenCalled();
  });
});
