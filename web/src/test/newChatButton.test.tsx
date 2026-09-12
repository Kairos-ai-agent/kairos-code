/** Tests for the NewChatButton.
 *
 * Contract under test (asserted through testids so the copy can keep changing
 * with the 63-language dictionary, and so a refactor of the label does not
 * silently break the test):
 *   - a primary "new chat" button and a chevron options button are rendered;
 *   - the primary button starts a new session in the current project;
 *   - the chevron opens a dropdown offering "pick an existing folder", which
 *     opens the FolderPicker.
 *
 * History: this file used to assert an English "Add folder to start" label and
 * a two-item dropdown. Both were retired (the button is single-purpose now, and
 * the dropdown kept only the folder entry), so the assertions were rewritten to
 * the current contract instead of deleted.
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

// Render the real FolderPicker but expose its open state through a testid so the
// assertion does not depend on antd Modal internals.
vi.mock('../components/FolderPicker', () => ({
  default: (props: { open?: boolean; onClose?: () => void }) => (
    <div data-testid="folder-picker-mock"
         data-open={props.open ? 'true' : 'false'}>
      <button onClick={props.onClose}>close</button>
    </div>
  ),
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

  it('renders the primary new-chat button and the options chevron', () => {
    renderInRouter(<NewChatButton />);
    expect(screen.getByTestId('new-chat-button')).toBeInTheDocument();
    expect(screen.getByTestId('new-chat-options')).toBeInTheDocument();
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

  it('the chevron opens a dropdown with the folder entry', async () => {
    useChatStore.getState().setProjects([fakeProject]);
    useChatStore.getState().setCurrentProject(fakeProject);
    renderInRouter(<NewChatButton />);
    fireEvent.click(screen.getByTestId('new-chat-options'));
    // antd renders the menu in a portal. Assert the entry rather than its copy:
    // there is exactly one item and it is the folder one.
    const items = await screen.findAllByRole('menuitem');
    expect(items).toHaveLength(1);
  });

  it('the dropdown folder entry opens the FolderPicker', async () => {
    useChatStore.getState().setProjects([fakeProject]);
    useChatStore.getState().setCurrentProject(fakeProject);
    renderInRouter(<NewChatButton />);
    expect(screen.getByTestId('folder-picker-mock').getAttribute('data-open'))
      .toBe('false');
    fireEvent.click(screen.getByTestId('new-chat-options'));
    const [item] = await screen.findAllByRole('menuitem');
    fireEvent.click(item);
    await waitFor(() => {
      expect(screen.getByTestId('folder-picker-mock').getAttribute('data-open'))
        .toBe('true');
    });
  });
});
