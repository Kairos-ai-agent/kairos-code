/**
 * Today — minimal stats + recent activity.
 *
 * The old Dashboard had 4 stat cards + a 12-column agent grid +
 * recent messages list. In the new chat-centric layout, most of
 * that lives in the chat sidebar / thread. Today only shows:
 *   - 3 numbers: active sessions, total rounds, avg score
 *   - 1 list: most recent sessions across all projects
 *
 * No auto-refresh (we want this page to be cheap to load).
 */
import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Card, Row, Col, Statistic, List, Tag, Spin, Empty, Button } from 'antd';
import {
  ProjectOutlined, ThunderboltOutlined, BarChartOutlined,
  RightOutlined,
} from '@ant-design/icons';

import { useThemeTokens } from '../hooks/useThemeTokens';
import { useChatStore } from '../stores/chatStore';
import api from '../api/client';
import type { LoopSession, Project } from '../types';
import AlertPanel from '../components/AlertPanel';
import CogsPanel from '../components/CogsPanel';

const Today: React.FC = () => {
  const tokens = useThemeTokens();
  const navigate = useNavigate();
  const projects = useChatStore((s) => s.projects);
  const setCurrentProject = useChatStore((s) => s.setCurrentProject);
  const setSessions = useChatStore((s) => s.setSessions);
  const [loading, setLoading] = useState(true);
  const [aggregate, setAggregate] = useState<{
    totalSessions: number;
    totalRounds: number;
    avgScore: number;
    perProject: { project: Project; sessions: LoopSession[] }[];
  }>({ totalSessions: 0, totalRounds: 0, avgScore: 0, perProject: [] });

  useEffect(() => {
    setLoading(true);
    Promise.all(
      projects.map((p) =>
        api.get<{ sessions: LoopSession[] }>(`/projects/${p.id}/sessions`)
          .then((r) => ({ project: p, sessions: r.data.sessions || [] }))
          .catch(() => ({ project: p, sessions: [] })))
    ).then((rows) => {
      let totalSessions = 0, totalRounds = 0, scoreSum = 0, scoreCount = 0;
      for (const r of rows) {
        totalSessions += r.sessions.length;
        for (const s of r.sessions) {
          totalRounds += s.round_count || 0;
          if (s.last_score) { scoreSum += s.last_score; scoreCount += 1; }
        }
      }
      setAggregate({
        totalSessions, totalRounds,
        avgScore: scoreCount > 0 ? Math.round(scoreSum / scoreCount) : 0,
        perProject: rows,
      });
      setLoading(false);
    });
  }, [projects, setSessions]);

  const openSession = (pid: string, sid: string) => {
    const p = projects.find((x) => x.id === pid);
    if (p) setCurrentProject(p);
    navigate(`/chat/${sid}`);
  };

  // Sort: most recent across all projects, top 10.
  const recent: Array<{ projectId: string; projectName: string; s: LoopSession }> = [];
  for (const row of aggregate.perProject) {
    for (const s of row.sessions) {
      recent.push({ projectId: row.project.id, projectName: row.project.name, s });
    }
  }
  recent.sort((a, b) => (b.s.last_activity || 0) - (a.s.last_activity || 0));
  const top = recent.slice(0, 10);

  return (
    <div style={{ padding: '24px 16px', maxWidth: 960, margin: '0 auto' }}>
      <div style={{ marginBottom: 24 }}>
        <div style={{ fontSize: 22, fontWeight: 700, color: tokens.labelPrimary }}>
          Today
        </div>
        <div style={{ fontSize: 13, color: tokens.labelTertiary, marginTop: 4 }}>
          A snapshot of your work across all projects.
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}>
          <Spin />
        </div>
      ) : (
        <>
          <Row gutter={[16, 16]} style={{ marginBottom: 24 }}>
            <Col xs={24} sm={8}>
              <Card style={{ background: tokens.bgLay1,
                              border: `1px solid ${tokens.border}` }}>
                <Statistic title="Projects" value={projects.length}
                           prefix={<ProjectOutlined />} />
              </Card>
            </Col>
            <Col xs={24} sm={8}>
              <Card style={{ background: tokens.bgLay1,
                              border: `1px solid ${tokens.border}` }}>
                <Statistic title="Total sessions"
                           value={aggregate.totalSessions}
                           prefix={<ThunderboltOutlined />} />
              </Card>
            </Col>
            <Col xs={24} sm={8}>
              <Card style={{ background: tokens.bgLay1,
                              border: `1px solid ${tokens.border}` }}>
                <Statistic title="Avg score"
                           value={aggregate.avgScore}
                           prefix={<BarChartOutlined />}
                           suffix={aggregate.avgScore ? '/100' : '—'} />
              </Card>
            </Col>
          </Row>

          <Card title="Recent activity"
                style={{ background: tokens.bgLay1,
                         border: `1px solid ${tokens.border}` }}
                styles={{ body: { padding: 0 } }}>
            {top.length === 0 ? (
              <Empty
                image={<ThunderboltOutlined style={{ fontSize: 32,
                                                   color: tokens.labelTertiary }} />}
                description={
                  <span style={{ color: tokens.labelTertiary, fontSize: 13 }}>
                    No sessions yet — head to a project and start a loop.
                  </span>
                }
                style={{ padding: 24 }}
              />
            ) : (
              <List
                dataSource={top}
                renderItem={(item) => (
                  <List.Item
                    onClick={() => openSession(item.projectId, item.s.session_id)}
                    style={{
                      padding: '12px 16px', cursor: 'pointer',
                      borderBottom: `1px solid ${tokens.border}`,
                    }}
                    actions={[<RightOutlined key="go"
                                             style={{ color: tokens.labelTertiary }} />]}
                  >
                    <List.Item.Meta
                      title={
                        <span style={{ color: tokens.labelPrimary }}>
                          {item.s.round_count === 0
                            ? 'New session'
                            : `Loop · ${item.s.round_count} round${item.s.round_count === 1 ? '' : 's'}`}
                        </span>
                      }
                      description={
                        <span style={{ color: tokens.labelTertiary, fontSize: 12 }}>
                          {item.projectName} · {fmtTime(item.s.last_activity)}
                        </span>
                      }
                    />
                    {item.s.last_score > 0 && (
                      <Tag color={item.s.last_score >= 80 ? 'green'
                                   : item.s.last_score >= 50 ? 'orange' : 'red'}>
                        score {item.s.last_score}
                      </Tag>
                    )}
                  </List.Item>
                )}
              />
            )}
          </Card>

          <div style={{ marginTop: 16, textAlign: 'center' }}>
            <Button type="link" onClick={() => navigate('/chat')}>
              Open the chat →
            </Button>
          </div>

          <div style={{ marginTop: 24 }}>
            <AlertPanel />
          </div>
          <div style={{ marginTop: 16 }}>
            <CogsPanel />
          </div>
        </>
      )}
    </div>
  );
};

function fmtTime(ts: number): string {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  const now = new Date();
  const isToday = d.toDateString() === now.toDateString();
  if (isToday) return `today ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`;
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

export default Today;
