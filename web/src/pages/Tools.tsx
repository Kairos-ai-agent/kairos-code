/**
 * Tools page — single dashboard with entry points to the four
 * P2 features that didn't have a UI yet:
 *
 *   - Teams       (kairos.teams)   — multi-agent team dispatch
 *   - Cloud       (kairos.cloud)   — remote runner delegation
 *   - Voice       (kairos.voice)   — STT + TTS providers
 *   - ComputerUse (kairos.computer_use) — desktop automation
 *
 * Each tile shows status (configured / not) and a quick action.
 * Detailed views open in the existing `Loop` page or a sub-modal.
 *
 * Backend status is best-effort: if the endpoint 404s (because the
 * project's runtime didn't attach the subsystem), we surface that
 * as a tile-level warning instead of failing the whole page.
 */
import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Card, Row, Col, Button, Tag, Space, Tooltip, Empty, Spin, Modal,
  Input, Select, Form, Statistic, App as AntdApp, Switch, Alert, Table,
} from 'antd';
import {
  TeamOutlined, CloudOutlined, AudioOutlined, DesktopOutlined,
  ArrowLeftOutlined, CheckCircleFilled, CloseCircleFilled,
  PlayCircleOutlined, ReloadOutlined,
  ApiOutlined, MessageOutlined, ToolOutlined,
} from '@ant-design/icons';

import { useChatStore } from '../stores/chatStore';
import { useThemeTokens } from '../hooks/useThemeTokens';
import api from '../api/client';
import { formatError } from '../utils/formatError';
import { useT } from '../i18n';

type Tool = 'teams' | 'cloud' | 'voice' | 'computer' | 'capabilities';

const TOOLS: { key: Tool; titleKey: string; icon: React.ReactNode;
              descriptionKey: string; route: string; statusKey: string }[] = [
  { key: 'teams', titleKey: 'tools.page.teamsTitle',
    icon: <TeamOutlined style={{ fontSize: 28 }} />,
    descriptionKey: 'tools.page.teamsDescription',
    route: 'teams', statusKey: 'tools.page.statusPerProject' },
  { key: 'cloud', titleKey: 'tools.page.cloudTitle',
    icon: <CloudOutlined style={{ fontSize: 28 }} />,
    descriptionKey: 'tools.page.cloudDescription',
    route: 'cloud', statusKey: 'common.optional' },
  { key: 'voice', titleKey: 'tools.page.voiceTitle',
    icon: <AudioOutlined style={{ fontSize: 28 }} />,
    descriptionKey: 'tools.page.voiceDescription',
    route: 'voice', statusKey: 'common.optional' },
  { key: 'computer', titleKey: 'tools.page.computerTitle',
    icon: <DesktopOutlined style={{ fontSize: 28 }} />,
    descriptionKey: 'tools.page.computerDescription',
    route: 'computer', statusKey: 'tools.page.statusSafetyRequired' },
  { key: 'capabilities', titleKey: 'tools.capabilities.title',
    icon: <ApiOutlined style={{ fontSize: 28 }} />,
    descriptionKey: 'tools.capabilities.description',
    route: 'capabilities', statusKey: 'tools.capabilities.status' },
];

/**
 * Render the `**...**` spans of a translated string as <code> blocks, so a
 * translator can move the provider / class names around inside the sentence
 * without touching JSX.
 */
const withCode = (text: string): React.ReactNode =>
  text.split(/(\*\*[^*]+\*\*)/).map((part, i) =>
    (part.startsWith('**') && part.endsWith('**')
      ? <code key={i}>{part.slice(2, -2)}</code>
      : <React.Fragment key={i}>{part}</React.Fragment>));

const Tools: React.FC = () => {
  const t = useT();
  const tokens = useThemeTokens();
  const navigate = useNavigate();
  const { tool } = useParams<{ tool?: string }>();
  const currentProject = useChatStore((s) => s.currentProject);
  const { message: msgApi } = AntdApp.useApp();

  if (tool) {
    return <ToolDetail tool={tool as Tool}
                       onBack={() => navigate('/tools')}
                       currentProject={currentProject}
                       notify={msgApi} />;
  }

  return (
    <div style={{ padding: '24px 16px', maxWidth: 1100, margin: '0 auto' }}>
      <div style={{ marginBottom: 24 }}>
        <div style={{ fontSize: 22, fontWeight: 700, color: tokens.labelPrimary }}>
          {t('tools.page.title')}
        </div>
        <div style={{ fontSize: 13, color: tokens.labelTertiary, marginTop: 4 }}>
          {t('tools.page.subtitle')}
        </div>
      </div>

      <Row gutter={[16, 16]}>
        {TOOLS.map((item) => (
          <Col xs={24} sm={12} lg={6} key={item.key}>
            <Card
              hoverable
              onClick={() => navigate(`/tools/${item.route}`)}
              style={{
                background: tokens.bgLay1,
                border: `1px solid ${tokens.border}`,
                height: '100%',
                cursor: 'pointer',
              }}
              styles={{ body: { padding: 20 } }}
            >
              <div style={{ color: tokens.labelPrimary, marginBottom: 12 }}>
                {item.icon}
              </div>
              <div style={{ fontWeight: 600, fontSize: 15,
                            color: tokens.labelPrimary, marginBottom: 6 }}>
                {t(item.titleKey)}
              </div>
              <div style={{ fontSize: 12, color: tokens.labelTertiary,
                            lineHeight: 1.5, minHeight: 50 }}>
                {t(item.descriptionKey)}
              </div>
              <div style={{ marginTop: 12 }}>
                <Tag>{t(item.statusKey)}</Tag>
              </div>
            </Card>
          </Col>
        ))}
      </Row>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Per-tool detail
// ---------------------------------------------------------------------------

const ToolDetail: React.FC<{
  tool: Tool; onBack: () => void;
  currentProject: ReturnType<typeof useChatStore.getState>['currentProject'];
  notify: ReturnType<typeof AntdApp.useApp>['message'];
}> = ({ tool, onBack, currentProject, notify }) => {
  const t = useT();
  const tokens = useThemeTokens();
  const meta = TOOLS.find((item) => item.key === tool)!;
  return (
    <div style={{ padding: '20px 24px', maxWidth: 960, margin: '0 auto' }}>
      <Button type="text" icon={<ArrowLeftOutlined />}
              onClick={onBack} style={{ marginBottom: 16 }}>
        {t('tools.page.backToTools')}
      </Button>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16,
                    marginBottom: 16 }}>
        <div style={{ color: tokens.labelPrimary }}>{meta.icon}</div>
        <div>
          <div style={{ fontSize: 20, fontWeight: 600,
                        color: tokens.labelPrimary }}>{t(meta.titleKey)}</div>
          <div style={{ fontSize: 13, color: tokens.labelTertiary }}>
            {t(meta.descriptionKey)}
          </div>
        </div>
      </div>
      {tool === 'teams' && <TeamsPanel currentProject={currentProject} notify={notify} />}
      {tool === 'cloud' && <CloudPanel currentProject={currentProject} notify={notify} />}
      {tool === 'voice' && <VoicePanel notify={notify} />}
      {tool === 'computer' && <ComputerPanel notify={notify} />}
      {tool === 'capabilities' && <CapabilitiesPanel currentProject={currentProject} />}
    </div>
  );
};

// ---- Capabilities (R38.12) ----
// One screen answering "what does the agent actually have here, and what is
// missing?" — read from the runtime (the loader, the MCP registry, the plugin
// manager), not from the registry files, so it cannot disagree with reality the
// way /extensions/summary did.
const CapabilitiesPanel: React.FC<{ currentProject: any }> = ({ currentProject }) => {
  const t = useT();
  const tokens = useThemeTokens();
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setFailed(false);
    try {
      const res = await api.get('/extensions/capabilities', {
        params: currentProject?.id ? { project_id: currentProject.id } : {},
      });
      setData(res.data);
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, [currentProject?.id]);

  useEffect(() => { load(); }, [load]);

  const card = { background: tokens.bgLay1, border: `1px solid ${tokens.border}` };

  if (loading) {
    return <Card style={card}><Spin /></Card>;
  }
  if (failed || !data) {
    return (
      <Card style={card}>
        <Empty description={t('tools.capabilities.loadFailed')} />
        <div style={{ textAlign: 'center', marginTop: 12 }}>
          <Button icon={<ReloadOutlined />} onClick={load}>
            {t('tools.capabilities.reload')}
          </Button>
        </div>
      </Card>
    );
  }

  const skills = data.skills || {};
  const mcp = data.mcp || {};
  const plugins = data.plugins || {};
  const problems: string[] = data.problems || [];
  const nativeTools: string[] = data.nativeTools || [];
  const configured: any[] = mcp.configured || [];
  const bundledServers: any[] = mcp.bundledServers || [];
  const rejected: Record<string, string> = mcp.rejected || {};

  const SCOPE_KEYS: Record<string, string> = {
    bundled: 'tools.capabilities.scopeBundled',
    global: 'tools.capabilities.scopeGlobal',
    project: 'tools.capabilities.scopeProject',
    unknown: 'tools.capabilities.scopeUnknown',
    'bundled-plugin': 'tools.capabilities.sourceBundledPlugin',
    user: 'tools.capabilities.sourceUser',
    config: 'tools.capabilities.sourceConfig',
  };
  const label = (value: string) =>
    (SCOPE_KEYS[value] ? t(SCOPE_KEYS[value]) : value);

  const skillRows = (skills.items || []).map((s: any) => ({
    key: s.name,
    name: s.name,
    scope: s.scope,
    priority: s.priority,
    trigger: Boolean(s.when && Object.keys(s.when).length),
    description: s.description,
  }));
  const mcpRows = configured.map((m: any) => ({
    key: m.name,
    name: m.name,
    transport: m.transport,
    source: m.source,
    enabled: m.enabled,
    detail: m.url || m.command || '',
  }));

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card
        style={card}
        title={t('tools.capabilities.problemsTitle')}
        extra={<Button size="small" icon={<ReloadOutlined />} onClick={load}>
          {t('tools.capabilities.reload')}
        </Button>}
      >
        {problems.length === 0
          ? <Alert type="success" showIcon message={t('tools.capabilities.noProblems')} />
          : <Space direction="vertical" size={6} style={{ width: '100%' }}>
              {problems.map((p, i) => (
                <Alert key={i} type="warning" showIcon message={<code>{p}</code>} />
              ))}
            </Space>}
      </Card>

      <Card style={card} title={t('tools.capabilities.summaryTitle')}>
        <Row gutter={16}>
          <Col span={6}>
            <Statistic title={t('tools.capabilities.skills')}
                       value={skills.count || 0}
                       suffix={`/ ${skills.filesOnDisk || 0}`} />
          </Col>
          <Col span={6}>
            <Statistic title={t('tools.capabilities.mcpServers')}
                       value={configured.length} />
          </Col>
          <Col span={6}>
            <Statistic title={t('tools.capabilities.offlineServers')}
                       value={bundledServers.length}
                       suffix={`/ ${bundledServers.reduce(
                         (n: number, s: any) => n + (s.toolCount || 0), 0)}`} />
          </Col>
          <Col span={6}>
            <Statistic title={t('tools.capabilities.plugins')}
                       value={plugins.count || 0}
                       suffix={`+ ${plugins.bundledCount || 0}`} />
          </Col>
        </Row>
      </Card>

      <Card style={card} title={t('tools.capabilities.skillsTitle')}>
        <Table size="small" pagination={{ pageSize: 8 }}
               dataSource={skillRows}
               columns={[
                 { title: t('tools.capabilities.colName'), dataIndex: 'name',
                   render: (v: string) => <code>{v}</code> },
                 { title: t('tools.capabilities.colScope'), dataIndex: 'scope',
                   width: 120, render: (v: string) => <Tag>{label(v)}</Tag> },
                 { title: t('tools.capabilities.colPriority'), dataIndex: 'priority',
                   width: 100 },
                 { title: t('tools.capabilities.colTrigger'), dataIndex: 'trigger',
                   width: 110,
                   render: (v: boolean) => v
                     ? <CheckCircleFilled style={{ color: tokens.success }} />
                     : <CloseCircleFilled style={{ color: tokens.labelTertiary }} /> },
               ]}
        />
      </Card>

      <Card style={card} title={t('tools.capabilities.mcpTitle')}>
        <Table size="small" pagination={false}
               dataSource={mcpRows}
               locale={{ emptyText: t('tools.capabilities.noServers') }}
               columns={[
                 { title: t('tools.capabilities.colName'), dataIndex: 'name',
                   render: (v: string) => <code>{v}</code> },
                 { title: t('tools.capabilities.colTransport'), dataIndex: 'transport',
                   width: 110 },
                 { title: t('tools.capabilities.colSource'), dataIndex: 'source',
                   width: 150, render: (v: string) => <Tag color="blue">{label(v)}</Tag> },
                 { title: t('tools.capabilities.colEnabled'), dataIndex: 'enabled',
                   width: 100,
                   render: (v: boolean) => v
                     ? <Tag color="green">{t('tools.capabilities.on')}</Tag>
                     : <Tag>{t('tools.capabilities.off')}</Tag> },
                 { title: t('tools.capabilities.colDetail'), dataIndex: 'detail',
                   ellipsis: true, render: (v: string) => <span style={{
                     fontSize: 12, color: tokens.labelTertiary }}>{v}</span> },
               ]}
        />
        {Object.keys(rejected).length > 0 && (
          <div style={{ marginTop: 12 }}>
            <div style={{ fontSize: 13, color: tokens.labelSecondary,
                          marginBottom: 6 }}>
              {t('tools.capabilities.rejectedTitle')}
            </div>
            {Object.entries(rejected).map(([name, reason]) => (
              <div key={name} style={{ fontSize: 12 }}>
                <code>{name}</code> — {reason}
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card style={card} title={t('tools.capabilities.offlineTitle')}>
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          {bundledServers.map((s: any) => (
            <div key={s.name}>
              <code>{s.name}</code>{' '}
              <Tag color="green">{t('tools.capabilities.offline')}</Tag>{' '}
              <span style={{ fontSize: 12, color: tokens.labelTertiary }}>
                {s.toolCount} {t('tools.capabilities.toolsUnit')}
              </span>
              <div style={{ marginTop: 4 }}>
                {(s.tools || []).map((n: string) => (
                  <Tag key={n} style={{ marginBottom: 4 }}>{n}</Tag>
                ))}
              </div>
            </div>
          ))}
        </Space>
      </Card>

      <Card style={card} title={t('tools.capabilities.pluginsTitle')}>
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          {(plugins.bundled || []).map((p: any) => (
            <div key={`b-${p.name}`}>
              <Tag color="blue">{t('tools.capabilities.shipped')}</Tag>{' '}
              <code>{p.name}</code> <span style={{
                fontSize: 12, color: tokens.labelTertiary }}>v{p.version}</span>
              <div style={{ marginTop: 4 }}>
                {(p.capabilities || []).map((c: string) => (
                  <Tag key={c}>{c}</Tag>
                ))}
              </div>
            </div>
          ))}
          {(plugins.installed || []).length === 0 && (
            <div style={{ fontSize: 13, color: tokens.labelTertiary }}>
              {t('tools.capabilities.noUserPlugins')}
            </div>
          )}
          {(plugins.installed || []).map((p: any) => (
            <div key={`i-${p.name}`}>
              <Tag>{t('tools.capabilities.installedTag')}</Tag>{' '}
              <code>{p.name}</code>
            </div>
          ))}
          <div style={{ fontSize: 12, color: tokens.labelTertiary }}>
            {t('tools.capabilities.registryHint')
              .replace('{n}', String(plugins.availableInRegistry || 0))}
          </div>
        </Space>
      </Card>

      <Card style={card} title={t('tools.capabilities.nativeTitle')}>
        {nativeTools.map((n) => <Tag key={n} style={{ marginBottom: 4 }}>{n}</Tag>)}
      </Card>
    </Space>
  );
};

// ---- Teams ----
const TeamsPanel: React.FC<{
  currentProject: any; notify: any;
}> = ({ currentProject, notify }) => {
  const t = useT();
  const tokens = useThemeTokens();
  const [sessions, setSessions] = useState<{ team_id: string; project_id: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [taskInput, setTaskInput] = useState('');
  const [maxWorkers, setMaxWorkers] = useState(3);
  const [busy, setBusy] = useState(false);
  const projectId = currentProject?.id;

  const refresh = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      // /api/projects/{id}/team/list would be the ideal endpoint.
      // For now we show an empty list — the create form is the main action.
      setSessions([]);
    } finally { setLoading(false); }
  }, [projectId]);

  useEffect(() => { refresh(); }, [refresh]);

  const create = async () => {
    if (!projectId || !taskInput.trim()) {
      notify.warning(t('tools.page.needProjectAndTasks'));
      return;
    }
    const lines = taskInput.split('\n').map((s) => s.trim()).filter(Boolean);
    setBusy(true);
    try {
      const r = await api.post(`/projects/${projectId}/team/create`, {
        descriptions: lines, max_workers: maxWorkers,
        merge_strategy: 'fast_forward',
      });
      notify.success(t('tools.page.teamCreated',
        { team: r.data.team_id, count: r.data.task_count }));
      setTaskInput('');
    } catch (e: any) {
      notify.error(e?.response?.data?.detail || t('tools.page.createTeamFailed'));
    } finally { setBusy(false); }
  };

  return (
    <Card style={{ background: tokens.bgLay1, border: `1px solid ${tokens.border}` }}>
      <Alert type="info" showIcon style={{ marginBottom: 16 }}
             message={t('tools.page.teamsAlert')} />
      <Form layout="vertical">
        <Form.Item label={t('tools.page.teamsTasksLabel')}>
          <Input.TextArea rows={6} value={taskInput}
            onChange={(e) => setTaskInput(e.target.value)}
            placeholder={t('tools.page.teamsTasksPlaceholder')}
            disabled={!projectId} />
        </Form.Item>
        <Form.Item label={t('tools.page.maxWorkersLabel')}>
          <Select value={maxWorkers} onChange={setMaxWorkers}
                  options={[1, 2, 3, 4, 5].map((n) => ({ value: n, label: String(n) }))} />
        </Form.Item>
        <Button type="primary" loading={busy} disabled={!projectId} onClick={create}
                style={{ background: tokens.labelPrimary, border: 'none' }}>
          {t('tools.page.createTeam')}
        </Button>
      </Form>
    </Card>
  );
};

// ---- Cloud ----
const CloudPanel: React.FC<{
  currentProject: any; notify: any;
}> = ({ currentProject, notify }) => {
  const t = useT();
  const tokens = useThemeTokens();
  const [task, setTask] = useState('');
  const [delegateUrl, setDelegateUrl] = useState('');
  const [busy, setBusy] = useState(false);

  const delegate = async () => {
    if (!currentProject) {
      notify.warning(t('tools.page.needProject'));
      return;
    }
    setBusy(true);
    try {
      // Register the delegator for the project (in-memory, per-session).
      // Real CloudDelegator wiring lives in kairos.cloud.
      await api.post(`/projects/${currentProject.id}/cloud/submit`, {
        description: task,
        work_dir: currentProject.work_dir || '',
      });
      notify.success(t('tools.page.cloudSubmitted'));
    } catch (e: any) {
      // 503 = no delegator configured; that's expected unless
      // KAIROS_CLOUD_URL is set in the env. Surface as a soft error.
      const detail = e?.response?.data?.detail;
      if (e?.response?.status === 503) {
        notify.warning(t('tools.page.noCloudDelegator'));
      } else {
        notify.error(detail || t('tools.page.delegateFailed'));
      }
    } finally { setBusy(false); }
  };

  return (
    <Card style={{ background: tokens.bgLay1, border: `1px solid ${tokens.border}` }}>
      <Alert type="warning" showIcon style={{ marginBottom: 16 }}
             message={t('tools.page.cloudAlert')} />
      <Form layout="vertical">
        <Form.Item label={t('tools.page.runnerUrlLabel')}>
          <Input value={delegateUrl} onChange={(e) => setDelegateUrl(e.target.value)}
                 placeholder={t('tools.page.runnerUrlPlaceholder')} />
        </Form.Item>
        <Form.Item label={t('tools.page.taskDescriptionLabel')}>
          <Input.TextArea rows={4} value={task} onChange={(e) => setTask(e.target.value)}
                          placeholder={t('tools.page.cloudTaskPlaceholder')} />
        </Form.Item>
        <Button type="primary" loading={busy} disabled={!currentProject} onClick={delegate}
                style={{ background: tokens.labelPrimary, border: 'none' }}>
          {t('tools.page.submitToCloud')}
        </Button>
      </Form>
    </Card>
  );
};

// ---- Voice ----
const VoicePanel: React.FC<{ notify: any }> = ({ notify }) => {
  const t = useT();
  const tokens = useThemeTokens();
  return (
    <Card style={{ background: tokens.bgLay1, border: `1px solid ${tokens.border}` }}>
      <Alert type="info" showIcon style={{ marginBottom: 16 }}
             message={t('tools.page.voiceAlert')} />
      <Row gutter={16}>
        <Col span={12}>
          <Card size="small" title={t('tools.page.sttTitle')} type="inner">
            <p>{withCode(t('tools.page.sttDefault'))}</p>
            <p>{withCode(t('tools.page.sttProduction'))}</p>
          </Card>
        </Col>
        <Col span={12}>
          <Card size="small" title={t('tools.page.ttsTitle')} type="inner">
            <p>{withCode(t('tools.page.ttsDefault'))}</p>
            <p>{withCode(t('tools.page.ttsProduction'))}</p>
          </Card>
        </Col>
      </Row>
      <div style={{ marginTop: 16, fontSize: 12, color: tokens.labelTertiary }}>
        {withCode(t('tools.page.voiceFooter'))}
      </div>
    </Card>
  );
};

// ---- Computer use ----
const ComputerPanel: React.FC<{ notify: any }> = ({ notify }) => {
  const t = useT();
  const tokens = useThemeTokens();
  const [confirm, setConfirm] = useState(false);
  return (
    <Card style={{ background: tokens.bgLay1, border: `1px solid ${tokens.border}` }}>
      <Alert type="error" showIcon style={{ marginBottom: 16 }}
             message={t('tools.page.computerAlert')} />
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}>
          <Statistic title={t('tools.page.mockBackend')}
                     valueStyle={{ color: tokens.success, fontSize: 18 }} />
        </Col>
        <Col span={8}>
          <Statistic title={t('tools.page.platformBackend')}
                     value={navigator?.platform || t('common.unknown')}
                     valueStyle={{ color: tokens.labelSecondary, fontSize: 14 }} />
        </Col>
        <Col span={8}>
          <Statistic title={t('tools.page.autoConfirm')}
                     value={confirm ? t('common.on') : t('common.off')}
                     valueStyle={{ color: confirm ? tokens.danger : tokens.success,
                                    fontSize: 18 }} />
        </Col>
      </Row>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Switch checked={confirm} onChange={setConfirm} />
        <span style={{ color: tokens.labelSecondary, fontSize: 13 }}>
          {t('tools.page.allowInputActions')}
        </span>
      </div>
      <Alert type="info" showIcon style={{ marginTop: 16 }}
             message={t('tools.page.computerRealBackendAlert')} />
    </Card>
  );
};

export default Tools;

