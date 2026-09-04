/**
 * NewChatButton — the primary "New chat" CTA at the top of the sidebar.
 *
 * Behaviour is context-sensitive:
 *   - **Current project exists** → primary click starts a fresh session
 *     in that project. Right-side chevron opens a dropdown with
 *     "New project from folder…" so the user can also pick a new
 *     folder to start fresh.
 *   - **No project yet** → the button becomes a single "Add a folder to
 *     start" CTA that opens the FolderPicker modal directly. (The
 *     dropdown would be empty otherwise.)
 *
 * The "New project from folder" item in the dropdown is the new home
 * for the create-project flow that used to live in the avatar menu
 * ("New project → /projects"). The user wanted those two entry points
 * merged into one — the "New chat" button — so creating a project is
 * a one-click action when no project exists, and a one-click option
 * from the dropdown when one already does.
 */
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, Dropdown, App as AntdApp, Tooltip } from 'antd';
import {
  PlusOutlined, DownOutlined, FolderOpenOutlined,
} from '@ant-design/icons';

import api from '../api/client';
import { useChatStore } from '../stores/chatStore';
import { useThemeTokens } from '../hooks/useThemeTokens';
import FolderPicker from './FolderPicker';

const NewChatButton: React.FC = () => {
  const tokens = useThemeTokens();
  const navigate = useNavigate();
  const { message: msgApi } = AntdApp.useApp();
  const [folderOpen, setFolderOpen] = useState(false);

  const startNewSession = async () => {
    // R38.6.4: every "New chat" click creates a brand-new project
    // in a default directory and starts a fresh thread. We no
    // longer reuse the current project — the user said the old
    // "new session in current project" behavior was surprising
    // and made the chat page unresponsive when the active project
    // was stale. The new flow:
    //   1. POST /api/projects to create a project in
    //      `<workspace>/.kairos_chats/chat_<timestamp>` (the
    //      backend auto-creates the folder if missing).
    //   2. setCurrentProject + setCurrentMessages([]) in the
    //      store (so the in-memory thread starts clean for the
    //      new project).
    //   3. window.location.assign('/chat') to land on a fresh
    //      <Chat /> with the right project.
    const fresh = useChatStore.getState();
    console.info('[NewChatButton] startNewSession clicked');
    try {
      // Use the first fixed parent the backend accepts. The
      // orchestrator's default workspace is `./workspace`, so
      // child `.kairos_chats` is reserved for ad-hoc new chats.
      const ts = Date.now();
      const workDir = `./workspace/.kairos_chats/chat_${ts}`;
      const r = await api.post<{ id: string; name: string }>(
        '/projects',
        {
          // R38.6.4: name is "Untitled" — no preset topic. The user
          // types the first message; a future enhancement will
          // rename the project to that (or a summary of it). The
          // directory name (work_dir) is still timestamped so two
          // back-to-back "New chat" clicks don't collide on disk.
          name: 'Untitled',
          description: 'Created by New chat button; rename after typing.',
          work_dir: workDir,
        }
      );
      const newProject = r.data;
      console.info('[NewChatButton] created project',
                   newProject.id, 'at', workDir);
      // R38.6.4: also append to the projects list so the sidebar
      // shows the new project after reload. Without this the
      // sidebar still shows the old project (e.g. "123") and
      // FolderPicker still displays the old work_dir.
      const existing = fresh.projects || [];
      const merged = existing.some((p) => p.id === newProject.id)
                      ? existing
                      : [...existing, newProject];
      fresh.setProjects(merged);
      fresh.setCurrentProject(newProject);
      fresh.setCurrentSessionId(null);
      if (typeof window !== 'undefined') {
        window.location.assign('/chat');
      } else {
        navigate('/chat', { replace: true });
      }
    } catch (e: any) {
      console.error('[NewChatButton] create project failed:', e);
      const detail = e?.response?.data?.detail || e?.message || 'failed';
      msgApi.error(`Failed to start new chat: ${detail}`);
    }
  };

  const openFolder = () => {
    setFolderOpen(true);
  };

  // R38.6.4: the button is now single-purpose — every click creates
  // a new project in the default directory. The chevron dropdown
  // only has the "Pick existing folder" entry, since picking an
  // existing folder is the only remaining alternative workflow.
  const menuItems = [
    {
      key: 'new_project',
      icon: <FolderOpenOutlined />,
      label: 'Pick existing folder…',
      onClick: openFolder,
    },
  ];

  return (
    <div style={{ display: 'flex', gap: 4, marginBottom: 12 }}>
      <Button
        type="primary"
        icon={<PlusOutlined />}
        onClick={startNewSession}
        block
        style={{
          background: tokens.labelPrimary, color: tokens.bgBase,
          border: 'none', fontWeight: 500,
        }}
      >
        New chat
      </Button>
      <Dropdown
        menu={{ items: menuItems }}
        trigger={['click']}
        placement="bottomRight"
      >
        <Tooltip title="More new-chat options">
          <Button
            type="primary"
            icon={<DownOutlined />}
            style={{
              background: tokens.labelPrimary, color: tokens.bgBase,
              border: 'none', flex: '0 0 auto',
              padding: '0 10px',
            }}
            aria-label="New chat options"
          />
        </Tooltip>
      </Dropdown>
      <FolderPicker
        open={folderOpen}
        onClose={() => setFolderOpen(false)}
      />
    </div>
  );
};

export default NewChatButton;
