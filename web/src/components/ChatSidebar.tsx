/**
 * ChatSidebar — left rail of the chat UI.
 *
 * Layout (DSH / ChatGPT beta style):
 *
 *   ┌──────────────────────────────┐
 *   │  [ +  New chat             ]  │  ← primary CTA
 *   │  ──────────────────────────  │
 *   │  PROJECTS    12              │  ← top section, default 10
 *   │  ● demo                      │     with "Show all" expand
 *   │    test1                     │
 *   │    test2                     │
 *   │    ...                       │
 *   │  [Show all (15)]             │
 *   │  ──────────────────────────  │
 *   │  SESSIONS in "demo"          │  ← bottom section, per-project
 *   │  Today                       │     grouped by date
 *   │   • Loop · 3     ●          │
 *   │   • Try /plan   85          │
 *   │  Yesterday                   │
 *   │   • ...                      │
 *   │  ──────────────────────────  │
 *   │   [empty space flex]         │
 *   └──────────────────────────────┘
 *
 * Projects are loaded once at the topbar level (see AppLayout) and
 * pushed into the chat store. We render the top 10 by default and
 * add a "Show all (N)" toggle when there are more. The active
 * project is highlighted; clicking another project switches the
 * chat store's currentProject and navigates to /chat (resets the
 * session thread because sessions are scoped per project).
 *
 * Sessions for the current project are grouped by `last_activity`:
 *   - Today (last 24h) / Yesterday (24-48h) /
 *     Previous 7 days (2-7d) / Older (>7d)
 */
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Button, Spin, Empty, Tooltip } from 'antd';
import {
  MessageOutlined, ThunderboltOutlined,
  CheckCircleFilled, CloseCircleFilled, DownOutlined,
  UpOutlined, ProjectOutlined,
} from '@ant-design/icons';

import { useChatStore } from '../stores/chatStore';
import { useThemeTokens } from '../hooks/useThemeTokens';
import api from '../api/client';
import NewChatButton from './NewChatButton';
import type { LoopSession, Project } from '../types';

const DEFAULT_PROJECT_LIMIT = 10;

const ChatSidebar: React.FC = () => {
  const tokens = useThemeTokens();
  const navigate = useNavigate();
  const { sessionId } = useParams<{ sessionId?: string }>();
  const projects = useChatStore((s) => s.projects);
  const currentProject = useChatStore((s) => s.currentProject);
  const setCurrentProject = useChatStore((s) => s.setCurrentProject);
  const sessions = useChatStore((s) => s.sessions);
  const setSessions = useChatStore((s) => s.setSessions);
  const setCurrentSessionId = useChatStore((s) => s.setCurrentSessionId);
  const [loading, setLoading] = useState(false);
  const [projectsExpanded, setProjectsExpanded] = useState(false);

  // Re-fetch sessions whenever the project changes.
  useEffect(() => {
    if (!currentProject) {
      setSessions([]);
      return;
    }
    setLoading(true);
    api.get<{ sessions: LoopSession[] }>(`/projects/${currentProject.id}/sessions`)
      .then((r) => setSessions(r.data.sessions || []))
      .catch(() => setSessions([]))
      .finally(() => setLoading(false));
  }, [currentProject, setSessions]);

  // Sort projects: current first, then by created_at desc.
  const sortedProjects = useMemo(() => {
    const arr = [...projects];
    arr.sort((a, b) => {
      // Current project bubbles to the top.
      if (currentProject && a.id === currentProject.id) return -1;
      if (currentProject && b.id === currentProject.id) return 1;
      // Otherwise most recent first.
      return (b.created_at || 0) - (a.created_at || 0);
    });
    return arr;
  }, [projects, currentProject]);

  const visibleProjects = projectsExpanded
    ? sortedProjects
    : sortedProjects.slice(0, DEFAULT_PROJECT_LIMIT);
  const hiddenCount = sortedProjects.length - visibleProjects.length;

  const grouped = useMemo(() => groupByDate(sessions), [sessions]);

  const selectSession = (sid: string) => {
    setCurrentSessionId(sid);
    navigate(`/chat/${sid}`);
  };

  const selectProject = (p: Project) => {
    if (currentProject?.id === p.id) return;  // no-op
    setCurrentProject(p);
    navigate('/chat');
  };

  return (
    <div style={{
      height: `calc(100vh - 52px)`,
      display: 'flex', flexDirection: 'column',
      padding: '12px 8px',
      boxSizing: 'border-box',
    }}>
      <NewChatButton />

      <div style={{ flex: 1, overflowY: 'auto', padding: '0 4px' }}>
        {/* ------------------- Projects list ------------------- */}
        {projects.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 6,
              padding: '6px 8px 4px',
            }}>
              <span style={{
                fontSize: 11, fontWeight: 600, color: tokens.labelTertiary,
                letterSpacing: '0.04em', textTransform: 'uppercase',
              }}>
                Projects
              </span>
              <span style={{
                fontSize: 11, color: tokens.labelTertiary,
              }}>
                {projects.length}
              </span>
            </div>
            {visibleProjects.map((p) => (
              <ProjectRow
                key={p.id}
                project={p}
                active={currentProject?.id === p.id}
                onClick={() => selectProject(p)}
              />
            ))}
            {hiddenCount > 0 && !projectsExpanded && (
              <Button
                type="text" block size="small"
                icon={<DownOutlined />}
                onClick={() => setProjectsExpanded(true)}
                style={{ color: tokens.labelTertiary, fontSize: 12,
                          height: 28, marginTop: 2 }}
              >
                Show all ({sortedProjects.length})
              </Button>
            )}
            {projectsExpanded && sortedProjects.length > DEFAULT_PROJECT_LIMIT && (
              <Button
                type="text" block size="small"
                icon={<UpOutlined />}
                onClick={() => setProjectsExpanded(false)}
                style={{ color: tokens.labelTertiary, fontSize: 12,
                          height: 28, marginTop: 2 }}
              >
                Show less
              </Button>
            )}
          </div>
        )}

        {/* ------------------- Sessions list ------------------- */}
        {currentProject && (
          <>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 6,
              padding: '6px 8px 4px',
              borderTop: `1px solid ${tokens.border}`,
              marginTop: 4,
            }}>
              <span style={{
                fontSize: 11, fontWeight: 600, color: tokens.labelTertiary,
                letterSpacing: '0.04em', textTransform: 'uppercase',
              }}>
                Sessions
              </span>
              {sessions.length > 0 && (
                <span style={{ fontSize: 11, color: tokens.labelTertiary }}>
                  {sessions.length}
                </span>
              )}
            </div>
            {loading && (
              <div style={{ display: 'flex', justifyContent: 'center',
                            padding: 16 }}>
                <Spin size="small" />
              </div>
            )}
            {!loading && sessions.length === 0 && (
              <Empty
                image={<MessageOutlined style={{ fontSize: 24,
                                                color: tokens.labelTertiary }} />}
                imageStyle={{ height: 32 }}
                description={
                  <span style={{ color: tokens.labelTertiary, fontSize: 12 }}>
                    No sessions yet — start a new loop below.
                  </span>
                }
                style={{ marginTop: 12 }}
              />
            )}
            {!loading && grouped.map((group) => (
              <div key={group.label} style={{ marginBottom: 8 }}>
                <div style={{
                  padding: '4px 8px 2px', fontSize: 10,
                  color: tokens.labelTertiary,
                }}>
                  {group.label}
                </div>
                {group.items.map((s) => (
                  <SessionRow
                    key={s.session_id}
                    session={s}
                    active={s.session_id === sessionId}
                    onClick={() => selectSession(s.session_id)}
                  />
                ))}
              </div>
            ))}
          </>
        )}

        {!currentProject && projects.length === 0 && (
          <Empty
            image={<ProjectOutlined style={{ fontSize: 28,
                                            color: tokens.labelTertiary }} />}
            imageStyle={{ height: 40 }}
            description={
              <span style={{ color: tokens.labelTertiary, fontSize: 12 }}>
                Add a folder from the top bar to start your first project.
              </span>
            }
            style={{ marginTop: 32 }}
          />
        )}
      </div>
    </div>
  );
};

const ProjectRow: React.FC<{
  project: Project;
  active: boolean;
  onClick: () => void;
}> = ({ project, active, onClick }) => {
  const tokens = useThemeTokens();
  return (
    <Tooltip
      title={project.description && project.description !== project.name
             ? project.description : undefined}
      placement="right"
    >
      <div
        onClick={onClick}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => { if (e.key === 'Enter') onClick(); }}
        data-testid={`project-row-${project.id}`}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '7px 10px', borderRadius: 8,
          cursor: 'pointer',
          background: active ? tokens.bgLay2 : 'transparent',
          color: active ? tokens.labelPrimary : tokens.labelSecondary,
          transition: 'background 0.12s',
        }}
        onMouseEnter={(e) => {
          if (!active) e.currentTarget.style.background = tokens.bgLay1;
        }}
        onMouseLeave={(e) => {
          if (!active) e.currentTarget.style.background = 'transparent';
        }}
      >
        {active && (
          <span style={{
            display: 'inline-block', width: 4, height: 16,
            borderRadius: 2, background: tokens.labelPrimary,
          }} />
        )}
        <ProjectOutlined style={{ color: active ? tokens.labelPrimary
                                              : tokens.labelTertiary }} />
        <div style={{
          flex: 1, minWidth: 0,
          fontSize: 13, fontWeight: active ? 600 : 500,
          whiteSpace: 'nowrap', overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}>
          {project.name || project.id}
        </div>
      </div>
    </Tooltip>
  );
};

const SessionRow: React.FC<{
  session: LoopSession;
  active: boolean;
  onClick: () => void;
}> = ({ session, active, onClick }) => {
  const tokens = useThemeTokens();
  const title = useMemo(() => {
    if (!session.round_count) {
      return session.running ? 'Starting…' : 'Empty session';
    }
    return `Loop · ${session.round_count} round${session.round_count === 1 ? '' : 's'}`;
  }, [session]);

  return (
    <div
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter') onClick(); }}
      data-testid={`session-row-${session.session_id}`}
      style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '7px 10px', borderRadius: 8,
        cursor: 'pointer',
        background: active ? tokens.bgLay2 : 'transparent',
        color: active ? tokens.labelPrimary : tokens.labelSecondary,
        transition: 'background 0.12s',
      }}
      onMouseEnter={(e) => {
        if (!active) e.currentTarget.style.background = tokens.bgLay1;
      }}
      onMouseLeave={(e) => {
        if (!active) e.currentTarget.style.background = 'transparent';
      }}
    >
      {session.running
        ? <ThunderboltOutlined style={{ color: tokens.coderAccent }} />
        : session.round_count === 0
          ? <MessageOutlined style={{ color: tokens.labelTertiary }} />
          : session.last_approve
            ? <CheckCircleFilled style={{ color: tokens.success }} />
            : <CloseCircleFilled style={{ color: tokens.warning }} />}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 12, fontWeight: active ? 600 : 500,
          whiteSpace: 'nowrap', overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}>
          {title}
        </div>
        {session.round_count > 0 && (
          <div style={{
            fontSize: 10, color: tokens.labelTertiary,
            whiteSpace: 'nowrap', overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}>
            {`R${session.last_round} · score ${session.last_score}`}
          </div>
        )}
      </div>
    </div>
  );
};

interface Group { label: string; items: LoopSession[]; }

function groupByDate(sessions: LoopSession[]): Group[] {
  const now = Date.now() / 1000;
  const oneDay = 24 * 3600;
  const buckets: Record<string, LoopSession[]> = {
    'Today': [], 'Yesterday': [], 'Previous 7 days': [], 'Older': [],
  };
  for (const s of sessions) {
    const age = now - (s.last_activity || s.started_at || 0);
    if (age < oneDay) buckets['Today'].push(s);
    else if (age < 2 * oneDay) buckets['Yesterday'].push(s);
    else if (age < 7 * oneDay) buckets['Previous 7 days'].push(s);
    else buckets['Older'].push(s);
  }
  const order = ['Today', 'Yesterday', 'Previous 7 days', 'Older'];
  return order
    .filter((k) => buckets[k].length > 0)
    .map((k) => ({ label: k, items: buckets[k] }));
}

export default ChatSidebar;
