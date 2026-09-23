/** Tests for the project list in ChatSidebar.
 *
 * The project list sits at the top of the sidebar:
 *   - Default 10 items shown
 *   - "Show all (N)" button appears when more than 10
 *   - Clicking a project sets it as current and navigates to /chat
 *   - Active project is highlighted
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { ConfigProvider } from 'antd';
import { MemoryRouter } from 'react-router-dom';

import ChatSidebar from '../components/ChatSidebar';
import { useChatStore } from '../stores/chatStore';
import type { Project, LoopSession } from '../types';

// Stub the api client so the sidebar doesn't hit a real backend.
// vi.mock is hoisted to the top of the file, so this is applied
// before ChatSidebar imports api.
vi.mock('../api/client', () => {
  const sessionsGet = vi.fn((_url?: string) =>
    Promise.resolve({ data: { sessions: [] } }));
  const otherGet = vi.fn((_url?: string) => Promise.resolve({ data: {} }));
  return {
    default: {
      get: vi.fn((url: string) =>
        typeof url === 'string' && url.includes('/sessions')
          ? sessionsGet(url) : otherGet(url)),
      post: vi.fn(() => Promise.resolve({ data: {} })),
    },
    connectWebSocket: vi.fn(),
  };
});

function makeProject(id: string, name: string, createdAt = 0): Project {
  return {
    id, name, description: name, workspace: `/w/${id}`,
    work_dir: `/w/${id}`, status: 'active',
    task_count: 0, agent_count: 0, created_at: createdAt,
  };
}

const renderSidebar = () => {
  return render(
    <ConfigProvider>
      <MemoryRouter initialEntries={['/chat']}>
        <ChatSidebar />
      </MemoryRouter>
    </ConfigProvider>,
  );
};

describe('ChatSidebar project list', () => {
  beforeEach(() => {
    useChatStore.getState().reset();
  });

  it('shows the empty state when there are no projects', () => {
    renderSidebar();
    expect(screen.getByText(/Add a folder from the top bar/)).toBeInTheDocument();
    // No project rows. Scoped to the rows rather than asserting the word
    // "Projects" is absent from the document: the navigation rail has a
    // "Projects" destination of its own, and a document-wide check would fail
    // for a reason that has nothing to do with the project list.
    expect(document.querySelector('[data-testid^="project-row-"]')).toBeNull();
  });

  it('shows up to 10 projects by default and hides the rest', () => {
    const list: Project[] = [];
    for (let i = 0; i < 15; i++) {
      list.push(makeProject(`p${i}`, `Project ${i}`, 15 - i));
    }
    useChatStore.getState().setProjects(list);
    useChatStore.getState().setCurrentProject(list[0]);
    renderSidebar();
    // Header — scoped to the project list, because the navigation rail also has
    // a "Projects" destination and a document-wide lookup now matches twice.
    const header = within(screen.getByTestId('project-list-header'));
    expect(header.getByText('Projects')).toBeInTheDocument();
    expect(header.getByText('15')).toBeInTheDocument();
    // First 10 visible
    for (let i = 0; i < 10; i++) {
      expect(screen.getByText(`Project ${i}`)).toBeInTheDocument();
    }
    // 11th hidden
    expect(screen.queryByText('Project 10')).not.toBeInTheDocument();
    // Expand button visible
    expect(screen.getByText(/Show all \(15\)/)).toBeInTheDocument();
  });

  it('does not show the "Show all" button when <= 10 projects', () => {
    const list: Project[] = [];
    for (let i = 0; i < 5; i++) {
      list.push(makeProject(`p${i}`, `Project ${i}`));
    }
    useChatStore.getState().setProjects(list);
    useChatStore.getState().setCurrentProject(list[0]);
    renderSidebar();
    expect(screen.queryByText(/Show all/)).not.toBeInTheDocument();
  });

  it('clicking a project row selects it and navigates to /chat', () => {
    const p1 = makeProject('p1', 'Project One', 2);
    const p2 = makeProject('p2', 'Project Two', 1);
    useChatStore.getState().setProjects([p1, p2]);
    useChatStore.getState().setCurrentProject(p1);
    renderSidebar();
    // Click p2
    fireEvent.click(screen.getByTestId('project-row-p2'));
    // Now current should be p2
    expect(useChatStore.getState().currentProject?.id).toBe('p2');
  });

  it('clicking the active project is a no-op (does not navigate away)', () => {
    const p1 = makeProject('p1', 'Project One');
    useChatStore.getState().setProjects([p1]);
    useChatStore.getState().setCurrentProject(p1);
    renderSidebar();
    fireEvent.click(screen.getByTestId('project-row-p1'));
    // Still current.
    expect(useChatStore.getState().currentProject?.id).toBe('p1');
  });

  it('"Show all" button reveals the hidden projects', () => {
    const list: Project[] = [];
    for (let i = 0; i < 12; i++) {
      list.push(makeProject(`p${i}`, `Project ${i}`, 12 - i));
    }
    useChatStore.getState().setProjects(list);
    useChatStore.getState().setCurrentProject(list[0]);
    renderSidebar();
    // p10 hidden initially
    expect(screen.queryByText('Project 10')).not.toBeInTheDocument();
    expect(screen.queryByText('Project 11')).not.toBeInTheDocument();
    // Expand
    fireEvent.click(screen.getByText(/Show all/));
    expect(screen.getByText('Project 10')).toBeInTheDocument();
    expect(screen.getByText('Project 11')).toBeInTheDocument();
    // And the button changes to "Show less"
    expect(screen.getByText(/Show less/)).toBeInTheDocument();
  });

  it('"Show less" button collapses back to 10', () => {
    const list: Project[] = [];
    for (let i = 0; i < 12; i++) {
      list.push(makeProject(`p${i}`, `Project ${i}`, 12 - i));
    }
    useChatStore.getState().setProjects(list);
    useChatStore.getState().setCurrentProject(list[0]);
    renderSidebar();
    fireEvent.click(screen.getByText(/Show all/));
    fireEvent.click(screen.getByText(/Show less/));
    expect(screen.queryByText('Project 10')).not.toBeInTheDocument();
  });

  it('current project is highlighted', () => {
    const p1 = makeProject('p1', 'Project One', 2);
    const p2 = makeProject('p2', 'Project Two', 1);
    useChatStore.getState().setProjects([p1, p2]);
    useChatStore.getState().setCurrentProject(p2);
    renderSidebar();
    // The active row has a vertical bar marker (a 4×16 span) as
    // its first child. The non-active row doesn't.
    const p2Row = screen.getByTestId('project-row-p2');
    const p1Row = screen.getByTestId('project-row-p1');
    // p2's first child is the active bar span.
    expect(p2Row.firstElementChild?.tagName).toBe('SPAN');
    expect((p2Row.firstElementChild as HTMLElement).style.width).toBe('4px');
    // p1's first child is the icon (ProjectOutlined → SPAN).
    // (ProjectOutlined renders an <span role="img">.)
    // What we really care about: p1 does NOT have the 4×16 bar.
    const p1Bar = p1Row.querySelector('span[class*="anticon"]');
    expect(p1Bar).not.toBeNull();  // p1 has the icon
    // p1 should not have a 4×16 bar as its first child.
    const p1FirstSpan = p1Row.firstElementChild as HTMLElement;
    expect(p1FirstSpan.style.width).not.toBe('4px');
  });

  it('keeps a fixed created_at order — the current project does NOT jump to the top', () => {
    const p1 = makeProject('p1', 'Alpha', 1);
    const p2 = makeProject('p2', 'Bravo', 2);
    const p3 = makeProject('p3', 'Charlie', 3);
    useChatStore.getState().setProjects([p1, p2, p3]);
    useChatStore.getState().setCurrentProject(p1);  // oldest
    const { container } = renderSidebar();
    const rows = container.querySelectorAll('[data-testid^="project-row-"]');
    // R38.6.5: newest first, selection does not reorder anything.
    expect(rows[0].getAttribute('data-testid')).toBe('project-row-p3');
    expect(rows[1].getAttribute('data-testid')).toBe('project-row-p2');
    expect(rows[2].getAttribute('data-testid')).toBe('project-row-p1');
  });

  it('clicking a project does not change the list order', () => {
    const p1 = makeProject('p1', 'Alpha', 1);
    const p2 = makeProject('p2', 'Bravo', 2);
    const p3 = makeProject('p3', 'Charlie', 3);
    useChatStore.getState().setProjects([p1, p2, p3]);
    useChatStore.getState().setCurrentProject(p2);
    const { container } = renderSidebar();
    const order = () => Array.from(
      container.querySelectorAll('[data-testid^="project-row-"]'),
    ).map((r) => r.getAttribute('data-testid'));
    expect(order()).toEqual([
      'project-row-p3', 'project-row-p2', 'project-row-p1',
    ]);
    // Switch to the oldest project — it must stay last.
    fireEvent.click(screen.getByTestId('project-row-p1'));
    expect(useChatStore.getState().currentProject?.id).toBe('p1');
    expect(order()).toEqual([
      'project-row-p3', 'project-row-p2', 'project-row-p1',
    ]);
  });
});
