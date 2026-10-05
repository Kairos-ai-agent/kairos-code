/**
 * NewChatButton — the primary "New chat" CTA at the top of the sidebar.
 *
 * One button, one purpose: clicking it creates a fresh project in a default
 * directory and lands you on an empty thread.
 *
 * There used to be a chevron on its right whose dropdown held a single entry,
 * "Pick existing folder…". That duplicated the FolderPicker that already sits
 * above the chat input (see ChatComposer), so the chevron is gone: the folder
 * flow lives in one place, next to where you type.
 */
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Button, App as AntdApp } from 'antd';
import { PlusOutlined } from '@ant-design/icons';

import api from '../api/client';
import { useChatStore } from '../stores/chatStore';
import { useThemeTokens } from '../hooks/useThemeTokens';
import { useT } from '../i18n';
import type { Project } from '../types';

const NewChatButton: React.FC = () => {
  const t = useT();
  const tokens = useThemeTokens();
  const navigate = useNavigate();
  const { message: msgApi } = AntdApp.useApp();

  const startNewSession = async () => {
    // R38.6.4: every "New chat" click creates a brand-new project
    // in a default directory and starts a fresh thread. We do
    // no longer reuse the current project — the user said the old
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
      // The endpoint returns the full project record (kairos/core/
      // orchestrator.py), so type it as one — the sidebar and FolderPicker
      // read work_dir/status/task_count off this object.
      const r = await api.post<Project>(
        '/projects',
        {
          // R38.6.4: name is "Untitled" — no preset topic. The user
          // types the first message; a future enhancement will
          // rename the project to that (or a summary of it). The
          // directory name (work_dir) is still timestamped so two
          // back-to-back "New chat" clicks don't collide on disk.
          name: t('shell.newChat.untitled'),
          description: t('shell.newChat.defaultDescription'),
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
      const detail = e?.response?.data?.detail || e?.message || t('common.failed');
      msgApi.error(t('shell.newChat.failedToStart', { detail }));
    }
  };

  return (
    <div style={{ display: 'flex', gap: 4, marginBottom: 12 }}>
      <Button
        type="primary"
        data-testid="new-chat-button"
        icon={<PlusOutlined />}
        onClick={startNewSession}
        block
        style={{
          background: tokens.labelPrimary, color: tokens.bgBase,
          border: 'none', fontWeight: 500,
        }}
      >
        {t('shell.newChat.newChat')}
      </Button>
    </div>
  );
};

export default NewChatButton;
