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
import { useNavigate, useParams, useLocation } from 'react-router-dom';
import { Button, Spin, Empty, Tooltip, Popconfirm, Input, App as AntdApp } from 'antd';
import {
  MessageOutlined, ThunderboltOutlined,
  CheckCircleFilled, CloseCircleFilled, DownOutlined,
  UpOutlined, ProjectOutlined, DeleteOutlined,
  AppstoreOutlined, ToolOutlined, SettingOutlined,
  SunOutlined, MoonOutlined, ShopOutlined,
  HistoryOutlined, DashboardOutlined, BranchesOutlined, SyncOutlined,
  FieldTimeOutlined, FileTextOutlined,
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

  // R38.10: rename in place — double-click the name, type, Enter. Optimistic:
  // the list (and the chat header, if it is the current project) update at once
  // and roll back if the write fails.
  const renameProject = async (p: Project, name: string) => {
    const previous = projects;
    setProjects(projects.map((x) => (x.id === p.id ? { ...x, name } : x)));
    if (currentProject?.id === p.id) setCurrentProject({ ...currentProject, name });
    try {
      await api.patch(`/projects/${p.id}`, { name });
    } catch (e: any) {
      setProjects(previous);
      if (currentProject?.id === p.id) setCurrentProject(currentProject);
      msgApi.error(e?.response?.data?.detail || t('shell.sidebar.renameFailed'));
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
        {projects.length > 0 && (
          <div data-testid="project-list" style={{ marginBottom: 12 }}>
            <div data-testid="project-list-header" style={{
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
                onRename={(name) => renameProject(p, name)}
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
/**
 * Route match for a rail cell: the route itself, or a child of it
 * (`/trace/<project>` keeps Trace lit). `/` is not a destination — the
 * router redirects it to `/run`.
 */
function isActive(to: string, pathname: string): boolean {
  return pathname === to || pathname.startsWith(`${to}/`);
}

/**
 * A labelled group of rail cells. The label is what turns nine equal-weight
 * icons into three readable questions ("where do I work / what is installed /
 * what is the state of things"), and one grid for every group keeps the
 * column edges aligned down the whole rail.
 */
const NavGroup: React.FC<{
  label: string;
  hint?: string;
  children: React.ReactNode;
}> = ({ label, hint, children }) => {
  const tokens = useThemeTokens();
  return (
    <div>
      <div
        data-testid="footer-group-label"
        title={hint}
        style={{
          fontSize: 10,
          letterSpacing: '0.06em',
          textTransform: 'uppercase',
          color: tokens.labelTertiary,
          padding: '0 4px 4px',
          userSelect: 'none',
        }}
      >
        {label}
      </div>
      <div
        style={{
          display: 'grid',
          // Three columns, not four: a cell here is ~72px, which fits the
          // longest labels in any language ("Marketplace", "Dashboard"). Four
          // columns gave ~52px and truncated them — the grid has to be sized by
          // the longest word it must hold, not by how many items look tidy.
          gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
          gap: 4,
        }}
      >
        {children}
      </div>
    </div>
  );
};

export const SidebarFooter: React.FC = () => {
  const t = useT();
  const tokens = useThemeTokens();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const mode = useThemeStore((s) => s.mode);
  const toggle = useThemeStore((s) => s.toggle);
  const openSettings = useSettingsStore((s) => s.openDrawer);

  // Match the visual weight of the existing ProjectRow / SessionRow
  // buttons (padding 7px 10px, borderRadius 8, fontSize 13). This
  // keeps the footer feeling native to the sidebar instead of a
  // generic "settings" panel.
  // One icon size and one cell for the whole rail: the old footer mixed
  // 12/14px icons, 3- and 4-column rows (61-85px) and two different button
  // chromes, which is what made it read as an even smear of nine icons.
  const FOOTER_ICON = { fontSize: 14 } as const;
  const LABEL = {
    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
  } as const;

  // A destination: equal column, icon over label, hover wash.
  //
  // The label sits *below* the icon rather than beside it. Side by side, a
  // four-column cell in this sidebar is ~52px, and an icon plus "全部项目" does
  // not fit in 52px — so the rail truncated its own labels (a screenshot check
  // caught "全…" where "全部项目" was meant). Stacked, the label gets the whole
  // cell width, and the grid stays a grid instead of a flex row that happens to
  // align at the left edge.
  const baseBtn = {
    padding: '6px 2px',
    borderRadius: 8,
    background: 'transparent',
    border: 'none',
    color: tokens.labelSecondary,
    fontSize: 11,
    lineHeight: 1.2,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 3,
    minWidth: 0,
    cursor: 'pointer',
    transition: 'background 0.12s, color 0.12s',
  } as const;

  // The cell the user is standing on. The inset bar is the same language
  // ProjectRow uses for the active project — one rule for "you are here" —
  // and `aria-current` carries the same fact to screen readers.
  const activeBtn = {
    background: tokens.bgLay2,
    boxShadow: `inset 2px 0 0 ${tokens.labelPrimary}`,
    color: tokens.labelPrimary,
    fontWeight: 600,
  } as const;

  const navRow = (
    testId: string,
    icon: React.ReactNode,
    label: string,
    /** Route to navigate to; `null` for a control that only opens something. */
    to: string | null,
    onClick?: () => void,
  ) => {
    const active = to !== null && isActive(to, pathname);
    return (
      <button
        type="button"
        data-testid={testId}
        title={label}
        aria-current={active ? 'page' : undefined}
        onClick={() => { onClick?.(); if (to) navigate(to); }}
        style={active ? { ...baseBtn, ...activeBtn } : baseBtn}
        onMouseEnter={(e) => {
          if (!active) e.currentTarget.style.background = tokens.bgLay2;
        }}
        onMouseLeave={(e) => {
          if (!active) e.currentTarget.style.background = 'transparent';
        }}
      >
        {icon}
        <span style={LABEL}>{label}</span>
      </button>
    );
  };

  return (
    <div
      data-testid="sidebar-footer"
      style={{
        borderTop: `1px solid ${tokens.border}`,
        marginTop: 8,
        paddingTop: 8,
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
      }}
    >
      {/* Destination groups. R38.13: the old footer hid six destinations
          behind an "Advanced" disclosure and gave two of its four rows a
          different shape, so the rail read as nine equal-weight icons and
          still couldn't answer "where am I?". Now: three labelled groups,
          one uniform 4-column grid, and the current route marked. */}
      <NavGroup label={t('nav.sectionNavigate')} hint={t('nav.sectionNavigateHint')}>
        {navRow('footer-run', <ThunderboltOutlined style={FOOTER_ICON} />,
                t('nav.run'), '/run')}
        {navRow('footer-history', <HistoryOutlined style={FOOTER_ICON} />,
                t('nav.history'), '/history')}
        {navRow('footer-chat', <MessageOutlined style={FOOTER_ICON} />,
                t('nav.chat'), '/chat')}
        {navRow('footer-tasks', <FieldTimeOutlined style={FOOTER_ICON} />,
                t('nav.tasks'), '/tasks')}
        {navRow('footer-artifacts', <FileTextOutlined style={FOOTER_ICON} />,
                t('nav.artifacts'), '/artifacts')}
      </NavGroup>

      <NavGroup label={t('nav.sectionExtensions')} hint={t('nav.sectionExtensionsHint')}>
        {navRow('footer-tools', <ToolOutlined style={FOOTER_ICON} />,
                t('shell.sidebar.tools'), '/tools')}
        {navRow('footer-marketplace', <ShopOutlined style={FOOTER_ICON} />,
                t('nav.marketplace'), '/marketplace')}
        {navRow('footer-trace', <BranchesOutlined style={FOOTER_ICON} />,
                t('nav.trace'), '/trace')}
      </NavGroup>

      <NavGroup label={t('nav.sectionSystem')} hint={t('nav.sectionSystemHint')}>
        {navRow('footer-today', <AppstoreOutlined style={FOOTER_ICON} />,
                t('shell.sidebar.today'), '/today')}
        {navRow('footer-projects', <ProjectOutlined style={FOOTER_ICON} />,
                t('nav.projects'), '/projects')}
        {navRow('footer-loop', <SyncOutlined style={FOOTER_ICON} />,
                t('nav.loop'), '/loop')}
        {navRow('footer-dashboard', <DashboardOutlined style={FOOTER_ICON} />,
                t('nav.dashboard'), '/dashboard')}
      </NavGroup>

      {/* Settings and the theme switch share the same grid as everything
          else: they are one click, and pretending they need a different
          chrome was the thing that made the footer look uneven. */}
      <NavGroup label={t('nav.sectionPreferences')} hint={t('nav.sectionPreferencesHint')}>
        {navRow('footer-settings', <SettingOutlined style={FOOTER_ICON} />,
                t('common.settings'), null, openSettings)}
        <button
          type="button"
          data-testid="footer-theme"
          onClick={toggle}
          aria-label={t(mode === 'dark' ? 'shell.sidebar.switchToLight'
                                        : 'shell.sidebar.switchToDark')}
          title={t(mode === 'dark' ? 'shell.sidebar.switchToLight'
                                   : 'shell.sidebar.switchToDark')}
          style={baseBtn}
          onMouseEnter={(e) => { e.currentTarget.style.background = tokens.bgLay2; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
        >
          {mode === 'dark' ? <SunOutlined style={FOOTER_ICON} />
                           : <MoonOutlined style={FOOTER_ICON} />}
          <span style={LABEL}>
            {t(mode === 'dark' ? 'shell.sidebar.light' : 'shell.sidebar.dark')}
          </span>
        </button>
      </NavGroup>
    </div>
  );
};

const ProjectRow: React.FC<{
  project: Project;
  active: boolean;
  onClick: () => void;
  onDelete: () => void;
  /** R38.10: double-click the name to edit it in place. */
  onRename: (name: string) => void;
}> = ({ project, active, onClick, onDelete, onRename }) => {
  const t = useT();
  const tokens = useThemeTokens();
  const [hover, setHover] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(project.name || '');
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
  const startEdit = () => {
    setDraft(project.name || '');
    setEditing(true);
  };
  const commit = () => {
    if (!editing) return;
    setEditing(false);
    const next = draft.trim();
    if (!next || next === (project.name || '')) return;   // nothing to do
    onRename(next);
  };
  const cancel = () => {
    setEditing(false);
    setDraft(project.name || '');
  };
  return (
    <Tooltip
      title={project.description && project.description !== project.name
             ? project.description : undefined}
      placement="right"
    >
      <div
        onClick={onClick}
        onDoubleClick={(e) => { stop(e); startEdit(); }}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => { if (e.key === 'Enter' && !editing) onClick(); }}
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
        {editing ? (
          <Input
            autoFocus
            size="small"
            value={draft}
            maxLength={120}
            data-testid={`project-rename-input-${project.id}`}
            onClick={stop}
            onDoubleClick={stop}
            onChange={(e) => setDraft(e.target.value)}
            onPressEnter={commit}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === 'Escape') { e.stopPropagation(); cancel(); }
            }}
            style={{ flex: 1, minWidth: 0, fontSize: 13, height: 24 }}
          />
        ) : (
          <div
            data-testid={`project-name-${project.id}`}
            title={t('shell.sidebar.renameHint')}
            style={{
              flex: 1, minWidth: 0,
              fontSize: 13, fontWeight: active ? 600 : 500,
              whiteSpace: 'nowrap', overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {project.name || project.id}
          </div>
        )}
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

