/** Tests for the NewChatButton (merged "New chat" + "New project" CTA).
 *
 * Two modes:
 *   - No project → single "Add folder to start" button that opens
 *     the FolderPicker modal.
 *   - With project → "New chat" button + dropdown chevron with two
 *     items: "New chat (in current project)" and
 *     "New project from folder…".
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

// Spy on the FolderPicker's modal-open hook. We render the real
// FolderPicker (it has its own Modal + recents) but track the call
// by the antd Modal's data-attribute.
vi.mock('../components/FolderPicker', () => ({
  default: (props: { open?: boolean; onClose?: () => void }) => (
    <div data-testid="folder-picker-mock"
         data-open={props.open ? 'true' : 'false'}>
      <button onClick={props.onClose}>close</button>
    </div>
  ),
}));

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

  it('shows "Add folder to start" when no project is selected', () => {
    renderInRouter(<NewChatButton />);
    expect(screen.getByText(/Add folder to start/)).toBeInTheDocument();
    // No "New chat" yet — that requires a project.
    expect(screen.queryByText('New chat')).not.toBeInTheDocument();
  });

  it('clicking "Add folder to start" opens the FolderPicker', () => {
    renderInRouter(<NewChatButton />);
    fireEvent.click(screen.getByText(/Add folder to start/));
    // The mocked FolderPicker's data-open should flip to true.
    expect(screen.getByTestId('folder-picker-mock').getAttribute('data-open'))
      .toBe('true');
  });

  it('shows the split button + chevron when a project exists', () => {
    useChatStore.getState().setProjects([fakeProject]);
    useChatStore.getState().setCurrentProject(fakeProject);
    renderInRouter(<NewChatButton />);
    expect(screen.getByText('New chat')).toBeInTheDocument();
    // The chevron button has aria-label.
    expect(screen.getByLabelText('New chat options')).toBeInTheDocument();
  });

  it('clicking the "New chat" button starts a new session in the current project', () => {
    useChatStore.getState().setProjects([fakeProject]);
    useChatStore.getState().setCurrentProject(fakeProject);
    useChatStore.getState().setCurrentSessionId('old-session');
    renderInRouter(<NewChatButton />);
    fireEvent.click(screen.getByText('New chat'));
    // currentSessionId is cleared; the page navigates to /chat.
    expect(useChatStore.getState().currentSessionId).toBeNull();
  });

  it('clicking the chevron opens the dropdown with the two options', async () => {
    useChatStore.getState().setProjects([fakeProject]);
    useChatStore.getState().setCurrentProject(fakeProject);
    renderInRouter(<NewChatButton />);
    fireEvent.click(screen.getByLabelText('New chat options'));
    // AntD renders the dropdown menu in a portal — it should appear
    // on the next tick.
    await waitFor(() => {
      expect(screen.getByText(/New chat \(in current project\)/))
        .toBeInTheDocument();
    });
    expect(screen.getByText(/New project from folder/)).toBeInTheDocument();
  });

  it('dropdown "New project from folder…" opens the FolderPicker', async () => {
    useChatStore.getState().setProjects([fakeProject]);
    useChatStore.getState().setCurrentProject(fakeProject);
    renderInRouter(<NewChatButton />);
    fireEvent.click(screen.getByLabelText('New chat options'));
    await waitFor(() => {
      expect(screen.getByText(/New project from folder/))
        .toBeInTheDocument();
    });
    fireEvent.click(screen.getByText(/New project from folder/));
    await waitFor(() => {
      expect(screen.getByTestId('folder-picker-mock').getAttribute('data-open'))
        .toBe('true');
    });
  });
});
