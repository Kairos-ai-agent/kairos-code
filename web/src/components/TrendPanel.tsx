/**
 * TrendPanel — visualize multi-run trend data.
 *
 * Two tabs:
 *  - Overview: pass_rate / cost over time (sparkline + table)
 *  - Per-case: which cases are flaky (non-deterministic)?
 *
 * Reads from /api/trend and /api/trend/per_case (R25).
 */
import React, { useEffect, useState } from 'react';
import { Card, Tabs, Table, Empty, Tag, Spin, Statistic, Row, Col, Alert } from 'antd';
import { LineChartOutlined, FireOutlined } from '@ant-design/icons';

import api from '../api/client';
import { formatError } from '../utils/formatError';

interface RunPoint {
  timestamp: string;
  path: string;
  suite_name: string;
  run_id: string;
  cases: number;
  passed: number;
  pass_rate: number;
  cost_usd: number;
  tokens: number;
  avg_duration_ms: number;
  p95_duration_ms: number;
}

interface TrendReport {
  suite_name: string;
  runs: RunPoint[];
  n_total: number;
  n_passed: number;
  avg_pass_rate: number;
  avg_cost_usd: number;
  cost_first_to_last: number;
  pass_rate_first_to_last: number;
  window: number;
}

interface CaseTrendPoint {
  case_name: string;
  pass_rate: number;
  n_runs: number;
  n_passed: number;
  n_failed: number;
  flaky: boolean;
  history: boolean[];
}

const TrendPanel: React.FC = () => {
  const [trend, setTrend] = useState<TrendReport | null>(null);
  const [perCase, setPerCase] = useState<CaseTrendPoint[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = async () => {
    setLoading(true);
    setErr(null);
    try {
      const [t, p] = await Promise.all([
        api.get<TrendReport>('/trend', { params: { window: 20 } }),
        api.get<{ cases: CaseTrendPoint[] }>('/trend/per_case', { params: { window: 20 } }),
      ]);
      setTrend(t.data);
      setPerCase(p.data.cases || []);
    } catch (e: any) {
      setErr(formatError(e, 'Failed to load trend'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { refresh(); /* eslint-disable-next-line */ }, []);

  return (
    <Card
      size="small"
      title={<><LineChartOutlined /> Trend</>}
      extra={
        <a onClick={refresh} style={{ fontSize: 11, cursor: 'pointer' }}>
          refresh
        </a>
      }
    >
      {err && <Alert type="error" message={err} closable
                    onClose={() => setErr(null)} style={{ marginBottom: 12 }} />}
      <Spin spinning={loading}>
        <Tabs size="small" items={[
          {
            key: 'overview',
            label: <span><LineChartOutlined /> Overview</span>,
            children: (
              <OverviewTab trend={trend} />
            ),
          },
          {
            key: 'per_case',
            label: <span><FireOutlined /> Per-case</span>,
            children: (
              <PerCaseTab cases={perCase} />
            ),
          },
        ]} />
      </Spin>
    </Card>
  );
};

const OverviewTab: React.FC<{ trend: TrendReport | null }> = ({ trend }) => {
  if (!trend || trend.n_total === 0) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description="No runs yet" />;
  }
  return (
    <div>
      <Row gutter={16} style={{ marginBottom: 12 }}>
        <Col span={6}>
          <Statistic title="Runs" value={trend.n_total} />
        </Col>
        <Col span={6}>
          <Statistic title="Pass rate (avg)"
            value={trend.avg_pass_rate * 100}
            precision={0}
            suffix="%"
            valueStyle={{ color: trend.avg_pass_rate >= 0.5 ? '#3f8600' : '#cf1322' }} />
        </Col>
        <Col span={6}>
          <Statistic title="Cost (avg)"
            prefix="$"
            value={trend.avg_cost_usd}
            precision={4} />
        </Col>
        <Col span={6}>
          <Statistic title="Cost trend"
            value={trend.cost_first_to_last}
            precision={0}
            suffix="%"
            valueStyle={{ color: trend.cost_first_to_last > 0 ? '#cf1322' : '#3f8600' }} />
        </Col>
      </Row>
      <Table<RunPoint>
        size="small"
        pagination={false}
        rowKey={(r) => r.run_id}
        dataSource={trend.runs}
        columns={[
          {
            title: 'Run', dataIndex: 'run_id', key: 'run_id',
            render: (id: string) => <code style={{ fontSize: 11 }}>{id}</code>,
          },
          {
            title: 'Pass', dataIndex: 'pass_rate', key: 'pass_rate',
            render: (v: number) => (
              <span style={{
                color: v >= 0.5 ? '#3f8600' : '#cf1322',
                fontWeight: 500,
              }}>
                {(v * 100).toFixed(0)}%
              </span>
            ),
          },
          { title: 'Cost', dataIndex: 'cost_usd', key: 'cost',
            render: (v: number) => `$${v.toFixed(4)}` },
          { title: 'p95 ms', dataIndex: 'p95_duration_ms', key: 'p95',
            render: (v: number) => <Tag color="blue">{v.toFixed(0)}</Tag> },
          { title: 'Cases', dataIndex: 'cases', key: 'cases' },
          { title: 'Tokens', dataIndex: 'tokens', key: 'tokens',
            render: (v: number) => v.toLocaleString() },
        ]}
      />
    </div>
  );
};

const PerCaseTab: React.FC<{ cases: CaseTrendPoint[] | null }> = ({ cases }) => {
  if (!cases || cases.length === 0) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description="No per-case data" />;
  }
  const flaky = cases.filter((c) => c.flaky);
  const stable = cases.filter((c) => !c.flaky);
  return (
    <div>
      {flaky.length > 0 && (
        <Alert
          type="warning" showIcon style={{ marginBottom: 12 }}
          message={`${flaky.length} flaky case(s) — non-deterministic behavior`}
          description={flaky.map((c) => c.case_name).join(', ')}
        />
      )}
      <Table<CaseTrendPoint>
        size="small"
        pagination={false}
        rowKey={(c) => c.case_name}
        dataSource={cases}
        columns={[
          {
            title: 'Case', dataIndex: 'case_name', key: 'case_name',
            render: (n: string, r: CaseTrendPoint) => (
              <span>
                <code style={{ fontSize: 12 }}>{n}</code>{' '}
                {r.flaky && <Tag color="orange" style={{ fontSize: 10 }}>flaky</Tag>}
              </span>
            ),
          },
          {
            title: 'Pass rate', dataIndex: 'pass_rate', key: 'pass_rate',
            render: (v: number, r: CaseTrendPoint) => (
              <span style={{
                color: v >= 0.8 ? '#3f8600' : v >= 0.5 ? '#faad14' : '#cf1322',
                fontWeight: 500,
              }}>
                {(v * 100).toFixed(0)}% ({r.n_passed}/{r.n_runs})
              </span>
            ),
          },
          {
            title: 'History', dataIndex: 'history', key: 'history',
            render: (history: boolean[]) => (
              <span style={{ fontFamily: 'monospace', fontSize: 11 }}>
                {history.map((p, i) => (
                  <span key={i} style={{ color: p ? '#3f8600' : '#cf1322' }}>
                    {p ? '✓' : '✗'}
                  </span>
                ))}
              </span>
            ),
          },
        ]}
      />
      <div style={{ marginTop: 8, fontSize: 11, color: '#999' }}>
        Showing {cases.length} case(s) ({flaky.length} flaky, {stable.length} stable)
      </div>
    </div>
  );
};

export default TrendPanel;

