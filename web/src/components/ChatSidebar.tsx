/**
 * ChatSidebar — left rail of the chat UI.
 *
 * Layout (R37 — minimax-code inspired):
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
 *   │  ──────────────────────────  │  ← footer (R37: moved here from
 *   │  Today  Tools  ⚙   ☀ Light │     the topbar)
 *   └──────────────────────────────┘
 *
 * Projects are loaded once at the topbar level (see AppLayout) and
 * pushed into the chat store. We render the top 10 by default and
 * add a "Show all (N)" toggle when there are more. The active
 * project is highlighted; clicking another project switches the
 * chat store's currentProject and navigates to /chat (resets the
 * session thread because sessions are scoped per project).
 *
 * R37 footer (new): Today / Tools / Settings / Theme live at the
 * bottom of the left rail. They were previously icons in the
 * topbar; the user requested moving them to the bottom-left so
 * the topbar can stay minimal.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Button, Spin, Empty, Tooltip, Popconfirm, App as AntdApp } from 'antd';
import {
  MessageOutlined, ThunderboltOutlined,
  CheckCircleFilled, CloseCircleFilled, DownOutlined,
  UpOutlined, ProjectOutlined, DeleteOutlined,
  AppstoreOutlined, ToolOutlined, SettingOutlined,
  SunOutlined, MoonOutlined,
  HistoryOutlined, DashboardOutlined, BranchesOutlined, SyncOutlined,
} from '@ant-design/icons';

import { useChatStore } from '../stores/chatStore';
import { useThemeStore } from '../stores/themeStore';
import { useSettingsStore } from '../stores/settingsStore';
import { useThemeTokens } from '../hooks/useThemeTokens';
import { useT, type TFunc } from '../i18n';
import api from '../api/client';
import { formatError } from '../utils/formatError';
import NewChatButton from './NewChatButton';
import type { LoopSession, Project } from '../types';

const DEFAULT_PROJECT_LIMIT = 10;

const ChatSidebar: React.FC = () => {
  const t = useT();
  const tokens = useThemeTokens();
  const navigate = useNavigate();
  const { sessionId } = useParams<{ sessionId?: string }>();
  const { message: msgApi } = AntdApp.useApp();
  const projects = useChatStore((s) => s.projects);
  const setProjects = useChatStore((s) => s.setProjects);
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

  // Order projects by created_at desc — a FIXED order that does not
  // depend on which one is selected. R38.6.5: the old comparator
  // bubbled the current project to the top, so clicking a project (or
  // auto-opening its latest session) silently reordered the sidebar.
  // The active row is marked with the left bar + highlight instead,
  // which is enough to show where you are without moving anything.
  const sortedProjects = useMemo(() => {
    return [...projects].sort((a, b) => {
      const dt = (b.created_at || 0) - (a.created_at || 0);
      if (dt !== 0) return dt;
      // Stable tie-breaker: equal timestamps must not shuffle on
      // re-render (Array#sort is not guaranteed stable for ties).
      return (a.id || '').localeCompare(b.id || '');
    });
  }, [projects]);

  const visibleProjects = projectsExpanded
    ? sortedProjects
    : sortedProjects.slice(0, DEFAULT_PROJECT_LIMIT);
  const hiddenCount = sortedProjects.length - visibleProjects.length;

  const grouped = useMemo(() => groupByDate(sessions, t), [sessions, t]);

  const selectSession = (sid: string) => {
    setCurrentSessionId(sid);
    navigate(`/chat/${sid}`);
  };

  // Pick a project = switch to it and start on its most recent
  // conversation. Soft navigation (no full page reload — the hard
  // reload here used to flash the page and wipe the thread); the
  // store's setCurrentProject clears the in-memory thread, and
  // Chat.tsx auto-loads the project's most recent session, so
  // history comes back instead of looking lost.
  const selectProject = (p: Project) => {
    if (currentProject?.id === p.id) return;
    setCurrentProject(p);
    navigate('/chat', { replace: true });
  };

  // Delete a project. Backend removes it (plus its sessions and
  // reference files); we update the local store. If the deleted
  // project was the current one, switch to the next available
  // project (or clear if none remain).
  const deleteProject = async (p: Project) => {
    try {
      await api.delete(`/projects/${p.id}`);
      const next = projects.filter((x) => x.id !== p.id);
      setProjects(next);
      if (currentProject?.id === p.id) {
        setCurrentProject(next[0] || null);
        if (next[0]) navigate('/chat');
      }
      msgApi.success(t('shell.sidebar.projectDeleted', { name: p.name || p.id }));
    } catch (e: any) {
      const detail = e?.response?.data?.detail || t('shell.sidebar.deleteFailed');
      msgApi.error(detail);
    }
  };

  return (
    <div style={{
      height: `calc(100vh - 52px)`,
      display: 'flex', flexDirection: 'column',
      padding: '12px 8px',
      boxSizing: 'border-box',
    }}>
      <NewChatButton />

      <div style={{ flex: 1, overflowY: 'auto', padding: '0 4px', minHeight: 0 }}>
        {/* ------------------- Projects list ------------------- */}
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
                {t('common.projects')}
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
                onDelete={() => deleteProject(p)}
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
                {t('shell.sidebar.showAll', { n: sortedProjects.length })}
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
                {t('shell.sidebar.showLess')}
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
                {t('shell.sidebar.sessions')}
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
                styles={{ image: { height: 32 } }}
                description={
                  <span style={{ color: tokens.labelTertiary, fontSize: 12 }}>
                    {t('shell.sidebar.noSessions')}
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
            styles={{ image: { height: 40 } }}
            description={
              <span style={{ color: tokens.labelTertiary, fontSize: 12 }}>
                {t('shell.sidebar.addFolderToStart')}
              </span>
            }
            style={{ marginTop: 32 }}
          />
        )}
      </div>

      {/* ------------------- Footer (R37) ------------------- */}
      <SidebarFooter />
    </div>
  );
};


// ---------------------------------------------------------------------------
// SidebarFooter — bottom-left controls (Today / Tools / Settings / Theme)
// ---------------------------------------------------------------------------
//
// R37: these used to be icons in the topbar. The user requested they
// all move to the bottom-left (minimax-code style). They sit at the
// very bottom of the ChatSidebar so the active project's history
// stays visually anchored above them.

// Exported so the vitest tests can mount it in isolation. Production
// code uses it via ChatSidebar's render tree.
export const SidebarFooter: React.FC = () => {
  const t = useT();
  const tokens = useThemeTokens();
  const navigate = useNavigate();
  const mode = useThemeStore((s) => s.mode);
  const toggle = useThemeStore((s) => s.toggle);
  const openSettings = useSettingsStore((s) => s.openDrawer);

  // Match the visual weight of the existing ProjectRow / SessionRow
  // buttons (padding 7px 10px, borderRadius 8, fontSize 13). This
  // keeps the footer feeling native to the sidebar instead of a
  // generic "settings" panel.
  // One icon size and one grid for the whole footer: the old markup mixed
  // 12/14px icons, 3- and 4-button rows (61-85px) and three alignment modes.
  const FOOTER_ICON = { fontSize: 14 } as const;
  const LABEL = {
    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
  } as const;

  // A destination: equal column, centred icon + label, hover wash.
  const baseBtn = {
    padding: '7px 6px',
    borderRadius: 8,
    background: 'transparent',
    border: 'none',
    color: tokens.labelSecondary,
    fontSize: 12,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 4,
    minWidth: 0,
    cursor: 'pointer',
    transition: 'background 0.12s, color 0.12s',
  } as const;

  // Destinations live on a 3-column grid so every row shares the same column
  // edges and no label can be squeezed onto a second line.
  const gridRow = {
    display: 'grid',
    gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
    gap: 4,
  } as const;

  // Utility bars (the Advanced disclosure and the theme switch) are a different
  // class of control, so they get a hairline outline and left-aligned content
  // instead of pretending to be destinations.
  const barBtn = {
    ...baseBtn,
    justifyContent: 'flex-start',
    paddingInlineStart: 10,
    // The hairline border would otherwise add 2px and break the row rhythm
    // (bars 33px tall next to 31px destinations) — compensate vertically.
    paddingBlock: 6,
    border: `1px solid ${tokens.border}`,
    color: tokens.labelTertiary,
  } as const;

  const barHover = (on: boolean) => (e: React.MouseEvent<HTMLButtonElement>) => {
    e.currentTarget.style.background = on ? tokens.bgLay2 : 'transparent';
    e.currentTarget.style.color = on ? tokens.labelSecondary : tokens.labelTertiary;
  };

  // R38.8: navigation collapsed to three primary views. Chat / Today /
  // Tools / Loop / Trace / Projects / Dashboard still exist (and keep
  // their routes) but live behind one "Advanced" toggle, so a new user
  // is not asked to choose between eight peer destinations on day one.
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const navBtn = (
    testId: string,
    icon: React.ReactNode,
    label: string,
    onClick: () => void,
  ) => (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      style={baseBtn}
      onMouseEnter={(e) => { e.currentTarget.style.background = tokens.bgLay2; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
    >
      {icon}
      <span style={LABEL}>{label}</span>
    </button>
  );

  return (
    <div
      data-testid="sidebar-footer"
      style={{
        borderTop: `1px solid ${tokens.border}`,
        marginTop: 8,
        paddingTop: 8,
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
      }}
    >
      {/* ----- Primary views: the three things a user actually does ----- */}
      <div style={gridRow}>
        <Tooltip title={t('nav.run')} placement="top">
          {navBtn('footer-run', <ThunderboltOutlined style={FOOTER_ICON} />,
                  t('nav.run'), () => navigate('/run'))}
        </Tooltip>
        <Tooltip title={t('nav.history')} placement="top">
          {navBtn('footer-history', <HistoryOutlined style={FOOTER_ICON} />,
                  t('nav.history'), () => navigate('/history'))}
        </Tooltip>
        <Tooltip title={t('shell.sidebar.settingsTooltip')} placement="top">
          {navBtn('footer-settings', <SettingOutlined style={FOOTER_ICON} />,
                  t('common.settings'), openSettings)}
        </Tooltip>
      </div>

      {/* ----- Utilities: bars, not destinations ----- */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <Tooltip title={t('nav.advancedHint')} placement="top">
          <button
            type="button"
            data-testid="footer-advanced"
            aria-expanded={advancedOpen}
            onClick={() => setAdvancedOpen((v) => !v)}
            style={barBtn}
            onMouseEnter={barHover(true)}
            onMouseLeave={barHover(false)}
          >
            {advancedOpen ? <UpOutlined style={FOOTER_ICON} />
                          : <DownOutlined style={FOOTER_ICON} />}
            <span style={{ ...LABEL, marginInlineStart: 2 }}>{t('nav.advanced')}</span>
          </button>
        </Tooltip>

        {/* Same 3-column grid as the primary row: seven destinations fill
            three tidy rows and every cell keeps the same width. */}
        <div
          data-testid="footer-advanced-group"
          style={{
            display: advancedOpen ? 'grid' : 'none',
            gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
            gap: 4,
          }}
        >
          {/* 「新建对话」is gone from here: it duplicated the NewChatButton at
              the top of the sidebar (which also creates the project for you).
              That leaves six entries, i.e. two tidy rows of three. */}
          <Tooltip title={t('shell.sidebar.today')} placement="top">
            {navBtn('footer-today', <AppstoreOutlined style={FOOTER_ICON} />,
                    t('shell.sidebar.today'), () => navigate('/today'))}
          </Tooltip>
          <Tooltip title={t('shell.sidebar.tools')} placement="top">
            {navBtn('footer-tools', <ToolOutlined style={FOOTER_ICON} />,
                    t('shell.sidebar.tools'), () => navigate('/tools'))}
          </Tooltip>
          <Tooltip title={t('nav.loop')} placement="top">
            {navBtn('footer-loop', <SyncOutlined style={FOOTER_ICON} />,
                    t('nav.loop'), () => navigate('/loop'))}
          </Tooltip>
          <Tooltip title={t('nav.trace')} placement="top">
            {navBtn('footer-trace', <BranchesOutlined style={FOOTER_ICON} />,
                    t('nav.trace'), () => navigate('/trace'))}
          </Tooltip>
          <Tooltip title={t('nav.projects')} placement="top">
            {navBtn('footer-projects', <ProjectOutlined style={FOOTER_ICON} />,
                    t('nav.projects'), () => navigate('/projects'))}
          </Tooltip>
          <Tooltip title={t('nav.dashboard')} placement="top">
            {navBtn('footer-dashboard', <DashboardOutlined style={FOOTER_ICON} />,
                    t('nav.dashboard'), () => navigate('/dashboard'))}
          </Tooltip>
        </div>

        {/* A preference, not a destination — same bar treatment as Advanced. */}
        <Tooltip
          title={t(mode === 'dark' ? 'shell.sidebar.switchToLight'
                                   : 'shell.sidebar.switchToDark')}
          placement="top"
        >
          <button
            type="button"
            data-testid="footer-theme"
            onClick={toggle}
            style={barBtn}
            onMouseEnter={barHover(true)}
            onMouseLeave={barHover(false)}
          >
            {mode === 'dark' ? <SunOutlined style={FOOTER_ICON} />
                             : <MoonOutlined style={FOOTER_ICON} />}
            <span style={{ ...LABEL, marginInlineStart: 2 }}>
              {t(mode === 'dark' ? 'shell.sidebar.light' : 'shell.sidebar.dark')}
            </span>
          </button>
        </Tooltip>
      </div>
    </div>
  );
};

const ProjectRow: React.FC<{
  project: Project;
  active: boolean;
  onClick: () => void;
  onDelete: () => void;
}> = ({ project, active, onClick, onDelete }) => {
  const t = useT();
  const tokens = useThemeTokens();
  const [hover, setHover] = useState(false);
  // stopPropagation so clicking the delete icon doesn't also
  // select the project. Popconfirm handles the confirm UI.
  // The signature accepts both React.MouseEvent (Button onClick) and
  // MouseEvent (Popconfirm's onConfirm) since antd v5 uses the native
  // type for the latter.
  const stop = (e?: unknown) => {
    const evt = e as { stopPropagation?: () => void } | undefined;
    if (evt && typeof evt.stopPropagation === 'function') {
      evt.stopPropagation();
    }
  };
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
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        data-testid={`project-row-${project.id}`}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '7px 10px', borderRadius: 8,
          cursor: 'pointer',
          background: active ? tokens.bgLay2 : 'transparent',
          color: active ? tokens.labelPrimary : tokens.labelSecondary,
          transition: 'background 0.12s',
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
        {/* Delete button — only visible on hover (or when active).
            Uses Popconfirm so the user gets one extra click before
            the project is gone. */}
        <Popconfirm
          title={t('shell.sidebar.deleteProjectTitle', { name: project.name || project.id })}
          description={t('shell.sidebar.deleteProjectDescription')}
          okText={t('common.delete')}
          okType="danger"
          cancelText={t('common.cancel')}
          onConfirm={(e) => { stop(e); onDelete(); }}
          onCancel={stop}
        >
          <Button
            type="text"
            size="small"
            icon={<DeleteOutlined />}
            onClick={stop}
            data-testid={`project-delete-${project.id}`}
            aria-label={t('shell.sidebar.deleteProjectAria', { name: project.name || project.id })}
            style={{
              color: tokens.labelTertiary,
              opacity: hover || active ? 1 : 0,
              transition: 'opacity 0.12s, color 0.12s',
              padding: '0 4px', height: 22, minWidth: 22,
            }}
            onMouseEnter={(e) => { e.currentTarget.style.color = tokens.danger; }}
            onMouseLeave={(e) => { e.currentTarget.style.color = tokens.labelTertiary; }}
          />
        </Popconfirm>
      </div>
    </Tooltip>
  );
};

const SessionRow: React.FC<{
  session: LoopSession;
  active: boolean;
  onClick: () => void;
}> = ({ session, active, onClick }) => {
  const t = useT();
  const tokens = useThemeTokens();
  const title = useMemo(() => {
    if (!session.round_count) {
      return session.running
        ? t('shell.sidebar.sessionStarting')
        : t('shell.sidebar.sessionEmpty');
    }
    return session.round_count === 1
      ? t('shell.sidebar.sessionLoopRound', { n: session.round_count })
      : t('shell.sidebar.sessionLoopRounds', { n: session.round_count });
  }, [session, t]);

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
            {t('shell.sidebar.roundScore', { round: session.last_round, score: session.last_score })}
          </div>
        )}
      </div>
    </div>
  );
};

interface Group { label: string; items: LoopSession[]; }

/**
 * Date buckets in display order. `id` is an internal, never-rendered
 * key; `labelKey` is resolved through `t` so the group headers follow
 * the UI language (buckets must not carry display text).
 */
const DATE_BUCKETS: { id: string; labelKey: string }[] = [
  { id: 'today', labelKey: 'shell.sidebar.today' },
  { id: 'yesterday', labelKey: 'shell.sidebar.groupYesterday' },
  { id: 'last7', labelKey: 'shell.sidebar.groupPrevious7Days' },
  { id: 'older', labelKey: 'shell.sidebar.groupOlder' },
];

function groupByDate(sessions: LoopSession[], t: TFunc): Group[] {
  const now = Date.now() / 1000;
  const oneDay = 24 * 3600;
  const buckets: Record<string, LoopSession[]> = {
    today: [], yesterday: [], last7: [], older: [],
  };
  for (const s of sessions) {
    const age = now - (s.last_activity || s.started_at || 0);
    if (age < oneDay) buckets.today.push(s);
    else if (age < 2 * oneDay) buckets.yesterday.push(s);
    else if (age < 7 * oneDay) buckets.last7.push(s);
    else buckets.older.push(s);
  }
  return DATE_BUCKETS
    .filter((b) => buckets[b.id].length > 0)
    .map((b) => ({ label: t(b.labelKey), items: buckets[b.id] }));
}

export default ChatSidebar;

