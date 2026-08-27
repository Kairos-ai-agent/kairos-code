/**
 * CostDashboard — visualize per-model spend.
 *
 * Reads the cost summary endpoint (Round 16) and renders:
 *  - Total cost + total call count
 *  - Per-model breakdown (call count, prompt/completion
 *    tokens, total cost, avg duration)
 *  - Recent entries (newest first, capped at 50)
 *
 * Refreshes every 30s by default; the user can hit "Refresh"
 * to force a fetch.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Card, Tag, Button, Table, Empty, Spin, Statistic, Row, Col, Tooltip } from 'antd';
import {
  DollarOutlined, ReloadOutlined, ClockCircleOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';

import api from '../api/client';

interface CostSummary {
  calls: number;
  cost_usd: number;
  in_memory: { calls: number; cost_usd: number; models: Record<string, any> };
  by_model: Record<string, {
    calls: number;
    prompt_tokens: number;
    completion_tokens: number;
    cost_usd: number;
    avg_duration_ms: number;
  }>;
}

interface RecentEntry {
  timestamp: number;
  model: string;
  provider: string;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  duration_ms: number;
}

const REFRESH_MS = 30_000;

const CostDashboard: React.FC = () => {
  const [summary, setSummary] = useState<CostSummary | null>(null);
  const [recent, setRecent] = useState<RecentEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<number>(0);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const [s, r] = await Promise.all([
        api.get<CostSummary>('/cost/summary'),
        api.get<RecentEntry[]>('/cost/recent?limit=20'),
      ]);
      setSummary(s.data);
      setRecent(r.data || []);
      setLastRefresh(Date.now());
    } catch (e: any) {
      setErr(e?.response?.data?.detail || e?.message || 'Failed to load cost data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(t);
  }, [refresh]);

  if (err) {
    return (
      <Card
        size="small"
        title={<><DollarOutlined /> Cost</>}
        extra={<Button size="small" icon={<ReloadOutlined />} onClick={refresh}>Retry</Button>}
      >
        <div style={{ color: '#cf1322', fontSize: 12 }}>{err}</div>
      </Card>
    );
  }

  const totalCost = summary?.cost_usd ?? 0;
  const totalCalls = summary?.calls ?? 0;
  const byModelEntries = Object.entries(summary?.by_model ?? {})
    .sort((a, b) => (b[1].cost_usd as number) - (a[1].cost_usd as number));

  return (
    <Card
      size="small"
      title={<><DollarOutlined /> Cost</>}
      extra={
        <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {lastRefresh > 0 && (
            <span style={{ fontSize: 11, opacity: 0.6 }}>
              <ClockCircleOutlined /> {new Date(lastRefresh).toLocaleTimeString()}
            </span>
          )}
          <Button
            size="small" icon={<ReloadOutlined />}
            loading={loading} onClick={refresh}
          >
            Refresh
          </Button>
        </span>
      }
    >
      <Spin spinning={loading}>
        <Row gutter={16} style={{ marginBottom: 12 }}>
          <Col span={12}>
            <Statistic
              title="Total spend"
              prefix={<DollarOutlined />}
              value={totalCost}
              precision={6}
              valueStyle={{ color: totalCost > 1 ? '#cf1322' : '#3f8600' }}
            />
          </Col>
          <Col span={12}>
            <Statistic
              title="LLM calls"
              prefix={<ThunderboltOutlined />}
              value={totalCalls}
            />
          </Col>
        </Row>
        {byModelEntries.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="No LLM calls recorded yet"
          />
        ) : (
          <Table<[string, any]>
            size="small"
            pagination={false}
            rowKey={(r) => r[0]}
            dataSource={byModelEntries}
            columns={[
              {
                title: 'Model', dataIndex: '0', key: 'model',
                render: (m: string) => <code>{m}</code>,
              },
              {
                title: 'Calls', dataIndex: '1', key: 'calls',
                render: (r: any) => r.calls,
              },
              {
                title: 'Tokens', key: 'tokens',
                render: (_: any, r: [string, any]) => (
                  <span style={{ fontSize: 11 }}>
                    {(r[1].prompt_tokens || 0).toLocaleString()} ↑ / {(r[1].completion_tokens || 0).toLocaleString()} ↓
                  </span>
                ),
              },
              {
                title: 'Cost', dataIndex: '1', key: 'cost',
                render: (r: any) => (
                  <span style={{
                    fontWeight: 500,
                    color: r.cost_usd > 0.5 ? '#cf1322' : undefined,
                  }}>
                    ${(r.cost_usd || 0).toFixed(6)}
                  </span>
                ),
              },
              {
                title: 'Avg ms', dataIndex: '1', key: 'ms',
                render: (r: any) => (
                  <Tag color="blue">{r.avg_duration_ms || 0}</Tag>
                ),
              },
            ]}
          />
        )}
        {recent.length > 0 && (
          <div style={{ marginTop: 12 }}>
            <div style={{ fontSize: 11, color: '#666', marginBottom: 4 }}>
              Recent calls
            </div>
            <div style={{
              maxHeight: 120, overflow: 'auto',
              border: '1px solid #f0f0f0', borderRadius: 4, padding: 4,
              fontFamily: 'ui-monospace, SFMono-Regular, monospace',
              fontSize: 10,
            }}>
              {recent.slice(0, 10).map((e, i) => (
                <div key={i} style={{ padding: '2px 0' }}>
                  <span style={{ color: '#999' }}>
                    {new Date(e.timestamp * 1000).toLocaleTimeString()}
                  </span>{' '}
                  <code>{e.model}</code>{' '}
                  <span style={{ color: '#1677ff' }}>
                    ${(e.cost_usd || 0).toFixed(6)}
                  </span>{' '}
                  <span style={{ color: '#666' }}>
                    {e.prompt_tokens}↑ {e.completion_tokens}↓ {e.duration_ms}ms
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </Spin>
    </Card>
  );
};

export default CostDashboard;
