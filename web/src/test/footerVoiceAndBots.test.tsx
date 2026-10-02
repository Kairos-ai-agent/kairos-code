/**
 * The rail's bottom-left preferences group: voice mode, WeChat, settings.
 *
 * Voice mode started life as a tab inside the settings drawer, which put it
 * two clicks and a drawer away from the work; the user asked for it on the
 * rail, so the coverage lives here now. The bot-connector entry that used to
 * sit beside it has since been removed by request — the personal-WeChat path
 * is the native iLink channel now, so an entry that only reached the generic
 * connector API was a dead end, and the rail keeps WeChat instead.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { App, ConfigProvider } from 'antd';
import { MemoryRouter } from 'react-router-dom';

import ChatSidebar from '../components/ChatSidebar';
import { useSettingsStore } from '../stores/settingsStore';

// The rail reaches for its project list and the panels reach for their own
// endpoints; nothing here talks to a real backend. Everything else keeps its
// real shape so importing a panel never trips over a missing export.
vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  const empty = () => Promise.resolve({ data: {} });
  return {
    ...actual,
    default: {
      ...((actual as { default?: object }).default ?? {}),
      get: vi.fn(() => empty()),
      post: vi.fn(() => empty()),
      delete: vi.fn(() => empty()),
    },
    connectWebSocket: vi.fn(),
    getVoices: vi.fn(() => Promise.resolve({ voices: [] })),
    listWeixinAccounts: vi.fn(() => Promise.resolve({ accounts: [] })),
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

describe('bottom-left rail: voice mode, WeChat, settings', () => {
  it('offers voice mode and WeChat on the rail', () => {
    renderRail();
    expect(screen.getByTestId('footer-voice')).toBeInTheDocument();
    expect(screen.getByTestId('footer-weixin')).toBeInTheDocument();
    expect(screen.getByTestId('footer-settings')).toBeInTheDocument();
  });

  it('no longer offers the old bot-connector entry', () => {
    renderRail();
    expect(screen.queryByTestId('footer-bots')).not.toBeInTheDocument();
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
});
