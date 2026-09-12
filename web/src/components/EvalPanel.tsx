/**
 * EvalPanel — UI for the eval dataset record / replay / derive
 * operations (R19).
 *
 * Three sections:
 *   - Datasets list (counts, sizes, last-modified)
 *   - Record: pick a run JSON, append to a dataset
 *   - Replay: pick a dataset, re-run against the current agent
 *   - Derive: scan git log for kairos commits, write a suite
 *
 * The buttons call the /api/cost/datasets/* endpoints added in
 * R19.1. A status message below the form reports success/failure.
 */
import React, { useEffect, useState } from 'react';
import {
  Card, Form, Input, Button, List, Tag, Empty, Alert, Tabs,
  message,
} from 'antd';
import {
  DatabaseOutlined, PlayCircleOutlined, BranchesOutlined,
  FileAddOutlined, ReloadOutlined,
} from '@ant-design/icons';

import api from '../api/client';
import { formatError } from '../utils/formatError';
import { useT } from '../i18n';

interface DatasetEntry {
  name: string;
  path: string;
  size_bytes: number;
  count: number;
  mtime: number;
}

interface DeriveResult {
  cases: number;
  out_path: string;
}

interface RecordResult {
  recorded: number;
  dataset: string;
}

interface ReplayResult {
  run_id: string;
  pass_rate: number;
  total_cost_usd: number;
  out_path: string;
}

const EvalPanel: React.FC = () => {
  const t = useT();
  const [datasets, setDatasets] = useState<DatasetEntry[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [deriveResult, setDeriveResult] = useState<DeriveResult | null>(null);
  const [recordResult, setRecordResult] = useState<RecordResult | null>(null);
  const [replayResult, setReplayResult] = useState<ReplayResult | null>(null);
  const [dsDir, setDsDir] = useState<string>('');

  const refresh = async () => {
    setErr(null);
    try {
      const r = await api.get<DatasetEntry[]>('/cost/datasets', {
        params: dsDir ? { directory: dsDir } : {},
      });
      setDatasets(r.data);
    } catch (e: any) {
      setErr(formatError(e, 'Failed to list datasets'));
    }
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleRecord = async (values: any) => {
    setBusy('record');
    setRecordResult(null);
    try {
      const r = await api.post<RecordResult>('/cost/datasets/record', null, {
        params: {
          run_path: values.run_path,
          dataset_path: values.dataset_path,
          only_passed: values.only_passed !== false,
        },
      });
      setRecordResult(r.data);
      message.success(`Recorded ${r.data.recorded} case(s)`);
      refresh();
    } catch (e: any) {
      setErr(formatError(e, 'Record failed'));
    } finally {
      setBusy(null);
    }
  };

  const handleReplay = async (values: any) => {
    setBusy('replay');
    setReplayResult(null);
    try {
      const r = await api.post<ReplayResult>('/cost/datasets/replay', null, {
        params: {
          dataset_path: values.dataset_path,
          out_path: values.out_path || '',
        },
      });
      setReplayResult(r.data);
      message.success(
        `Replay: pass_rate=${(r.data.pass_rate * 100).toFixed(0)}%, cost=$${r.data.total_cost_usd.toFixed(4)}`,
      );
    } catch (e: any) {
      setErr(formatError(e, 'Replay failed'));
    } finally {
      setBusy(null);
    }
  };

  const handleDerive = async (values: any) => {
    setBusy('derive');
    setDeriveResult(null);
    try {
      const r = await api.post<DeriveResult>('/cost/datasets/derive', null, {
        params: {
          repo_path: values.repo_path || '.',
          out_path: values.out_path || '',
          limit: values.limit || 50,
        },
      });
      setDeriveResult(r.data);
      message.success(`Derived ${r.data.cases} case(s)`);
    } catch (e: any) {
      setErr(formatError(e, 'Derive failed'));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card
      size="small"
      title={<><DatabaseOutlined /> {t('evalPanel.title')}</>}
      extra={
        <Button size="small" icon={<ReloadOutlined />} onClick={refresh}>
          {t('common.refresh')}
        </Button>
      }
    >
      {err && (
        <Alert type="error" message={err} closable
               onClose={() => setErr(null)} style={{ marginBottom: 12 }} />
      )}

      <Tabs
        size="small"
        items={[
          {
            key: 'datasets',
            label: <span><DatabaseOutlined /> {t('evalPanel.tabs.datasets')}</span>,
            children: (
              <div>
                <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                  <Input
                    size="small"
                    placeholder={t('evalPanel.datasets.dirPlaceholder')}
                    value={dsDir}
                    onChange={(e) => setDsDir(e.target.value)}
                    onPressEnter={refresh}
                  />
                  <Button size="small" onClick={refresh}>{t('evalPanel.datasets.list')}</Button>
                </div>
                {datasets.length === 0 ? (
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description={t('evalPanel.datasets.empty')}
                  />
                ) : (
                  <List
                    size="small"
                    dataSource={datasets}
                    renderItem={(d) => (
                      <List.Item>
                        <List.Item.Meta
                          title={
                            <code style={{ fontSize: 12 }}>{d.name}</code>
                          }
                          description={
                            <span style={{ fontSize: 11 }}>
                              {t('evalPanel.datasets.meta', {
                                cases: d.count,
                                size: (d.size_bytes / 1024).toFixed(1),
                                time: new Date(d.mtime * 1000).toLocaleString(),
                              })}
                            </span>
                          }
                        />
                        <Tag color="blue">{d.path.split(/[\\/]/).slice(-2, -1)[0]}</Tag>
                      </List.Item>
                    )}
                  />
                )}
              </div>
            ),
          },
          {
            key: 'record',
            label: <span><FileAddOutlined /> {t('evalPanel.tabs.record')}</span>,
            children: (
              <Form layout="vertical" size="small" onFinish={handleRecord}>
                <Form.Item name="run_path" label={t('evalPanel.record.runJson')} rules={[{ required: true }]}>
                  <Input placeholder={t('evalPanel.record.runJsonPlaceholder')} />
                </Form.Item>
                <Form.Item name="dataset_path" label={t('evalPanel.record.datasetPath')}
                           rules={[{ required: true }]}>
                  <Input placeholder={t('evalPanel.datasetPlaceholder')} />
                </Form.Item>
                <Form.Item name="only_passed" label={t('evalPanel.record.onlyPassed')} valuePropName="checked" initialValue={true}>
                  <input type="checkbox" />
                </Form.Item>
                <Button type="primary" htmlType="submit" loading={busy === 'record'}>
                  {t('evalPanel.record.submit')}
                </Button>
                {recordResult && (
                  <Alert
                    type="success" style={{ marginTop: 8 }}
                    message={`Recorded ${recordResult.recorded} case(s) to ${recordResult.dataset}`}
                  />
                )}
              </Form>
            ),
          },
          {
            key: 'replay',
            label: <span><PlayCircleOutlined /> {t('evalPanel.tabs.replay')}</span>,
            children: (
              <Form layout="vertical" size="small" onFinish={handleReplay}>
                <Form.Item name="dataset_path" label={t('evalPanel.replay.datasetPath')} rules={[{ required: true }]}>
                  <Input placeholder={t('evalPanel.datasetPlaceholder')} />
                </Form.Item>
                <Form.Item name="out_path" label={t('evalPanel.replay.outPath')}>
                  <Input placeholder={t('evalPanel.replay.outPlaceholder')} />
                </Form.Item>
                <Button type="primary" htmlType="submit" loading={busy === 'replay'}>
                  {t('evalPanel.replay.submit')}
                </Button>
                {replayResult && (
                  <Alert
                    type="success" style={{ marginTop: 8 }}
                    message={`Run ${replayResult.run_id}: pass_rate=${(replayResult.pass_rate * 100).toFixed(0)}%, cost=$${replayResult.total_cost_usd.toFixed(4)}`}
                  />
                )}
              </Form>
            ),
          },
          {
            key: 'derive',
            label: <span><BranchesOutlined /> {t('evalPanel.tabs.derive')}</span>,
            children: (
              <Form layout="vertical" size="small" onFinish={handleDerive}>
                <Form.Item name="repo_path" label={t('evalPanel.derive.repoPath')} initialValue=".">
                  <Input placeholder="." />
                </Form.Item>
                <Form.Item name="out_path" label={t('evalPanel.derive.outYaml')}>
                  <Input placeholder={t('evalPanel.derive.outPlaceholder')} />
                </Form.Item>
                <Form.Item name="limit" label={t('evalPanel.derive.commitLimit')} initialValue={50}>
                  <Input type="number" min={1} max={1000} />
                </Form.Item>
                <Button type="primary" htmlType="submit" loading={busy === 'derive'}>
                  {t('evalPanel.derive.submit')}
                </Button>
                {deriveResult && (
                  <Alert
                    type="success" style={{ marginTop: 8 }}
                    message={`Derived ${deriveResult.cases} case(s) → ${deriveResult.out_path}`}
                  />
                )}
              </Form>
            ),
          },
        ]}
      />
    </Card>
  );
};

export default EvalPanel;

