/**
 * Tasks — the work that outlives a chat turn.
 *
 * Three kinds of work keep going when nobody is typing, and until now the app
 * could show none of them:
 *
 *   • a **durable task** — the `.har/` contract from Round 29. `kairos/har.py`
 *     could create, resume and record one, and no screen could start one:
 *     "kick off a multi-day refactor, close the laptop, pick it up tomorrow"
 *     needed a terminal. `kairos/durable.py` binds it to the project's real
 *     Coder/Reviewer loop, and this page drives it.
 *   • **background subagents** — handles from `spawn_subagent(background=true)`,
 *     which were previously only discoverable if you already knew the handle.
 *   • **autonomous jobs** — the registry's job records.
 *
 * Deep-linkable: `?project=<id>`, like Run and History. Without a project it
 * asks for one instead of guessing.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert, Button, Card, Col, Empty, Input, Row, Select, Space, Spin, Tag,
  Typography, App as AntdApp,
} from 'antd';
import { ReloadOutlined, PlayCircleOutlined, StepForwardOutlined } from '@ant-design/icons';
import { useSearchParams } from 'react-router-dom';

import api from '../api/client';
import { useT } from '../i18n';
import { formatError } from '../utils/formatError';

const { Title, Text, Paragraph } = Typography;

/** The durable state the API reports (see kairos/durable.py::status). */
interface DurableState {
  goal: string;
  har_id: string;
  round: number;
  last_score: number | null;
  last_approve: boolean | null;
  last_summary: string;
  no_progress_count: number;
  history: { round?: number; summary?: string; score?: number; approved?: boolean }[];
  root: string;
}

interface TasksPayload {
  project_id: string;
  durable: DurableState | null;
  subagents: { handle?: string; status?: string; goal?: string }[];
  autonomous: { id?: string; status?: string; goal?: string }[];
  counts: { durable?: number; subagents?: number; autonomous?: number };
}

interface ProjectOption {
  id: string;
  name?: string;
}

const POLL_MS = 4000;

const Tasks: React.FC = () => {
  const t = useT();
  const { message: msgApi } = AntdApp.useApp();
  const [params, setParams] = useSearchParams();
  const projectId = params.get('project') || '';

  const [projects, setProjects] = useState<ProjectOption[]>([]);
  const [data, setData] = useState<TasksPayload | null>(null);
  const [goal, setGoal] = useState('');
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const alive = useRef(true);

  useEffect(() => () => { alive.current = false; }, []);

  // One project list, for the picker. Accepts either shape the route may
  // return (a bare array, or an object with `projects`) rather than guessing.
  useEffect(() => {
    let cancelled = false;
    api.get('/projects')
      .then((res) => {
        if (cancelled) return;
        const list = Array.isArray(res.data) ? res.data : (res.data?.projects || []);
        setProjects(list as ProjectOption[]);
      })
      .catch(() => { setProjects([]); });
    return () => { cancelled = true; };
  }, []);

  const load = useCallback(async () => {
    if (!projectId) return;
    try {
      const res = await api.get('/tasks/' + encodeURIComponent(projectId));
      if (alive.current) {
        setData(res.data as TasksPayload);
        setError('');
      }
    } catch (e) {
      if (alive.current) setError(formatError(e));
    }
  }, [projectId]);

  useEffect(() => {
    if (!projectId) {
      setData(null);
      return undefined;
    }
    setLoading(true);
    load().finally(() => { if (alive.current) setLoading(false); });
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [projectId, load]);

  const startTask = async () => {
    setBusy('start');
    try {
      await api.post('/tasks/' + encodeURIComponent(projectId), { goal: goal.trim() });
      msgApi.success(t('tasks.started'));
      setGoal('');
      await load();
    } catch (e) {
      msgApi.error(formatError(e));
    } finally {
      setBusy('');
    }
  };

  const resumeTask = async (ticks: number) => {
    setBusy('resume');
    try {
      const res = await api.post('/tasks/' + encodeURIComponent(projectId) + '/resume',
                                 { ticks });
      const code = res.data?.code;
      // The state layer's codes, from kairos/har.py: 4 = the loop approved.
      if (code === 4) msgApi.success(t('tasks.resumeApproved'));
      else if (code === 2) msgApi.warning(t('tasks.resumeLocked'));
      else msgApi.info(t('tasks.resumeRan', { n: ticks }));
      await load();
    } catch (e) {
      msgApi.error(formatError(e));
    } finally {
      setBusy('');
    }
  };

  const durable = data?.durable || null;
  const history = useMemo(() => (durable?.history || []).slice(0, 8), [durable]);

  const projectName = useMemo(() => {
    const found = projects.find((p) => p.id === projectId);
    return found?.name || projectId;
  }, [projects, projectId]);

  return (
    <div data-testid="tasks-page">
      <Title level={3}>{t('tasks.title')}</Title>
      <Paragraph type="secondary">{t('tasks.subtitle')}</Paragraph>

      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          data-testid="tasks-project-select"
          style={{ minWidth: 280 }}
          placeholder={t('tasks.chooseProject')}
          value={projectId || undefined}
          onChange={(value: string) => {
            const next = new URLSearchParams(params);
            next.set('project', value);
            setParams(next);
          }}
          options={projects.map((p) => ({ value: p.id, label: p.name || p.id }))}
          showSearch
          optionFilterProp="label"
        />
        <Button icon={<ReloadOutlined />} onClick={load} disabled={!projectId}>
          {t('tasks.refresh')}
        </Button>
      </Space>

      {error && (
        <Alert type="error" showIcon style={{ marginBottom: 16 }} message={error} />
      )}

      {!projectId && (
        <Empty description={t('tasks.chooseProject')} />
      )}

      {projectId && loading && !data && <Spin />}

      {projectId && data && (
        <Row gutter={16}>
          <Col xs={24} lg={14}>
            <Card
              title={t('tasks.durable')}
              extra={<Text type="secondary">{projectName}</Text>}
              style={{ marginBottom: 16 }}
            >
              {durable ? (
                <>
                  <Paragraph style={{ marginBottom: 8 }}>
                    <Text strong>{t('tasks.goal')}: </Text>
                    <Text>{durable.goal}</Text>
                  </Paragraph>
                  <Space wrap style={{ marginBottom: 12 }}>
                    <Tag color="blue">{t('tasks.round', { n: durable.round })}</Tag>
                    {typeof durable.last_score === 'number' && (
                      <Tag>{t('tasks.score', { n: durable.last_score })}</Tag>
                    )}
                    <Tag color={durable.last_approve ? 'green' : 'default'}>
                      {durable.last_approve ? t('tasks.approved') : t('tasks.notApproved')}
                    </Tag>
                  </Space>
                  {durable.last_summary && (
                    <Paragraph type="secondary" style={{ marginBottom: 12 }}>
                      {durable.last_summary}
                    </Paragraph>
                  )}
                  <Space wrap>
                    <Button
                      type="primary"
                      icon={<PlayCircleOutlined />}
                      loading={busy === 'resume'}
                      onClick={() => resumeTask(1)}
                      data-testid="tasks-resume-1"
                    >
                      {t('tasks.resume1')}
                    </Button>
                    <Button
                      icon={<StepForwardOutlined />}
                      loading={busy === 'resume'}
                      onClick={() => resumeTask(5)}
                      data-testid="tasks-resume-5"
                    >
                      {t('tasks.resume5')}
                    </Button>
                  </Space>

                  <Title level={5} style={{ marginTop: 20 }}>{t('tasks.history')}</Title>
                  {history.length === 0
                    ? <Text type="secondary">{t('tasks.noHistory')}</Text>
                    : (
                      <ul style={{ paddingInlineStart: 20 }}>
                        {history.map((h, i) => (
                          <li key={String(h.round ?? i)}>
                            <Text>
                              {t('tasks.historyLine', {
                                round: h.round ?? i + 1,
                                score: typeof h.score === 'number' ? h.score : '—',
                              })}
                            </Text>
                            {h.summary ? <Text type="secondary"> · {h.summary}</Text> : null}
                          </li>
                        ))}
                      </ul>
                    )}
                </>
              ) : (
                <>
                  <Paragraph type="secondary">{t('tasks.noDurable')}</Paragraph>
                  <Input.TextArea
                    data-testid="tasks-goal"
                    rows={3}
                    value={goal}
                    placeholder={t('tasks.goalPlaceholder')}
                    onChange={(e) => setGoal(e.target.value)}
                  />
                  <Button
                    type="primary"
                    style={{ marginTop: 12 }}
                    disabled={!goal.trim()}
                    loading={busy === 'start'}
                    onClick={startTask}
                    data-testid="tasks-start"
                  >
                    {t('tasks.start')}
                  </Button>
                </>
              )}
            </Card>
          </Col>

          <Col xs={24} lg={10}>
            <Card title={t('tasks.background')} style={{ marginBottom: 16 }}>
              <Title level={5} style={{ marginTop: 0 }}>{t('tasks.subagents')}</Title>
              <TaskList
                items={(data.subagents || []).map((s) => ({
                  key: String(s.handle || JSON.stringify(s)),
                  label: s.handle || t('tasks.unnamed'),
                  status: s.status,
                }))}
                empty={t('tasks.none')}
              />
              <Title level={5} style={{ marginTop: 16 }}>{t('tasks.autonomous')}</Title>
              <TaskList
                items={(data.autonomous || []).map((a) => ({
                  key: String(a.id || JSON.stringify(a)),
                  label: a.goal || a.id || t('tasks.unnamed'),
                  status: a.status,
                }))}
                empty={t('tasks.none')}
              />
            </Card>
          </Col>
        </Row>
      )}
    </div>
  );
};

const TaskList: React.FC<{
  items: { key: string; label: string; status?: string }[];
  empty: string;
}> = ({ items, empty }) => {
  if (items.length === 0) return <Text type="secondary">{empty}</Text>;
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      {items.map((item) => (
        <Space key={item.key} wrap>
          <Text code>{item.label}</Text>
          {item.status ? <Tag>{item.status}</Tag> : null}
        </Space>
      ))}
    </Space>
  );
};

export default Tasks;
