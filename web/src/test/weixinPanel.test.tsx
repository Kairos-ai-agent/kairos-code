/**
 * The WeChat (official ClawBot / iLink) channel: its rail entry and the
 * right-hand drawer behind it.
 *
 * The channel is native — the server holds the WeChat login and renders the QR
 * PNG — so what these cover is the surface the user actually touches: one click
 * gets a scannable image, the status line tracks the phone, a confirmed scan
 * refreshes the account list, an expired code offers a new one, and an unknown
 * status still says something. Nothing here talks to a backend.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { App, ConfigProvider } from 'antd';
import { MemoryRouter } from 'react-router-dom';

import ChatSidebar from '../components/ChatSidebar';
import { WeixinPanel } from '../components/WeixinPanel';

const PNG_MAGIC = new Uint8Array([0x89, 0x50, 0x4e, 0x47]);

const waitState = (over: Record<string, unknown> = {}) => ({
  qrcode: 'QR-1', qrcode_url: 'https://liteapp.weixin.qq.com/q/x',
  status: 'wait', connected: false, ...over,
});

// The rail reaches for its project list and the settings panels reach for their
// own endpoints; the WeChat functions are the ones under test.
vi.mock('../api/client', () => {
  const empty = () => Promise.resolve({ data: {} });
  return {
    default: {
      get: vi.fn(() => empty()),
      post: vi.fn(() => empty()),
      delete: vi.fn(() => empty()),
    },
    connectWebSocket: vi.fn(),
    getVoices: vi.fn(() => Promise.resolve({ voices: [] })),
    // SettingsDrawer's robot/voice panels (rendered by ChatSidebar).
    createImPairing: vi.fn(() => Promise.resolve({
      pairing_id: 'pair-1', status: 'waiting', expires_at: 0,
      account_id: '', display_name: '', has_qr: false, expired: false,
    })),
    getImPairing: vi.fn(() => Promise.resolve({
      pairing_id: 'pair-1', status: 'waiting', expires_at: 0,
      account_id: '', display_name: '', has_qr: false, expired: false,
    })),
    cancelImPairing: vi.fn(() => Promise.resolve()),
    imPairingQrUrl: (id: string) => `/api/im/pairings/${id}/qr.png`,
    listImAccounts: vi.fn(() => Promise.resolve({ accounts: [] })),
    listImBindings: vi.fn(() => Promise.resolve({ bindings: [] })),
    // ---- the WeChat channel ------------------------------------------------
    weixinQrPng: vi.fn(() => Promise.resolve({
      blob: new Blob([PNG_MAGIC], { type: 'image/png' }),
      qrcode: 'QR-1',
    })),
    weixinLoginStatus: vi.fn(() => Promise.resolve(waitState())),
    listWeixinAccounts: vi.fn(() => Promise.resolve({ accounts: [] })),
    deleteWeixinAccount: vi.fn(() => Promise.resolve({ ok: true })),
  };
});

const client = async () => (await import('../api/client')) as unknown as Record<string, any>;

const renderRail = () => render(
  <ConfigProvider>
    <App>
      <MemoryRouter initialEntries={['/chat']}>
        <ChatSidebar />
      </MemoryRouter>
    </App>
  </ConfigProvider>,
);

const renderPanel = () => render(
  <ConfigProvider>
    <App>
      <WeixinPanel />
    </App>
  </ConfigProvider>,
);

beforeEach(async () => {
  // jsdom has no object-URL support; a deterministic stub lets the <img> src
  // be asserted (and keeps the panel from falling back to a blank src).
  (URL as unknown as { createObjectURL: unknown }).createObjectURL =
    vi.fn(() => 'blob:weixin-qr');
  (URL as unknown as { revokeObjectURL: unknown }).revokeObjectURL = vi.fn();

  const c = await client();
  c.weixinQrPng.mockResolvedValue({
    blob: new Blob([PNG_MAGIC], { type: 'image/png' }), qrcode: 'QR-1',
  });
  c.weixinLoginStatus.mockResolvedValue(waitState());
  c.listWeixinAccounts.mockResolvedValue({ accounts: [] });
  c.deleteWeixinAccount.mockResolvedValue({ ok: true });
  c.listImAccounts.mockResolvedValue({ accounts: [] });
  c.listImBindings.mockResolvedValue({ bindings: [] });
});

describe('WeChat channel', () => {
  it('offers the entry on the rail', () => {
    renderRail();
    expect(screen.getByTestId('footer-weixin')).toBeInTheDocument();
  });

  it('opens the drawer with a one-click generate button', async () => {
    renderRail();
    fireEvent.click(screen.getByTestId('footer-weixin'));
    expect(await screen.findByTestId('weixin-panel')).toBeInTheDocument();
    expect(screen.getByTestId('weixin-generate')).toBeInTheDocument();
  });

  it('turns one request into a scannable image', async () => {
    renderPanel();
    fireEvent.click(screen.getByTestId('weixin-generate'));
    const img = await screen.findByTestId('weixin-qr');
    expect(img.getAttribute('src')).toBe('blob:weixin-qr');
    const c = await client();
    expect(c.weixinQrPng).toHaveBeenCalledTimes(1);
  });

  it('refreshes the account list once a scan is confirmed', async () => {
    const c = await client();
    c.listWeixinAccounts
      .mockResolvedValueOnce({ accounts: [] })
      .mockResolvedValue({
        accounts: [{ id: 'acct-9', name: 'My WeChat', user_id: 'u9', status: 'online' }],
      });
    c.weixinLoginStatus.mockResolvedValue(
      waitState({ status: 'confirmed', connected: true, account_id: 'acct-9' }));

    renderPanel();
    fireEvent.click(screen.getByTestId('weixin-generate'));
    expect(await screen.findByTestId('weixin-account-acct-9')).toBeInTheDocument();
  });

  it('offers a regenerate button when the code expires', async () => {
    const c = await client();
    c.weixinLoginStatus.mockResolvedValue(
      waitState({ status: 'expired', expired: true }));

    renderPanel();
    fireEvent.click(screen.getByTestId('weixin-generate'));
    const regen = await screen.findByTestId('weixin-regenerate');
    fireEvent.click(regen);
    await waitFor(() => expect(c.weixinQrPng).toHaveBeenCalledTimes(2));
  });

  it('asks for the pairing code and sends it with the next poll', async () => {
    const c = await client();
    c.weixinLoginStatus
      .mockResolvedValueOnce(waitState({ status: 'need_verifycode' }))
      .mockResolvedValue(waitState({ status: 'scaned' }));

    renderPanel();
    fireEvent.click(screen.getByTestId('weixin-generate'));
    const input = await screen.findByTestId('weixin-verify-input');
    fireEvent.change(input, { target: { value: '1234' } });
    fireEvent.click(screen.getByTestId('weixin-verify-submit'));

    await waitFor(() => {
      const sent = c.weixinLoginStatus.mock.calls.some(
        (args: unknown[]) => args[1] === '1234');
      expect(sent).toBe(true);
    });
  });

  it('labels an unknown status instead of blanking the panel', async () => {
    const c = await client();
    c.weixinLoginStatus.mockResolvedValue(waitState({ status: 'brand_new_status' }));

    renderPanel();
    fireEvent.click(screen.getByTestId('weixin-generate'));
    const status = await screen.findByTestId('weixin-status');
    await waitFor(() => {
      expect((status.textContent || '').trim().length).toBeGreaterThan(0);
    });
    // The raw server value is never shown to the user.
    expect(status.textContent).not.toContain('brand_new_status');
  });

  it('shows a readable error when the backend is unreachable', async () => {
    const c = await client();
    c.weixinQrPng.mockRejectedValue({
      response: { status: 503, data: { detail: 'weixin not initialized' } },
    });

    renderPanel();
    fireEvent.click(screen.getByTestId('weixin-generate'));
    const alert = await screen.findByTestId('weixin-error');
    expect(alert.textContent).toContain('weixin not initialized');
    // The panel is still on screen — an error never takes it down.
    expect(screen.getByTestId('weixin-panel')).toBeInTheDocument();
  });

  it('lists a bound account and wires its delete action', async () => {
    const c = await client();
    c.listWeixinAccounts.mockResolvedValue({
      accounts: [{ id: 'acct-1', name: 'Bot 1', user_id: 'u1', status: 'online' }],
    });

    renderPanel();
    const del = await screen.findByTestId('weixin-account-delete-acct-1');
    fireEvent.click(del);
    // antd's confirm dialog is what asks before the DELETE goes out.
    await waitFor(() => {
      expect(document.querySelector('.ant-modal-confirm')).toBeTruthy();
    });
  });
});
