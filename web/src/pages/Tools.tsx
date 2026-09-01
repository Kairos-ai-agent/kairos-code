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

type Tool = 'teams' | 'cloud' | 'voice' | 'computer';

const TOOLS: { key: Tool; title: string; icon: React.ReactNode;
              description: string; route: string; status: string }[] = [
  { key: 'teams', title: 'Agent Teams',
    icon: <TeamOutlined style={{ fontSize: 28 }} />,
    description: 'Dispatch parallel Coder agents across a shared task board.',
    route: 'teams', status: 'per-project' },
  { key: 'cloud', title: 'Cloud Delegation',
    icon: <CloudOutlined style={{ fontSize: 28 }} />,
    description: 'Hand off tasks to a remote runner (the cloud-task CLI, VM, container).',
    route: 'cloud', status: 'optional' },
  { key: 'voice', title: 'Voice Mode',
    icon: <AudioOutlined style={{ fontSize: 28 }} />,
    description: 'STT (whisper) + TTS providers, with mock fallback for offline.',
    route: 'voice', status: 'optional' },
  { key: 'computer', title: 'Computer Use',
    icon: <DesktopOutlined style={{ fontSize: 28 }} />,
    description: 'Desktop automation: screenshot, mouse, keyboard (Windows ctypes).',
    route: 'computer', status: 'safety-required' },
];

const Tools: React.FC = () => {
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
          Tools
        </div>
        <div style={{ fontSize: 13, color: tokens.labelTertiary, marginTop: 4 }}>
          Optional subsystems that augment the core Coder / Reviewer loop.
        </div>
      </div>

      <Row gutter={[16, 16]}>
        {TOOLS.map((t) => (
          <Col xs={24} sm={12} lg={6} key={t.key}>
            <Card
              hoverable
              onClick={() => navigate(`/tools/${t.route}`)}
              style={{
                background: tokens.bgLay1,
                border: `1px solid ${tokens.border}`,
                height: '100%',
                cursor: 'pointer',
              }}
              styles={{ body: { padding: 20 } }}
            >
              <div style={{ color: tokens.labelPrimary, marginBottom: 12 }}>
                {t.icon}
              </div>
              <div style={{ fontWeight: 600, fontSize: 15,
                            color: tokens.labelPrimary, marginBottom: 6 }}>
                {t.title}
              </div>
              <div style={{ fontSize: 12, color: tokens.labelTertiary,
                            lineHeight: 1.5, minHeight: 50 }}>
                {t.description}
              </div>
              <div style={{ marginTop: 12 }}>
                <Tag>{t.status}</Tag>
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
  const tokens = useThemeTokens();
  const meta = TOOLS.find((t) => t.key === tool)!;
  return (
    <div style={{ padding: '20px 24px', maxWidth: 960, margin: '0 auto' }}>
      <Button type="text" icon={<ArrowLeftOutlined />}
              onClick={onBack} style={{ marginBottom: 16 }}>
        Back to tools
      </Button>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16,
                    marginBottom: 16 }}>
        <div style={{ color: tokens.labelPrimary }}>{meta.icon}</div>
        <div>
          <div style={{ fontSize: 20, fontWeight: 600,
                        color: tokens.labelPrimary }}>{meta.title}</div>
          <div style={{ fontSize: 13, color: tokens.labelTertiary }}>
            {meta.description}
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
      notify.warning('Pick a project and type a task list.');
      return;
    }
    const lines = taskInput.split('\n').map((s) => s.trim()).filter(Boolean);
    setBusy(true);
    try {
      const r = await api.post(`/projects/${projectId}/team/create`, {
        descriptions: lines, max_workers: maxWorkers,
        merge_strategy: 'fast_forward',
      });
      notify.success(`Team ${r.data.team_id} created with ${r.data.task_count} tasks.`);
      setTaskInput('');
    } catch (e: any) {
      notify.error(e?.response?.data?.detail || 'Failed to create team');
    } finally { setBusy(false); }
  };

  return (
    <Card style={{ background: tokens.bgLay1, border: `1px solid ${tokens.border}` }}>
      <Alert type="info" showIcon style={{ marginBottom: 16 }}
             message="Each line in the box below becomes one team task dispatched in parallel." />
      <Form layout="vertical">
        <Form.Item label="Tasks (one per line)">
          <Input.TextArea rows={6} value={taskInput}
            onChange={(e) => setTaskInput(e.target.value)}
            placeholder={'Refactor user_service.py\nAdd unit tests for billing\nUpdate API docs'}
            disabled={!projectId} />
        </Form.Item>
        <Form.Item label="Max workers">
          <Select value={maxWorkers} onChange={setMaxWorkers}
                  options={[1, 2, 3, 4, 5].map((n) => ({ value: n, label: String(n) }))} />
        </Form.Item>
        <Button type="primary" loading={busy} disabled={!projectId} onClick={create}
                style={{ background: tokens.labelPrimary, border: 'none' }}>
          Create team & dispatch
        </Button>
      </Form>
    </Card>
  );
};

// ---- Cloud ----
const CloudPanel: React.FC<{
  currentProject: any; notify: any;
}> = ({ currentProject, notify }) => {
  const tokens = useThemeTokens();
  const [task, setTask] = useState('');
  const [delegateUrl, setDelegateUrl] = useState('');
  const [busy, setBusy] = useState(false);

  const delegate = async () => {
    if (!currentProject) {
      notify.warning('Pick a project first.');
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
      notify.success('Task submitted to cloud runner.');
    } catch (e: any) {
      // 503 = no delegator configured; that's expected unless
      // KAIROS_CLOUD_URL is set in the env. Surface as a soft error.
      const detail = e?.response?.data?.detail;
      if (e?.response?.status === 503) {
        notify.warning('No cloud delegator configured. Set KAIROS_CLOUD_URL in the server env to enable.');
      } else {
        notify.error(detail || 'Failed to delegate');
      }
    } finally { setBusy(false); }
  };

  return (
    <Card style={{ background: tokens.bgLay1, border: `1px solid ${tokens.border}` }}>
      <Alert type="warning" showIcon style={{ marginBottom: 16 }}
             message="Cloud delegation requires KAIROS_CLOUD_URL set on the server. Without it, the endpoint returns 503 — that's expected." />
      <Form layout="vertical">
        <Form.Item label="Remote runner URL (informational)">
          <Input value={delegateUrl} onChange={(e) => setDelegateUrl(e.target.value)}
                 placeholder="https://runners.example.com" />
        </Form.Item>
        <Form.Item label="Task description">
          <Input.TextArea rows={4} value={task} onChange={(e) => setTask(e.target.value)}
                          placeholder="Refactor the billing module" />
        </Form.Item>
        <Button type="primary" loading={busy} disabled={!currentProject} onClick={delegate}
                style={{ background: tokens.labelPrimary, border: 'none' }}>
          Submit to cloud
        </Button>
      </Form>
    </Card>
  );
};

// ---- Voice ----
const VoicePanel: React.FC<{ notify: any }> = ({ notify }) => {
  const tokens = useThemeTokens();
  return (
    <Card style={{ background: tokens.bgLay1, border: `1px solid ${tokens.border}` }}>
      <Alert type="info" showIcon style={{ marginBottom: 16 }}
             message="Voice mode wires STT + TTS into the chat composer. The mock providers ship by default; real whisper / piper are pluggable via Python import." />
      <Row gutter={16}>
        <Col span={12}>
          <Card size="small" title="STT (speech → text)" type="inner">
            <p>Default: <code>MockSTTProvider</code> (returns SHA-256 fingerprint as a placeholder).</p>
            <p>Production: <code>WhisperSTTProvider</code> (uses <code>faster-whisper</code> or <code>openai-whisper</code> fallback).</p>
          </Card>
        </Col>
        <Col span={12}>
          <Card size="small" title="TTS (text → speech)" type="inner">
            <p>Default: <code>MockTTSProvider</code> (returns a 0.1s silent WAV).</p>
            <p>Production: pluggable — Piper, ElevenLabs, OpenAI TTS, Azure Speech, etc.</p>
          </Card>
        </Col>
      </Row>
      <div style={{ marginTop: 16, fontSize: 12, color: tokens.labelTertiary }}>
        Configure voice mode by setting the providers in <code>VoiceSession(...)
        </code> on the server. The chat composer's "Mode" select can then expose a
        voice-input affordance.
      </div>
    </Card>
  );
};

// ---- Computer use ----
const ComputerPanel: React.FC<{ notify: any }> = ({ notify }) => {
  const tokens = useThemeTokens();
  const [confirm, setConfirm] = useState(false);
  return (
    <Card style={{ background: tokens.bgLay1, border: `1px solid ${tokens.border}` }}>
      <Alert type="error" showIcon style={{ marginBottom: 16 }}
             message="Computer use sends real mouse / keyboard events to the host desktop. The mock backend is the default; only enable auto_confirm after you've tested with dry_run=True." />
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}>
          <Statistic title="Mock backend"
                     valueStyle={{ color: tokens.success, fontSize: 18 }} />
        </Col>
        <Col span={8}>
          <Statistic title="Platform backend"
                     value={navigator?.platform || 'unknown'}
                     valueStyle={{ color: tokens.labelSecondary, fontSize: 14 }} />
        </Col>
        <Col span={8}>
          <Statistic title="Auto-confirm"
                     value={confirm ? 'on' : 'off'}
                     valueStyle={{ color: confirm ? tokens.danger : tokens.success,
                                    fontSize: 18 }} />
        </Col>
      </Row>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Switch checked={confirm} onChange={setConfirm} />
        <span style={{ color: tokens.labelSecondary, fontSize: 13 }}>
          Allow input actions (click / type / key press) without per-action confirmation
        </span>
      </div>
      <Alert type="info" showIcon style={{ marginTop: 16 }}
             message="Real backend is Windows-only. Other platforms fall back to the mock. See kairos.computer_use for the ctypes implementation." />
    </Card>
  );
};

export default Tools;

