/**
 * AlertPanel — visualize the recent cost-regression alerts (R30).
 *
 * Reads from three endpoints (R30 backend):
 *   - GET  /api/alerts/recent   — newest-first list of fired alerts
 *   - GET  /api/alerts/summary  — counts by severity + last_critical_at
 *   - POST /api/alerts/mute     — persist a mute for a key
 *   - GET  /api/alerts/mutes    — list active mutes
 *   - DELETE /api/alerts/mute/{key}
 *
 * Behavior:
 *   - 30 s auto-refresh
 *   - Severity color-coding: info=blue, warning=orange, critical=red
 *   - Status icons: ✓ sent, ✗ failed, ◌ skipped
 *   - Mute button per alert (key=kind:metric, default 1 h)
 *   - Muted alerts are dimmed but still visible (so the user knows
 *     the system is still working, just silenced)
 *
 * Why server-side mutes?  They survive a browser refresh and are
 * shared between team members using the same backend.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Card, Tag, Button, Statistic, Row, Col, Empty, Spin, Tooltip, message as antMessage } from 'antd';
import {
  AlertOutlined, ReloadOutlined, ClockCircleOutlined,
  CheckCircleFilled, CloseCircleFilled, MinusCircleFilled,
  BellOutlined, BellFilled,
} from '@ant-design/icons';

import api from '../api/client';
import { formatError } from '../utils/formatError';
import { useThemeTokens } from '../hooks/useThemeTokens';

interface FiredAlert {
  timestamp: number;
  severity: 'info' | 'warning' | 'critical' | string;
  kind: string;
  message: string;
  metric: string;
  baseline: number;
  current: number;
  delta_pct: number;
  channel: string;
  channel_url: string;
  status: 'sent' | 'failed' | 'skipped' | string;
  error: string;
}

interface AlertSummary {
  total: number;
  by_severity: { info: number; warning: number; critical: number };
  by_status: { sent: number; failed: number; skipped: number };
  by_kind: Record<string, number>;
  last_critical_at: number | null;
  active_mutes: number;
}

interface Mutes {
  mutes: Record<string, number>;
  count: number;
}

const REFRESH_MS = 30_000;
const SEV_COLOR: Record<string, string> = {
  info: 'blue',
  warning: 'orange',
  critical: 'red',
};

function statusIcon(status: string) {
  if (status === 'sent') return <CheckCircleFilled style={{ color: '#52c41a' }} />;
  if (status === 'failed') return <CloseCircleFilled style={{ color: '#cf1322' }} />;
  return <MinusCircleFilled style={{ color: '#999' }} />;
}

function muteKey(a: FiredAlert): string {
  return `${a.kind}:${a.metric}`;
}

const AlertPanel: React.FC = () => {
  const tokens = useThemeTokens();
  const [entries, setEntries] = useState<FiredAlert[]>([]);
  const [summary, setSummary] = useState<AlertSummary | null>(null);
  const [mutes, setMutes] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState(0);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const [r, s, m] = await Promise.all([
        api.get<{ entries: FiredAlert[]; count: number }>('/alerts/recent?limit=50'),
        api.get<AlertSummary>('/alerts/summary'),
        api.get<Mutes>('/alerts/mutes'),
      ]);
      setEntries(r.data.entries || []);
      setSummary(s.data);
      setMutes(m.data.mutes || {});
      setLastRefresh(Date.now());
    } catch (e: any) {
      setErr(formatError(e, 'Failed to load alerts'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(t);
  }, [refresh]);

  const mute = useCallback(async (a: FiredAlert, durationS = 3600) => {
    const key = muteKey(a);
    try {
      await api.post('/alerts/mute', { key, duration_s: durationS });
      // Update local state immediately
      setMutes((prev) => ({ ...prev, [key]: Date.now() / 1000 + durationS }));
      antMessage.success(`Muted ${key} for ${durationS / 3600}h`);
    } catch (e: any) {
      antMessage.error(e?.response?.data?.detail || 'mute failed');
    }
  }, []);

  if (err && !summary) {
    return (
      <Card
        size="small"
        title={<><AlertOutlined /> Alerts</>}
        extra={<Button size="small" icon={<ReloadOutlined />} onClick={refresh}>Retry</Button>}
        style={{ background: tokens.bgLay1, border: `1px solid ${tokens.border}` }}
      >
        <div style={{ color: '#cf1322', fontSize: 12 }}>{err}</div>
      </Card>
    );
  }

  const isMuted = (a: FiredAlert) => {
    const exp = mutes[muteKey(a)];
    return exp !== undefined && exp > Date.now() / 1000;
  };

  return (
    <Card
      size="small"
      title={<><AlertOutlined /> Alerts</>}
      extra={
        <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {lastRefresh > 0 && (
            <span style={{ fontSize: 11, opacity: 0.6 }}>
              <ClockCircleOutlined /> {new Date(lastRefresh).toLocaleTimeString()}
            </span>
          )}
          <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={refresh}>
            Refresh
          </Button>
        </span>
      }
      style={{ background: tokens.bgLay1, border: `1px solid ${tokens.border}` }}
    >
      <Spin spinning={loading}>
        {summary && (
          <Row gutter={8} style={{ marginBottom: 12 }}>
            <Col span={8}>
              <Statistic
                title={<span style={{ fontSize: 11 }}>Critical</span>}
                value={summary.by_severity.critical}
                valueStyle={{
                  color: summary.by_severity.critical > 0 ? '#cf1322' : undefined,
                  fontSize: 18,
                }}
                prefix={<AlertOutlined />}
              />
            </Col>
            <Col span={8}>
              <Statistic
                title={<span style={{ fontSize: 11 }}>Warning</span>}
                value={summary.by_severity.warning}
                valueStyle={{ color: summary.by_severity.warning > 0 ? '#d48806' : undefined, fontSize: 18 }}
              />
            </Col>
            <Col span={8}>
              <Statistic
                title={<span style={{ fontSize: 11 }}>Info</span>}
                value={summary.by_severity.info}
                valueStyle={{ fontSize: 18 }}
              />
            </Col>
          </Row>
        )}

        {summary && summary.active_mutes > 0 && (
          <div style={{ fontSize: 11, color: tokens.labelTertiary, marginBottom: 8 }}>
            <BellFilled /> {summary.active_mutes} active mute{summary.active_mutes === 1 ? '' : 's'}
          </div>
        )}

        {entries.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="No alerts yet"
            style={{ padding: 16 }}
          />
        ) : (
          <div
            style={{
              maxHeight: 320,
              overflow: 'auto',
              border: `1px solid ${tokens.border}`,
              borderRadius: 4,
            }}
          >
            {entries.map((a, i) => {
              const muted = isMuted(a);
              return (
                <div
                  key={`${a.timestamp}-${i}`}
                  style={{
                    padding: '8px 10px',
                    borderBottom: i < entries.length - 1 ? `1px solid ${tokens.border}` : 'none',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    opacity: muted ? 0.4 : 1,
                    transition: 'opacity 0.2s',
                  }}
                  data-testid={`alert-row-${a.kind}`}
                >
                  <Tooltip title={a.status}>
                    {statusIcon(a.status)}
                  </Tooltip>
                  <Tag color={SEV_COLOR[a.severity] || 'default'} style={{ margin: 0 }}>
                    {a.severity.toUpperCase()}
                  </Tag>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        fontSize: 12,
                        color: tokens.labelPrimary,
                        fontFamily: 'ui-monospace, SFMono-Regular, monospace',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                      title={a.message}
                    >
                      {a.message}
                    </div>
                    <div style={{ fontSize: 10, color: tokens.labelTertiary, marginTop: 2 }}>
                      {a.kind} · {a.metric} · +{a.delta_pct.toFixed(0)}%
                      {' · '}
                      {new Date(a.timestamp * 1000).toLocaleTimeString()}
                    </div>
                  </div>
                  {muted ? (
                    <Tag color="default" style={{ margin: 0 }}>muted</Tag>
                  ) : (
                    <Tooltip title={`Mute ${muteKey(a)} for 1 hour`}>
                      <Button
                        size="small"
                        type="text"
                        icon={<BellOutlined />}
                        onClick={() => mute(a)}
                        data-testid={`mute-${a.kind}`}
                      />
                    </Tooltip>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Spin>
    </Card>
  );
};

export default AlertPanel;

