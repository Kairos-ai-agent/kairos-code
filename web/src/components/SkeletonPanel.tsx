/**
 * SkeletonPanel — the general-skeleton run's status and per-criterion Verdict.
 *
 * The non-code path of ``POST /api/projects/{id}/start`` (``kind`` /
 * ``workspace_kind`` set) runs on the domain-neutral skeleton in the
 * background (``kairos/skeleton/runner.py``) and reports through one read-only
 * endpoint, ``GET /api/projects/{id}/skeleton`` — the skeleton's twin of
 * ``GET /loop``. This panel is the UI half of that: it polls the state, shows
 * the per-criterion ``evidence`` the verifier produced (criterion / satisfied /
 * reported / actual), and lets the user stop a live run.
 *
 * Two rules the loop already paid for, kept here:
 *
 * 1. A terminal status must take "运行中" off the screen for good. The runner
 *    stamps a terminal status in its task's done-callback, so a read agrees
 *    with it — but a poll that left the server *before* the run ended can land
 *    *after* it and flip ``running`` back on (the loop's ``loop.ended`` bug,
 *    see ``mergeLoopState`` in pages/Chat.tsx). ``mergeSkeletonState`` therefore
 *    treats a run id it has already seen terminal as closed and never
 *    resurrects it.
 * 2. No viewport height is computed here: the shell owns the viewport
 *    (``AppLayout``); this is an ordinary card that fills its slot.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Card, Tag, Button, Space, Typography, Spin, message as antMessage,
} from 'antd';
import {
  CheckCircleFilled, CloseCircleFilled, QuestionCircleFilled,
  StopOutlined, ThunderboltOutlined,
} from '@ant-design/icons';

import api from '../api/client';
import { formatError } from '../utils/formatError';
import { useThemeTokens } from '../hooks/useThemeTokens';
import { useT } from '../i18n';

const { Text } = Typography;

/** Poll cadence — the same 2 s the Loop page uses for GET /loop. */
const POLL_MS = 2000;

/** Runner lifecycle words that mean "this run is over". */
const TERMINAL_STATUS = new Set(['done', 'failed', 'stopped']);

export interface SkeletonEvidence {
  criterion?: string;
  satisfied?: boolean | null;
  reported?: unknown;
  actual?: unknown;
  [key: string]: unknown;
}

export interface SkeletonVerdict {
  passed?: boolean | null;
  reason?: string;
  verifier?: string;
  requires_human?: boolean;
  score?: number | null;
  evidence?: SkeletonEvidence[];
}

export interface SkeletonState {
  run_id?: string;
  session_id?: string;
  /** running | done | failed | stopped | none */
  status?: string;
  running?: boolean;
  workspace_kind?: string;
  outcome?: string;
  passed?: boolean | null;
  verdict?: SkeletonVerdict | null;
  error?: string;
  artifacts?: string[];
  run_file?: string;
  stop_requested?: boolean;
}

export function isTerminalStatus(status?: string): boolean {
  return !!status && TERMINAL_STATUS.has(status);
}

/**
 * Fold one GET /skeleton response into the panel's state.
 *
 * ``running`` is derived from the server's own lifecycle word, and a run id
 * already seen terminal (or already stopped from the UI) is closed: no later —
 * possibly stale, in-flight — response may set it back to true. The skeleton
 * twin of ``mergeLoopState``; exported so the terminal-clearing rule is
 * testable on its own.
 */
export function mergeSkeletonState(
  prev: SkeletonState | null,
  data: SkeletonState | null | undefined,
  closedRuns: Set<string>,
): SkeletonState | null {
  if (!data) return prev;
  // A "none" answer (the server holds no record) is not news: it must not erase
  // a run the panel has already shown. That is the 未运行-after-a-finished-run
  // bug -- the server used to answer "none" for a run whose live state had been
  // dropped, and the panel blanked. It only seeds the panel when nothing is on
  // screen yet (prev === null).
  if (!data.run_id && data.status === 'none') return prev;
  const runId = data.run_id || prev?.run_id || '';
  const closed = isTerminalStatus(data.status)
    || (!!runId && closedRuns.has(runId));
  const running = !closed
    && (data.status === 'running' || data.running === true);
  return { ...(prev || {}), ...data, run_id: runId, running };
}

const STATUS_COLOR: Record<string, string> = {
  running: 'processing',
  done: 'green',
  failed: 'red',
  stopped: 'default',
  none: 'default',
};

const STATUS_KEY: Record<string, string> = {
  running: 'skeleton.status.running',
  done: 'skeleton.status.done',
  failed: 'skeleton.status.failed',
  stopped: 'skeleton.status.stopped',
  none: 'skeleton.status.none',
};

const SAT_KEY = (sat: boolean | null | undefined): string =>
  sat === true ? 'skeleton.evidence.satisfied'
    : sat === false ? 'skeleton.evidence.unsatisfied'
      : 'skeleton.evidence.unknown';

/** Render a reported/actual value that may be a string, number, or a list. */
function renderValue(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'string') return v;
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

const SkeletonPanel: React.FC<{ projectId: string | null }> = ({ projectId }) => {
  const t = useT();
  const tokens = useThemeTokens();
  const [state, setState] = useState<SkeletonState | null>(null);
  const [loading, setLoading] = useState(false);
  const [stopping, setStopping] = useState(false);
  // Holds the server's own `detail` text; the localised fallback
  // (`t('skeleton.loadFailed')`) is applied at render, so this callback needs
  // no dependency on `t` — which matters because outside an I18nProvider
  // `useT()` returns a fresh function every render and a `refresh` that
  // changed identity each time would re-run the polling effect on every
  // render.
  const [err, setErr] = useState<string | null>(null);
  // Run ids whose terminal status we have already observed (or applied
  // optimistically after Stop). A ref so the poll closure always reads the
  // live set — the same endedSessionsRef discipline as Chat.tsx.
  const closedRunsRef = useRef<Set<string>>(new Set());

  const refresh = useCallback(async () => {
    if (!projectId) { setState(null); setLoading(false); return; }
    try {
      const r = await api.get<SkeletonState>(`/projects/${projectId}/skeleton`);
      const data = r.data || null;
      if (data?.run_id && isTerminalStatus(data.status)) {
        closedRunsRef.current.add(data.run_id);
      }
      setState((prev) => mergeSkeletonState(prev, data, closedRunsRef.current));
      setErr(null);
    } catch (e) {
      setErr(formatError(e, '').trim());
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    closedRunsRef.current = new Set();
    setState(null);
    setErr(null);
    if (!projectId) return;
    setLoading(true);
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [projectId, refresh]);

  // Deliberately not a useCallback: it is only ever an onClick handler, so it
  // can close over the current `t` and does not belong in any effect deps.
  const stop = async () => {
    if (!projectId) return;
    setStopping(true);
    try {
      await api.post(`/projects/${projectId}/skeleton/stop`);
      // Apply the terminal state now, not 2 s from now: the Stop button's
      // whole point is that 运行中 comes off the moment it is clicked. The
      // next poll agrees — the runner stamps `stopped` in its done-callback.
      const runId = state?.run_id;
      if (runId) closedRunsRef.current.add(runId);
      setState((prev) => (prev ? { ...prev, status: 'stopped', running: false } : prev));
      antMessage.success(t('skeleton.stop.success'));
    } catch (e: any) {
      antMessage.error(formatError(e, t('skeleton.stop.failed')));
    } finally {
      setStopping(false);
    }
  };

  const status = state?.status || 'none';
  const running = !!state?.running;
  const verdict = state?.verdict || null;
  const evidence = verdict?.evidence || [];
  const passed = state?.passed ?? verdict?.passed ?? null;

  const evidenceIcon = (sat: boolean | null | undefined) =>
    (sat === true ? <CheckCircleFilled style={{ color: tokens.success }} />
      : sat === false ? <CloseCircleFilled style={{ color: tokens.danger }} />
        : <QuestionCircleFilled style={{ color: tokens.labelTertiary }} />);

  const outcomeTag = () => {
    if (passed === true) return <Tag color="green">{t('skeleton.outcome.passed')}</Tag>;
    if (passed === false) return <Tag color="orange">{t('skeleton.outcome.failed')}</Tag>;
    if (verdict) return <Tag>{t('skeleton.outcome.undecided')}</Tag>;
    return null;
  };

  return (
    <Card
      size="small"
      data-testid="skeleton-panel"
      title={
        <Space>
          <ThunderboltOutlined />
          <Text strong>{t('skeleton.title')}</Text>
        </Space>
      }
      extra={
        <Space>
          <Tag color={STATUS_COLOR[status] || 'default'} data-testid="skeleton-status">
            {t(STATUS_KEY[status] || 'skeleton.status.none')}
          </Tag>
          {running && (
            <Button
              size="small"
              danger
              icon={<StopOutlined />}
              loading={stopping}
              onClick={stop}
              data-testid="skeleton-stop"
            >
              {t('common.stop')}
            </Button>
          )}
        </Space>
      }
      style={{
        marginBottom: 12,
        background: tokens.bgLay1,
        border: `1px solid ${tokens.border}`,
      }}
      styles={{ body: { padding: 8 } }}
    >
      <Spin spinning={loading}>
        {err !== null && (
          <div style={{ color: tokens.danger, fontSize: 12, marginBottom: 6 }}>
            {err || t('skeleton.loadFailed')}
          </div>
        )}

        {!state || status === 'none' ? (
          <Text type="secondary" style={{ fontSize: 12 }}>{t('skeleton.empty')}</Text>
        ) : (
          <Space direction="vertical" size={6} style={{ width: '100%' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              {state.workspace_kind && <Tag color="purple">{state.workspace_kind}</Tag>}
              {running && (
                <Tag color="processing" icon={<ThunderboltOutlined />} data-testid="skeleton-running">
                  {t('common.running')}
                </Tag>
              )}
              {outcomeTag()}
              {state.run_id && (
                <Text type="secondary" style={{ fontSize: 11 }}>
                  {t('skeleton.runId')}:{' '}
                  <Text code style={{ fontSize: 11 }}>{state.run_id}</Text>
                </Text>
              )}
            </div>

            {state.error && (
              <div style={{ fontSize: 12, color: tokens.danger }}>
                {t('skeleton.error')}: {state.error}
              </div>
            )}
            {verdict?.reason && (
              <div style={{ fontSize: 12, color: tokens.labelSecondary }}>
                {t('skeleton.reason')}: {verdict.reason}
              </div>
            )}
            {verdict?.verifier && (
              <div style={{ fontSize: 11, color: tokens.labelTertiary }}>
                {t('skeleton.verifier')}: {verdict.verifier}
              </div>
            )}

            <div style={{ fontSize: 12, color: tokens.labelPrimary, marginTop: 2 }}>
              {t('skeleton.evidence.title')}
            </div>
            {evidence.length === 0 ? (
              <Text type="secondary" style={{ fontSize: 12 }}>
                {t('skeleton.evidence.empty')}
              </Text>
            ) : (
              <div data-testid="skeleton-evidence" style={{ maxHeight: 220, overflow: 'auto' }}>
                {evidence.map((ev, i) => (
                  <div
                    key={i}
                    data-testid={`skeleton-evidence-${i}`}
                    style={{
                      display: 'flex', gap: 6, padding: '4px 6px',
                      alignItems: 'flex-start',
                      borderBottom: i < evidence.length - 1
                        ? `1px solid ${tokens.border}` : 'none',
                    }}
                  >
                    <span style={{ marginTop: 2 }}>{evidenceIcon(ev.satisfied)}</span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{
                        fontSize: 12, color: tokens.labelPrimary, wordBreak: 'break-word',
                      }}>
                        {ev.criterion || ''}
                      </div>
                      <Space size={8} wrap style={{ marginTop: 2 }}>
                        <Text type="secondary" style={{ fontSize: 10 }}>
                          {t(SAT_KEY(ev.satisfied))}
                        </Text>
                        {renderValue(ev.reported) !== '' && (
                          <Text type="secondary" style={{ fontSize: 10 }}>
                            {t('skeleton.evidence.reported')}: {renderValue(ev.reported)}
                          </Text>
                        )}
                        {renderValue(ev.actual) !== '' && (
                          <Text type="secondary" style={{ fontSize: 10 }}>
                            {t('skeleton.evidence.actual')}: {renderValue(ev.actual)}
                          </Text>
                        )}
                      </Space>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Space>
        )}
      </Spin>
    </Card>
  );
};

export default SkeletonPanel;
