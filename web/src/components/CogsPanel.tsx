/**
 * CogsPanel — Cost of Goods Sold value metrics (R36).
 *
 * Displays the 5 derived metrics from `GET /api/cost/value`:
 *   - cost_per_case     ($ per eval case)
 *   - cost_per_passing  ($ per passing eval case)
 *   - cost_per_alert    ($ per fired alert)
 *   - efficiency        (passing / total)
 *   - approval_yield    (1 - critical/total alerts)
 *
 * Plus the raw totals: total cost, n LLM calls, dataset totals,
 * alert totals. The R35 endpoint returns `None` for zero-denominator
 * metrics; the UI renders those as "—" so the user can distinguish
 * "N/A" from "$0" or "0%".
 *
 * Refreshes every 30 s (matches CostDashboard's polling interval
 * so the user sees a consistent snapshot).
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Card, Row, Col, Statistic, Spin, Empty, Tooltip, Tag, Button } from 'antd';
import {
  DollarOutlined, ExperimentOutlined, AlertOutlined,
  ThunderboltOutlined, PercentageOutlined, CheckCircleOutlined,
  ReloadOutlined, ClockCircleOutlined, InfoCircleOutlined,
} from '@ant-design/icons';

import api from '../api/client';
import { formatError } from '../utils/formatError';
import { useThemeTokens } from '../hooks/useThemeTokens';
import { useT } from '../i18n';

interface CogsResponse {
  total_cost_usd: number;
  n_llm_calls: number;
  dataset: { total_cases: number; passed: number; failed: number };
  alerts: { total: number; critical: number };
  metrics: {
    cost_per_case: number | null;
    cost_per_passing: number | null;
    cost_per_alert: number | null;
    efficiency: number | null;
    approval_yield: number | null;
  };
}

const REFRESH_MS = 30_000;

/** Format a ratio metric (or null → "—"). */
function fmtRatio(v: number | null, suffix = ''): string {
  if (v === null || v === undefined) return '—';
  return `${v.toFixed(4)}${suffix}`;
}

function fmtPercent(v: number | null): string {
  if (v === null || v === undefined) return '—';
  return `${(v * 100).toFixed(1)}%`;
}

const CogsPanel: React.FC = () => {
  const t = useT();
  const tokens = useThemeTokens();
  const [data, setData] = useState<CogsResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastRefresh, setLastRefresh] = useState(0);

  const refresh = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const r = await api.get<CogsResponse>('/cost/value');
      setData(r.data);
      setLastRefresh(Date.now());
    } catch (e: any) {
      setErr(formatError(e, 'Failed to load COGS metrics'));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, REFRESH_MS);
    return () => clearInterval(t);
  }, [refresh]);

  if (err && !data) {
    return (
      <Card
        size="small"
        title={<><DollarOutlined /> {t('cogsPanel.title')}</>}
        extra={
          <Button size="small" icon={<ReloadOutlined />} onClick={refresh}>
            {t('common.retry')}
          </Button>
        }
        style={{ background: tokens.bgLay1, border: `1px solid ${tokens.border}` }}
      >
        <div style={{ color: '#cf1322', fontSize: 12 }}>{err}</div>
      </Card>
    );
  }

  if (!data) {
    return (
      <Card size="small" title={<><DollarOutlined /> {t('cogsPanel.title')}</>}>
        <Spin />
      </Card>
    );
  }

  const m = data.metrics;

  return (
    <Card
      size="small"
      title={
        <span>
          <DollarOutlined /> {t('cogsPanel.title')}
          <Tooltip title={t('cogsPanel.tooltip')}>
            <InfoCircleOutlined style={{ marginInlineStart: 6, opacity: 0.4 }} />
          </Tooltip>
        </span>
      }
      extra={
        <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {lastRefresh > 0 && (
            <span style={{ fontSize: 11, opacity: 0.6 }}>
              <ClockCircleOutlined /> {new Date(lastRefresh).toLocaleTimeString()}
            </span>
          )}
          <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={refresh}>
            {t('common.refresh')}
          </Button>
        </span>
      }
      style={{ background: tokens.bgLay1, border: `1px solid ${tokens.border}` }}
    >
      <Spin spinning={loading}>
        {/* Top: 3 big totals */}
        <Row gutter={8} style={{ marginBottom: 12 }}>
          <Col span={8}>
            <Statistic
              title={<span style={{ fontSize: 11 }}>{t('cogsPanel.stat.totalSpend')}</span>}
              value={data.total_cost_usd}
              precision={4}
              prefix={<DollarOutlined />}
              valueStyle={{ fontSize: 18 }}
            />
          </Col>
          <Col span={8}>
            <Statistic
              title={<span style={{ fontSize: 11 }}>{t('cogsPanel.stat.evalCases')}</span>}
              value={data.dataset.total_cases}
              prefix={<ExperimentOutlined />}
              valueStyle={{ fontSize: 18 }}
            />
          </Col>
          <Col span={8}>
            <Statistic
              title={<span style={{ fontSize: 11 }}>{t('cogsPanel.stat.alertsFired')}</span>}
              value={data.alerts.total}
              prefix={<AlertOutlined />}
              valueStyle={{
                fontSize: 18,
                color: data.alerts.critical > 0 ? '#cf1322' : undefined,
              }}
              suffix={
                data.alerts.critical > 0 ? (
                  <Tag color="red" style={{ marginInlineStart: 4, fontSize: 10 }}>
                    {t('cogsPanel.critCount', { n: data.alerts.critical })}
                  </Tag>
                ) : null
              }
            />
          </Col>
        </Row>

        {/* Bottom: 5 derived metrics */}
        <Row gutter={[12, 8]} style={{ borderTop: `1px solid ${tokens.border}`, paddingTop: 12 }}>
          <Col xs={12} sm={8} md={8} lg={4}>
            <Tooltip title={t('cogsPanel.ratio.costPerCase')}>
              <Statistic
                title={<span style={{ fontSize: 10 }}>{t('cogsPanel.metric.costPerCase')}</span>}
                value={fmtRatio(m.cost_per_case)}
                valueStyle={{ fontSize: 14 }}
                prefix={<DollarOutlined />}
              />
            </Tooltip>
          </Col>
          <Col xs={12} sm={8} md={8} lg={5}>
            <Tooltip title={t('cogsPanel.ratio.costPerPass')}>
              <Statistic
                title={<span style={{ fontSize: 10 }}>{t('cogsPanel.metric.costPerPass')}</span>}
                value={fmtRatio(m.cost_per_passing)}
                valueStyle={{ fontSize: 14, color: '#3f8600' }}
                prefix={<CheckCircleOutlined />}
              />
            </Tooltip>
          </Col>
          <Col xs={12} sm={8} md={8} lg={5}>
            <Tooltip title={t('cogsPanel.ratio.costPerAlert')}>
              <Statistic
                title={<span style={{ fontSize: 10 }}>{t('cogsPanel.metric.costPerAlert')}</span>}
                value={fmtRatio(m.cost_per_alert)}
                valueStyle={{ fontSize: 14 }}
                prefix={<ThunderboltOutlined />}
              />
            </Tooltip>
          </Col>
          <Col xs={12} sm={8} md={8} lg={5}>
            <Tooltip title={t('cogsPanel.ratio.efficiency')}>
              <Statistic
                title={<span style={{ fontSize: 10 }}>{t('cogsPanel.metric.efficiency')}</span>}
                value={fmtPercent(m.efficiency)}
                valueStyle={{
                  fontSize: 14,
                  color: m.efficiency === null ? undefined :
                    m.efficiency >= 0.8 ? '#3f8600' :
                    m.efficiency >= 0.5 ? '#d48806' : '#cf1322',
                }}
                prefix={<PercentageOutlined />}
              />
            </Tooltip>
          </Col>
          <Col xs={24} sm={8} md={8} lg={5}>
            <Tooltip title={t('cogsPanel.ratio.approvalYield')}>
              <Statistic
                title={<span style={{ fontSize: 10 }}>{t('cogsPanel.metric.approvalYield')}</span>}
                value={fmtPercent(m.approval_yield)}
                valueStyle={{
                  fontSize: 14,
                  color: m.approval_yield === null ? undefined :
                    m.approval_yield >= 0.8 ? '#3f8600' : '#d48806',
                }}
              />
            </Tooltip>
          </Col>
        </Row>

        {/* Subtitle: n_llm_calls + dataset/alert composition */}
        <div
          style={{
            fontSize: 11,
            color: tokens.labelTertiary,
            marginTop: 10,
            fontFamily: 'ui-monospace, SFMono-Regular, monospace',
          }}
          data-testid="cogs-subtitle"
        >
          {t('cogsPanel.subtitle', {
            calls: data.n_llm_calls, passed: data.dataset.passed,
            failed: data.dataset.failed, critical: data.alerts.critical,
            total: data.alerts.total,
          })}
        </div>
      </Spin>
    </Card>
  );
};

export default CogsPanel;

