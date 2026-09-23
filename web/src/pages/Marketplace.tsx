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
  marketplace: string;
  install: string;
  description: string;
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
}

/** One remote marketplace this build can search (`GET /extensions/market/sources`). */
interface MarketSource {
  id: string;
  label: string;
  homepage?: string | null;
  kind: string;
  description: string;
  installable: boolean;
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
  const [sources, setSources] = useState<MarketSource[]>([]);
  const [remote, setRemote] = useState<{ loading: boolean; ok: boolean;
                                        total: number; entries: RemoteEntry[];
                                        error?: string | null }>(
    { loading: false, ok: true, total: 0, entries: [] });

  useEffect(() => {
    api.get<{ sources: MarketSource[] }>('/extensions/market/sources')
      .then((r) => setSources(r.data.sources || []))
      // An older backend has no market endpoints: the curated tab still works.
      .catch(() => setSources([]));
  }, []);

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
      await api.post('/extensions/market/install',
                     { source: entry.source, id: entry.upstream || entry.name,
                       scope: 'user' });
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
        <div data-testid="market-source-row"
             style={{ display: 'flex', gap: 6, marginBottom: 10,
                      alignItems: 'center', flexWrap: 'wrap' }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t('market.sourceLabel')}
          </Text>
          {/* CheckableTag's unchecked state is bare text by default, which reads
              as a stray label next to the selected pill rather than as something
              you can click. Give every chip a border so the row looks like one
              control. */}
          {(() => {
            const chip = (on: boolean) => ({
              border: `1px solid ${on ? 'transparent' : tokens.border}`,
              padding: '2px 10px',
              borderRadius: 12,
            });
            return (
              <>
                <Tag.CheckableTag checked={source === 'curated'}
                                  onChange={() => setSource('curated')}
                                  style={chip(source === 'curated')}
                                  data-testid="market-source-curated">
                  {t('market.sourceCurated')}
                </Tag.CheckableTag>
                {sources.map((s) => (
                  <Tag.CheckableTag key={s.id} checked={source === s.id}
                                    onChange={() => setSource(s.id)}
                                    style={chip(source === s.id)}
                                    data-testid={`market-source-${s.id}`}>
                    {s.label}
                  </Tag.CheckableTag>
                ))}
              </>
            );
          })()}
          {activeSource?.homepage && (
            <a href={activeSource.homepage} target="_blank" rel="noreferrer"
               style={{ fontSize: 12 }}>
              {t('market.sourceOpen')}
            </a>
          )}
        </div>
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
  const [plugins, setPlugins] = useState<PluginItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get<{ plugins: PluginItem[] }>('/extensions/plugins')
      .then((r) => setPlugins(r.data.plugins || []))
      .catch(() => setPlugins([]))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <Spin style={{ display: 'block', marginTop: 40 }} />;
  if (plugins.length === 0) return <Empty description={t('market.emptyPlugins')} />;

  return (
    <>
      <Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 12 }}>
        {t('market.pluginsNote')}
      </Paragraph>
      <div style={{ display: 'grid', gap: 10 }}>
        {plugins.map((p) => (
          <Card key={p.name} size="small" data-testid={`plugin-card-${p.name}`}
                styles={{ body: { padding: '12px 14px' } }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
              <AppstoreOutlined style={{ fontSize: 16, marginTop: 3,
                                         color: tokens.labelSecondary }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  <Text strong style={{ fontSize: 14 }}>{p.name}</Text>
                  {p.marketplace && <Tag style={{ marginInlineEnd: 0 }}>{p.marketplace}</Tag>}
                </div>
                <div style={{ fontSize: 12.5, color: tokens.labelSecondary, marginTop: 4 }}>
                  {p.description}
                </div>
              </div>
              <CheckCircleFilled style={{ color: tokens.success, fontSize: 14 }} />
            </div>
          </Card>
        ))}
      </div>
    </>
  );
};

// ---------------------------------------------------------------- skills

const SkillTab: React.FC = () => {
  const t = useT();
  const tokens = useThemeTokens();
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<string>('__all__');
  const [limit, setLimit] = useState(SKILL_PAGE);

  useEffect(() => {
    api.get<{ skills: SkillItem[] }>('/extensions/skills')
      .then((r) => setSkills(r.data.skills || []))
      .catch(() => setSkills([]))
      .finally(() => setLoading(false));
  }, []);

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

  if (loading) return <Spin style={{ display: 'block', marginTop: 40 }} />;

  const shown = filtered.slice(0, limit);

  return (
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

      {filtered.length === 0 ? (
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
  );
};
