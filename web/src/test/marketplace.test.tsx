/**
 * The Marketplace page talks to endpoints that a slightly older backend does
 * not have. The interesting behaviour is therefore not "does it render cards"
 * but "does it stay honest when the backend cannot install anything" — it must
 * keep browsing instead of showing buttons that would fail, and it must never
 * claim an install it did not see.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { App as AntdApp, ConfigProvider } from 'antd';

const get = vi.fn();
const post = vi.fn();

vi.mock('../api/client', () => ({
  default: { get: (...a: unknown[]) => get(...a), post: (...a: unknown[]) => post(...a) },
  connectWebSocket: vi.fn(),
}));

import Marketplace from '../pages/Marketplace';

const MCP = {
  name: 'time', category: 'utility', transport: 'stdio',
  command: 'python', args: ['-m', 'x'], description: 'Clock and timezone',
  env_keys: [], offical: false, official: true, stars: null,
  bundled: true, needs_network: false,
};

const PLUGIN = {
  name: 'code-review', marketplace: 'bundled',
  install: 'bundled', description: 'Review skills',
};

const SKILL = {
  name: 'docx', category: 'docs', description: 'Word documents',
  source: 'bundled', status: 'installed', bytes: 100,
  path: 'kairos/skills/docx/SKILL.md', on_disk: true,
};

function mockApi(opts: { installEndpoints: boolean }) {
  get.mockImplementation((url: string) => {
    if (url === '/extensions/mcps') return Promise.resolve({ data: { servers: [MCP] } });
    if (url === '/extensions/plugins') return Promise.resolve({ data: { plugins: [PLUGIN] } });
    if (url === '/extensions/skills') return Promise.resolve({ data: { skills: [SKILL] } });
    if (url === '/extensions/mcp/installed') {
      return opts.installEndpoints
        ? Promise.resolve({ data: { servers: [{ name: 'time', layer: 'bundled', enabled: true }] } })
        : Promise.reject(new Error('404'));
    }
    return Promise.resolve({ data: {} });
  });
}

const renderPage = () => render(
  <ConfigProvider>
    {/* The page reports outcomes through antd's message API, which needs the
        App context to exist — without it useApp() returns a stub and every
        success or failure turns into a TypeError. */}
    <AntdApp><Marketplace /></AntdApp>
  </ConfigProvider>,
);

describe('Marketplace', () => {
  beforeEach(() => { get.mockReset(); post.mockReset(); });

  it('lists registry servers with their honest badges', async () => {
    mockApi({ installEndpoints: true });
    renderPage();
    const card = await screen.findByTestId('mcp-card-time');
    expect(card.textContent).toContain('time');
    expect(card.textContent).toContain('Clock and timezone');
  });

  it('shows Install when a server is not enabled', async () => {
    mockApi({ installEndpoints: true });
    get.mockImplementation((url: string) => {
      if (url === '/extensions/mcps') return Promise.resolve({ data: { servers: [MCP] } });
      if (url === '/extensions/mcp/installed') return Promise.resolve({ data: { servers: [] } });
      return Promise.resolve({ data: { plugins: [], skills: [] } });
    });
    renderPage();
    expect(await screen.findByTestId('mcp-install-time')).toBeInTheDocument();
  });

  it('shows Remove (not Install) for an enabled server', async () => {
    mockApi({ installEndpoints: true });
    renderPage();
    expect(await screen.findByTestId('mcp-uninstall-time')).toBeInTheDocument();
    expect(screen.queryByTestId('mcp-install-time')).toBeNull();
  });

  it('degrades to browse-only when the backend has no install endpoints', async () => {
    mockApi({ installEndpoints: false });
    renderPage();
    // Still browses...
    expect(await screen.findByTestId('mcp-card-time')).toBeInTheDocument();
    // ...but offers no button that would fail.
    await waitFor(() => expect(screen.getByText(/仅可浏览|can browse but not install/i))
      .toBeInTheDocument());
    expect(screen.queryByTestId('mcp-install-time')).toBeNull();
    expect(screen.queryByTestId('mcp-uninstall-time')).toBeNull();
  });

  it('reports the real probe outcome instead of assuming success', async () => {
    mockApi({ installEndpoints: true });
    post.mockResolvedValue({
      data: { ok: false, error: 'ModuleNotFoundError: No module named mcp', ms: 12 },
    });
    renderPage();
    fireEvent.click(await screen.findByTestId('mcp-probe-btn-time'));
    await waitFor(() => {
      const row = screen.getByTestId('mcp-probe-time');
      expect(row.textContent).toContain('ModuleNotFoundError');
    });
    // The endpoint called must be the real one.
    expect(post.mock.calls[0][0]).toBe('/extensions/mcp/probe');
  });

  it('shows the tool count when a probe succeeds', async () => {
    mockApi({ installEndpoints: true });
    post.mockResolvedValue({ data: { ok: true, tools: ['now', 'sleep'], ms: 30 } });
    renderPage();
    fireEvent.click(await screen.findByTestId('mcp-probe-btn-time'));
    await waitFor(() => {
      expect(screen.getByTestId('mcp-probe-time').textContent).toContain('2');
    });
  });

  it('switches to the plugins and skills tabs', async () => {
    mockApi({ installEndpoints: true });
    renderPage();
    fireEvent.click(screen.getByTestId('market-tab-plugins'));
    expect(await screen.findByTestId('plugin-card-code-review')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('market-tab-skills'));
    expect(await screen.findByTestId('skill-card-docx')).toBeInTheDocument();
  });
});
