/**
 * The Marketplace page talks to endpoints that a slightly older backend does
 * not have. The interesting behaviour is therefore not "does it render cards"
 * but "does it stay honest when the backend cannot install anything" — it must
 * keep browsing instead of showing buttons that would fail, and it must never
 * claim an install it did not see.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
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
  installed: true, origin: 'bundled',
};

const SKILL = {
  name: 'docx', category: 'docs', description: 'Word documents',
  source: 'bundled', status: 'installed', bytes: 100,
  path: 'kairos/skills/docx/SKILL.md', on_disk: true,
  installed: true, scope: 'bundled',
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

// ---------------------------------------------------------------------------
// Remote plugin and skill sources
//
// The plugin and skill tabs used to show only what is already on disk, so "is
// there anything else?" had no answer inside the app. These tests cover that
// answer and the three ways it can lie: a source that could not be read looking
// like one with nothing in it, an Install button on something that cannot be
// installed, and an install that skipped files without saying so.
// ---------------------------------------------------------------------------

// Deliberately in the server's order, with the Cline source *before* the
// installable one: the tab must open on a source the user can act on, not on
// the first row of the list.
const KIND_SOURCES = [
  { id: 'registry', label: 'Official MCP Registry', kind: 'mcp', total: null,
    homepage: 'https://registry.modelcontextprotocol.io', installable: true,
    needs_query: true, note: 'the API reports a page cursor, not a total',
    description: 'The reference registry.' },
  { id: 'cline-plugins', label: 'Cline plugins', kind: 'plugin', total: null,
    homepage: 'https://github.com/cline/marketplace/tree/main/registry/plugins',
    installable: false, needs_query: false,
    note: 'not installable here: the Cline CLI installs these',
    description: "Cline's plugin listings." },
  { id: 'anthropic-plugins', label: 'Claude plugins (official)', kind: 'plugin',
    total: 311, homepage: 'https://github.com/anthropics/claude-plugins-official',
    installable: true, needs_query: false, note: 'install converts the plugin',
    description: "Anthropic's official plugin directory." },
  { id: 'cline-skills', label: 'Cline skills', kind: 'skill', total: null,
    homepage: 'https://github.com/cline/marketplace/tree/main/registry/skills',
    installable: false, needs_query: false, note: null,
    description: "Cline's SKILL.md packs." },
  { id: 'anthropic-skills', label: 'Claude skills (anthropics/skills)',
    kind: 'skill', total: 19, homepage: 'https://github.com/anthropics/skills',
    installable: true, needs_query: false,
    note: 'SKILL.md packs; installed into the user skills scope',
    description: "Anthropic's SKILL.md collection." },
];

const PLUGIN_ENTRY = {
  name: 'confetti', source: 'anthropic-plugins', upstream: 'confetti',
  description: 'A plugin that throws confetti', version: '2.0.0',
  category: 'fun', transport: null, command: null, args: [], url: null,
  env_keys: [], homepage: 'https://example.com/confetti', installable: true,
  kind: 'plugin', note: null,
};

const PLUGIN_HOLLOW = {
  name: 'shell-plugin', source: 'cline-plugins', upstream: 'shell-plugin',
  description: 'A Cline CLI bundle', version: null, category: 'shell',
  transport: null, command: null, args: [], url: null, env_keys: [],
  homepage: 'https://example.com/shell', installable: false, kind: 'plugin',
  note: 'not installable here: the Cline CLI installs these',
};

const SKILL_ENTRY = {
  name: 'brand-guidelines', source: 'anthropic-skills', upstream: 'brand-guidelines',
  description: 'House style rules', version: null, category: 'writing',
  transport: null, command: null, args: [], url: null, env_keys: [],
  homepage: null, installable: true, kind: 'skill', note: null,
};

/** One search response, in the shape the server sends (`items` *and* `entries`). */
const page = (source: string, entries: unknown[], extra: Record<string, unknown> = {}) => ({
  ok: true, source, total: entries.length, items: entries, entries,
  note: null, error: null, ...extra,
});

function mockKinds(bySource: Record<string, unknown>,
                   opts: { plugins?: unknown[] } = {}) {
  get.mockImplementation((url: string, cfg?: any) => {
    if (url === '/extensions/mcps') return Promise.resolve({ data: { servers: [MCP] } });
    if (url === '/extensions/mcp/installed') return Promise.resolve({ data: { servers: [] } });
    if (url === '/extensions/plugins') {
      return Promise.resolve({ data: { plugins: opts.plugins ?? [PLUGIN] } });
    }
    if (url === '/extensions/skills') return Promise.resolve({ data: { skills: [SKILL] } });
    if (url === '/extensions/market/sources') {
      return Promise.resolve({ data: { sources: KIND_SOURCES } });
    }
    if (url === '/extensions/market/search') {
      const id = String(cfg?.params?.source ?? '');
      return Promise.resolve({ data: bySource[id] ?? page(id, []) });
    }
    return Promise.resolve({ data: {} });
  });
}

const openPlugins = async () => {
  fireEvent.click(screen.getByTestId('market-tab-plugins'));
  await screen.findByTestId('plugin-source-local');
};

describe('Marketplace remote plugin and skill sources', () => {
  beforeEach(() => { get.mockReset(); post.mockReset(); });

  it('opens the plugins tab on a source it can install from, with no query', async () => {
    mockKinds({ 'anthropic-plugins': page('anthropic-plugins', [PLUGIN_ENTRY],
                                          { total: 311 }) });
    renderPage();
    await openPlugins();

    // Not the first source in the list: the Cline listing carries no install
    // information, so opening on it would put the user in front of rows with
    // no button. The one that can be installed from is what 311 means.
    expect(await screen.findByTestId('plugin-remote-card-confetti'))
      .toBeInTheDocument();
    expect(screen.getByTestId('plugin-remote-install-confetti')).toBeEnabled();

    // Browsing a source that lists itself sends no query at all.
    const call = get.mock.calls.find((c) => c[0] === '/extensions/market/search');
    expect(call).toBeTruthy();
    expect((call as any[])[1].params.source).toBe('anthropic-plugins');
    expect((call as any[])[1].params.q).toBeUndefined();
    // Its own total is on screen — the number the source declared, not a guess.
    expect(screen.getByTestId('plugin-source-anthropic-plugins').textContent)
      .toContain('311');
  });

  it('keeps the installed plugins as a source of their own', async () => {
    mockKinds({ 'anthropic-plugins': page('anthropic-plugins', [PLUGIN_ENTRY]) });
    renderPage();
    await openPlugins();
    fireEvent.click(screen.getByTestId('plugin-source-local'));
    expect(await screen.findByTestId('plugin-card-code-review')).toBeInTheDocument();
  });

  it('shows the reason instead of a dead button when an entry cannot be installed', async () => {
    mockKinds({
      'anthropic-plugins': page('anthropic-plugins', [PLUGIN_ENTRY]),
      'cline-plugins': page('cline-plugins', [PLUGIN_HOLLOW]),
    });
    renderPage();
    await openPlugins();
    fireEvent.click(screen.getByTestId('plugin-source-cline-plugins'));

    await screen.findByTestId('plugin-remote-card-shell-plugin');
    const note = screen.getByTestId('plugin-remote-note-shell-plugin');
    expect(note.textContent).toContain('the Cline CLI installs these');
    // No button that could not work.
    expect(screen.queryByTestId('plugin-remote-install-shell-plugin')).toBeNull();
  });

  it('renders a source that could not be read differently from an empty one', async () => {
    mockKinds({
      'anthropic-plugins': page('anthropic-plugins', [], { total: 0 }),
      'cline-plugins': {
        ok: false, source: 'cline-plugins', total: null, items: [], entries: [],
        note: null, error: 'cline-plugins answered HTTP 403',
      },
    });
    renderPage();
    await openPlugins();
    fireEvent.click(screen.getByTestId('plugin-source-cline-plugins'));

    const alert = await screen.findByTestId('plugin-source-error');
    expect(alert.textContent).toContain('403');
    expect(alert.textContent).toContain('Cline plugins');
    // A failed question is not an empty answer.
    expect(screen.queryByTestId('plugin-source-empty')).toBeNull();
    expect(screen.getByTestId('plugin-retry')).toBeInTheDocument();

    // The other source answered, with nothing in it: a different screen.
    fireEvent.click(screen.getByTestId('plugin-source-anthropic-plugins'));
    expect(await screen.findByTestId('plugin-source-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('plugin-source-error')).toBeNull();
    expect(screen.queryByTestId('plugin-retry')).toBeNull();
  });

  it('browses a skill source with the same pane', async () => {
    mockKinds({ 'anthropic-skills': page('anthropic-skills', [SKILL_ENTRY],
                                         { total: 19 }) });
    renderPage();
    fireEvent.click(screen.getByTestId('market-tab-skills'));
    expect(await screen.findByTestId('skill-remote-card-brand-guidelines'))
      .toBeInTheDocument();
    expect(screen.getByTestId('skill-remote-install-brand-guidelines')).toBeEnabled();
    expect(screen.getByTestId('skill-source-anthropic-skills').textContent)
      .toContain('19');
  });

  it('lists every warning of an install that skipped files', async () => {
    mockKinds({ 'anthropic-plugins': page('anthropic-plugins', [PLUGIN_ENTRY]) });
    post.mockResolvedValue({
      data: {
        ok: true, changed: true, kind: 'plugin',
        installed_path: 'C:/tmp/.kairos/plugins/confetti',
        warnings: [
          'confetti: 935 files were in the plugin; only the first 400 were copied',
          'confetti: commands/run.md depends on ${CLAUDE_SESSION_ID}, which only Claude Code resolves, so it was skipped',
        ],
      },
    });
    renderPage();
    await openPlugins();
    fireEvent.click(await screen.findByTestId('plugin-remote-install-confetti'));

    const list = await screen.findByTestId('plugin-install-warnings');
    expect(list.querySelectorAll('li')).toHaveLength(2);
    expect(list.textContent).toContain('only the first 400 were copied');
    expect(list.textContent).toContain('${CLAUDE_SESSION_ID}');
    // Named, and the id that travels is the entry's own.
    expect(post.mock.calls[0][0]).toBe('/extensions/market/install');
    expect(post.mock.calls[0][1]).toMatchObject({
      source: 'anthropic-plugins', id: 'confetti', scope: 'user',
    });
  });

  it('re-reads the installed list after an install', async () => {
    mockKinds({ 'anthropic-plugins': page('anthropic-plugins', [PLUGIN_ENTRY]) });
    post.mockResolvedValue({
      data: { ok: true, changed: true, kind: 'plugin',
              installed_path: '/tmp/.kairos/plugins/confetti', warnings: [] },
    });
    renderPage();
    await openPlugins();
    await screen.findByTestId('plugin-remote-card-confetti');
    const before = get.mock.calls.filter((c) => c[0] === '/extensions/plugins').length;

    fireEvent.click(screen.getByTestId('plugin-remote-install-confetti'));
    await waitFor(() => expect(
      get.mock.calls.filter((c) => c[0] === '/extensions/plugins').length,
    ).toBe(before + 1));
    // ...and says where it landed, rather than only that it went well.
    const report = await screen.findByTestId('plugin-install-report');
    expect(report.textContent).toContain('confetti');
  });

  it('does not report success for an install the server refused', async () => {
    mockKinds({ 'anthropic-plugins': page('anthropic-plugins', [PLUGIN_ENTRY]) });
    post.mockRejectedValue({
      response: { status: 400, data: {
        detail: 'confetti: it ships no LICENSE file, so its licence cannot be determined',
      } },
    });
    renderPage();
    await openPlugins();
    fireEvent.click(await screen.findByTestId('plugin-remote-install-confetti'));

    await waitFor(() => expect(screen.getByText(/no LICENSE file/)).toBeInTheDocument());
    // A 4xx is not the success shape, so there is no report to show.
    expect(screen.queryByTestId('plugin-install-report')).toBeNull();
  });

  it('says a download is in flight instead of looking hung', async () => {
    mockKinds({ 'anthropic-plugins': page('anthropic-plugins', [PLUGIN_ENTRY]) });
    let finish: (v: unknown) => void = () => {};
    post.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    renderPage();
    await openPlugins();
    fireEvent.click(await screen.findByTestId('plugin-remote-install-confetti'));

    const busy = await screen.findByTestId('plugin-install-busy-confetti');
    // ...and it counts the seconds, because a large plugin really is minutes.
    const atStart = busy.textContent || '';
    await waitFor(() => expect(busy.textContent).not.toBe(atStart), { timeout: 4000 });
    expect(busy.textContent).toMatch(/[1-9]/);
    await act(async () => {
      finish({ data: { ok: true, changed: true, kind: 'plugin', warnings: [] } });
    });
    await waitFor(() =>
      expect(screen.queryByTestId('plugin-install-busy-confetti')).toBeNull());
  });

  it('gives the install request a budget longer than the client default', async () => {
    // A plugin conversion measured live at ~3 minutes. The shared client's 120 s
    // would abort the request while the server was still writing the plugin, so
    // the page would report a failure and the plugin would appear installed
    // after a refresh. The install call has to carry its own timeout.
    const DEFAULT_TIMEOUT_MS = 120000;
    mockKinds({ 'anthropic-plugins': page('anthropic-plugins', [PLUGIN_ENTRY]) });
    post.mockResolvedValue({
      data: { ok: true, changed: true, kind: 'plugin',
              installed_path: '/tmp/.kairos/plugins/confetti', warnings: [] },
    });
    renderPage();
    await openPlugins();
    fireEvent.click(await screen.findByTestId('plugin-remote-install-confetti'));
    await waitFor(() => expect(post).toHaveBeenCalled());

    const [url, body, config] = post.mock.calls[0];
    expect(url).toBe('/extensions/market/install');
    expect(body).toMatchObject({ source: 'anthropic-plugins', id: 'confetti' });
    expect(config?.timeout).toBeGreaterThan(DEFAULT_TIMEOUT_MS);
    expect(config.timeout).toBeGreaterThanOrEqual(600000);
    // And the browse that fed the list keeps the default: only the write is long.
    const search = get.mock.calls.find((c) => c[0] === '/extensions/market/search');
    expect((search as any[])[1]?.timeout).toBeUndefined();
  });
});

describe('installed state comes from disk, not from a catalogue', () => {
  beforeEach(() => { get.mockReset(); post.mockReset(); });

  it('marks a plugin installed from a remote source, and lists it as installed', async () => {
    const fromMarket = { ...PLUGIN, name: 'code-modernization',
                         marketplace: '', install: '', origin: 'user' };
    const remoteEntry = { ...PLUGIN_ENTRY, name: 'code-modernization',
                          id: 'code-modernization' };
    mockKinds({ 'anthropic-plugins': page('anthropic-plugins', [remoteEntry]) },
              { plugins: [PLUGIN, fromMarket] });
    renderPage();
    await openPlugins();

    // The row that offers the install says it has already happened.
    await screen.findByTestId('plugin-remote-card-code-modernization');
    expect(screen.getByTestId('plugin-remote-installed-code-modernization'))
      .toBeInTheDocument();

    // And it is under "installed here" — not only under the source it came from,
    // which is the state a fresh install used to leave the page in.
    fireEvent.click(screen.getByTestId('plugin-source-local'));
    expect(await screen.findByTestId('plugin-card-code-modernization')).toBeInTheDocument();
  });

  it('does not list a catalogue entry as installed when nothing is on disk', async () => {
    mockKinds({}, { plugins: [{ ...PLUGIN, installed: false, origin: 'registry' }] });
    renderPage();
    await openPlugins();
    fireEvent.click(screen.getByTestId('plugin-source-local'));

    // The registry is a shelf, not a state: an entry with nothing behind it is
    // not an installed plugin, and the count must not include it.
    expect(screen.queryByTestId('plugin-card-code-review')).toBeNull();
    expect(screen.getByTestId('plugin-source-local').textContent).toContain('0');
  });

  it('counts only the skills that are on disk', async () => {
    get.mockImplementation((url: string) => {
      if (url === '/extensions/mcps') return Promise.resolve({ data: { servers: [MCP] } });
      if (url === '/extensions/mcp/installed') return Promise.resolve({ data: { servers: [] } });
      if (url === '/extensions/plugins') return Promise.resolve({ data: { plugins: [PLUGIN] } });
      if (url === '/extensions/skills') {
        return Promise.resolve({ data: { skills: [
          SKILL,
          { ...SKILL, name: 'gone', installed: false, on_disk: false, status: 'missing' },
        ] } });
      }
      if (url === '/extensions/market/sources') {
        return Promise.resolve({ data: { sources: KIND_SOURCES } });
      }
      return Promise.resolve({ data: {} });
    });
    renderPage();
    fireEvent.click(screen.getByTestId('market-tab-skills'));
    // The tab opens on a source it can install from; the installed list is a
    // click away, exactly as it is for a user.
    fireEvent.click(await screen.findByTestId('skill-source-local'));

    // Two skills are listed; one is a record whose file is gone. The chip counts
    // what is installed, so a broken record cannot inflate it.
    const chip = screen.getByTestId('skill-source-local');
    expect(chip.textContent).toMatch(/1(?!\d)/);
    expect(await screen.findByTestId('skill-card-gone')).toBeInTheDocument();
  });
});
