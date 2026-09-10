import { formatError } from '../utils/formatError';
import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  Card, Row, Col, Input, Button, List, Tag, Typography, Space, Avatar, Badge, message, Progress,
} from 'antd';
import {
  SendOutlined, RobotOutlined, ReloadOutlined, StopOutlined, PlayCircleOutlined,
  CheckCircleOutlined, CloseCircleOutlined, CodeOutlined, FileTextOutlined,
  CheckOutlined, BarChartOutlined, BranchesOutlined, QuestionCircleOutlined,
  UndoOutlined, BulbOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import { Modal, Tabs, Tooltip, Select, Alert } from 'antd';
import MermaidRenderer from '../components/MermaidRenderer';
import PlanPanel from '../components/PlanPanel';
import PlanHistoryPanel from '../components/PlanHistoryPanel';
import CostDashboard from '../components/CostDashboard';
import EvalPanel from '../components/EvalPanel';
import TrendPanel from '../components/TrendPanel';
import SkillSearchPalette from '../components/SkillSearchPalette';
import api, { revertFile } from '../api/client';
import { useAgentStore } from '../stores/agentStore';
import { onWebSocketMessage, onWebSocketState } from '../api/client';
import type { AgentState, Message } from '../types';

const { Title, Text } = Typography;
const { TextArea } = Input;

interface LoopState {
  running: boolean;
  session_id?: string;
  round: number;
  last_score: number;
  last_approve: boolean;
  no_progress_count: number;
  history: any[];
}

interface PlanState {
  pending: boolean;
  decision: string | null;
  text: string;
  round: number;
}

interface Issue {
  category: string;
  severity: string;
  file?: string;
  line?: number;
  description: string;
  fix_instruction: string;
  _source_reviewer?: string;
}

interface Stats {
  running: boolean;
  rounds: Array<{ round: number; score: number; approve: boolean;
                  issues: number; summary: string; ts: number }>;
  score_window: number[];
  total_tokens_used: number;
  approximate_cost_usd: number;
  infra_failure_streak: number;
  no_progress_count: number;
}

interface PlanViz {
  mermaid: string;
  file_tree: string;
  round: number;
}

interface AskState {
  pending: boolean;
  question: string;
  context: string;
  round: number;
}

interface Checkpoint {
  sha: string;
  round: number;
  score: number;
  approved: boolean;
  summary: string;
  ts: number;
}

const roleAvatars: Record<string, string> = {
  coder: '🛠️',
  reviewer: '🔍',
};

const statusColors: Record<string, string> = {
  idle: '#52c41a',
  thinking: '#faad14',
  acting: '#1677ff',
  error: '#ff4d4f',
};

const severityColor: Record<string, string> = {
  CRITICAL: 'red',
  MAJOR: 'orange',
  MINOR: 'blue',
  SUGGESTION: 'green',
};

const Loop: React.FC = () => {
  const agents = useAgentStore((s) => s.agents);
  const messages = useAgentStore((s) => s.messages);
  const setAgents = useAgentStore((s) => s.setAgents);
  const setMessages = useAgentStore((s) => s.setMessages);
  const [projects, setProjects] = useState<any[]>([]);
  const [selectedProject, setSelectedProject] = useState<string | null>(null);
  const [requirement, setRequirement] = useState('');
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [loop, setLoop] = useState<LoopState | null>(null);
  const [plan, setPlan] = useState<PlanState | null>(null);
  const [wsOpen, setWsOpen] = useState(false);
  // Per-agent stream buffer. Cleared when a new turn begins (we detect
  // "thinking" event as the start signal — a cleaner approach is to
  // timestamp on each event and reset on >2s gap, but thinking events
  // are reliable enough).
  const [streamBuffer, setStreamBuffer] = useState<Record<string, string>>({});
  const lastChunkTs = useRef<Record<string, number>>({});
  const [stats, setStats] = useState<Stats | null>(null);
  const [planViz, setPlanViz] = useState<PlanViz | null>(null);
  const [ask, setAsk] = useState<AskState | null>(null);
  const [askAnswer, setAskAnswer] = useState('');
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const [diffFromRound, setDiffFromRound] = useState<number>(0);
  const [diffToRound, setDiffToRound] = useState<number>(0);
  const [diffPatch, setDiffPatch] = useState<string>('');
  const [diffFiles, setDiffFiles] = useState<Array<{ path: string; added: number; removed: number }>>([]);
  const [precheckHint, setPrecheckHint] = useState<{ summary: string; fixes: any[]; ts: number } | null>(null);
  // Auto-dismiss timer id; cleared on new hint so the latest one stays visible.
  const precheckTimerRef = useRef<number | null>(null);
  const activityRef = useRef<HTMLDivElement>(null);

  const coder = agents.find((a) => a.role === 'coder');
  const reviewer = agents.find((a) => a.role === 'reviewer');

  // Poll loop state for current project every 2s.
  useEffect(() => {
    if (!selectedProject) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const [loopR, planR, statsR, askR, cpR] = await Promise.all([
          api.get(`/projects/${selectedProject}/loop`),
          api.get(`/projects/${selectedProject}/plan`),
          api.get(`/projects/${selectedProject}/stats`).catch(() => null),
          api.get(`/projects/${selectedProject}/ask`).catch(() => null),
          api.get(`/projects/${selectedProject}/checkpoint`).catch(() => null),
        ]);
        if (!cancelled) {
          setLoop(loopR.data);
          setPlan(planR.data);
          if (statsR) setStats(statsR.data);
          if (askR) setAsk(askR.data);
          if (cpR) setCheckpoints(cpR.data.checkpoints || []);
          if (planR.data?.pending || planR.data?.text) {
            const vizR = await api
              .get(`/projects/${selectedProject}/plan/visualization`)
              .catch(() => null);
            if (vizR && !cancelled) setPlanViz(vizR.data);
          }
        }
      } catch (e) { /* ignore */ }
    };
    tick();
    const id = setInterval(tick, 2000);
    return () => { cancelled = true; clearInterval(id); };
  }, [selectedProject]);

  const loadDiff = useCallback(async (from: number, to: number) => {
    if (!selectedProject) return;
    try {
      const r = await api.get(`/projects/${selectedProject}/diff`, {
        params: { from_round: from, to_round: to },
      });
      setDiffPatch(r.data.patch || '');
      setDiffFromRound(from);
      setDiffToRound(to);
    } catch { /* ignore */ }
  }, [selectedProject]);

  const submitAskAnswer = useCallback(async () => {
    if (!selectedProject || !askAnswer.trim()) return;
    try {
      await api.post(`/projects/${selectedProject}/ask/answer`,
        { answer: askAnswer });
      setAskAnswer('');
      message.success('Answer sent');
    } catch (e: any) {
      message.error(e?.message || 'answer failed');
    }
  }, [selectedProject, askAnswer]);


  const revertOneFile = useCallback(async (sha: string, path: string) => {
    if (!selectedProject) return;
    Modal.confirm({
      title: `Revert ${path}?`,
      content: `This restores the file to its state at checkpoint ${sha.slice(0, 8)}. Other files are not touched.`,
      okText: 'Revert file',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await revertFile(selectedProject, sha, path);
          message.success(`Reverted ${path}`);
        } catch (e: any) {
          message.error(e?.message || 'revert failed');
        }
      },
    });
  }, [selectedProject]);
  const rollbackTo = useCallback(async (sha: string) => {
    if (!selectedProject) return;
    Modal.confirm({
      title: 'Roll back to checkpoint?',
      content: 'This overwrites uncommitted changes in the workspace.',
      okText: 'Roll back',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await api.post(`/projects/${selectedProject}/checkpoint`, { sha });
          message.success('Rolled back');
        } catch (e: any) {
          message.error(e?.message || 'rollback failed');
        }
      },
    });
  }, [selectedProject]);

  // Load projects.
  useEffect(() => {
    api.get('/projects').then((r) => setProjects(r.data.projects || []))
      .catch(() => {});
  }, []);

  // WS → update agents + messages + live stream buffer.
  useEffect(() => {
    const unsubMsg = onWebSocketMessage((data) => {
      if (data.type === 'agent_update' && data.agents) setAgents(data.agents);
      if (data.type === 'activity') {
        api.get('/messages?limit=200').then((r) => setMessages(r.data.messages || []))
          .catch(() => {});
      }
      // Live stream delta from the LLM. We append per-sender into a buffer
      // keyed by agent_id so each agent's typewriter stays independent.
      if (data.type === 'activity' && data.message?.topic === 'stream.chunk') {
        const m = data.message;
        const sender = m.sender;
        const prev = lastChunkTs.current[sender] || 0;
        lastChunkTs.current[sender] = Date.now();
        setStreamBuffer((b) => ({
          ...b,
          // If >5s since last chunk for this sender, treat it as a new
          // chunked run and clear. Otherwise append.
          [sender]: (Date.now() - prev > 5000 ? '' : (b[sender] || '')) + (m.content || ''),
        }));
      }
      // When Coder starts a new "thinking" turn, clear that agent's buffer
      // so the new turn's text replaces the old one cleanly.
      if (data.type === 'activity' && data.message?.topic === 'agent.thinking') {
        const sender = data.message.sender;
        setStreamBuffer((b) => ({ ...b, [sender]: '' }));
      }
      // Pre-check events: surface lint/test failures + auto-detected fixes as a banner.
      // loop.precheck_failed = summary (always present when something failed).
      // loop.precheck_fixable = JSON list of detected patterns (ModuleNotFoundError,
      // port-in-use, etc.) that the Coder can fix in 1 turn.
      if (data.type === 'activity') {
        const m = data.message;
        if (m?.topic === 'loop.precheck_failed') {
          setPrecheckHint({
            summary: m.content || 'Pre-check failed',
            fixes: [],
            ts: Date.now(),
          });
        } else if (m?.topic === 'loop.precheck_fixable') {
          let fixes: any[] = [];
          try { fixes = JSON.parse(m.content || '[]'); } catch { fixes = []; }
          setPrecheckHint({
            summary: fixes.length + ' auto-detected fixable error(s)',
            fixes,
            ts: Date.now(),
          });
        // Auto-dismiss after 60s; replace any prior timer so the newest one wins.
        if ((m?.topic === 'loop.precheck_failed' || m?.topic === 'loop.precheck_fixable')
            && typeof window !== 'undefined') {
          if (precheckTimerRef.current) window.clearTimeout(precheckTimerRef.current);
          precheckTimerRef.current = window.setTimeout(() => setPrecheckHint(null), 60000);
        }
      }
      }
    });
    const unsubState = onWebSocketState((s) => {
      setWsOpen(s === 'open');
      if (s === 'open') {
        api.get('/agents').then((r) => setAgents(r.data.agents || [])).catch(() => {});
        api.get('/messages?limit=200').then((r) => setMessages(r.data.messages || []))
          .catch(() => {});
      }
    });
    return () => { unsubMsg(); unsubState(); };
  }, [setAgents, setMessages]);

  // Auto-scroll activity to bottom (newest at end).
  useEffect(() => {
    if (activityRef.current) {
      activityRef.current.scrollTop = activityRef.current.scrollHeight;
    }
  }, [messages]);

  const handleStart = async () => {
    if (!selectedProject || !requirement.trim()) {
      message.warning('Pick a project and enter a requirement');
      return;
    }
    setStarting(true);
    try {
      await api.post(`/projects/${selectedProject}/start`, { requirement });
      message.success('Loop started');
    } catch (e: any) {
      message.error(e?.response?.data?.detail || 'Failed to start loop');
    } finally {
      setStarting(false);
    }
  };

  const handleStop = async () => {
    if (!selectedProject) return;
    setStopping(true);
    try {
      await api.post(`/projects/${selectedProject}/stop`);
      message.success('Stop requested');
    } catch (e: any) {
      message.error(e?.response?.data?.detail || 'Failed to stop');
    } finally {
      setStopping(false);
    }
  };

  const handleApprovePlan = async () => {
    if (!selectedProject) return;
    try {
      await api.post(`/projects/${selectedProject}/plan/approve`);
      message.success('Plan approved — loop continues');
    } catch (e: any) {
      message.error(e?.response?.data?.detail || 'Failed to approve');
    }
  };

  const handleRejectPlan = async () => {
    if (!selectedProject) return;
    try {
      await api.post(`/projects/${selectedProject}/plan/reject`);
      message.warning('Plan rejected — loop will stop');
    } catch (e: any) {
      message.error(e?.response?.data?.detail || 'Failed to reject');
    }
  };

  const lastRound = loop?.history?.[loop.history.length - 1];
  const lastIssues: Issue[] = lastRound?.review?.issues || [];
  const lastSummary = lastRound?.review?.summary || '';

  return (
    <div style={{ height: 'calc(100vh - 120px)', display: 'flex', flexDirection: 'column' }}>
      <SkillSearchPalette />
      <Space style={{ marginBottom: 12 }}>
        <Title level={3} style={{ margin: 0 }}>Loop Review</Title>
        <Tag color={loop?.running ? 'processing' : 'default'}>
          {loop?.running ? `Round ${loop.round} running...` : (loop ? `Stopped @ R${loop.round}` : 'Idle')}
        </Tag>
        {loop && (
          <Tag color={loop.last_approve ? 'green' : 'orange'}>
            {loop.last_approve
              ? 'no bugs'
              : `${lastRound?.issues ?? lastIssues.length} bug(s)`}
          </Tag>
        )}
        {loop && loop.no_progress_count > 0 && (
          <Tag color="red">no-progress {loop.no_progress_count}/{3}</Tag>
        )}
      </Space>

      {/* PLAN APPROVAL CARD — only visible while plan is pending. */}
      {plan?.pending && (
        <Card
          size="small"
          style={{ marginBottom: 12, borderColor: '#faad14' }}
          title={
            <Space>
              <FileTextOutlined style={{ color: '#faad14' }} />
              <Text strong>Coder's Plan — waiting for your approval</Text>
            </Space>
          }
          extra={
            <Space>
              <Button danger icon={<CloseCircleOutlined />} onClick={handleRejectPlan}>
                Reject
              </Button>
              <Button type="primary" icon={<CheckOutlined />} onClick={handleApprovePlan}>
                Approve
              </Button>
            </Space>
          }
          bodyStyle={{ padding: 12, maxHeight: 240, overflow: 'auto' }}
        >
          <pre style={{
            margin: 0, fontSize: 12, whiteSpace: 'pre-wrap',
            fontFamily: 'monospace', color: '#d9d9d9',
          }}>
            {plan.text || '(empty plan)'}
          </pre>
        </Card>
      )}

      <Row gutter={12} style={{ marginBottom: 12 }}>
        <Col span={6}>
          <Card title="Projects" size="small" bodyStyle={{ padding: 4, maxHeight: 200, overflow: 'auto' }}>
            <List
              size="small"
              dataSource={projects}
              renderItem={(p: any) => (
                <List.Item
                  onClick={() => setSelectedProject(p.id)}
                  style={{
                    cursor: 'pointer',
                    background: selectedProject === p.id ? '#177ddc22' : undefined,
                    padding: '4px 8px',
                  }}
                >
                  <Space direction="vertical" size={0}>
                    <Text strong style={{ fontSize: 13 }}>{p.name}</Text>
                    <Tag style={{ fontSize: 10, margin: 0 }}>{p.status}</Tag>
                  </Space>
                </List.Item>
              )}
            />
          </Card>
        </Col>
        <Col span={9}>
          <Card title="Coder" size="small">
            <Space direction="vertical" size={4} style={{ width: '100%' }}>
              <Space>
                <Badge dot color={statusColors[coder?.status || 'idle'] || '#d9d9d9'}>
                  <Avatar style={{ background: '#1f1f1f' }}>{roleAvatars.coder}</Avatar>
                </Badge>
                <Text strong>{coder?.name || 'Coder'}</Text>
                <Tag color={coder?.status === 'idle' ? 'default' : 'processing'}>
                  {coder?.status || 'idle'}
                </Tag>
                {coder?.current_turn && coder?.total_turns ? (
                  <Tag color="cyan">{coder.current_turn}/{coder.total_turns}</Tag>
                ) : null}
                {coder?.current_tool ? (
                  <Tag color="geekblue">{coder.current_tool}</Tag>
                ) : null}
              </Space>
              <Text type="secondary" style={{ fontSize: 11 }}>{coder?.model || ''}</Text>
              {/* Live stream preview. Empty when agent isn't generating. */}
              {streamBuffer[coder?.agent_id || ''] && (
                <div style={{
                  marginTop: 4, padding: 6, background: '#0a0a0a',
                  border: '1px solid #303030', borderRadius: 4,
                  fontSize: 11, fontFamily: 'monospace',
                  whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                  maxHeight: 120, overflow: 'auto',
                }}>
                  {streamBuffer[coder!.agent_id]}
                </div>
              )}
            </Space>
          </Card>
        </Col>
        <Col span={9}>
          <Card title="Reviewer" size="small">
            <Space direction="vertical" size={4} style={{ width: '100%' }}>
              <Space>
                <Badge dot color={statusColors[reviewer?.status || 'idle'] || '#d9d9d9'}>
                  <Avatar style={{ background: '#1f1f1f' }}>{roleAvatars.reviewer}</Avatar>
                </Badge>
                <Text strong>{reviewer?.name || 'Reviewer'}</Text>
                <Tag color={reviewer?.status === 'idle' ? 'default' : 'processing'}>
                  {reviewer?.status || 'idle'}
                </Tag>
                {reviewer?.current_tool ? (
                  <Tag color="geekblue">{reviewer.current_tool}</Tag>
                ) : null}
              </Space>
              <Text type="secondary" style={{ fontSize: 11 }}>{reviewer?.model || ''}</Text>
            </Space>
          </Card>
        </Col>
      </Row>

      <div style={{ flex: 1, display: 'flex', gap: 12, minHeight: 0 }}>
        {/* Center: activity stream */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 }}>
          {precheckHint && (
            <Alert
              type={precheckHint.fixes.length > 0 ? 'warning' : 'error'}
              showIcon
              closable
              onClose={() => setPrecheckHint(null)}
              message={precheckHint.summary}
              description={
                precheckHint.fixes.length > 0 ? (
                  <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
                  {precheckHint.fixes.map((f, idx) => (
                    <li key={idx}>
                      <Text code style={{ fontSize: 11 }}>{f.kind}</Text>
                      : {f.match?.slice(0, 120)}
                      {' → '}<Text type='success'>{f.fix_instruction}</Text>
                    </li>
                  ))}
                </ul>
              ) : null
            }
            style={{ flexShrink: 0 }}
          />
        )}
          <Card
            title={
              <Space>
                <span>Activity</span>
                {loop?.running ? <Tag color="processing">live</Tag> : null}
              </Space>
            }
            size="small"
            style={{ flex: 1, minHeight: 0 }}
            bodyStyle={{ padding: 0, height: 'calc(100% - 40px)', overflow: 'hidden' }}
          >
            <div ref={activityRef} style={{ height: '100%', overflow: 'auto', padding: '8px 12px' }}>
              {messages.length === 0 ? (
                <div style={{ textAlign: 'center', padding: '40px 0', color: '#666' }}>
                  <RobotOutlined style={{ fontSize: 36, marginBottom: 8 }} />
                  <div style={{ fontSize: 13 }}>No activity yet. Start a project to begin.</div>
                </div>
              ) : (
                messages.map((msg, i) => {
                  const agentName = agents.find((a) => a.agent_id === msg.sender)?.name || msg.sender;
                  return (
                    <div key={msg.id || i} style={{
                      padding: '6px 8px',
                      borderBottom: '1px solid #303030',
                      fontSize: 12,
                    }}>
                      <Space size={6} style={{ marginBottom: 2 }}>
                        <Text strong style={{ fontSize: 11 }}>{agentName}</Text>
                        <Text type="secondary" style={{ fontSize: 10 }}>
                          {new Date(msg.timestamp * 1000).toLocaleTimeString()}
                        </Text>
                        <Tag style={{ fontSize: 9, margin: 0, padding: '0 4px' }}
                          color={
                            msg.msg_type === 'error' ? 'red'
                            : msg.msg_type === 'result' ? 'green'
                            : msg.msg_type === 'warning' ? 'orange'
                            : 'blue'
                          }>
                          {msg.topic}
                        </Tag>
                      </Space>
                      <div style={{ color: '#d9d9d9', wordBreak: 'break-word', whiteSpace: 'pre-wrap' }}>
                        {typeof msg.content === 'string'
                          ? msg.content.slice(0, 600)
                          : JSON.stringify(msg.content).slice(0, 600)}
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </Card>

          {/* Start / Stop controls */}
          <Card size="small" bodyStyle={{ padding: 8 }}>
            <Space.Compact style={{ width: '100%' }}>
              <TextArea
                value={requirement}
                onChange={(e) => setRequirement(e.target.value)}
                placeholder="Describe what to build..."
                autoSize={{ minRows: 1, maxRows: 3 }}
                disabled={loop?.running}
                onPressEnter={(e) => { if (!e.shiftKey && !loop?.running) { e.preventDefault(); handleStart(); } }}
              />
              {loop?.running ? (
                <Button danger icon={<StopOutlined />} onClick={handleStop} loading={stopping}>
                  Stop
                </Button>
              ) : (
                <Button type="primary" icon={<PlayCircleOutlined />} onClick={handleStart} loading={starting}>
                  Start Loop
                </Button>
              )}
            </Space.Compact>
          </Card>
        </div>

        {/* Middle: stats, plan viz, diff, checkpoints */}
        <div style={{ width: 380, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>

          {/* Bug chart + cost */}
          {stats && (
            <Card title={<><BarChartOutlined /> Stats</>} size="small"
                  bodyStyle={{ padding: 8 }}>
              <Space direction="vertical" size={4} style={{ width: '100%' }}>
                <div style={{ fontSize: 11, color: '#aaa' }}>
                  Tokens: {stats.total_tokens_used.toLocaleString()} ·
                  ~${stats.approximate_cost_usd.toFixed(4)}
                </div>
                <div style={{ fontSize: 11, color: '#aaa' }}>
                  Infra streak: {stats.infra_failure_streak} ·
                  No-progress: {stats.no_progress_count}
                </div>
                {stats.rounds.length > 0 && (
                  <div style={{
                    height: 60, display: 'flex', alignItems: 'flex-end',
                    gap: 2, marginTop: 4,
                  }}>
                    {stats.rounds.map((r) => (
                      <Tooltip key={r.round}
                               title={'R' + r.round + ': ' + r.issues + ' bug(s)' + (r.approve ? ' · passed' : '')}>
                        <div style={{
                          flex: 1,
                          height: Math.max(4, r.score) + '%',
                          background: r.approve ? '#52c41a'
                            : r.score >= 60 ? '#faad14' : '#ff4d4f',
                          borderRadius: 2,
                        }} />
                      </Tooltip>
                    ))}
                  </div>
                )}
              </Space>
            </Card>
          )}

          {/* Plan visualization */}
          {planViz && planViz.mermaid && (
            <Card title={<><BranchesOutlined /> Plan</>} size="small"
                  bodyStyle={{ padding: 8 }}>
              <Tabs size="small" items={[
                {
                  key: 'graph',
                  label: 'Diagram',
                  children: (
                    <MermaidRenderer source={planViz.mermaid} />
                  ),
                },
                {
                  key: 'tree',
                  label: 'Files',
                  children: (
                    <>
                    {diffFiles.length > 0 && (
                <div style={{ maxHeight: 140, overflow: 'auto', marginBottom: 6 }}>
                  {diffFiles.map((f) => (
                    <div key={f.path} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, padding: '2px 0' }}>
                      <span style={{ flex: 1, color: '#d9d9d9', fontFamily: 'monospace' }}>{f.path}</span>
                      <span style={{ color: '#52c41a' }}>+{f.added}</span>
                      <span style={{ color: '#ff4d4f' }}>-{f.removed}</span>
                      {checkpoints.length > 0 && (
                        <Tooltip title={`Revert to R${checkpoints[0].round}`}>
                          <Button size='small' type='text' danger icon={<UndoOutlined />}
                            onClick={() => revertOneFile(checkpoints[0].sha, f.path)} />
                        </Tooltip>
                      )}
                    </div>
                  ))}
                </div>
              )}
              <pre style={{
                      fontSize: 11, background: '#1a1a1a', padding: 8,
                      borderRadius: 4, maxHeight: 200, overflow: 'auto',
                      color: '#d9d9d9', whiteSpace: 'pre-wrap',
                    }}>{planViz.file_tree}</pre>
                    </>
                  ),
                },
              ]} />
            </Card>
          )}

          {/* Round 13: live TodoWrite-style plan checklist. */}
          <div style={{ marginBottom: 12 }}>
            <PlanPanel messages={messages} />
          </div>

          {/* Round 14: plan history timeline — the diff between
              each round's plan snapshot. Sourced from session.history
              (R12.2 added the per-round plan field). */}
          {loop?.history && Array.isArray(loop.history) && loop.history.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <PlanHistoryPanel history={loop.history} />
            </div>
          )}

          {/* Round 16: cost dashboard. Reads from /api/cost/summary
              + /api/cost/recent (litellm cost_callback logs in
              kairos/cost.py). Polls every 30s. */}
          <div style={{ marginBottom: 12 }}>
            <CostDashboard />
          </div>

          {/* Round 19: eval panel — list datasets, record/replay/derive
              from the web without touching the CLI. */}
          <div style={{ marginBottom: 12 }}>
            <EvalPanel />
          </div>

          {/* Round 25: trend panel — multi-run pass_rate / cost
              over time, plus a per-case flaky list. */}
          <div style={{ marginBottom: 12 }}>
            <TrendPanel />
          </div>

          {/* Diff viewer */}
          {checkpoints.length >= 2 && (
            <Card title="Diff" size="small" bodyStyle={{ padding: 8 }}>
              <Space size={4} style={{ marginBottom: 6 }}>
                <Select size="small" style={{ width: 80 }}
                        value={diffFromRound}
                        onChange={(v) => loadDiff(v, diffToRound || checkpoints[0].round)}
                        options={checkpoints.map((c) => ({
                          value: c.round, label: 'R' + c.round,
                        }))} />
                <span style={{ color: '#aaa' }}>{'->'}</span>
                <Select size="small" style={{ width: 80 }}
                        value={diffToRound}
                        onChange={(v) => loadDiff(diffFromRound || checkpoints[checkpoints.length - 2].round, v)}
                        options={checkpoints.map((c) => ({
                          value: c.round, label: 'R' + c.round,
                        }))} />
              </Space>
              {diffFiles.length > 0 && (
                <div style={{ maxHeight: 140, overflow: 'auto', marginBottom: 6 }}>
                  {diffFiles.map((f) => (
                    <div key={f.path} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, padding: '2px 0' }}>
                      <span style={{ flex: 1, color: '#d9d9d9', fontFamily: 'monospace' }}>{f.path}</span>
                      <span style={{ color: '#52c41a' }}>+{f.added}</span>
                      <span style={{ color: '#ff4d4f' }}>-{f.removed}</span>
                      {checkpoints.length > 0 && (
                        <Tooltip title={`Revert to R${checkpoints[0].round}`}>
                          <Button size='small' type='text' danger icon={<UndoOutlined />}
                            onClick={() => revertOneFile(checkpoints[0].sha, f.path)} />
                        </Tooltip>
                      )}
                    </div>
                  ))}
                </div>
              )}
              <pre style={{
                fontSize: 10, background: '#1a1a1a', padding: 6,
                borderRadius: 4, maxHeight: 220, overflow: 'auto',
                color: '#d9d9d9', whiteSpace: 'pre-wrap',
              }}>{diffPatch || '(no diff loaded)'}</pre>
            </Card>
          )}

          {/* Checkpoint rollback */}
          {checkpoints.length > 0 && (
            <Card title="Checkpoints" size="small"
                  bodyStyle={{ padding: 8, maxHeight: 180, overflow: 'auto' }}>
              <List size="small" dataSource={checkpoints}
                    renderItem={(cp) => (
                <List.Item style={{ padding: '4px 0' }}
                  actions={[
                    <Tooltip key="rb" title="Roll back workspace to this round">
                      <Button size="small" danger icon={<UndoOutlined />}
                              onClick={() => rollbackTo(cp.sha)} />
                    </Tooltip>,
                  ]}>
                  <List.Item.Meta
                    avatar={<Tag color={cp.approved ? 'green' : 'orange'}>{cp.round}</Tag>}
                    title={<span style={{ fontSize: 12 }}>
                      R{cp.round} {cp.approved ? 'OK' : 'X'} ({cp.score})
                    </span>}
                    description={<span style={{ fontSize: 10, color: '#888' }}>
                      {cp.summary.slice(0, 60)}
                    </span>}
                  />
                </List.Item>
              )} />
            </Card>
          )}
        </div>

        {/* Ask-human modal */}
        <Modal
          title={<><QuestionCircleOutlined /> Reviewer asks</>}
          open={!!(ask && ask.pending)}
          onCancel={() => setAsk(null)}
          onOk={submitAskAnswer}
          okText="Send answer"
          cancelText="Ignore"
        >
          <Space direction="vertical" style={{ width: '100%' }}>
            <Text strong>{ask ? ask.question : ''}</Text>
            {ask && ask.context && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {ask.context}
              </Text>
            )}
            <TextArea
              value={askAnswer}
              onChange={(e) => setAskAnswer(e.target.value)}
              placeholder="Your answer..."
              autoSize={{ minRows: 2, maxRows: 6 }}
            />
          </Space>
        </Modal>

        {/* Right: latest review */}
        <div style={{ width: 360, flexShrink: 0 }}>
          <Card title="Latest Review" size="small" style={{ height: '100%', overflow: 'auto' }}
            bodyStyle={{ padding: 8 }}>
            {lastIssues.length === 0 && !lastSummary ? (
              <Text type="secondary">No review yet.</Text>
            ) : (
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                {lastSummary && (
                  <div style={{ fontSize: 12, color: '#aaa', padding: 4, background: '#1a1a1a', borderRadius: 4 }}>
                    {lastSummary}
                  </div>
                )}
                {lastIssues.map((it, idx) => (
                  <div key={idx} style={{
                    padding: 8, background: '#1a1a1a', borderRadius: 4,
                    borderLeft: `3px solid ${
                      it.severity === 'CRITICAL' ? '#ff4d4f'
                      : it.severity === 'MAJOR' ? '#faad14'
                      : it.severity === 'MINOR' ? '#1677ff' : '#52c41a'
                    }`,
                  }}>
                    <Space size={4} style={{ marginBottom: 4 }}>
                      <Tag color={severityColor[it.severity]} style={{ margin: 0 }}>{it.severity}</Tag>
                      <Text type="secondary" style={{ fontSize: 11 }}>{it.category}</Text>
                    </Space>
                    <div style={{ fontSize: 12, marginBottom: 4 }}>
                      <Text code style={{ fontSize: 11 }}>{it.file}{it.line ? `:${it.line}` : ''}</Text>
                    </div>
                    <div style={{ fontSize: 12, marginBottom: 4 }}>{it.description}</div>
                    <div style={{ fontSize: 11, color: '#52c41a' }}>
                      <CodeOutlined /> {it.fix_instruction}
                    </div>
                  </div>
                ))}
              </Space>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
};

export default Loop;

