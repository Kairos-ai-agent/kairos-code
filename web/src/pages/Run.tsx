/**
 * Run — the business-output home page.
 *
 * The whole app used to open on a chat composer, which answers "what can I type"
 * before answering "how is my work doing". This page answers exactly three
 * questions and nothing else:
 *
 *   1. Did the last change pass the gate?   (verdict, rounds, rejections, score)
 *   2. What did it cost?                    (spend, calls, tokens)
 *   3. Is it better than last time?          (Δ score, Δ rounds, Δ cost)
 *
 * Everything else (chat, tools, trace, today, projects, the raw loop view) is
 * still reachable, but it lives under "Advanced" in the sidebar.
 *
 * The Gate Report itself is one click away: view it in a tab, download the
 * HTML/Markdown, or copy the badge for a PR description.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Button, Card, Col, Empty, Input, Row, Select, Space, Spin, Table, Tag,
  Tooltip, Typography, message,
} from 'antd';
import {
  CheckCircleOutlined, CloseCircleOutlined, CopyOutlined, DownloadOutlined,
  EyeOutlined, FallOutlined, PlayCircleOutlined, ReloadOutlined, RiseOutlined,
  StopOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import api from '../api/client';
import { useT } from '../i18n';
import { useChatStore } from '../stores/chatStore';
import { useThemeTokens } from '../hooks/useThemeTokens';

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;

interface GateIssue {
  severity?: string;
  file?: string;
  line?: number | null;
  description?: string;
  location?: string;
}

interface GateRound {
  round: number;
  score: number;
  approve: boolean;
  summary?: string;
  coder_summary?: string;
  issues: GateIssue[];
}

interface GatePayload {
  project_id: string;
  project_name: string;
  state: 'passed' | 'rejected' | 'running' | 'empty';
  passed: boolean;
  first_pass: boolean;
  rounds_total: number;
  rejected_rounds: number;
  final_score: number;
  score_curve: number[];
  issues_total: number;
  cost_usd: number;
  cost_calls: number;
  tokens: number;
  badge: string;
  rounds: GateRound[];
  learned: Record<string, number>;
}

interface SessionRow {
  session_id: string;
  round_count: number;
  last_score: number;
  last_approve: boolean;
  started_at: number;
  running?: boolean;
}

interface LoopState {
  running: boolean;
  session_id?: string;
  round: number;
  last_score: number;
  last_approve: boolean;
}

const fmtCost = (usd: number) => `$${(usd || 0).toFixed(4)}`;
const fmtWhen = (ts: number) =>
  ts ? new Date(ts * 1000).toLocaleString() : '—';

/** Delta chip: better/worse than the previous run (lower cost is better). */
const Delta: React.FC<{ label: string; value: number; invert?: boolean; suffix?: string }> = ({
  label, value, invert, suffix = '',
}) => {
  const good = invert ? value <= 0 : value >= 0;
  if (!isFinite(value) || value === 0) {
    return <Text type="secondary">{label}: —</Text>;
  }
  return (
    <Text style={{ color: good ? 'var(--kairos-ok, #1a7f37)' : 'var(--kairos-bad, #cf222e)' }}>
      {good ? <RiseOutlined /> : <FallOutlined />} {label}: {value > 0 ? '+' : ''}
      {value.toFixed(suffix === '$' ? 4 : 0)}{suffix === '$' ? '' : ''}
    </Text>
  );
};

const Run: React.FC = () => {
  const t = useT();
  const tokens = useThemeTokens();
  const projects = useChatStore((s) => s.projects);
  const currentProject = useChatStore((s) => s.currentProject);
  const setCurrentProject = useChatStore((s) => s.setCurrentProject);

  const [projectId, setProjectId] = useState<string>(currentProject?.id || '');
  const [gate, setGate] = useState<GatePayload | null>(null);
  const [prevGate, setPrevGate] = useState<GatePayload | null>(null);
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [loop, setLoop] = useState<LoopState | null>(null);
  const [loading, setLoading] = useState(false);
  const [requirement, setRequirement] = useState('');
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!projectId && currentProject) setProjectId(currentProject.id);
  }, [currentProject, projectId]);

  useEffect(() => {
    if (!projectId && projects.length) setProjectId(projects[0].id);
  }, [projects, projectId]);

  const reportUrl = useCallback(
    (sessionId: string, format: 'html' | 'md' | 'json' = 'html', download = false) => {
      const q = new URLSearchParams({ format });
      if (sessionId) q.set('session', sessionId);
      if (download) q.set('download', '1');
      return `/api/projects/${projectId}/gate-report?${q.toString()}`;
    },
    [projectId],
  );

  const load = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    setError('');
    try {
      const [sessionsRes, loopRes] = await Promise.all([
        api.get(`/projects/${projectId}/sessions`),
        api.get(`/projects/${projectId}/loop`).catch(() => ({ data: null })),
      ]);
      const rows: SessionRow[] = sessionsRes.data.sessions || [];
      setSessions(rows);
      setLoop(loopRes.data);

      const [current, previous] = rows;
      if (current?.session_id) {
        const res = await api.get(`/projects/${projectId}/gate-report`, {
          params: { format: 'json', session: current.session_id },
        });
        setGate(res.data);
      } else {
        const res = await api.get(`/projects/${projectId}/gate-report`, {
          params: { format: 'json' },
        });
        setGate(res.data);
      }
      if (previous?.session_id) {
        const res = await api.get(`/projects/${projectId}/gate-report`, {
          params: { format: 'json', session: previous.session_id },
        });
        setPrevGate(res.data);
      } else {
        setPrevGate(null);
      }
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || t('run.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [projectId, t]);

  useEffect(() => { void load(); }, [load]);

  // Live polling while a loop is running (the loop emits on the WebSocket, but
  // polling keeps this page correct even if the socket dropped).
  useEffect(() => {
    if (!loop?.running) return undefined;
    const id = window.setInterval(() => { void load(); }, 2500);
    return () => window.clearInterval(id);
  }, [loop?.running, load]);

  const start = async () => {
    if (!projectId || !requirement.trim()) return;
    setStarting(true);
    try {
      await api.post(`/projects/${projectId}/start`, { requirement });
      message.success(t('run.started'));
      setRequirement('');
      await load();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || t('run.startFailed'));
    } finally {
      setStarting(false);
    }
  };

  const stop = async () => {
    try {
      await api.post(`/projects/${projectId}/stop`);
      message.success(t('run.stopped'));
      await load();
    } catch {
      message.error(t('run.stopFailed'));
    }
  };

  const copyBadge = async () => {
    if (!gate) return;
    try {
      await navigator.clipboard.writeText(gate.badge);
      message.success(t('run.badgeCopied'));
    } catch {
      message.warning(gate.badge);
    }
  };

  const selected = useMemo(
    () => projects.find((p) => p.id === projectId), [projects, projectId],
  );

  const verdictTag = () => {
    if (!gate) return <Tag>{t('run.state.empty')}</Tag>;
    if (loop?.running) return <Tag color="processing">{t('run.state.running')}</Tag>;
    if (gate.state === 'passed') {
      return (
        <Tag color="success" icon={<CheckCircleOutlined />} data-testid="run-verdict">
          {t('run.state.passed')}
        </Tag>
      );
    }
    if (gate.state === 'rejected') {
      return (
        <Tag color="error" icon={<CloseCircleOutlined />} data-testid="run-verdict">
          {t('run.state.rejected')}
        </Tag>
      );
    }
    return <Tag data-testid="run-verdict">{t('run.state.empty')}</Tag>;
  };

  const scoreDelta = gate && prevGate
    ? gate.final_score - prevGate.final_score
    : NaN;
  const roundsDelta = gate && prevGate
    ? gate.rounds_total - prevGate.rounds_total
    : NaN;
  const costDelta = gate && prevGate
    ? gate.cost_usd - prevGate.cost_usd
    : NaN;

  if (!projectId) {
    return (
      <div style={{ padding: 24 }} data-testid="run-empty">
        <Empty description={t('run.noProject')} />
        <Paragraph type="secondary" style={{ marginBlockStart: 12 }}>
          {t('run.demoHint')} <Text code>kairos demo</Text>
        </Paragraph>
      </div>
    );
  }

  return (
    <div style={{ padding: 24, maxWidth: 1100, marginInline: 'auto' }} data-testid="run-page">
      <Space style={{ marginBlockEnd: 16, width: '100%' }} align="center" wrap>
        <Title level={4} style={{ margin: 0 }}>{t('run.title')}</Title>
        <Select
          data-testid="run-project-select"
          style={{ minWidth: 220 }}
          value={projectId}
          onChange={(v) => {
            setProjectId(v);
            const p = projects.find((x) => x.id === v);
            if (p) setCurrentProject(p);
          }}
          options={projects.map((p) => ({ value: p.id, label: p.name }))}
        />
        <Tooltip title={t('run.refresh')}>
          <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading} />
        </Tooltip>
        <div style={{ flex: 1 }} />
        {loop?.running ? (
          <Button danger icon={<StopOutlined />} onClick={() => void stop()}>
            {t('run.stop')}
          </Button>
        ) : null}
      </Space>

      {error ? (
        <Alert type="error" showIcon style={{ marginBlockEnd: 16 }}
               message={error} data-testid="run-error" />
      ) : null}

      {loading && !gate ? <Spin /> : null}

      {gate && gate.rounds_total === 0 && !loop?.running ? (
        <Card data-testid="run-no-runs">
          <Empty description={t('run.noRuns')} />
          <Paragraph type="secondary" style={{ marginBlockStart: 12 }}>
            {t('run.noRunsHint')}
          </Paragraph>
          <Paragraph type="secondary">
            {t('run.demoHint')} <Text code>kairos demo</Text>
          </Paragraph>
        </Card>
      ) : null}

      {gate && (gate.rounds_total > 0 || loop?.running) ? (
        <>
          <Row gutter={[12, 12]}>
            {/* 1) Did it pass? */}
            <Col xs={24} md={8}>
              <Card data-testid="run-verdict-card" size="small"
                    title={<Space>{t('run.card.verdict')}{verdictTag()}</Space>}>
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <Text>
                    {t('run.rounds')}: <Text strong data-testid="run-rounds">{gate.rounds_total}</Text>
                    {' · '}
                    {t('run.rejected')}: <Text strong>{gate.rejected_rounds}</Text>
                  </Text>
                  <Text>
                    {t('run.finalScore')}: <Text strong>{gate.final_score}</Text>
                    {' · '}
                    {t('run.firstPass')}: {gate.first_pass ? t('run.yes') : t('run.no')}
                  </Text>
                  <Text type="secondary">
                    {t('run.issues')}: {gate.issues_total}
                  </Text>
                  {loop?.running ? (
                    <Text type="secondary">
                      {t('run.liveRound')}: {loop.round} · {t('run.finalScore')}: {loop.last_score}
                    </Text>
                  ) : null}
                </Space>
              </Card>
            </Col>

            {/* 2) What did it cost? */}
            <Col xs={24} md={8}>
              <Card size="small" title={t('run.card.cost')} data-testid="run-cost-card">
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <Text style={{ fontSize: 20, fontWeight: 600 }} data-testid="run-cost">
                    {fmtCost(gate.cost_usd)}
                  </Text>
                  <Text type="secondary">
                    {t('run.calls')}: {gate.cost_calls} · {t('run.tokens')}: {gate.tokens}
                  </Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {t('run.costHint')}
                  </Text>
                </Space>
              </Card>
            </Col>

            {/* 3) Better or worse than last time? */}
            <Col xs={24} md={8}>
              <Card size="small" title={t('run.card.delta')} data-testid="run-delta-card">
                {prevGate ? (
                  <Space direction="vertical" size={4}>
                    <span data-testid="run-delta">
                      <Delta label={t('run.finalScore')} value={scoreDelta} />
                    </span>
                    <Text type="secondary">
                      {t('run.rounds')}: {roundsDelta > 0 ? '+' : ''}{isFinite(roundsDelta) ? roundsDelta : '—'}
                    </Text>
                    <Text type="secondary">
                      {t('run.cost')}: {isFinite(costDelta)
                        ? `${costDelta > 0 ? '+' : ''}${fmtCost(costDelta)}` : '—'}
                    </Text>
                  </Space>
                ) : (
                  <Text type="secondary">{t('run.noPrev')}</Text>
                )}
              </Card>
            </Col>
          </Row>

          <Card
            size="small"
            style={{ marginBlockStart: 16 }}
            title={t('run.gateReport')}
            extra={
              <Space wrap>
                <Button
                  size="small" icon={<EyeOutlined />} data-testid="run-view-report"
                  onClick={() => window.open(reportUrl(sessions[0]?.session_id || ''), '_blank')}
                >
                  {t('run.view')}
                </Button>
                <Button
                  size="small" icon={<DownloadOutlined />} data-testid="run-download-html"
                  href={reportUrl(sessions[0]?.session_id || '', 'html', true)}
                >
                  HTML
                </Button>
                <Button size="small" icon={<DownloadOutlined />}
                        href={reportUrl(sessions[0]?.session_id || '', 'md', true)}>
                  Markdown
                </Button>
                <Button size="small" icon={<CopyOutlined />} onClick={() => void copyBadge()}
                        data-testid="run-copy-badge">
                  {t('run.copyBadge')}
                </Button>
              </Space>
            }
          >
            <Paragraph type="secondary" style={{ marginBlockEnd: 8 }}>
              <Text code>{gate.badge}</Text>
            </Paragraph>
            <Table<GateRound>
              size="small"
              rowKey="round"
              data-testid="run-rounds-table"
              pagination={false}
              dataSource={gate.rounds}
              columns={[
                { title: t('run.th.round'), dataIndex: 'round', width: 70,
                  render: (v: number) => `R${v}` },
                { title: t('run.th.score'), dataIndex: 'score', width: 80 },
                { title: t('run.th.verdict'), dataIndex: 'approve', width: 110,
                  render: (ok: boolean) =>
                    ok ? <Tag color="success">{t('run.approved')}</Tag>
                       : <Tag color="error">{t('run.rejectedTag')}</Tag> },
                { title: t('run.th.issues'), key: 'issues', width: 80,
                  render: (_: unknown, r: GateRound) => r.issues.length },
                { title: t('run.th.summary'), key: 'summary',
                  render: (_: unknown, r: GateRound) => (
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      {(r.coder_summary || r.summary || '').slice(0, 120)}
                    </Text>
                  ) },
              ]}
            />
          </Card>

          <Card
            size="small"
            style={{ marginBlockStart: 16 }}
            title={<Space><ThunderboltOutlined />{t('run.startTitle')}</Space>}
          >
            <TextArea
              data-testid="run-requirement"
              rows={2}
              value={requirement}
              onChange={(e) => setRequirement(e.target.value)}
              placeholder={t('run.startPlaceholder')}
              disabled={loop?.running}
            />
            <Space style={{ marginBlockStart: 8 }}>
              <Button
                type="primary"
                icon={<PlayCircleOutlined />}
                loading={starting}
                disabled={loop?.running || !requirement.trim()}
                onClick={() => void start()}
                data-testid="run-start"
              >
                {t('run.start')}
              </Button>
              {loop?.running ? (
                <Text type="secondary">{t('run.running')}</Text>
              ) : null}
              {selected?.work_dir ? (
                <Text type="secondary" style={{ fontSize: 12 }}>{selected.work_dir}</Text>
              ) : null}
            </Space>
          </Card>
        </>
      ) : null}
    </div>
  );
};

export default Run;
