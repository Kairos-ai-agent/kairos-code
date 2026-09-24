/**
 * Marketplace — one place to browse and install what the agent can do.
 *
 * Three tabs, because the three kinds of extension differ in how they get
 * installed, and pretending otherwise is what made "extensions" feel like
 * three unrelated features:
 *
 *   MCP servers  a curated registry (kairos/extensions/mcps.json) plus the
 *                servers that ship inside this install. Installing one is a
 *                write into the user's (or the project's) mcp.yaml — no more
 *                hand-editing YAML — and "Test" really starts the server and
 *                waits for an MCP handshake, because a server that is listed
 *                but cannot answer is worse than one that is absent.
 *   Plugins      directories with a kairos-plugin.yaml manifest. They are
 *                loaded from disk, so there is nothing to install: the card
 *                shows what it contributes and opens its folder.
 *   Skills       SKILL.md packs. Shipped with the app; the tab is a searchable
 *                index (there are hundreds) so the user can see what exists
 *                instead of asking the agent to guess.
 *
 * The page never reports success it did not see: every action is a real
 * request, and the state it shows afterwards comes back from the server.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Badge, Button, Card, Empty, Input, Segmented, Space, Spin,
  Tag, Tooltip, Typography, App as AntdApp,
} from 'antd';
import {
  ApiOutlined, AppstoreOutlined, BookOutlined, CheckCircleFilled,
  CloudOutlined, DownloadOutlined, ExperimentOutlined, DeleteOutlined,
  ReloadOutlined, SearchOutlined, ThunderboltOutlined,
  CloudDownloadOutlined,
} from '@ant-design/icons';

import { useThemeTokens } from '../hooks/useThemeTokens';
import { useT } from '../i18n';
import api from '../api/client';

const { Text, Paragraph } = Typography;

// ---------------------------------------------------------------- types

interface McpItem {
  name: string;
  category: string;
  transport: string;
  command: string;
  args: string[];
  description: string;
  env_keys: string[];
  source?: string | null;
  official: boolean;
  stars?: string | null;
  bundled: boolean;
  needs_network: boolean;
}

interface PluginItem {
  name: string;
  marketplace?: string;
  install?: string;
  description?: string;
  /** On disk, so the agent will actually load it — not "in a list". */
  installed?: boolean;
  origin?: string | null;  // bundled | user | registry
  path?: string | null;
  version?: string | null;
  capabilities?: string[];
}

interface SkillItem {
  name: string;
  category?: string | null;
  description?: string | null;
  source?: string | null;
  status: string;
  bytes?: number | null;
  path?: string | null;
  on_disk: boolean;
  installed?: boolean;
  scope?: string | null;
}

/** One remote marketplace this build can search (`GET /extensions/market/sources`). */
interface MarketSource {
  id: string;
  label: string;
  homepage?: string | null;
  /** mcp | plugin | skill | mixed — decides which tab offers it. */
  kind: string;
  description: string;
  installable: boolean;
  /** True when the source can only answer a question, not list itself. */
  needs_query?: boolean;
  /** The upstream's own count, or null when it declares none. */
  total?: number | null;
  /** Why this source is what it is — shown next to the selector, not hidden. */
  note?: string | null;
}

/**
 * One entry from a remote marketplace, normalised server-side to the same fields
 * the curated registry uses.
 *
 * `installable` is the honest one: an entry with neither a launcher nor a URL is
 * still shown, but it gets a link to its homepage instead of an Install button
 * that could not work.
 */
interface RemoteEntry {
  name: string;
  source: string;
  upstream?: string | null;
  description: string;
  version?: string | null;
  category?: string | null;
  transport?: string | null;
  command?: string | null;
  args: string[];
  url?: string | null;
  env_keys: string[];
  homepage?: string | null;
  installable: boolean;
  /** mcp | plugin | skill — decided by the source the entry came from. */
  kind?: string | null;
  /** The entry's own reason for not being installable, when it has one. */
  note?: string | null;
}

/** One page of `GET /extensions/market/search`. */
interface MarketSearchResponse {
  ok: boolean;
  source: string;
  total: number | null;
  /** The list, under both of its names — older builds only send `entries`. */
  items?: RemoteEntry[];
  entries?: RemoteEntry[];
  note?: string | null;
  /** Set when the source could not be asked. Distinct from an empty result. */
  error?: string | null;
}

/** The result of `POST /extensions/market/install`. */
interface MarketInstallResult {
  ok: boolean;
  changed: boolean;
  kind: string;
  installed_path?: string | null;
  /** Files a conversion had to skip, named. Never swallowed. */
  warnings?: string[];
}

/** One install that came back, kept on screen until the next one. */
interface InstallReport {
  name: string;
  kind: string;
  changed: boolean;
  installedPath: string | null;
  warnings: string[];
}

/** One row of `GET /extensions/mcp/installed` (absent on older backends). */
interface InstalledEntry {
  name: string;
  layer: string;        // bundled | user | project
  enabled: boolean;
}

type ProbeResult = { ok: boolean; tools?: string[]; error?: string | null; ms?: number };

/** How many skills to render before asking the user to keep going. */
const SKILL_PAGE = 60;

/** How many entries one browse of a remote source asks for. */
const REMOTE_PAGE = 60;

/**
 * How long an install is given.
 *
 * An install is not a search: a large plugin is converted file by file — the
 * live measurement for microsoft/azure-skills (935 files, of which the first
 * 400 are copied) is ~3 minutes. The client's default 120 s aborts such a
 * request *while the server keeps writing it*, so the page would report a
 * failure and the plugin would be sitting there installed after a refresh.
 * Being told "it failed" about something that succeeded is worse than waiting,
 * so this one call gets its own budget; browse and search keep the default.
 */
const INSTALL_TIMEOUT_MS = 600000;

/** After this many seconds, say that it can take a while, not just that it is busy. */
const SLOW_INSTALL_SECONDS = 5;

/**
 * The installed-here list in a tab's source row. It is a chip like any other
 * source so that "what I have" and "what I could get" are the same control, but
 * it is never a source id the market endpoint would recognise.
 */
const LOCAL = '__local__';

const Marketplace: React.FC = () => {
  const t = useT();
  const tokens = useThemeTokens();
  const { message: msgApi } = AntdApp.useApp();
  const [tab, setTab] = useState<'mcp' | 'plugins' | 'skills'>('mcp');

  return (
    <div style={{ padding: '20px 24px 40px', maxWidth: 1080, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
        <ThunderboltOutlined style={{ fontSize: 18, color: tokens.labelPrimary }} />
        <Text strong style={{ fontSize: 18 }}>{t('market.title')}</Text>
      </div>
      <Paragraph type="secondary" style={{ fontSize: 13, marginBottom: 16 }}>
        {t('market.subtitle')}
      </Paragraph>

      <Segmented
        value={tab}
        onChange={(v) => setTab(v as typeof tab)}
        options={[
          { label: <span data-testid="market-tab-mcp">{t('market.tabMcp')}</span>,
            value: 'mcp' },
          { label: <span data-testid="market-tab-plugins">{t('market.tabPlugins')}</span>,
            value: 'plugins' },
          { label: <span data-testid="market-tab-skills">{t('market.tabSkills')}</span>,
            value: 'skills' },
        ]}
        style={{ marginBottom: 16 }}
      />

      {tab === 'mcp' && <McpTab />}
      {tab === 'plugins' && <PluginTab />}
      {tab === 'skills' && <SkillTab />}
    </div>
  );
};

export default Marketplace;

// ---------------------------------------------------------------- shared
//
// The three tabs differ in what they install, not in how they choose where to
// look. One source row and one remote pane are shared, so a fix to "an empty
// result must not look like an unreachable source" holds for all of them.

/** Every remote marketplace this build can reach, and whether the answer is in. */
function useMarketSources(): { sources: MarketSource[]; loaded: boolean } {
  const [state, setState] = useState<{ sources: MarketSource[]; loaded: boolean }>(
    { sources: [], loaded: false });
  useEffect(() => {
    api.get<{ sources: MarketSource[] }>('/extensions/market/sources')
      .then((r) => setState({ sources: r.data.sources || [], loaded: true }))
      // An older backend has no market endpoints. That is a finished answer —
      // "there is nowhere else to look" — so the tabs fall back to the
      // installed-here list instead of spinning forever.
      .catch(() => setState({ sources: [], loaded: true }));
  }, []);
  return state;
}

/** The sources of one kind, plus `mixed`, which every kind may claim. */
const sourcesOfKind = (all: MarketSource[], kind: string): MarketSource[] =>
  all.filter((s) => {
    const k = String(s.kind || '').toLowerCase();
    return k === kind || k === 'mixed';
  });

/**
 * Where a tab opens. A new user has nothing installed, so defaulting to the
 * installed list would open the tab on an empty screen. The first source of this
 * kind that can actually be installed from wins: the point of browsing is to get
 * something, and a listing that carries no install information (Cline's, which
 * only the Cline CLI can install) would open the tab on rows with no button —
 * or, when that source is rate-limited, on an error. Sources of the kind that
 * cannot be installed from are still one click away.
 */
function useDefaultSource(source: string, setSource: (id: string) => void,
                          kind: string, sources: MarketSource[],
                          loaded: boolean): void {
  useEffect(() => {
    if (source || !loaded) return;
    const remote = sourcesOfKind(sources, kind);
    const pick = remote.find((s) => s.installable) || remote[0];
    setSource(pick ? pick.id : LOCAL);
  }, [source, loaded, kind, sources, setSource]);
}

/**
 * The source row: one chip per place to look, the installed-here list first.
 *
 * A source's own `total` rides on its chip when upstream declared one, so the
 * number is visible before the click rather than inferred after it.
 */
const SourceRow: React.FC<{
  testPrefix: string;
  sources: MarketSource[];
  value: string;
  onChange: (id: string) => void;
  first: { id: string; label: string; testId: string; total?: number | null };
  homepage?: string | null;
}> = ({ testPrefix, sources, value, onChange, first, homepage }) => {
  const t = useT();
  const tokens = useThemeTokens();
  // CheckableTag's unchecked state is bare text by default, which reads as a
  // stray label next to the selected pill rather than as something you can
  // click. Give every chip a border so the row looks like one control.
  const chip = (on: boolean) => ({
    border: `1px solid ${on ? 'transparent' : tokens.border}`,
    padding: '2px 10px',
    borderRadius: 12,
  });
  const withTotal = (s: { label: string; total?: number | null }) => (
    <>
      {s.label}
      {typeof s.total === 'number' ? ` · ${s.total}` : ''}
    </>
  );
  return (
    <div data-testid={`${testPrefix}-source-row`}
         style={{ display: 'flex', gap: 6, marginBottom: 10,
                  alignItems: 'center', flexWrap: 'wrap' }}>
      <Text type="secondary" style={{ fontSize: 12 }}>
        {t('market.sourceLabel')}
      </Text>
      <Tag.CheckableTag checked={value === first.id}
                        onChange={() => onChange(first.id)}
                        style={chip(value === first.id)}
                        data-testid={first.testId}>
        {withTotal(first)}
      </Tag.CheckableTag>
      {sources.map((s) => (
        <Tag.CheckableTag key={s.id} checked={value === s.id}
                          onChange={() => onChange(s.id)}
                          style={chip(value === s.id)}
                          data-testid={`${testPrefix}-source-${s.id}`}>
          {withTotal(s)}
        </Tag.CheckableTag>
      ))}
      {homepage && (
        <a href={homepage} target="_blank" rel="noreferrer" style={{ fontSize: 12 }}>
          {t('market.sourceOpen')}
        </a>
      )}
    </div>
  );
};

/**
 * One remote source of one kind: browse, filter, install.
 *
 * Everything it claims comes back from the server — the entry list, the total,
 * the reason a source could not be read and the warnings a conversion produced.
 * It is mounted with `key={source.id}`, so switching sources starts from a
 * clean slate rather than showing the previous source's rows under a new name.
 */
const RemoteSourcePane: React.FC<{
  source: MarketSource;
  testPrefix: string;
  /** Names already installed here, so a card can say so. */
  installed: Set<string>;
  /** Re-read the installed list. Called only after the server said ok. */
  onInstalled: () => void;
}> = ({ source, testPrefix, installed, onInstalled }) => {
  const t = useT();
  const tokens = useThemeTokens();
  const { message: msgApi } = AntdApp.useApp();
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  /** Seconds the current install has been running — see INSTALL_TIMEOUT_MS. */
  const [elapsed, setElapsed] = useState(0);
  const [report, setReport] = useState<InstallReport | null>(null);
  const [state, setState] = useState<{
    loading: boolean; ok: boolean; total: number | null;
    entries: RemoteEntry[]; error: string | null;
  }>({ loading: true, ok: true, total: source.total ?? null, entries: [], error: null });

  // A three-minute install that shows nothing moving is a hang as far as the
  // user is concerned. Count the seconds out loud.
  useEffect(() => {
    if (!busy) { setElapsed(0); return; }
    setElapsed(0);
    const id = setInterval(() => setElapsed((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [busy]);

  const browse = useCallback(async (q: string) => {
    setState((s) => ({ ...s, loading: true }));
    try {
      const r = await api.get<MarketSearchResponse>('/extensions/market/search', {
        params: { source: source.id, q: q || undefined, limit: REMOTE_PAGE },
      });
      setState({
        loading: false,
        // `ok:false` with an `error` is a failed question, not an empty answer.
        ok: r.data.ok !== false,
        total: r.data.total ?? null,
        entries: r.data.items || r.data.entries || [],
        error: r.data.error || null,
      });
    } catch (e: any) {
      setState({ loading: false, ok: false, total: null, entries: [],
                 error: e?.response?.data?.detail || String(e?.message || e) });
    }
  }, [source.id]);

  // Browse with no query as soon as the source is on screen: these sources
  // list themselves, and a tab that opens empty reads as broken.
  useEffect(() => { browse(''); }, [browse]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return state.entries;
    return state.entries.filter((e) => e.name.toLowerCase().includes(q)
      || (e.description || '').toLowerCase().includes(q)
      || (e.category || '').toLowerCase().includes(q));
  }, [state.entries, query]);

  const install = async (entry: RemoteEntry) => {
    const key = `${entry.source}:${entry.name}`;
    setBusy(key);
    try {
      // The server re-resolves the entry from the source; only the id travels.
      // Its own timeout: a plugin conversion is minutes, not the client's 120 s.
      const r = await api.post<MarketInstallResult>('/extensions/market/install',
        { source: entry.source, id: entry.upstream || entry.name, scope: 'user' },
        { timeout: INSTALL_TIMEOUT_MS });
      const warnings = r.data?.warnings || [];
      setReport({
        name: entry.name,
        kind: r.data?.kind || '',
        changed: r.data?.changed !== false,
        installedPath: r.data?.installed_path || null,
        warnings,
      });
      msgApi.success(r.data?.changed === false
        ? t('market.alreadyInstalled', { name: entry.name })
        : t('market.installed', { name: entry.name }));
      // What is installed is state, not an assumption: ask again.
      onInstalled();
    } catch (e: any) {
      // A 4xx carries `detail` and is not a success shape at all.
      setReport(null);
      msgApi.error(e?.response?.data?.detail || t('market.actionFailed'));
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12,
                    alignItems: 'center', flexWrap: 'wrap' }}>
        <Input
          allowClear
          prefix={<SearchOutlined style={{ color: tokens.labelTertiary }} />}
          placeholder={t('market.searchRemote')}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onPressEnter={() => browse(query)}
          style={{ maxWidth: 320 }}
          data-testid={`${testPrefix}-remote-filter`}
        />
        <Button size="small" loading={state.loading}
                onClick={() => browse(query)}
                data-testid={`${testPrefix}-remote-search`}>
          {t('market.search')}
        </Button>
        {/* A re-browse of a list already on screen is a busy state, not a
            second page of chrome: the rows stay and the spinner sits here. */}
        {state.loading && state.entries.length > 0 && (
          <span data-testid={`${testPrefix}-browse-busy`}>
            <Spin size="small" />
          </span>
        )}
        {!state.loading && state.ok && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t('market.countOf', { shown: shown.length,
                                   total: state.total ?? state.entries.length })}
          </Text>
        )}
      </div>

      {report && (
        <Alert
          showIcon
          style={{ marginBottom: 12 }}
          data-testid={`${testPrefix}-install-report`}
          type={report.warnings.length ? 'warning' : 'success'}
          message={report.warnings.length
            ? t('market.installWarned', { name: report.name })
            : t('market.installed', { name: report.name })}
          description={(
            <>
              {report.installedPath && (
                <Text code style={{ fontSize: 11 }}>{report.installedPath}</Text>
              )}
              {report.warnings.length > 0 && (
                <ul data-testid={`${testPrefix}-install-warnings`}
                    style={{ margin: '6px 0 0', paddingInlineStart: 18 }}>
                  {report.warnings.map((w, i) => <li key={i}>{w}</li>)}
                </ul>
              )}
            </>
          )}
        />
      )}

      {state.loading && state.entries.length === 0 ? (
        <div data-testid={`${testPrefix}-browse-loading`}>
          <Spin style={{ display: 'block', marginTop: 24 }} />
        </div>
      ) : state.error ? (
        // "nothing matched" and "we could not ask" are different answers and
        // must not arrive on the same screen.
        <Alert
          type="warning"
          showIcon
          data-testid={`${testPrefix}-source-error`}
          message={t('market.sourceUnreachable', { source: source.label })}
          description={state.error}
          action={(
            <Button size="small" onClick={() => browse(query)}
                    data-testid={`${testPrefix}-retry`}>
              {t('market.retry')}
            </Button>
          )}
        />
      ) : shown.length === 0 ? (
        <div data-testid={`${testPrefix}-source-empty`}>
          <Empty description={t('market.emptyRemote')} />
        </div>
      ) : (
        <div style={{ display: 'grid', gap: 10 }}>
          {shown.map((e) => {
            const key = `${e.source}:${e.name}`;
            const isBusy = busy === key;
            return (
              <Card key={key} size="small"
                    data-testid={`${testPrefix}-remote-card-${e.name}`}
                    styles={{ body: { padding: '12px 14px' } }}>
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                  <CloudDownloadOutlined style={{ fontSize: 16, marginTop: 3,
                                                  color: tokens.labelSecondary }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center',
                                  gap: 6, flexWrap: 'wrap' }}>
                      <Text strong style={{ fontSize: 14 }}>{e.name}</Text>
                      <Tag style={{ marginInlineEnd: 0 }}>{source.label}</Tag>
                      {e.category && (
                        <Tag style={{ marginInlineEnd: 0 }}>{e.category}</Tag>
                      )}
                      {(installed.has(e.name)
                        || (e.upstream ? installed.has(e.upstream) : false)) && (
                        // An entry installs under `upstream || name` (that id is
                        // what travels in the request), so that is what to look
                        // for on disk — a Cline entry lands under its upstream id.
                        <Tag color="green" style={{ marginInlineEnd: 0 }}
                             data-testid={`${testPrefix}-remote-installed-${e.name}`}>
                          {t('market.installedTag')}
                        </Tag>
                      )}
                    </div>
                    <Paragraph type="secondary"
                               style={{ fontSize: 12, margin: '4px 0 6px' }}
                               ellipsis={{ rows: 2 }}>
                      {e.description}
                    </Paragraph>
                    {isBusy && (
                      <>
                        <Text type="secondary" style={{ fontSize: 11.5 }}
                              data-testid={`${testPrefix}-install-busy-${e.name}`}>
                          {t('market.installing')} · {t('market.installElapsed', { n: elapsed })}
                        </Text>
                        {/* A big plugin is converted file by file. Saying so is
                            the difference between "slow" and "broken". */}
                        {elapsed >= SLOW_INSTALL_SECONDS && (
                          <div data-testid={`${testPrefix}-install-slow-${e.name}`}
                               style={{ fontSize: 11, marginTop: 2,
                                        color: tokens.labelTertiary }}>
                            {t('market.installSlowHint')}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                  <Space direction="vertical" size={6} align="end">
                    {e.installable ? (
                      <Button size="small" type="primary" loading={isBusy}
                              onClick={() => install(e)}
                              data-testid={`${testPrefix}-remote-install-${e.name}`}>
                        {t('market.install')}
                      </Button>
                    ) : (
                      // Never a dead button. An entry with nothing to launch
                      // says why, in its own words.
                      <Text type="secondary"
                            style={{ fontSize: 11, maxWidth: 220,
                                     display: 'inline-block', textAlign: 'right' }}
                            data-testid={`${testPrefix}-remote-note-${e.name}`}>
                        {e.note || t('market.notInstallableHint')}
                      </Text>
                    )}
                    {e.homepage && (
                      <a href={e.homepage} target="_blank" rel="noreferrer"
                         style={{ fontSize: 12 }}>
                        {t('market.sourceOpen')}
                      </a>
                    )}
                  </Space>
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </>
  );
};

// ---------------------------------------------------------------- MCP

const McpTab: React.FC = () => {
  const t = useT();
  const tokens = useThemeTokens();
  const { message: msgApi } = AntdApp.useApp();
  const [servers, setServers] = useState<McpItem[]>([]);
  const [installed, setInstalled] = useState<Record<string, InstalledEntry>>({});
  const [probe, setProbe] = useState<Record<string, ProbeResult>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  /** True when this backend has no install endpoints (older builds). */
  const [readOnly, setReadOnly] = useState(false);

  const loadInstalled = () => {
    api.get<{ servers: InstalledEntry[] }>('/extensions/mcp/installed')
      .then((r) => {
        const map: Record<string, InstalledEntry> = {};
        for (const s of r.data.servers || []) map[s.name] = s;
        setInstalled(map);
        setReadOnly(false);
      })
      // A backend without the install endpoints still gets a usable page:
      // the registry is browsable, installation is simply not offered.
      .catch(() => setReadOnly(true));
  };

  useEffect(() => {
    api.get<{ servers: McpItem[] }>('/extensions/mcps')
      .then((r) => setServers(r.data.servers || []))
      .catch(() => setServers([]))
      .finally(() => setLoading(false));
    loadInstalled();
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = servers.filter((s) =>
      !q || s.name.toLowerCase().includes(q)
        || (s.description || '').toLowerCase().includes(q)
        || (s.category || '').toLowerCase().includes(q));
    // Bundled first (they work offline and need no setup), then the rest.
    return list.sort((a, b) => Number(b.bundled) - Number(a.bundled)
      || a.name.localeCompare(b.name));
  }, [servers, query]);

  const act = async (name: string, action: 'install' | 'uninstall') => {
    setBusy(name);
    try {
      await api.post(`/extensions/mcp/${action}`, { name, scope: 'user' });
      msgApi.success(t(action === 'install' ? 'market.installed' : 'market.uninstalled',
                        { name }));
      loadInstalled();
      // Installing changes whether the server starts; a stale probe would lie.
      setProbe((p) => { const n = { ...p }; delete n[name]; return n; });
    } catch (e: any) {
      msgApi.error(e?.response?.data?.detail || t('market.actionFailed'));
    } finally {
      setBusy(null);
    }
  };

  const runProbe = async (name: string) => {
    setBusy(name);
    try {
      const r = await api.post<ProbeResult>('/extensions/mcp/probe', { name, scope: 'user' });
      setProbe((p) => ({ ...p, [name]: r.data }));
      if (r.data.ok) msgApi.success(t('market.probeOk', { n: (r.data.tools || []).length }));
      else msgApi.error(r.data.error || t('market.probeFailed'));
    } catch (e: any) {
      setProbe((p) => ({ ...p, [name]: { ok: false, error: e?.response?.data?.detail
                                                  || String(e?.message || e) } }));
      msgApi.error(t('market.probeFailed'));
    } finally {
      setBusy(null);
    }
  };

  // ---- remote marketplaces -------------------------------------------
  //
  // 'curated' is the shipped registry: 26 servers someone actually ran, filtered
  // in the browser. The remote sources exist because "we picked 26" is not an
  // answer to "is there anything else?" — and only sources that carry install
  // information are wired up, so a row here can be installed rather than merely
  // admired.
  const [source, setSource] = useState<string>('curated');
  const { sources } = useMarketSources();
  const [remote, setRemote] = useState<{ loading: boolean; ok: boolean;
                                        total: number; entries: RemoteEntry[];
                                        error?: string | null }>(
    { loading: false, ok: true, total: 0, entries: [] });

  const searchRemote = useCallback(async (src: string, q: string) => {
    if (src === 'curated') return;
    setRemote((r) => ({ ...r, loading: true }));
    try {
      const res = await api.get<{ ok: boolean; total: number;
                                  entries: RemoteEntry[]; error?: string | null }>(
        '/extensions/market/search',
        { params: { source: src, q: q || undefined, limit: 30 } });
      setRemote({ loading: false, ok: res.data.ok !== false,
                  total: res.data.total || 0, entries: res.data.entries || [],
                  error: res.data.error || null });
    } catch (e: any) {
      setRemote({ loading: false, ok: false, total: 0, entries: [],
                  error: e?.response?.data?.detail || String(e?.message || e) });
    }
  }, []);

  // Search when the source changes, so picking a tab always shows something
  // rather than an empty page that looks broken.
  useEffect(() => {
    if (source !== 'curated') searchRemote(source, query);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source]);

  const installRemote = async (entry: RemoteEntry) => {
    const key = `${entry.source}:${entry.name}`;
    setBusy(key);
    try {
      // Same endpoint as the plugin and skill tabs, so the same budget: this
      // can be a plugin conversion, and 120 s would abort it mid-write.
      await api.post('/extensions/market/install',
                     { source: entry.source, id: entry.upstream || entry.name,
                       scope: 'user' },
                     { timeout: INSTALL_TIMEOUT_MS });
      msgApi.success(t('market.installed', { name: entry.name }));
      loadInstalled();
      setProbe((p) => { const n = { ...p }; delete n[entry.name]; return n; });
    } catch (e: any) {
      msgApi.error(e?.response?.data?.detail || t('market.actionFailed'));
    } finally {
      setBusy(null);
    }
  };

  if (loading) return <Spin style={{ display: 'block', marginTop: 40 }} />;

  const isRemote = source !== 'curated';
  const activeSource = sources.find((s) => s.id === source);

  return (
    <>
      {sources.length > 0 && (
        <SourceRow
          testPrefix="market"
          sources={sources}
          value={source}
          onChange={setSource}
          first={{ id: 'curated', label: t('market.sourceCurated'),
                   testId: 'market-source-curated' }}
          homepage={activeSource?.homepage}
        />
      )}
      {isRemote && activeSource && (
        <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 10 }}>
          {activeSource.description}
        </Paragraph>
      )}

      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
        <Input
          allowClear
          prefix={<SearchOutlined style={{ color: tokens.labelTertiary }} />}
          placeholder={isRemote ? t('market.searchRemote') : t('market.searchMcp')}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onPressEnter={() => { if (isRemote) searchRemote(source, query); }}
          style={{ maxWidth: 320 }}
        />
        {!isRemote && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t('market.countOf', { shown: filtered.length, total: servers.length })}
          </Text>
        )}
        {isRemote && (
          <Button size="small" onClick={() => searchRemote(source, query)}
                  loading={remote.loading} data-testid="market-remote-search">
            {t('market.search')}
          </Button>
        )}
        {isRemote && !remote.loading && remote.ok && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t('market.countOf', { shown: remote.entries.length, total: remote.total })}
          </Text>
        )}
        {readOnly && (
          <Tag color="orange">{t('market.readOnlyBackend')}</Tag>
        )}
      </div>

      {isRemote ? (
        remote.loading ? (
          <Spin style={{ display: 'block', marginTop: 24 }} />
        ) : !remote.ok ? (
          // "no results" and "we could not ask" must not look the same.
          <Alert type="warning" showIcon data-testid="market-remote-error"
                 message={t('market.remoteUnavailable')}
                 description={remote.error || undefined} />
        ) : remote.entries.length === 0 ? (
          <Empty description={t('market.emptyMcp')} />
        ) : (
          <div style={{ display: 'grid', gap: 10 }}>
            {remote.entries.map((e) => {
              const state = installed[e.name];
              const key = `${e.source}:${e.name}`;
              const launcher = e.command
                ? `${e.command} ${(e.args || []).join(' ')}`
                : (e.url || '');
              return (
                <Card key={key} size="small"
                      data-testid={`remote-card-${e.name}`}
                      styles={{ body: { padding: '12px 14px' } }}>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                    <CloudDownloadOutlined style={{ fontSize: 16, marginTop: 3,
                                                    color: tokens.labelSecondary }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', alignItems: 'center',
                                    gap: 6, flexWrap: 'wrap' }}>
                        <Text strong style={{ fontSize: 14 }}>{e.name}</Text>
                        <Tag style={{ marginInlineEnd: 0 }}>{activeSource?.label}</Tag>
                        {e.version && (
                          <Text type="secondary" style={{ fontSize: 11 }}>v{e.version}</Text>
                        )}
                        {state && (
                          <Tag color="green" style={{ marginInlineEnd: 0 }}>
                            {t('market.installedTag')}
                          </Tag>
                        )}
                        {!e.installable && (
                          <Tooltip title={t('market.notInstallableHint')}>
                            <Tag color="orange" style={{ marginInlineEnd: 0 }}>
                              {t('market.notInstallable')}
                            </Tag>
                          </Tooltip>
                        )}
                      </div>
                      <Paragraph type="secondary"
                                 style={{ fontSize: 12, margin: '4px 0 6px' }}
                                 ellipsis={{ rows: 2 }}>
                        {e.description}
                      </Paragraph>
                      {launcher && (
                        <Text code style={{ fontSize: 11 }}>{launcher}</Text>
                      )}
                      {(e.env_keys || []).length > 0 && (
                        <div style={{ marginTop: 4 }}>
                          <Text type="secondary" style={{ fontSize: 11 }}>
                            {t('market.needsEnv', { keys: e.env_keys.join(', ') })}
                          </Text>
                        </div>
                      )}
                    </div>
                    <Space direction="vertical" size={6} align="end">
                      {e.installable ? (
                        <Button size="small" type="primary" loading={busy === key}
                                onClick={() => installRemote(e)}
                                data-testid={`remote-install-${e.name}`}>
                          {t('market.install')}
                        </Button>
                      ) : (
                        <Tooltip title={t('market.notInstallableHint')}>
                          <Button size="small" disabled
                                  data-testid={`remote-install-${e.name}`}>
                            {t('market.install')}
                          </Button>
                        </Tooltip>
                      )}
                      {e.homepage && (
                        <a href={e.homepage} target="_blank" rel="noreferrer"
                           style={{ fontSize: 12 }}>
                          {t('market.sourceOpen')}
                        </a>
                      )}
                    </Space>
                  </div>
                </Card>
              );
            })}
          </div>
        )
      ) : filtered.length === 0 ? (
        <Empty description={t('market.emptyMcp')} />
      ) : (
        <div style={{ display: 'grid', gap: 10 }}>
          {filtered.map((s) => {
            const state = installed[s.name];
            const pr = probe[s.name];
            const isBusy = busy === s.name;
            return (
              <Card
                key={s.name}
                size="small"
                data-testid={`mcp-card-${s.name}`}
                styles={{ body: { padding: '12px 14px' } }}
              >
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                  <ApiOutlined style={{ fontSize: 16, marginTop: 3,
                                        color: tokens.labelSecondary }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center',
                                  gap: 6, flexWrap: 'wrap' }}>
                      <Text strong style={{ fontSize: 14 }}>{s.name}</Text>
                      {s.bundled && (
                        <Tooltip title={t('market.bundledHint')}>
                          <Tag color="green" style={{ marginInlineEnd: 0 }}>
                            {t('market.bundled')}
                          </Tag>
                        </Tooltip>
                      )}
                      {s.official && <Tag color="blue" style={{ marginInlineEnd: 0 }}>
                                       {t('market.official')}
                                     </Tag>}
                      {!s.needs_network && (
                        <Tooltip title={t('market.offlineHint')}>
                          <Tag color="cyan" style={{ marginInlineEnd: 0 }}>
                            {t('market.offline')}
                          </Tag>
                        </Tooltip>
                      )}
                      {s.category && <Tag style={{ marginInlineEnd: 0 }}>{s.category}</Tag>}
                      {state && (
                        <Tag color={state.enabled ? 'green' : 'default'}
                             style={{ marginInlineEnd: 0 }}>
                          {t('market.installedFrom', { layer: state.layer })}
                        </Tag>
                      )}
                    </div>
                    <div style={{ fontSize: 12.5, color: tokens.labelSecondary,
                                  marginTop: 4 }}>
                      {s.description}
                    </div>
                    {s.env_keys.length > 0 && (
                      <div style={{ fontSize: 11, color: tokens.labelTertiary,
                                    marginTop: 4 }}>
                        {t('market.needsEnv', { keys: s.env_keys.join(', ') })}
                      </div>
                    )}
                    {pr && (
                      <div
                        data-testid={`mcp-probe-${s.name}`}
                        style={{
                          fontSize: 11.5, marginTop: 6,
                          color: pr.ok ? tokens.success : tokens.danger,
                        }}
                      >
                        {pr.ok
                          ? <>✓ {t('market.probeOk', { n: (pr.tools || []).length })}</>
                          : <>✗ {pr.error || t('market.probeFailed')}</>}
                      </div>
                    )}
                  </div>
                  <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                    <Tooltip title={t('market.probeHint')}>
                      <Button
                        size="small"
                        icon={<ExperimentOutlined />}
                        loading={isBusy}
                        onClick={() => runProbe(s.name)}
                        data-testid={`mcp-probe-btn-${s.name}`}
                      >
                        {t('market.probe')}
                      </Button>
                    </Tooltip>
                    {!readOnly && (state?.enabled ? (
                      <Tooltip title={t('market.uninstallHint')}>
                        <Button
                          size="small"
                          danger
                          icon={<DeleteOutlined />}
                          disabled={isBusy}
                          onClick={() => act(s.name, 'uninstall')}
                          data-testid={`mcp-uninstall-${s.name}`}
                        >
                          {t('market.uninstall')}
                        </Button>
                      </Tooltip>
                    ) : (
                      <Tooltip title={t('market.installHint')}>
                        <Button
                          size="small"
                          type="primary"
                          icon={<DownloadOutlined />}
                          disabled={isBusy}
                          onClick={() => act(s.name, 'install')}
                          data-testid={`mcp-install-${s.name}`}
                        >
                          {t('market.install')}
                        </Button>
                      </Tooltip>
                    ))}
                  </div>
                </div>
              </Card>
            );
          })}
        </div>
      )}
    </>
  );
};

// ---------------------------------------------------------------- plugins

const PluginTab: React.FC = () => {
  const t = useT();
  const tokens = useThemeTokens();
  const { sources, loaded } = useMarketSources();
  const [plugins, setPlugins] = useState<PluginItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [source, setSource] = useState<string>('');

  const loadPlugins = useCallback(() => {
    setLoading(true);
    api.get<{ plugins: PluginItem[] }>('/extensions/plugins')
      .then((r) => setPlugins(r.data.plugins || []))
      .catch(() => setPlugins([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { loadPlugins(); }, [loadPlugins]);

  const remote = useMemo(() => sourcesOfKind(sources, 'plugin'), [sources]);
  useDefaultSource(source, setSource, 'plugin', sources, loaded);
  // The list on this tab is what is installed here, so the green check and the
  // "Installed" tag on the remote rows mean the same thing: the backend found
  // it on disk. A catalogue entry is not an installed plugin.
  const installedList = useMemo(() => plugins.filter((p) => p.installed), [plugins]);
  const installed = useMemo(() => new Set(installedList.map((p) => p.name)),
    [installedList]);
  const active = remote.find((s) => s.id === source);
  // Before the answer is in, `source` is empty and the installed list is what
  // there is to show. It is the same list the tab showed before it could browse.
  const onLocal = !source || source === LOCAL;

  if (loading && onLocal && !loaded) {
    return <Spin style={{ display: 'block', marginTop: 40 }} />;
  }

  return (
    <>
      {sources.length > 0 && (
        <SourceRow
          testPrefix="plugin"
          sources={remote}
          value={source}
          onChange={setSource}
          first={{ id: LOCAL, label: t('market.sourceLocal'),
                   testId: 'plugin-source-local', total: installedList.length }}
          homepage={active?.homepage}
        />
      )}

      {active && (
        <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
          {active.description}
        </Paragraph>
      )}
      {active?.note && (
        // The source's own caveat — "the Cline CLI installs these" — is not
        // decoration: it is the reason the rows below have no Install button.
        <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 10 }}
                   data-testid="plugin-source-note">
          {active.note}
        </Paragraph>
      )}

      {onLocal ? (
        <>
          <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 12 }}>
            {t('market.pluginsNote')}
          </Paragraph>
          {loading ? (
            <Spin style={{ display: 'block', marginTop: 40 }} />
          ) : installedList.length === 0 ? (
            <Empty description={t('market.emptyPlugins')} />
          ) : (
            <div style={{ display: 'grid', gap: 10 }}>
              {installedList.map((p) => (
                <Card key={p.name} size="small" data-testid={`plugin-card-${p.name}`}
                      styles={{ body: { padding: '12px 14px' } }}>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                    <AppstoreOutlined style={{ fontSize: 16, marginTop: 3,
                                               color: tokens.labelSecondary }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                        <Text strong style={{ fontSize: 14 }}>{p.name}</Text>
                        {p.marketplace ? (
                          <Tag style={{ marginInlineEnd: 0 }}>{p.marketplace}</Tag>
                        ) : p.origin ? (
                          <Tag style={{ marginInlineEnd: 0 }}>
                            {t('market.installedFrom', { layer: p.origin })}
                          </Tag>
                        ) : null}
                      </div>
                      <div style={{ fontSize: 12.5, color: tokens.labelSecondary,
                                    marginTop: 4 }}>
                        {p.description}
                      </div>
                    </div>
                    <CheckCircleFilled style={{ color: tokens.success, fontSize: 14 }} />
                  </div>
                </Card>
              ))}
            </div>
          )}
        </>
      ) : active ? (
        <RemoteSourcePane
          key={active.id}
          source={active}
          testPrefix="plugin"
          installed={installed}
          onInstalled={loadPlugins}
        />
      ) : (
        <Spin style={{ display: 'block', marginTop: 40 }} />
      )}
    </>
  );
};

// ---------------------------------------------------------------- skills

const SkillTab: React.FC = () => {
  const t = useT();
  const tokens = useThemeTokens();
  const { sources, loaded } = useMarketSources();
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<string>('__all__');
  const [limit, setLimit] = useState(SKILL_PAGE);
  const [source, setSource] = useState<string>('');

  const loadSkills = useCallback(() => {
    setLoading(true);
    api.get<{ skills: SkillItem[] }>('/extensions/skills')
      .then((r) => setSkills(r.data.skills || []))
      .catch(() => setSkills([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { loadSkills(); }, [loadSkills]);

  const remote = useMemo(() => sourcesOfKind(sources, 'skill'), [sources]);
  useDefaultSource(source, setSource, 'skill', sources, loaded);
  // Same rule as the plugins tab: only skills the loader actually found on
  // disk count as installed. A record whose file is gone is reported as
  // missing and is not counted here.
  const installedSkills = useMemo(
    () => skills.filter((s) => s.installed ?? s.on_disk), [skills]);
  const installed = useMemo(() => new Set(installedSkills.map((s) => s.name)),
    [installedSkills]);
  const active = remote.find((s) => s.id === source);
  const onLocal = !source || source === LOCAL;

  const categories = useMemo(() => {
    const set = new Set<string>();
    for (const s of skills) if (s.category) set.add(s.category);
    return ['__all__', ...Array.from(set).sort()];
  }, [skills]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return skills.filter((s) => {
      if (category !== '__all__' && s.category !== category) return false;
      if (!q) return true;
      return s.name.toLowerCase().includes(q)
        || (s.description || '').toLowerCase().includes(q);
    });
  }, [skills, query, category]);

  if (loading && onLocal && !loaded) {
    return <Spin style={{ display: 'block', marginTop: 40 }} />;
  }
  if (!onLocal && !active) {
    return <Spin style={{ display: 'block', marginTop: 40 }} />;
  }

  const shown = filtered.slice(0, limit);

  return (
    <>
      {sources.length > 0 && (
        <SourceRow
          testPrefix="skill"
          sources={remote}
          value={source}
          onChange={setSource}
          first={{ id: LOCAL, label: t('market.sourceLocal'),
                   testId: 'skill-source-local', total: installedSkills.length }}
          homepage={active?.homepage}
        />
      )}

      {active && (
        <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
          {active.description}
        </Paragraph>
      )}
      {active?.note && (
        <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 10 }}
                   data-testid="skill-source-note">
          {active.note}
        </Paragraph>
      )}

      {!onLocal && active ? (
        <RemoteSourcePane
          key={active.id}
          source={active}
          testPrefix="skill"
          installed={installed}
          onInstalled={loadSkills}
        />
      ) : (
        <>
          <div style={{ display: 'flex', gap: 8, marginBottom: 12,
                        alignItems: 'center', flexWrap: 'wrap' }}>
            <Input
              allowClear
              prefix={<SearchOutlined style={{ color: tokens.labelTertiary }} />}
              placeholder={t('market.searchSkill')}
              value={query}
              onChange={(e) => { setQuery(e.target.value); setLimit(SKILL_PAGE); }}
              style={{ maxWidth: 320 }}
            />
            {categories.length > 2 && (
              <Segmented
                size="small"
                value={category}
                onChange={(v) => { setCategory(String(v)); setLimit(SKILL_PAGE); }}
                options={categories.slice(0, 6).map((c) => ({
                  label: c === '__all__' ? t('market.allCategories') : c,
                  value: c,
                }))}
              />
            )}
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t('market.countOf', { shown: shown.length, total: filtered.length })}
            </Text>
          </div>

          {loading ? (
            <Spin style={{ display: 'block', marginTop: 40 }} />
          ) : filtered.length === 0 ? (
            <Empty description={t('market.emptySkills')} />
          ) : (
            <div style={{ display: 'grid', gap: 8 }}>
              {shown.map((s) => (
                <Card key={s.name} size="small" data-testid={`skill-card-${s.name}`}
                      styles={{ body: { padding: '10px 14px' } }}>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
                    <BookOutlined style={{ fontSize: 14, marginTop: 3,
                                           color: tokens.labelSecondary }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', gap: 6, alignItems: 'center',
                                    flexWrap: 'wrap' }}>
                        <Text strong style={{ fontSize: 13 }}>{s.name}</Text>
                        {s.category && <Tag style={{ marginInlineEnd: 0 }}>{s.category}</Tag>}
                        {s.on_disk
                          ? <Tag color="green" style={{ marginInlineEnd: 0 }}>
                              {t('market.skillReady')}
                            </Tag>
                          : <Tag color="red" style={{ marginInlineEnd: 0 }}>
                              {t('market.skillMissing')}
                            </Tag>}
                      </div>
                      {s.description && (
                        <div style={{ fontSize: 12, color: tokens.labelSecondary,
                                      marginTop: 3 }}>
                          {s.description}
                        </div>
                      )}
                    </div>
                  </div>
                </Card>
              ))}
              {filtered.length > shown.length && (
                <Button block icon={<ReloadOutlined />}
                        onClick={() => setLimit((n) => n + SKILL_PAGE)}
                        data-testid="skill-load-more">
                  {t('market.loadMore', { n: filtered.length - shown.length })}
                </Button>
              )}
            </div>
          )}
        </>
      )}
    </>
  );
};
