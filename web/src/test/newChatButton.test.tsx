/** Tests for the NewChatButton.
 *
 * Contract under test (asserted through testids so the copy can keep changing
 * with the 63-language dictionary, and so a refactor of the label does not
 * silently break the test):
 *   - exactly one button is rendered: the primary "new chat" action;
 *   - clicking it starts a brand-new project;
 *   - there is NO chevron/options button. Its dropdown held a single entry,
 *     "pick an existing folder", which duplicated the FolderPicker that
 *     already sits above the chat input (ChatComposer) — the user asked for
 *     the duplicate to go. The folder flow is covered by
 *     folderPicker.test.tsx and the composer.
 *
 * History: this file used to assert an English "Add folder to start" label and
 * a two-item dropdown; both were retired, and now the chevron itself is gone,
 * so its assertions went with it rather than being left to rot.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ConfigProvider, App as AntdApp } from 'antd';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../api/client', () => ({
  default: {
    get: vi.fn((_url?: string) => Promise.resolve({ data: {} })),
    post: vi.fn((_url?: string) => Promise.resolve({ data: {} })),
  },
  connectWebSocket: vi.fn(),
}));

import api from '../api/client';
import NewChatButton from '../components/NewChatButton';
import { useChatStore } from '../stores/chatStore';
import type { Project } from '../types';

const fakeProject: Project = {
  id: 'p1', name: 'demo', description: '', workspace: '/w',
  work_dir: '/w', status: 'active',
  task_count: 0, agent_count: 0, created_at: 1.0,
};

const renderInRouter = (ui: React.ReactElement) => {
  return render(
    <ConfigProvider>
      <AntdApp>
        <MemoryRouter initialEntries={['/chat']}>
          {ui}
        </MemoryRouter>
      </AntdApp>
    </ConfigProvider>,
  );
};

describe('NewChatButton', () => {
  beforeEach(() => {
    useChatStore.getState().reset();
  });

  it('renders only the primary new-chat button — no chevron', () => {
    renderInRouter(<NewChatButton />);
    expect(screen.getByTestId('new-chat-button')).toBeInTheDocument();
    expect(screen.queryByTestId('new-chat-options')).not.toBeInTheDocument();
  });

  it('clicking the primary button starts a brand-new chat project', async () => {
    useChatStore.getState().setProjects([fakeProject]);
    useChatStore.getState().setCurrentProject(fakeProject);
    renderInRouter(<NewChatButton />);
    fireEvent.click(screen.getByTestId('new-chat-button'));
    // R38.6.4: every click creates a fresh project under `<workspace>/`
    // `.kairos_chats/` instead of reusing the current one (the user called
    // the old behaviour surprising when the active project was stale).
    await waitFor(() => {
      expect(vi.mocked(api.post)).toHaveBeenCalledWith(
        '/projects', expect.anything());
    });
  });
});
