/**
 * Artifacts — what a run produced, as things you can read and answer.
 *
 * A round's plan, a round's result, a screenshot the browser tool took, a
 * rendered report: the loop has always produced these and the UI has always
 * shown them as lines scrolled past in a transcript. Here they are objects with
 * an address, a kind, and a comment thread — which is what makes "look at this
 * plan and tell me it is wrong" possible at all.
 *
 * Deep-linkable like Run, History and Tasks: `?project=<id>`.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert, Button, Card, Col, Empty, Input, Row, Select, Space, Spin, Tag,
  Typography, App as AntdApp,
} from 'antd';
import { ReloadOutlined, SendOutlined } from '@ant-design/icons';
import { useSearchParams } from 'react-router-dom';

import api from '../api/client';
import { useT } from '../i18n';
import { formatError } from '../utils/formatError';

const { Title, Text, Paragraph } = Typography;

interface ArtifactRow {
  id: string;
  project_id: string;
  kind: string;
  title: string;
  body?: string;
  path?: string;
  round_no?: number;
  created_at?: number;
  meta?: Record<string, unknown>;
}

interface CommentRow {
  id: number;
  author: string;
  body: string;
  created_at: number;
}

const KINDS = ['plan', 'report', 'screenshot', 'summary', 'note'];

const POLL_MS = 6000;

const Artifacts: React.FC = () => {
  const t = useT();
  const { message: msgApi } = AntdApp.useApp();
  const [params, setParams] = useSearchParams();
  const projectId = params.get('project') || '';

  const [projects, setProjects] = useState<{ id: string; name?: string }[]>([]);
  const [rows, setRows] = useState<ArtifactRow[]>([]);
  const [kind, setKind] = useState('');
  const [selected, setSelected] = useState<ArtifactRow | null>(null);
  const [thread, setThread] = useState<CommentRow[]>([]);
  const [draft, setDraft] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    api.get('/projects')
      .then((res) => {
        const list = Array.isArray(res.data) ? res.data : (res.data?.projects || []);
        setProjects(list);
      })
      .catch(() => setProjects([]));
  }, []);

  const load = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const query = kind ? '?kind=' + encodeURIComponent(kind) : '';
      const res = await api.get('/projects/' + encodeURIComponent(projectId)
                                + '/artifacts' + query);
      setRows((res.data?.artifacts || []) as ArtifactRow[]);
      setError('');
    } catch (e) {
      setError(formatError(e));
    } finally {
      setLoading(false);
    }
  }, [projectId, kind]);

  useEffect(() => {
    if (!projectId) { setRows([]); setSelected(null); return undefined; }
    load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [projectId, load]);

  const open = useCallback(async (row: ArtifactRow) => {
    setSelected(row);
    setThread([]);
    try {
      const res = await api.get('/artifacts/' + encodeURIComponent(row.id) + '/comments');
      setThread((res.data?.comments || []) as CommentRow[]);
    } catch {
      setThread([]);
    }
  }, []);

  const send = async () => {
    if (!selected || !draft.trim()) return;
    try {
      await api.post('/artifacts/' + encodeURIComponent(selected.id) + '/comments',
                     { body: draft.trim() });
      setDraft('');
      const res = await api.get('/artifacts/' + encodeURIComponent(selected.id) + '/comments');
      setThread((res.data?.comments || []) as CommentRow[]);
    } catch (e) {
      msgApi.error(formatError(e));
    }
  };

  const projectName = useMemo(() => {
    const found = projects.find((p) => p.id === projectId);
    return found?.name || projectId;
  }, [projects, projectId]);

  const kindOptions = useMemo(
    () => [{ value: '', label: t('artifacts.allKinds') },
           ...KINDS.map((k) => ({ value: k, label: t('artifacts.kind.' + k) }))],
    [t],
  );

  return (
    <div data-testid="artifacts-page">
      <Title level={3}>{t('artifacts.title')}</Title>
      <Paragraph type="secondary">{t('artifacts.subtitle')}</Paragraph>

      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          data-testid="artifacts-project-select"
          style={{ minWidth: 260 }}
          placeholder={t('artifacts.chooseProject')}
          value={projectId || undefined}
          onChange={(value: string) => {
            const next = new URLSearchParams(params);
            next.set('project', value);
            setParams(next);
            setSelected(null);
          }}
          options={projects.map((p) => ({ value: p.id, label: p.name || p.id }))}
          showSearch
          optionFilterProp="label"
        />
        <Select
          data-testid="artifacts-kind-select"
          style={{ minWidth: 160 }}
          value={kind}
          onChange={setKind}
          options={kindOptions}
        />
        <Button icon={<ReloadOutlined />} onClick={load} disabled={!projectId}>
          {t('artifacts.refresh')}
        </Button>
      </Space>

      {error && <Alert type="error" showIcon style={{ marginBottom: 16 }} message={error} />}
      {!projectId && <Empty description={t('artifacts.chooseProject')} />}

      {projectId && (
        <Row gutter={16}>
          <Col xs={24} lg={11}>
            <Card title={t('artifacts.produced', { name: projectName })}
                  extra={<Text type="secondary">{t('artifacts.count', { n: rows.length })}</Text>}>
              {loading && rows.length === 0 && <Spin />}
              {!loading && rows.length === 0 && (
                <Paragraph type="secondary">{t('artifacts.none')}</Paragraph>
              )}
              <Space direction="vertical" style={{ width: '100%' }}>
                {rows.map((row) => (
                  <Button
                    key={row.id}
                    type={selected?.id === row.id ? 'primary' : 'default'}
                    data-testid={'artifact-row-' + row.id}
                    onClick={() => open(row)}
                    style={{ width: '100%', textAlign: 'start', height: 'auto', padding: 10 }}
                  >
                    <Space direction="vertical" size={2} style={{ width: '100%' }}>
                      <Space wrap>
                        <Tag>{t('artifacts.kind.' + row.kind)}</Tag>
                        {row.round_no ? (
                          <Text type="secondary">{t('artifacts.round', { n: row.round_no })}</Text>
                        ) : null}
                      </Space>
                      <Text>{row.title}</Text>
                    </Space>
                  </Button>
                ))}
              </Space>
            </Card>
          </Col>

          <Col xs={24} lg={13}>
            <Card title={t('artifacts.detail')}>
              {!selected && <Paragraph type="secondary">{t('artifacts.pickOne')}</Paragraph>}
              {selected && (
                <>
                  <Title level={5} style={{ marginTop: 0 }}>{selected.title}</Title>
                  <Space wrap style={{ marginBottom: 8 }}>
                    <Tag>{t('artifacts.kind.' + selected.kind)}</Tag>
                    {selected.path ? <Text code>{selected.path}</Text> : null}
                  </Space>
                  {selected.kind === 'screenshot' && (
                    <div style={{ marginBottom: 12 }}>
                      {/* eslint-disable-next-line jsx-a11y/img-redundant-alt */}
                      <img
                        data-testid="artifact-image"
                        alt={t('artifacts.screenshotAlt')}
                        style={{ maxWidth: '100%', border: '1px solid #ddd' }}
                        src={'/api/artifacts/' + encodeURIComponent(selected.id) + '/file'}
                      />
                    </div>
                  )}
                  {selected.body ? (
                    <pre
                      data-testid="artifact-body"
                      style={{
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                        maxHeight: 320,
                        overflow: 'auto',
                        background: 'rgba(127,127,127,0.06)',
                        padding: 12,
                      }}
                    >
                      {selected.body}
                    </pre>
                  ) : null}

                  <Title level={5} style={{ marginTop: 16 }}>{t('artifacts.comments')}</Title>
                  {thread.length === 0 && (
                    <Paragraph type="secondary">{t('artifacts.noComments')}</Paragraph>
                  )}
                  <Space direction="vertical" style={{ width: '100%', marginBottom: 12 }}>
                    {thread.map((c) => (
                      <div key={c.id} data-testid={'comment-' + c.id}>
                        <Text strong>{c.author}</Text>
                        <Text> · {c.body}</Text>
                      </div>
                    ))}
                  </Space>
                  <Space.Compact style={{ width: '100%' }}>
                    <Input
                      data-testid="artifact-comment-input"
                      value={draft}
                      placeholder={t('artifacts.commentPlaceholder')}
                      onChange={(e) => setDraft(e.target.value)}
                      onPressEnter={send}
                    />
                    <Button
                      type="primary"
                      icon={<SendOutlined />}
                      data-testid="artifact-comment-send"
                      disabled={!draft.trim()}
                      onClick={send}
                    >
                      {t('artifacts.send')}
                    </Button>
                  </Space.Compact>
                </>
              )}
            </Card>
          </Col>
        </Row>
      )}
    </div>
  );
};

export default Artifacts;
