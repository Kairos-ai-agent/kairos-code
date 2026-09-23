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

// ---------------------------------------------------------------------------
// Remote marketplaces
//
// The curated registry is 26 servers someone ran. These tests cover the answer
// to "is there anything else?" — and the two ways that answer can be a lie: a
// source that cannot be reached must say so rather than look empty, and an entry
// with nothing to launch must not offer an Install button.
// ---------------------------------------------------------------------------

const SOURCES = [
  { id: 'registry', label: 'Official MCP Registry', homepage: 'https://registry.modelcontextprotocol.io',
    kind: 'mcp', description: 'The reference registry.', installable: true },
  { id: 'cline', label: 'Cline marketplace', homepage: 'https://github.com/cline/marketplace',
    kind: 'mcp', description: 'A manifest repository.', installable: true },
];

const REMOTE_ENTRY = {
  name: 'sqlite-tools', source: 'registry', upstream: 'io.github.example/sqlite-tools',
  description: 'SQLite helpers', version: '1.2.3', category: 'registry',
  transport: 'stdio', command: 'npx', args: ['-y', 'sqlite-tools@1.2.3'],
  url: null, env_keys: ['SQLITE_PATH'], homepage: 'https://example.com/sqlite',
  installable: true,
};

const REMOTE_HOLLOW = {
  name: 'hollow', source: 'registry', upstream: 'io.github.example/hollow',
  description: 'No launcher and no URL', version: '1.0.0', category: 'registry',
  transport: null, command: null, args: [], url: null, env_keys: [],
  homepage: 'https://example.com/hollow', installable: false,
};

function mockMarket(searchResult: unknown) {
  mockApi({ installEndpoints: true });
  get.mockImplementation((url: string) => {
    if (url === '/extensions/mcps') return Promise.resolve({ data: { servers: [MCP] } });
    if (url === '/extensions/mcp/installed') return Promise.resolve({ data: { servers: [] } });
    if (url === '/extensions/market/sources') return Promise.resolve({ data: { sources: SOURCES } });
    if (url === '/extensions/market/search') return Promise.resolve({ data: searchResult });
    return Promise.resolve({ data: {} });
  });
}

describe('Marketplace remote sources', () => {
  beforeEach(() => { get.mockReset(); post.mockReset(); });

  it('offers the remote marketplaces next to the curated one', async () => {
    mockMarket({ ok: true, source: 'registry', total: 0, entries: [] });
    renderPage();
    expect(await screen.findByTestId('market-source-curated')).toBeInTheDocument();
    expect(await screen.findByTestId('market-source-registry')).toBeInTheDocument();
    expect(await screen.findByTestId('market-source-cline')).toBeInTheDocument();
  });

  it('searches the chosen source and shows a launcher and its env requirements', async () => {
    mockMarket({ ok: true, source: 'registry', total: 1, entries: [REMOTE_ENTRY] });
    renderPage();
    fireEvent.click(await screen.findByTestId('market-source-registry'));

    const card = await screen.findByTestId('remote-card-sqlite-tools');
    expect(card.textContent).toContain('sqlite-tools');
    expect(card.textContent).toContain('npx -y sqlite-tools@1.2.3');
    // The credential is named, never its value.
    expect(card.textContent).toContain('SQLITE_PATH');

    // The search went to the market endpoint, with the source, not to the
    // curated registry.
    const call = get.mock.calls.find((c) => String(c[0]).includes('/extensions/market/search'));
    expect(call).toBeTruthy();
    expect((call as any[])[1].params.source).toBe('registry');
  });

  it('installs a remote entry by its upstream id, not by the display name', async () => {
    mockMarket({ ok: true, source: 'registry', total: 1, entries: [REMOTE_ENTRY] });
    post.mockResolvedValue({ data: { ok: true, changed: true } });
    renderPage();
    fireEvent.click(await screen.findByTestId('market-source-registry'));
    fireEvent.click(await screen.findByTestId('remote-install-sqlite-tools'));

    await waitFor(() => expect(post).toHaveBeenCalled());
    expect(post.mock.calls[0][0]).toBe('/extensions/market/install');
    // The registry is keyed by the namespaced name; the short one is a display
    // convenience and would resolve to nothing.
    expect(post.mock.calls[0][1]).toMatchObject({
      source: 'registry', id: 'io.github.example/sqlite-tools', scope: 'user',
    });
  });

  it('does not offer to install an entry that has nothing to launch', async () => {
    mockMarket({ ok: true, source: 'registry', total: 1, entries: [REMOTE_HOLLOW] });
    renderPage();
    fireEvent.click(await screen.findByTestId('market-source-registry'));

    const button = await screen.findByTestId('remote-install-hollow');
    expect(button).toBeDisabled();
    // ...and says why, and still links out.
    expect(screen.getByTestId('remote-card-hollow').textContent).toMatch(/启动命令|start command|不可一键安装|No launcher/i);
  });

  it('says a source is unreachable instead of showing an empty list', async () => {
    mockMarket({ ok: false, source: 'registry', total: 0, entries: [],
                 error: 'registry is unreachable (network is unreachable)' });
    renderPage();
    fireEvent.click(await screen.findByTestId('market-source-registry'));

    const alert = await screen.findByTestId('market-remote-error');
    expect(alert.textContent).toContain('unreachable');
    // An empty result and a failed request must not look the same.
    expect(screen.queryByTestId('remote-card-hollow')).toBeNull();
  });

  it('survives a backend with no market endpoints at all', async () => {
    mockApi({ installEndpoints: true });
    get.mockImplementation((url: string) => {
      if (url === '/extensions/mcps') return Promise.resolve({ data: { servers: [MCP] } });
      if (url === '/extensions/mcp/installed') return Promise.resolve({ data: { servers: [] } });
      if (url === '/extensions/market/sources') return Promise.reject(new Error('404'));
      return Promise.resolve({ data: {} });
    });
    renderPage();
    // The curated list still works; the source row simply is not there.
    expect(await screen.findByTestId('mcp-card-time')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId('market-source-row')).toBeNull());
  });
});
