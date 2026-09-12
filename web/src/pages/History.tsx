/**
 * History — every run, its verdict, its cost, and whether it was better than
 * the run before it.
 *
 * This replaces "browse the loop sidebar and hope": one row per session with the
 * numbers that matter, plus a link to the raw trace and the Gate Report. It is
 * the second of the three primary views (Run / History / Settings).
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Alert, Button, Card, Empty, Select, Space, Spin, Table, Tag, Tooltip, Typography,
} from 'antd';
import {
  CheckCircleOutlined, CloseCircleOutlined, DownloadOutlined, EyeOutlined,
  BranchesOutlined, ReloadOutlined,
} from '@ant-design/icons';
import api from '../api/client';
import { useT } from '../i18n';
import { useChatStore } from '../stores/chatStore';

const { Title, Text } = Typography;

interface GatePayload {
  session_id: string;
  state: 'passed' | 'rejected' | 'running' | 'empty';
  rounds_total: number;
  rejected_rounds: number;
  final_score: number;
  first_pass: boolean;
  cost_usd: number;
  cost_calls: number;
  badge: string;
  rounds: Array<{ round: number; score: number; approve: boolean; issues: unknown[] }>;
}

interface SessionRow {
  session_id: string;
  round_count: number;
  started_at: number;
  last_score: number;
  last_approve: boolean;
  running?: boolean;
  gate?: GatePayload | null;
}

const fmtWhen = (ts: number) => (ts ? new Date(ts * 1000).toLocaleString() : '—');

const History: React.FC = () => {
  const t = useT();
  const projects = useChatStore((s) => s.projects);
  const currentProject = useChatStore((s) => s.currentProject);

  const [searchParams, setSearchParams] = useSearchParams();
  const [projectId, setProjectId] = useState(
    searchParams.get('project') || currentProject?.id || '',
  );
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!projectId && projects.length) setProjectId(projects[0].id);
  }, [projects, projectId]);

  useEffect(() => {
    if (searchParams.get('project') !== projectId) {
      setSearchParams(projectId ? { project: projectId } : {}, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

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
      const res = await api.get(`/projects/${projectId}/sessions`)
        .catch(() => ({ data: null }));
      let sessions: SessionRow[] = res.data?.sessions || [];
      if (!sessions.length) {
        // Same fallback as Run: the report is DB-backed, the session list is not.
        const agg = await api.get(`/projects/${projectId}/gate-report`, {
          params: { format: 'json' },
        });
        if (agg.data?.rounds_total) {
          sessions = [{
            session_id: agg.data.session_id || '(latest)',
            round_count: agg.data.rounds_total,
            started_at: agg.data.rounds?.[0]?.created_at || 0,
            last_score: agg.data.final_score,
            last_approve: Boolean(agg.data.passed),
            gate: agg.data as GatePayload,
          }];
          setRows(sessions);
          return;
        }
      }
      // Fetch the receipt for the most recent runs so the table can show the
      // verdict + cost without opening anything (sessions are few in practice).
      const withGate = await Promise.all(sessions.slice(0, 25).map(async (s) => {
        try {
          const g = await api.get(`/projects/${projectId}/gate-report`, {
            params: { format: 'json', session: s.session_id },
          });
          return { ...s, gate: g.data as GatePayload };
        } catch {
          return { ...s, gate: null };
        }
      }));
      setRows([...withGate, ...sessions.slice(25)]);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || t('history.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [projectId, t]);

  useEffect(() => { void load(); }, [load]);

  const data = useMemo(() => rows.map((row, index) => {
    const prev = rows[index + 1];
    const score = row.gate?.final_score ?? row.last_score ?? 0;
    const prevScore = prev?.gate?.final_score ?? prev?.last_score ?? NaN;
    return {
      ...row,
      key: row.session_id,
      score,
      delta: prev ? score - prevScore : NaN,
      cost: row.gate?.cost_usd ?? 0,
      rounds: row.gate?.rounds_total ?? row.round_count ?? 0,
      rejected: row.gate?.rejected_rounds ?? 0,
      passed: row.gate ? row.gate.state === 'passed' : Boolean(row.last_approve),
      hasRuns: (row.gate?.rounds_total ?? row.round_count ?? 0) > 0,
    };
  }), [rows]);

  return (
    <div style={{ padding: 24, maxWidth: 1200, marginInline: 'auto' }} data-testid="history-page">
      <Space style={{ marginBlockEnd: 16 }} align="center" wrap>
        <Title level={4} style={{ margin: 0 }}>{t('history.title')}</Title>
        <Select
          data-testid="history-project-select"
          style={{ minWidth: 220 }}
          value={projectId}
          onChange={setProjectId}
          options={projects.map((p) => ({ value: p.id, label: p.name }))}
        />
        <Tooltip title={t('history.refresh')}>
          <Button icon={<ReloadOutlined />} onClick={() => void load()} loading={loading} />
        </Tooltip>
      </Space>

      {error ? (
        <Alert type="error" showIcon style={{ marginBlockEnd: 16 }} message={error} />
      ) : null}

      {loading && rows.length === 0 ? <Spin /> : null}

      <Card size="small" data-testid="history-table-card">
        <Table
          size="small"
          rowKey="key"
          dataSource={data}
          pagination={{ pageSize: 15, hideOnSinglePage: true }}
          locale={{
            emptyText: (
              <Empty description={t('history.empty')} />
            ),
          }}
          expandedRowRender={(row) => (
            row.gate && row.gate.rounds?.length ? (
              <Table
                size="small"
                rowKey="round"
                pagination={false}
                dataSource={row.gate.rounds}
                columns={[
                  { title: t('history.th.round'), dataIndex: 'round', width: 70,
                    render: (v: number) => `R${v}` },
                  { title: t('history.th.score'), dataIndex: 'score', width: 80 },
                  { title: t('history.th.verdict'), dataIndex: 'approve', width: 110,
                    render: (ok: boolean) => ok
                      ? <Tag color="success">{t('run.approved')}</Tag>
                      : <Tag color="error">{t('run.rejectedTag')}</Tag> },
                  { title: t('history.th.issues'), key: 'issues', width: 80,
                    render: (_: unknown, r: { issues: unknown[] }) => r.issues?.length ?? 0 },
                ]}
              />
            ) : <Text type="secondary">{t('history.noRounds')}</Text>
          )}
          columns={[
            { title: t('history.th.run'), dataIndex: 'session_id',
              render: (v: string, row) => (
                <Space size={6}>
                  <Text code style={{ fontSize: 12 }}>{v}</Text>
                  {row.running ? <Tag color="processing">{t('run.state.running')}</Tag> : null}
                  {row.gate?.first_pass ? <Tag>{t('history.firstPass')}</Tag> : null}
                </Space>
              ) },
            { title: t('history.th.when'), dataIndex: 'started_at', width: 170,
              render: (v: number) => <Text type="secondary">{fmtWhen(v)}</Text> },
            { title: t('history.th.verdict'), dataIndex: 'passed', width: 120,
              render: (passed: boolean, row) => (row.hasRuns
                ? (passed
                  ? <Tag color="success" icon={<CheckCircleOutlined />}>{t('run.state.passed')}</Tag>
                  : <Tag color="error" icon={<CloseCircleOutlined />}>{t('run.state.rejected')}</Tag>)
                : <Tag>{t('run.state.empty')}</Tag>) },
            { title: t('history.th.rounds'), dataIndex: 'rounds', width: 130,
              render: (v: number, row) => (
                <Text>{v}{row.rejected ? <Text type="secondary"> ({row.rejected} {t('history.rejectedShort')})</Text> : null}</Text>
              ) },
            { title: t('history.th.score'), dataIndex: 'score', width: 90 },
            { title: t('history.th.cost'), dataIndex: 'cost', width: 110,
              render: (v: number) => `$${(v || 0).toFixed(4)}` },
            { title: t('history.th.delta'), dataIndex: 'delta', width: 100,
              render: (v: number) => (!isFinite(v) || v === 0
                ? <Text type="secondary">—</Text>
                : <Text style={{ color: v > 0 ? '#1a7f37' : '#cf222e' }}>
                    {v > 0 ? '+' : ''}{v}
                  </Text>) },
            { title: '', key: 'actions', width: 150,
              render: (_: unknown, row) => (
                <Space size={4}>
                  <Tooltip title={t('history.view')}>
                    <Button
                      size="small" type="text" icon={<EyeOutlined />}
                      href={reportUrl(row.session_id)}
                      target="_blank"
                      disabled={!row.hasRuns}
                    />
                  </Tooltip>
                  <Tooltip title={t('history.download')}>
                    <Button
                      size="small" type="text" icon={<DownloadOutlined />}
                      href={reportUrl(row.session_id, 'md', true)}
                      disabled={!row.hasRuns}
                    />
                  </Tooltip>
                  <Tooltip title={t('history.trace')}>
                    <Button
                      size="small" type="text" icon={<BranchesOutlined />}
                      href={`/trace/${projectId}/${row.session_id}`}
                    />
                  </Tooltip>
                </Space>
              ) },
          ]}
        />
      </Card>
    </div>
  );
};

export default History;
