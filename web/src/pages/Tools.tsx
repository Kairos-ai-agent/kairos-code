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
  Input, Select, Form, Statistic, App as AntdApp, Switch, Alert,
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

type Tool = 'teams' | 'cloud' | 'voice' | 'computer';

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
    </div>
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

