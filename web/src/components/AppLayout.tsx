/**
 * AppLayout — the top-level shell (R37 update: bottom-left controls).
 *
 * Layout (minimax-code inspired):
 *
 *   ┌──────────────────────────────────────────────────────────┐
 *   │  ☰  K  Kairos                                            │  ← topbar (minimal)
 *   ├────────────┬─────────────────────────────────────────────┤
 *   │            │                                             │
 *   │  Sidebar   │              Main (Outlet)                  │
 *   │  (chat     │                                             │
 *   │  history)  │                                             │
 *   │            │                                             │
 *   │            │                                             │
 *   ├────────────┴─────────────────────────────────────────────┤
 *   │  Today  Tools                                            │  ← bottom-left footer
 *   │  ⚙ Settings  ☀ Theme                                    │  (continued)
 *   └──────────────────────────────────────────────────────────┘
 *
 * R37 changes (from user feedback):
 *   1. The topbar is now MINIMAL — sidebar toggle + logo only. The
 *      Today / Tools / Settings / Theme controls are moved to a
 *      footer at the bottom of the LEFT rail (so they live in the
 *      same column as the chat history — minimax-code style).
 *   2. The avatar / settings dropdown is GONE from the topbar.
 *   3. The FolderPicker is GONE from the topbar. The project picker
 *      now lives in the bottom-left of the chat composer (see
 *      `ChatComposer.tsx`) — in the action row, next to the
 *      intent-preview chip. This was a dedup pass: rendering the
 *      picker in two places was confusing, so the composer is the
 *      canonical location.
 *
 * The footer is implemented in ChatSidebar (it has the full
 * sidebar context including flex / theming).
 */
import React, { useEffect } from 'react';
import { Outlet, useNavigate } from 'react-router-dom';
import { Layout, Button, Tooltip } from 'antd';
import {
  MenuFoldOutlined, MenuUnfoldOutlined, AppstoreOutlined,
} from '@ant-design/icons';

import { useThemeStore } from '../stores/themeStore';
import { useChatStore } from '../stores/chatStore';
import { useSettingsStore } from '../stores/settingsStore';
import { useThemeTokens } from '../hooks/useThemeTokens';
import { LAYOUT } from '../styles/theme';
import api from '../api/client';
import type { Project } from '../types';
import ChatSidebar from './ChatSidebar';
import { SettingsDrawer } from './SettingsDrawer';
import WorkbenchPanel from './WorkbenchPanel';

const { Header, Sider, Content } = Layout;

/**
 * R38.6 §21: detect whether a provider config looks like the user
 * has actually set it (vs. the default placeholder values shipped
 * in DEFAULT). Used by the AppLayout settings mount-load to
 * decide whether to trust localStorage over a possibly-stale
 * backend response.
 *
 * "Configured" = any of:
 *   - any provider has a non-empty apiKey (the strongest signal)
 *   - any provider has a model that differs from the DEFAULT
 *     (catches users who have set the model but not yet the key)
 *   - any provider has an endpointUrl that differs from the
 *     DEFAULT (catches users pointing at a custom proxy)
 *
 * If none of these hold, the localStorage is just the DEFAULT
 * and the backend should win.
 */
const isProviderConfigured = (p: {
  openai: { apiKey: string; model: string; endpointUrl: string };
  anthropic: { apiKey: string; model: string; endpointUrl: string };
}): boolean => {
  if (p.openai?.apiKey && p.openai.apiKey.length > 0) return true;
  if (p.anthropic?.apiKey && p.anthropic.apiKey.length > 0) return true;
  if (p.openai?.model && p.openai.model !== 'gpt-4o') return true;
  if (p.anthropic?.model && p.anthropic.model !== 'claude-3-5-sonnet-latest') return true;
  if (p.openai?.endpointUrl && p.openai.endpointUrl !== 'https://api.openai.com/v1/chat/completions') return true;
  if (p.anthropic?.endpointUrl && p.anthropic.endpointUrl !== 'https://api.anthropic.com/v1/messages') return true;
  return false;
};

const AppLayout: React.FC = () => {
  const navigate = useNavigate();
  const tokens = useThemeTokens();
  const mode = useThemeStore((s) => s.mode);
  const toggle = useThemeStore((s) => s.toggle);
  const projects = useChatStore((s) => s.projects);
  const setProjects = useChatStore((s) => s.setProjects);
  const currentProject = useChatStore((s) => s.currentProject);
  const setCurrentProject = useChatStore((s) => s.setCurrentProject);
  const sidebarCollapsed = useChatStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useChatStore((s) => s.toggleSidebar);
  const workbenchOpen = useChatStore((s) => s.workbenchOpen);
  const toggleWorkbench = useChatStore((s) => s.toggleWorkbench);
  const settingsOpen = useSettingsStore((s) => s.drawerOpen);
  const openSettings = useSettingsStore((s) => s.openDrawer);
  const closeSettings = useSettingsStore((s) => s.closeDrawer);
  const setProvider = useSettingsStore((s) => s.setProvider);

  // First-time projects load. Done once at mount.
  // R38.6 §19: localStorage is the user's instant-render cache.
  // Backend is the source of truth. We merge them: if the
  // localStorage has FEWER projects than the backend returns, the
  // user has explicitly deleted some that the backend (perhaps
  // stale, pre-§18) still has. Trust the localStorage and only
  // add projects that aren't already there. This way:
  //   - new backend (§18): backend filters archived, list size
  //     matches localStorage, full sync
  //   - old backend: localStorage wins, deleted projects don't
  //     reappear until the user restarts the backend
  useEffect(() => {
    api.get<{ projects: Project[] }>('/projects').then((r) => {
      const backendList = r.data.projects || [];
      const localList = useChatStore.getState().projects;
      // Merge: take the union, but if localStorage has more
      // projects (impossible normally) trust localStorage. If
      // localStorage has fewer, take localStorage (user's intent).
      let merged = backendList;
      if (localList.length > 0 && localList.length < backendList.length) {
        // Backend is returning more than the user has locally.
        // Filter backend to only projects that are also in local.
        // This handles the stale-backend case gracefully.
        const localIds = new Set(localList.map((p) => p.id));
        merged = backendList.filter((p) => localIds.has(p.id));
      } else if (localList.length === 0 && backendList.length > 0) {
        // No localStorage (first visit) — trust backend.
        merged = backendList;
      } else if (localList.length > backendList.length) {
        // localStorage has more than backend. Use localStorage.
        // (E.g. backend is down or returned empty.)
        merged = localList;
      }
      setProjects(merged);
      // R38.6: reconcile stale localStorage state. If the persisted
      // currentProject no longer exists on the backend (because the
      // user moved to a new data dir / new backend), clear it so
      // the composer doesn't fire requests against a 404 project.
      const persisted = useChatStore.getState().currentProject;
      if (persisted && !merged.find((p) => p.id === persisted.id)) {
        setCurrentProject(null);
      }
      // Auto-pick the first project only when there's truly no
      // current selection (after the reconciliation above).
      if (merged.length > 0 && !useChatStore.getState().currentProject) {
        setCurrentProject(merged[0]);
      }
    }).catch(() => { /* offline / first paint — sidebar will retry */ });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // R38.6 §21: load LLM provider settings on mount so the composer
  // model chip shows the user's actual configured model (not the
  // store default) right after page load. Without this, the user
  // sees "gpt-4o" in the chip until they open the Settings
  // drawer, even though their saved setting is "agnes-2.5-flash".
  //
  // The SettingsDrawer also loads these on open, but that path
  // doesn't help the composer on the chat page. Doing it here
  // at the app-shell level means every page sees the right
  // model from the start.
  //
  // The fetch is best-effort: if the backend is unreachable
  // (offline / first paint), the store keeps its defaults and
  // the user can retry by opening the Settings drawer.
  //
  // R38.6 §21: localStorage-first merge — the same pattern as
  // the projects load above. The user reported
  // "llm model设置又丢失了" (LLM model lost again after refresh):
  //   - zustand persist restores from localStorage (good)
  //   - AppLayout mount fetches /api/projects/settings
  //   - if the backend is stale (pre-§14 code) or returning
  //     default-looking data, the setProvider(...) call can
  //     overwrite the user's localStorage with default values,
  //     making the chip flip from "agnes-2.5-flash" back to
  //     "gpt-4o" after the user has already configured it.
  // We defend against this by preferring localStorage whenever
  // it has a configured provider (non-empty apiKey OR non-default
  // model). The backend is only used as the source of truth for
  // fresh installs (no localStorage data yet).
  useEffect(() => {
    api.get('/projects/settings').then((r) => {
      const d = r.data || {};
      if (!d.provider) return;
      // The store was already rehydrated by zustand persist at
      // module load. Compare the localStorage value to the
      // backend's.
      const local = useSettingsStore.getState().provider;
      const localLooksConfigured = isProviderConfigured(local);
      if (localLooksConfigured) {
        // User has explicit config in localStorage. Don't
        // overwrite with a possibly-stale backend response.
        // This is the same defensive pattern as the projects
        // load above: trust the user's most recent state.
        return;
      }
      // No local config (first visit, or user cleared
      // localStorage). Adopt the backend's provider.
      setProvider(d.provider);
    }).catch(() => { /* offline / first paint — keep localStorage */ });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Watch for new projects (e.g. user just created one) and promote
  // them to current when the user is on the new-chat landing page.
  useEffect(() => {
    if (!currentProject && projects.length > 0) {
      setCurrentProject(projects[0]);
    }
  }, [projects, currentProject, setCurrentProject]);

  const sidebarWidth = sidebarCollapsed
    ? LAYOUT.sidebarCollapsedWidth
    : LAYOUT.sidebarWidth;
  // R38.6 §26: right-side Workbench panel width. The Content area
  // shrinks to accommodate it; we don't overlay (which would hide
  // the chat) — minimax-code's right panel pushes the main area.
  const workbenchWidth = workbenchOpen ? 360 : 0;

  return (
    <Layout style={{ minHeight: '100vh', background: tokens.bgBase }}>
      {/* ----- Topbar: minimal — sidebar toggle + logo only ----- */}
      <Header
        style={{
          position: 'sticky', top: 0, zIndex: 100,
          height: LAYOUT.topbarHeight, lineHeight: `${LAYOUT.topbarHeight}px`,
          padding: '0 16px',
          display: 'flex', alignItems: 'center', gap: 12,
          background: tokens.bgElevated,
          borderBottom: `1px solid ${tokens.border}`,
        }}
      >
        <Button
          type="text"
          icon={sidebarCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
          onClick={toggleSidebar}
          aria-label="Toggle sidebar"
          style={{ color: tokens.labelSecondary }}
        />
        <div
          onClick={() => navigate('/')}
          style={{
            display: 'flex', alignItems: 'center', gap: 8,
            cursor: 'pointer', userSelect: 'none',
            fontWeight: 600, fontSize: 15,
            color: tokens.labelPrimary,
          }}
        >
          {/* R38.6 §34: brand K icon. The topbar logo is
              24×24 to fit comfortably next to the 16px
              "Kairos" text. The img element lets the browser
              cache it; we set width/height explicitly so the
              layout doesn't reflow when the image loads. */}
          <img
            src="/branding/kairos-icon-64.png"
            alt="Kairos"
            width={24}
            height={24}
            style={{
              borderRadius: 6,
              display: 'block',
              objectFit: 'cover',
            }}
          />
          Kairos
        </div>
        <div style={{ flex: 1 }} />
        {/* R38.6 §26: topbar button to toggle the right-side
            Workbench panel. Sits at the right edge of the header
            so it doesn't compete with the logo for attention. */}
        <Tooltip title={workbenchOpen ? 'Hide workbench' : 'Show workbench'}>
          <Button
            data-testid="workbench-toggle-topbar"
            type={workbenchOpen ? 'primary' : 'text'}
            icon={<AppstoreOutlined />}
            size="small"
            onClick={toggleWorkbench}
          >
            {workbenchOpen ? 'Workbench' : ''}
          </Button>
        </Tooltip>
      </Header>

      <Layout>
        <Sider
          width={sidebarWidth}
          collapsedWidth={0}
          collapsible={false}
          trigger={null}
          style={{
            background: tokens.bgElevated,
            borderRight: `1px solid ${tokens.border}`,
            transition: 'width 0.18s ease',
            overflow: 'hidden',
          }}
        >
          <ChatSidebar />
        </Sider>
        <Content style={{ background: tokens.bgBase, overflow: 'hidden' }}>
          <Outlet />
        </Content>
        {/* R38.6 §26: right-side Workbench (file tree, changes,
            tasks, deliverables). The panel is always mounted but
            collapses to width 0 when closed (no layout shift on
            toggle). The internal WorkbenchPanel has its own
            collapse toggle for the tab strip itself. */}
        <Sider
          width={workbenchWidth}
          collapsedWidth={0}
          collapsible={false}
          trigger={null}
          style={{
            background: tokens.bgElevated,
            transition: 'width 0.18s ease',
            overflow: 'hidden',
          }}
          data-testid="workbench-sider"
        >
          {workbenchOpen && <WorkbenchPanel />}
        </Sider>
      </Layout>

      <SettingsDrawer open={settingsOpen} onClose={closeSettings} />
    </Layout>
  );
};

export default AppLayout;
