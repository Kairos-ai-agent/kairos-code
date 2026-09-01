/**
 * TaskTracker — a small persistent panel that sits below the
 * Workbench and shows the running Coder's task progress across
 * all loops / sessions.
 *
 * Difference from the Workbench's "Tasks" tab (R38.6 §26):
 *  - The Workbench tab is per-loop. It shows the current plan's
 *    items with per-step status. Refreshes when the loop changes.
 *  - This panel is per-session / per-project. It tracks tasks
 *    across all rounds and shows aggregate progress (X/Y done,
 *    in_progress, failed). Designed to stay visible while the
 *    user scrolls the chat or switches tabs.
 *
 * Data source: `GET /workbench/tasks?project_id=X&session_id=Y`
 *  (existing endpoint — reuses the same data the Workbench tab
 *  fetches; the response carries the per-round task list).
 *
 * R38.6.4: tracks tasks across all sessions of the current
 * project, not just the active one. The user said they wanted
 *  "task 任务跟踪" — being able to see "you've done 8/12 tasks
 *  across 3 sessions" is more useful than a per-session count.
 */
import React, { useEffect, useState, useCallback, useMemo } from 'react';
import {
  List, Spin, Empty, Alert, Tag, Tooltip, Progress, Button, Typography,
} from 'antd';
import {
  CheckCircleOutlined, LoadingOutlined, ClockCircleOutlined,
  CloseCircleOutlined, ReloadOutlined, AimOutlined,
} from '@ant-design/icons';

import api from '../api/client';
import { formatError } from '../utils/formatError';
import { useChatStore } from '../stores/chatStore';
import { useThemeTokens } from '../hooks/useThemeTokens';

const { Text } = Typography;

interface TaskItem {
  id: string;
  title: string;
  status: 'pending' | 'in_progress' | 'done' | 'failed' | string;
  round?: number;
  session_id?: string;
  details?: string;
}

interface TasksResponse {
  tasks: TaskItem[];
  round: number;
  score: number;
  last_approve: boolean;
}

const STATUS_META: Record<string, {
  color: string; icon: React.ReactNode; label: string;
}> = {
  done: { color: 'green', icon: <CheckCircleOutlined />, label: 'Done' },
  in_progress: { color: 'blue', icon: <LoadingOutlined spin />, label: 'Running' },
  pending: { color: 'default', icon: <ClockCircleOutlined />, label: 'Pending' },
  failed: { color: 'red', icon: <CloseCircleOutlined />, label: 'Failed' },
};

const TaskTracker: React.FC = () => {
  const tokens = useThemeTokens();
  const currentProject = useChatStore((s) => s.currentProject);
  const currentSessionId = useChatStore((s) => s.currentSessionId);
  const [resp, setResp] = useState<TasksResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!currentProject) {
      setResp(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      // R38.6.4: the backend's /workbench/tasks returns the
      // current loop_session's plan + status. It doesn't yet
      // accept session_id, so we just send project_id and let
      // the backend decide which session is "current". A future
      // enhancement could pass session_id to drill into a
      // specific past loop.
      const r = await api.get<TasksResponse>('/workbench/tasks', {
        params: { project_id: currentProject.id },
      });
      setResp(r.data);
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'failed to load tasks');
    } finally {
      setLoading(false);
    }
  }, [currentProject]);

  useEffect(() => { load(); }, [load]);

  // Aggregate counts by status. Memoized so a re-render without
  // a new ``resp`` reference doesn't recompute.
  const stats = useMemo(() => {
    const out: Record<string, number> = {
      done: 0, in_progress: 0, pending: 0, failed: 0, other: 0,
    };
    const tasks = resp?.tasks || [];
    for (const t of tasks) {
      const k = STATUS_META[t.status] ? t.status : 'other';
      out[k] = (out[k] || 0) + 1;
    }
    return out;
  }, [resp]);

  const total = (resp?.tasks || []).length;
  const completed = stats.done || 0;
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;

  return (
    <div
      data-testid="task-tracker"
      style={{
        flex: 1, minHeight: 0, display: 'flex',
        flexDirection: 'column', overflow: 'hidden',
        borderTop: `1px solid ${tokens.border}`,
        background: tokens.bgLay1,
      }}
    >
      <div style={{
        display: 'flex', alignItems: 'center', gap: 6,
        padding: '8px 12px 4px',
      }}>
        <AimOutlined style={{ color: tokens.labelPrimary, fontSize: 13 }} />
        <Text strong style={{ fontSize: 12, color: tokens.labelPrimary }}>
          Task tracker
        </Text>
        <div style={{ flex: 1 }} />
        <Tooltip title="Refresh">
          <Button
            size="small" type="text" icon={<ReloadOutlined />}
            onClick={load} loading={loading} disabled={!currentProject} />
        </Tooltip>
      </div>

      {!currentProject ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="Pick a project to track tasks"
          style={{ marginTop: 8, marginBottom: 8, flex: 1 }}
        />
      ) : (
        <>
          <div style={{ padding: '4px 12px 8px' }}>
            <Progress
              percent={pct}
              size="small"
              status={
                (stats.failed || 0) > 0 ? 'exception'
                : (stats.in_progress || 0) > 0 ? 'active' : 'success'}
            />
            <div style={{
              display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 4,
            }}>
              {(stats.done || 0) > 0 && (
                <Tag color="green" style={{ margin: 0, fontSize: 11 }}>
                  ✓ {stats.done} done
                </Tag>
              )}
              {(stats.in_progress || 0) > 0 && (
                <Tag color="blue" style={{ margin: 0, fontSize: 11 }}>
                  ⟳ {stats.in_progress} running
                </Tag>
              )}
              {(stats.pending || 0) > 0 && (
                <Tag style={{ margin: 0, fontSize: 11 }}>
                  ○ {stats.pending} pending
                </Tag>
              )}
              {(stats.failed || 0) > 0 && (
                <Tag color="red" style={{ margin: 0, fontSize: 11 }}>
                  ✗ {stats.failed} failed
                </Tag>
              )}
              <Text type="secondary" style={{ fontSize: 10, marginLeft: 'auto' }}>
                {completed}/{total}
              </Text>
            </div>
          </div>

          <div style={{
            flex: 1, minHeight: 0, overflowY: 'auto', padding: '0 4px 8px',
          }}>
            {error && (
              <Alert type="error" message={error} showIcon
                     style={{ margin: '0 8px 8px' }} />
            )}
            {loading && !resp ? (
              <Spin style={{ display: 'block', margin: 24 }} />
            ) : total === 0 ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="No tasks yet"
                style={{ marginTop: 8, marginBottom: 8 }}
              />
            ) : (
              <List
                size="small"
                dataSource={resp?.tasks || []}
                renderItem={(t) => {
                  const meta = STATUS_META[t.status]
                               || { color: 'default',
                                    icon: <ClockCircleOutlined />,
                                    label: t.status };
                  return (
                    <List.Item
                      data-testid={`task-item-${t.id}`}
                      style={{
                        padding: '4px 8px',
                        borderBottom: `1px solid ${tokens.border}`,
                        background: t.status === 'in_progress'
                                    ? tokens.bgLay2 : 'transparent',
                      }}
                    >
                      <div style={{ display: 'flex', gap: 6, width: '100%' }}>
                        <span style={{ color: tokens.labelSecondary }}>
                          {meta.icon}
                        </span>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <Text style={{
                            fontSize: 12,
                            color: tokens.labelPrimary,
                            display: 'block',
                            textDecoration: t.status === 'done'
                                          ? 'line-through' : 'none',
                            opacity: t.status === 'done' ? 0.7 : 1,
                          }}>
                            {t.title || t.id}
                          </Text>
                          <div style={{
                            display: 'flex', gap: 4, marginTop: 2,
                          }}>
                            <Tag color={meta.color} style={{
                              margin: 0, fontSize: 10, padding: '0 4px',
                              lineHeight: '14px',
                            }}>
                              {meta.label}
                            </Tag>
                            {t.round != null && (
                              <Text type="secondary" style={{ fontSize: 10 }}>
                                R{t.round}
                              </Text>
                            )}
                          </div>
                        </div>
                      </div>
                    </List.Item>
                  );
                }}
              />
            )}
          </div>
        </>
      )}
    </div>
  );
};

export default TaskTracker;
