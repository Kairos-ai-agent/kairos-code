/**
 * AppLayout — the top-level shell.
 *
 * Layout (ChatGPT beta / DSH Desktop style):
 *
 *   ┌──────────────────────────────────────────────────────────┐
 *   │  Topbar: logo · project picker · spacer · theme · ⚙       │  52px
 *   ├────────────┬─────────────────────────────────────────────┤
 *   │            │                                             │
 *   │  Sidebar   │              Main (Outlet)                  │
 *   │  (chat     │                                             │
 *   │  history)  │                                             │
 *   │            │                                             │
 *   │  280px     │             flex 1                          │
 *   │            │                                             │
 *   └────────────┴─────────────────────────────────────────────┘
 *
 * The Topbar is always visible (no scrolling). The Sidebar is
 * collapsible — when collapsed, a thin 0-width strip remains so the
 * menu toggle in the topbar can re-open it. The Main area scrolls
 * independently (chat thread + composer inside the page).
 */
import React, { useEffect, useState } from 'react';
import { Outlet, useNavigate, useLocation, NavLink } from 'react-router-dom';
import { Layout, Button, Dropdown, Select, Tooltip, Avatar, theme } from 'antd';
import {
  MenuFoldOutlined, MenuUnfoldOutlined,
  SunOutlined, MoonOutlined, SettingOutlined,
  PlusOutlined, AppstoreOutlined, MessageOutlined,
  GithubOutlined, BookOutlined, ToolOutlined,
} from '@ant-design/icons';

import { useThemeStore } from '../stores/themeStore';
import { useChatStore } from '../stores/chatStore';
import { useThemeTokens } from '../hooks/useThemeTokens';
import { LAYOUT } from '../styles/theme';
import api from '../api/client';
import type { Project } from '../types';
import ChatSidebar from './ChatSidebar';
import FolderPicker from './FolderPicker';

const { Header, Sider, Content } = Layout;

const AppLayout: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const tokens = useThemeTokens();
  const mode = useThemeStore((s) => s.mode);
  const toggle = useThemeStore((s) => s.toggle);
  const projects = useChatStore((s) => s.projects);
  const setProjects = useChatStore((s) => s.setProjects);
  const currentProject = useChatStore((s) => s.currentProject);
  const setCurrentProject = useChatStore((s) => s.setCurrentProject);
  const sidebarCollapsed = useChatStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useChatStore((s) => s.toggleSidebar);

  // First-time projects load. Done once at mount.
  useEffect(() => {
    api.get<{ projects: Project[] }>('/projects').then((r) => {
      setProjects(r.data.projects || []);
      // If we don't yet have a current project, default to the most
      // recent one. The chat sidebar / chat page will pick it up.
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

        <ProjectPicker
          value={currentProject?.id}
          onChange={(id) => {
            const p = projects.find((x) => x.id === id) || null;
            setCurrentProject(p);
            navigate('/chat');
          }}
        />

        <FolderPicker />

        <div style={{ flex: 1 }} />

        <Tooltip title="Today">
          <Button
            type="text"
            icon={<AppstoreOutlined />}
            onClick={() => navigate('/today')}
            style={{ color: tokens.labelSecondary }}
          />
        </Tooltip>
        <Tooltip title="Tools">
          <Button
            type="text"
            icon={<ToolOutlined />}
            onClick={() => navigate('/tools')}
            style={{ color: tokens.labelSecondary }}
          />
        </Tooltip>
        <Tooltip title={mode === 'dark' ? 'Switch to light' : 'Switch to dark'}>
          <Button
            type="text"
            onClick={toggle}
            style={{
              color: tokens.labelSecondary,
              display: 'inline-flex', alignItems: 'center', gap: 6,
              fontSize: 13,
            }}
            data-testid="theme-toggle"
          >
            {mode === 'dark' ? <SunOutlined /> : <MoonOutlined />}
            <span style={{ fontWeight: 500 }}>
              {mode === 'dark' ? 'Light' : 'Dark'}
            </span>
          </Button>
        </Tooltip>
        <Dropdown
          menu={{
            items: [
              { key: 'settings', icon: <SettingOutlined />,
                label: 'Settings', onClick: () => navigate('/settings') },
              { key: 'new', icon: <PlusOutlined />,
                label: 'New project', onClick: () => navigate('/projects') },
            ],
          }}
        >
          <Button type="text" style={{ color: tokens.labelSecondary }}>
            <Avatar size={26} style={{ background: tokens.labelPrimary,
                                        color: tokens.bgBase, fontSize: 12 }}>
              {(currentProject?.name || '?').charAt(0).toUpperCase()}
            </Avatar>
          </Button>
        </Dropdown>
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
    </Layout>
  );
};

const ProjectPicker: React.FC<{
  value?: string;
  onChange: (id: string) => void;
}> = ({ value, onChange }) => {
  const projects = useChatStore((s) => s.projects);
  const tokens = useThemeTokens();
  return (
    <Select
      value={value}
      onChange={onChange}
      variant="borderless"
      style={{ minWidth: 180, color: tokens.labelPrimary }}
      placeholder="Select a project"
      options={projects.map((p) => ({
        value: p.id, label: p.name || p.id,
      }))}
    />
  );
};

export default AppLayout;
