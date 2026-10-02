/**
 * Voice mode and the bot connector, on the rail's bottom-left.
 *
 * Both started life as tabs inside the settings drawer, which put them two
 * clicks and a drawer away from the work. The user asked for them on the rail,
 * so the coverage lives here now: the entry exists, and what it opens is the
 * panel itself. The old drawer tests asked the drawer and could not tell that
 * the drawer is no longer where they live.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { App, ConfigProvider } from 'antd';
import { MemoryRouter } from 'react-router-dom';

import ChatSidebar from '../components/ChatSidebar';
import { useSettingsStore } from '../stores/settingsStore';

// The rail reaches for its project list and the panels reach for their own
// endpoints; nothing here talks to a real backend.
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
    // Pairing: the panel only ever reads one of these.
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
    listImStatus: vi.fn(() => Promise.resolve({ accounts: 0, conversations: 0, pending: 0 })),
  };
});

const renderRail = () => render(
  <ConfigProvider>
    <App>
      <MemoryRouter initialEntries={['/chat']}>
        <ChatSidebar />
      </MemoryRouter>
    </App>
  </ConfigProvider>,
);

describe('bottom-left rail: voice mode and bots', () => {
  it('offers both on the rail', () => {
    renderRail();
    expect(screen.getByTestId('footer-voice')).toBeInTheDocument();
    expect(screen.getByTestId('footer-bots')).toBeInTheDocument();
  });

  it('opens the voice panel, and its switch still drives playback', async () => {
    renderRail();
    fireEvent.click(screen.getByTestId('footer-voice'));
    const toggle = await screen.findByTestId('voice-mode-switch');
    fireEvent.click(toggle);
    const voice = useSettingsStore.getState().voice;
    expect(voice.voiceMode).toBe(true);
    expect(voice.autoPlay).toBe(true); // setVoice keeps them in step
  });

  it('opens the bot connector with its endpoints, before any account exists', async () => {
    renderRail();
    fireEvent.click(screen.getByTestId('footer-bots'));
    await waitFor(() => {
      expect(screen.getByText(/\/api\/im\/\{account_id\}\/inbound$/)).toBeInTheDocument();
    });
    expect(screen.getByTestId('robot-add-account')).toBeInTheDocument();
  });

  it('connects a bot by showing a code, not by asking for a secret', async () => {
    renderRail();
    fireEvent.click(screen.getByTestId('footer-bots'));
    const connect = await screen.findByTestId('robot-connect');
    // Nothing to scan before the handshake starts.
    expect(screen.queryByTestId('robot-qr')).not.toBeInTheDocument();
    fireEvent.click(connect);
    expect(await screen.findByText(/Waiting for the connector/)).toBeInTheDocument();
  });

  it("puts the connector's own QR code on screen once it arrives", async () => {
    const client = await import('../api/client');
    const create = client.createImPairing as unknown as {
      mockResolvedValueOnce: (value: unknown) => void;
    };
    create.mockResolvedValueOnce({
      pairing_id: 'pair-9', status: 'qr', expires_at: 0,
      account_id: '', display_name: '', has_qr: true, expired: false,
    });
    renderRail();
    fireEvent.click(screen.getByTestId('footer-bots'));
    fireEvent.click(await screen.findByTestId('robot-connect'));
    const img = await screen.findByTestId('robot-qr');
    expect(img.getAttribute('src')).toContain('/api/im/pairings/pair-9/qr.png');
  });
});
