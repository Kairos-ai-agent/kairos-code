/**
 * Trace page — per-turn debug view of an agent's reasoning.
 *
 * Mirrors the `kairos.tracing` JSONL record format. For one
 * (project_id, session_id), the backend reads the on-disk JSONL
 * file and returns the events as a list. We then render them as:
 *
 *   - A horizontal stepper at the top: each turn is a circle,
 *     clickable. The currently-selected turn filters the events.
 *   - A list of events for the current turn, grouped by phase:
 *     prompt → completion → tool call → tool result → guardrail
 *   - A summary footer: total tokens + tool counts + error count.
 *
 * The page is read-only — it doesn't restart or modify the trace.
 */
import React, { useEffect, useState, useMemo } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import {
  Card, Spin, Empty, Tag, Select, Space, Tooltip, Statistic, Row, Col,
  Segmented, Button,
} from 'antd';
import {
  ArrowLeftOutlined, MessageOutlined, CodeOutlined, ToolOutlined,
  CheckCircleOutlined, CloseCircleOutlined, BulbOutlined,
  WarningOutlined,
} from '@ant-design/icons';

import { useChatStore } from '../stores/chatStore';
import { useThemeTokens } from '../hooks/useThemeTokens';
import api from '../api/client';
import { useT, tGlobal } from '../i18n';

type EventKind = 'session_start' | 'session_end' | 'prompt'
  | 'completion' | 'tool_call' | 'tool_result'
  | 'summary' | 'guardrail' | 'error';

interface TraceEvent {
  kind: EventKind;
  turn: number;
  timestamp: number;
  payload: Record<string, any>;
  seq: number;
  agent_id: string;
  session_id: string;
}

interface TraceData {
  project_id: string;
  session_id: string;
  agent_id: string;
  started_at: number;
  finished_at: number;
  turn_count: number;
  event_count: number;
  tokens: { prompt_tokens: number; completion_tokens: number;
            total_tokens: number };
  events: TraceEvent[];
}

const KIND_META: Record<EventKind, { color: string; labelKey: string;
                                       icon: React.ReactNode }> = {
  session_start: { color: 'blue', labelKey: 'trace.kind.sessionStart',
                   icon: <MessageOutlined /> },
  session_end:   { color: 'blue', labelKey: 'trace.kind.sessionEnd',
                   icon: <MessageOutlined /> },
  prompt:        { color: 'cyan', labelKey: 'trace.kind.prompt', icon: <MessageOutlined /> },
  completion:    { color: 'purple', labelKey: 'trace.kind.completion',
                   icon: <MessageOutlined /> },
  tool_call:     { color: 'orange', labelKey: 'trace.kind.toolCall',
                   icon: <ToolOutlined /> },
  tool_result:   { color: 'default', labelKey: 'trace.kind.toolResult',
                   icon: <ToolOutlined /> },
  summary:       { color: 'gold', labelKey: 'trace.kind.summary',
                   icon: <BulbOutlined /> },
  guardrail:     { color: 'magenta', labelKey: 'trace.kind.guardrail',
                   icon: <WarningOutlined /> },
  error:         { color: 'red', labelKey: 'trace.kind.error',
                   icon: <CloseCircleOutlined /> },
};

const Trace: React.FC = () => {
  const tokens = useThemeTokens();
  const t = useT();
  const navigate = useNavigate();
  const { projectId, sessionId } = useParams<{ projectId?: string;
                                                sessionId?: string }>();
  const currentProject = useChatStore((s) => s.currentProject);
  const setCurrentProject = useChatStore((s) => s.setCurrentProject);
  const [sessions, setSessions] = useState<{ session_id: string }[]>([]);
  const [trace, setTrace] = useState<TraceData | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedTurn, setSelectedTurn] = useState<number>(0);
  const [view, setView] = useState<'events' | 'tools' | 'tokens'>('events');

  // Pull projects + sessions once.
  useEffect(() => {
    if (!projectId) return;
    api.get<{ sessions: { session_id: string }[] }>(`/projects/${projectId}/sessions`)
      .then((r) => setSessions(r.data.sessions || []))
      .catch(() => setSessions([]));
  }, [projectId]);

  useEffect(() => {
    if (!projectId || !sessionId) {
      setTrace(null);
      return;
    }
    setLoading(true);
    api.get<TraceData>(`/projects/${projectId}/traces/${sessionId}`)
      .then((r) => {
        setTrace(r.data);
        // Default the selected turn to the last one.
        const turns = Array.from(new Set((r.data.events || [])
          .map((e) => e.turn).filter((t) => t > 0))).sort((a, b) => a - b);
        if (turns.length > 0) setSelectedTurn(turns[turns.length - 1]);
      })
      .catch(() => setTrace(null))
      .finally(() => setLoading(false));
  }, [projectId, sessionId]);

  const turns = useMemo(() => {
    if (!trace) return [] as number[];
    return Array.from(new Set(trace.events
      .map((e) => e.turn).filter((t) => t > 0))).sort((a, b) => a - b);
  }, [trace]);

  const events = useMemo(() => {
    if (!trace) return [] as TraceEvent[];
    if (selectedTurn === 0) return trace.events;
    return trace.events.filter((e) => e.turn === selectedTurn);
  }, [trace, selectedTurn]);

  const toolStats = useMemo(() => {
    if (!trace) return [] as { name: string; calls: number;
                                success: number; failed: number;
                                duration_ms: number }[];
    const byTool: Record<string, { calls: number; success: number;
                                   failed: number; duration_ms: number }> = {};
    for (const e of trace.events) {
      if (e.kind !== 'tool_result') continue;
      const name = e.payload?.name || 'unknown';
      const row = byTool[name] || { calls: 0, success: 0,
                                    failed: 0, duration_ms: 0 };
      row.calls += 1;
      if (e.payload?.success) row.success += 1; else row.failed += 1;
      row.duration_ms += Number(e.payload?.duration_ms || 0);
      byTool[name] = row;
    }
    return Object.entries(byTool)
      .map(([name, s]) => ({ name, ...s }))
      .sort((a, b) => b.calls - a.calls);
  }, [trace]);

  // Resolve project: either URL param, store, or first available.
  useEffect(() => {
    if (!projectId) {
      if (currentProject) navigate(`/trace/${currentProject.id}`, { replace: true });
    } else {
      // Sync the URL projectId with the chat store.
      if (!currentProject || currentProject.id !== projectId) {
        setCurrentProject({ id: projectId, name: projectId,
                            description: '', workspace: '', status: 'active',
                            task_count: 0, agent_count: 0, created_at: 0 });
      }
    }
  }, [projectId, currentProject, navigate, setCurrentProject]);

  return (
    <div style={{ padding: '20px 24px', maxWidth: 1100, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12,
                    marginBottom: 16 }}>
        <Button type="text" icon={<ArrowLeftOutlined />}
                onClick={() => navigate('/chat')}>
          {t('common.back')}
        </Button>
        <div>
          <div style={{ fontSize: 18, fontWeight: 600, color: tokens.labelPrimary }}>
            {t('trace.header.title')} {(sessionId || '').slice(0, 12)}…
          </div>
          {trace && (
            <div style={{ fontSize: 12, color: tokens.labelTertiary }}>
              {t('trace.header.stats', {
                events: trace.event_count, turns: trace.turn_count,
                tokens: trace.tokens.total_tokens,
              })}
            </div>
          )}
        </div>
        <div style={{ flex: 1 }} />
        <Space>
          <Select
            value={sessionId}
            onChange={(v) => navigate(`/trace/${projectId}/${v}`)}
            placeholder={t('trace.selectSession')}
            style={{ minWidth: 220 }}
            options={sessions.map((s) => ({
              value: s.session_id,
              label: t('trace.sessionOption', { id: s.session_id.slice(0, 8) }),
            }))}
          />
        </Space>
      </div>

      {loading && (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}>
          <Spin />
        </div>
      )}

      {!loading && !trace && (
        <Empty
          image={<ToolOutlined style={{ fontSize: 40, color: tokens.labelTertiary }} />}
          description={
            <span style={{ color: tokens.labelTertiary }}>
              {t('trace.empty')}
            </span>
          }
        />
      )}

      {trace && (
        <>
          {/* Token / tool / error summary */}
          <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
            <Col xs={12} sm={6}>
              <Card size="small" style={{ background: tokens.bgLay1,
                                          border: `1px solid ${tokens.border}` }}>
                <Statistic title={t('trace.stat.events')} value={trace.event_count} />
              </Card>
            </Col>
            <Col xs={12} sm={6}>
              <Card size="small" style={{ background: tokens.bgLay1,
                                          border: `1px solid ${tokens.border}` }}>
                <Statistic title={t('trace.stat.totalTokens')} value={trace.tokens.total_tokens} />
              </Card>
            </Col>
            <Col xs={12} sm={6}>
              <Card size="small" style={{ background: tokens.bgLay1,
                                          border: `1px solid ${tokens.border}` }}>
                <Statistic title={t('trace.stat.toolCalls')}
                           value={trace.events.filter((e) => e.kind === 'tool_call').length} />
              </Card>
            </Col>
            <Col xs={12} sm={6}>
              <Card size="small" style={{ background: tokens.bgLay1,
                                          border: `1px solid ${tokens.border}` }}>
                <Statistic title={t('trace.stat.errors')}
                           value={trace.events.filter((e) => e.kind === 'error').length}
                           valueStyle={{ color: trace.events.some((e) => e.kind === 'error')
                                                  ? tokens.danger : undefined }} />
              </Card>
            </Col>
          </Row>

          {/* Turn stepper */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 6,
            overflowX: 'auto', padding: '8px 0 16px',
            borderBottom: `1px solid ${tokens.border}`,
            marginBottom: 12,
          }}>
            <Button size="small" type={selectedTurn === 0 ? 'primary' : 'default'}
                    onClick={() => setSelectedTurn(0)}>
              {t('trace.filter.all')}
            </Button>
            {turns.map((turn) => (
              <Button key={turn} size="small"
                      type={selectedTurn === turn ? 'primary' : 'default'}
                      onClick={() => setSelectedTurn(turn)}>
                {t('trace.filter.turn')} {turn}
              </Button>
            ))}
          </div>

          {/* View toggle */}
          <div style={{ display: 'flex', justifyContent: 'flex-end',
                        marginBottom: 8 }}>
            <Segmented
              size="small"
              value={view}
              onChange={(v) => setView(v as any)}
              options={[
                { label: t('trace.col.events'), value: 'events' },
                { label: t('trace.col.tools'), value: 'tools' },
                { label: t('trace.col.tokens'), value: 'tokens' },
              ]}
            />
          </div>

          {view === 'events' && (
            <div>
              {events.length === 0 ? (
                <Empty description={t('trace.empty.events')} />
              ) : events.map((e, i) => (
                <EventCard key={i} event={e} />
              ))}
            </div>
          )}

          {view === 'tools' && (
            <Card size="small" style={{ background: tokens.bgLay1,
                                        border: `1px solid ${tokens.border}` }}
                  styles={{ body: { padding: 0 } }}>
              {toolStats.length === 0 ? (
                <Empty description={t('trace.empty.tools')} />
              ) : toolStats.map((tool) => (
                <div key={tool.name} style={{
                  display: 'flex', alignItems: 'center', gap: 12,
                  padding: '10px 16px',
                  borderBottom: `1px solid ${tokens.border}`,
                }}>
                  <Tag color="orange">{tool.name}</Tag>
                  <span style={{ color: tokens.labelSecondary, fontSize: 13 }}>
                    {t('trace.tools.calls', { n: tool.calls })}
                  </span>
                  <span style={{ color: tokens.success, fontSize: 13 }}>
                    {t('trace.tools.ok', { n: tool.success })}
                  </span>
                  {tool.failed > 0 && (
                    <span style={{ color: tokens.danger, fontSize: 13 }}>
                      {t('trace.tools.failed', { n: tool.failed })}
                    </span>
                  )}
                  <span style={{ color: tokens.labelTertiary, fontSize: 13 }}>
                    {tool.duration_ms}{t('trace.msUnit')}
                  </span>
                </div>
              ))}
            </Card>
          )}

          {view === 'tokens' && (
            <Card size="small" style={{ background: tokens.bgLay1,
                                        border: `1px solid ${tokens.border}` }}>
              <Row gutter={16}>
                <Col span={8}>
                  <Statistic title={t('trace.token.prompt')}
                             value={trace.tokens.prompt_tokens} />
                </Col>
                <Col span={8}>
                  <Statistic title={t('trace.token.completion')}
                             value={trace.tokens.completion_tokens} />
                </Col>
                <Col span={8}>
                  <Statistic title={t('trace.token.total')}
                             value={trace.tokens.total_tokens} />
                </Col>
              </Row>
            </Card>
          )}
        </>
      )}
    </div>
  );
};

const EventCard: React.FC<{ event: TraceEvent }> = ({ event }) => {
  const tokens = useThemeTokens();
  const t = useT();
  const meta = KIND_META[event.kind] || KIND_META.prompt;
  const [open, setOpen] = useState(false);
  const summary = renderSummary(event);
  return (
    <div style={{
      marginBottom: 8, background: tokens.bgLay1,
      border: `1px solid ${tokens.border}`,
      borderRadius: 8, overflow: 'hidden',
    }}>
      <div
        onClick={() => setOpen((v) => !v)}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '8px 12px', cursor: 'pointer',
          background: open ? tokens.bgLay2 : 'transparent',
        }}
      >
        <Tag color={meta.color} style={{ margin: 0 }}>
          {meta.icon} {t(meta.labelKey)}
        </Tag>
        {event.turn > 0 && (
          <span style={{ fontSize: 11, color: tokens.labelTertiary }}>
            {t('trace.turnUnit')} {event.turn}
          </span>
        )}
        <span style={{
          flex: 1, fontSize: 13, color: tokens.labelPrimary,
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        }}>
          {summary}
        </span>
        <span style={{ fontSize: 11, color: tokens.labelTertiary }}>
          {fmtTime(event.timestamp)}
        </span>
      </div>
      {open && (
        <pre style={{
          margin: 0, padding: 12,
          fontSize: 12, lineHeight: 1.5,
          color: tokens.labelSecondary,
          background: tokens.bgBase,
          fontFamily: 'SF Mono, "JetBrains Mono", Consolas, monospace',
          whiteSpace: 'pre-wrap', wordBreak: 'break-word',
          borderTop: `1px solid ${tokens.border}`,
          maxHeight: 320, overflow: 'auto',
        }}>
          {JSON.stringify(event.payload, null, 2)}
        </pre>
      )}
    </div>
  );
};

function renderSummary(e: TraceEvent): string {
  const p = e.payload || {};
  switch (e.kind) {
    case 'prompt': {
      const msgs = Array.isArray(p.messages) ? p.messages : [];
      const last = msgs[msgs.length - 1];
      const t = typeof last?.content === 'string' ? last.content
              : JSON.stringify(last?.content || '');
      return `→ ${t.slice(0, 200)}`;
    }
    case 'completion': {
      const c = (p.content || '').toString();
      const tools = Array.isArray(p.tool_calls) ? p.tool_calls : [];
      return `${c.slice(0, 160)}${tools.length ? ` [${tGlobal('trace.summary.toolCalls', { n: tools.length })}]` : ''}`;
    }
    case 'tool_call':
      return `${p.name || tGlobal('trace.summary.toolName')}(${JSON.stringify(p.args || {}).slice(0, 120)})`;
    case 'tool_result':
      return `${p.name || tGlobal('trace.summary.toolName')} → ${(p.output || '').toString().slice(0, 160)}`;
    case 'summary':
      return (p.summary || '').toString().slice(0, 200);
    case 'guardrail':
      return `${p.verdict || ''}${p.reason ? ' — ' + p.reason : ''}`;
    case 'error':
      return `${p.exc_type || 'Error'}: ${(p.error || '').toString().slice(0, 200)}`;
    case 'session_start':
      return `agent ${e.agent_id}`;
    case 'session_end':
      return 'session closed';
    default:
      return '';
  }
}

function fmtTime(ts: number): string {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export default Trace;
