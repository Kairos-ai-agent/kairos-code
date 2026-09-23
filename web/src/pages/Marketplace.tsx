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
import React, { useEffect, useMemo, useState } from 'react';
import {
  Badge, Button, Card, Empty, Input, Segmented, Spin, Tag, Tooltip,
  Typography, App as AntdApp,
} from 'antd';
import {
  ApiOutlined, AppstoreOutlined, BookOutlined, CheckCircleFilled,
  CloudOutlined, DownloadOutlined, ExperimentOutlined, DeleteOutlined,
  ReloadOutlined, SearchOutlined, ThunderboltOutlined,
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

  if (loading) return <Spin style={{ display: 'block', marginTop: 40 }} />;

  return (
    <>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12, alignItems: 'center' }}>
        <Input
          allowClear
          prefix={<SearchOutlined style={{ color: tokens.labelTertiary }} />}
          placeholder={t('market.searchMcp')}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ maxWidth: 320 }}
        />
        <Text type="secondary" style={{ fontSize: 12 }}>
          {t('market.countOf', { shown: filtered.length, total: servers.length })}
        </Text>
        {readOnly && (
          <Tag color="orange">{t('market.readOnlyBackend')}</Tag>
        )}
      </div>

      {filtered.length === 0 ? (
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
