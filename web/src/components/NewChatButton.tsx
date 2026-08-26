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
  MessageOutlined,
} from '@ant-design/icons';

import { useChatStore } from '../stores/chatStore';
import { useThemeTokens } from '../hooks/useThemeTokens';
import FolderPicker from './FolderPicker';

const NewChatButton: React.FC = () => {
  const tokens = useThemeTokens();
  const navigate = useNavigate();
  const { message: msgApi } = AntdApp.useApp();
  const currentProject = useChatStore((s) => s.currentProject);
  const setCurrentSessionId = useChatStore((s) => s.setCurrentSessionId);
  const [folderOpen, setFolderOpen] = useState(false);

  const startNewSession = () => {
    if (!currentProject) {
      // No project → no chat possible. Open the folder picker.
      setFolderOpen(true);
      return;
    }
    setCurrentSessionId(null);
    navigate('/chat');
  };

  const openFolder = () => {
    setFolderOpen(true);
  };

  // ----- "No project" mode: single "Add folder" CTA -----
  if (!currentProject) {
    return (
      <>
        <Button
          type="primary"
          icon={<FolderOpenOutlined />}
          onClick={openFolder}
          block
          style={{
            background: tokens.labelPrimary, color: tokens.bgBase,
            border: 'none', fontWeight: 500,
            marginBottom: 12,
          }}
        >
          Add folder to start
        </Button>
        <FolderPicker
          open={folderOpen}
          onClose={() => setFolderOpen(false)}
        />
      </>
    );
  }

  // ----- "Project exists" mode: button + chevron dropdown -----
  const menuItems = [
    {
      key: 'new_session',
      icon: <MessageOutlined />,
      label: 'New chat (in current project)',
      onClick: startNewSession,
    },
    {
      key: 'new_project',
      icon: <FolderOpenOutlined />,
      label: 'New project from folder…',
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
