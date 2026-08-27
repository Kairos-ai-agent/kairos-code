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
 *      now lives above the chat input (see `ChatComposer.tsx`).
 *      This was a dedup pass: rendering the picker in two places
 *      was confusing, so the composer is the canonical location.
 *
 * The footer is implemented in ChatSidebar (it has the full
 * sidebar context including flex / theming).
 */
import React, { useEffect } from 'react';
import { Outlet, useNavigate } from 'react-router-dom';
import { Layout, Button } from 'antd';
import {
  MenuFoldOutlined, MenuUnfoldOutlined,
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

const { Header, Sider, Content } = Layout;

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
  const settingsOpen = useSettingsStore((s) => s.drawerOpen);
  const openSettings = useSettingsStore((s) => s.openDrawer);
  const closeSettings = useSettingsStore((s) => s.closeDrawer);

  // First-time projects load. Done once at mount.
  useEffect(() => {
    api.get<{ projects: Project[] }>('/projects').then((r) => {
      setProjects(r.data.projects || []);
      const list = r.data.projects || [];
      if (list.length > 0 && !useChatStore.getState().currentProject) {
        setCurrentProject(list[0]);
      }
    }).catch(() => { /* offline / first paint — sidebar will retry */ });
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
          <span
            aria-hidden
            style={{
              width: 24, height: 24, borderRadius: 6,
              background: tokens.labelPrimary, color: tokens.bgBase,
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 13, fontWeight: 700,
            }}
          >K</span>
          Kairos
        </div>
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
      </Layout>

      <SettingsDrawer open={settingsOpen} onClose={closeSettings} />
    </Layout>
  );
};

export default AppLayout;
