/**
 * R38.10 — double-click a project name in the sidebar to rename it.
 *
 * The four behaviours that matter: the input opens on double-click, Enter saves
 * (optimistically, then PATCHes), Escape cancels without a request, and an empty
 * name is never sent.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { MemoryRouter } from 'react-router-dom';

import ChatSidebar from '../components/ChatSidebar';
import { useChatStore } from '../stores/chatStore';
import type { Project } from '../types';

const patch = vi.fn((_url: string, _body: unknown) =>
  Promise.resolve({ data: { id: 'p1', name: 'x' } }));
vi.mock('../api/client', () => ({
  default: {
    get: vi.fn((url: string) => Promise.resolve({
      data: typeof url === 'string' && url.includes('/sessions')
        ? { sessions: [] } : {},
    })),
    post: vi.fn(() => Promise.resolve({ data: {} })),
    delete: vi.fn(() => Promise.resolve({ data: {} })),
    patch: (url: string, body: unknown) => patch(url, body),
  },
  connectWebSocket: vi.fn(),
}));

function makeProject(id: string, name: string): Project {
  return {
    id, name, description: name, workspace: `/w/${id}`, work_dir: `/w/${id}`,
    status: 'active', task_count: 0, agent_count: 0, created_at: 0,
  };
}

const renderSidebar = () => render(
  <ConfigProvider>
    <MemoryRouter initialEntries={['/chat']}>
      <ChatSidebar />
    </MemoryRouter>
  </ConfigProvider>,
);

describe('project rename (R38.10)', () => {
  beforeEach(() => {
    patch.mockClear();
    useChatStore.setState({
      projects: [makeProject('p1', '未命名')],
      currentProject: makeProject('p1', '未命名'),
      sessions: [],
      currentSessionId: null,
      currentMessages: [],
    } as never);
  });

  it('opens an input on double-click and saves on Enter', async () => {
    renderSidebar();
    fireEvent.doubleClick(screen.getByTestId('project-row-p1'));

    const input = screen.getByTestId('project-rename-input-p1') as HTMLInputElement;
    expect(input.value).toBe('未命名');            // starts from the current name

    fireEvent.change(input, { target: { value: '抽卡拉到最底' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(patch).toHaveBeenCalledWith(
      '/projects/p1', { name: '抽卡拉到最底' }));
    // The list updates without waiting for the response.
    expect(screen.queryByTestId('project-rename-input-p1')).toBeNull();
    expect(screen.getByTestId('project-name-p1').textContent).toBe('抽卡拉到最底');
  });

  it('cancels on Escape and never calls the API', async () => {
    renderSidebar();
    fireEvent.doubleClick(screen.getByTestId('project-row-p1'));
    const input = screen.getByTestId('project-rename-input-p1') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '不要这个名字' } });
    fireEvent.keyDown(input, { key: 'Escape' });

    expect(screen.queryByTestId('project-rename-input-p1')).toBeNull();
    expect(screen.getByTestId('project-name-p1').textContent).toBe('未命名');
    expect(patch).not.toHaveBeenCalled();
  });

  it('never sends an empty name', async () => {
    renderSidebar();
    fireEvent.doubleClick(screen.getByTestId('project-row-p1'));
    const input = screen.getByTestId('project-rename-input-p1') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '   ' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(screen.queryByTestId('project-rename-input-p1')).toBeNull());
    expect(patch).not.toHaveBeenCalled();
    expect(screen.getByTestId('project-name-p1').textContent).toBe('未命名');
  });

  it('does not call the API when the name did not change', async () => {
    renderSidebar();
    fireEvent.doubleClick(screen.getByTestId('project-row-p1'));
    const input = screen.getByTestId('project-rename-input-p1') as HTMLInputElement;
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(screen.queryByTestId('project-rename-input-p1')).toBeNull());
    expect(patch).not.toHaveBeenCalled();
  });
});
