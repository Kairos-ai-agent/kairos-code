/**
 * R2 — the Tasks page.
 *
 * The API listed durable tasks, subagent handles and autonomous jobs from R2;
 * this page is the screen. These tests pin the parts that can silently rot: no
 * project means no guessing, starting a task posts the goal, resuming sends a
 * tick count, and the payload's three sections actually render.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ConfigProvider, App as AntdApp } from 'antd';
import { MemoryRouter } from 'react-router-dom';

const get = vi.fn();
const post = vi.fn();
vi.mock('../api/client', () => ({
  default: {
    get: (...args: unknown[]) => get(...args),
    post: (...args: unknown[]) => post(...args),
  },
}));

import Tasks from '../pages/Tasks';

const renderAt = (path: string) => render(
  <ConfigProvider>
    <AntdApp>
      <MemoryRouter initialEntries={[path]}>
        <Tasks />
      </MemoryRouter>
    </AntdApp>
  </ConfigProvider>,
);

const emptyPayload = {
  project_id: 'p1', durable: null, subagents: [], autonomous: [], counts: {},
};

const durablePayload = {
  project_id: 'p1',
  durable: {
    goal: 'migrate all 47 endpoints',
    har_id: 'abc12345',
    round: 3,
    last_score: 71,
    last_approve: false,
    last_summary: 'round 3 did some work',
    no_progress_count: 0,
    history: [
      { round: 3, score: 71, approved: false, summary: 'round 3 did some work' },
      { round: 2, score: 60, approved: false, summary: 'round 2 did some work' },
    ],
    root: 'E:/tmp/ws',
  },
  subagents: [{ handle: 'sub-77', status: 'running' }],
  autonomous: [{ id: 'job-1', status: 'queued', goal: 'watch the docs' }],
  counts: { durable: 1, subagents: 1, autonomous: 1 },
};

describe('Tasks page (R2)', () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
  });

  it('renders without a project and does not guess one', async () => {
    get.mockResolvedValue({ data: [] });
    renderAt('/tasks');

    await waitFor(() => expect(get).toHaveBeenCalledWith('/projects'));
    expect(screen.getByTestId('tasks-page')).toBeInTheDocument();
    expect(screen.queryByTestId('tasks-start')).toBeNull();
    expect(screen.queryByTestId('tasks-resume-1')).toBeNull();
    expect(get).not.toHaveBeenCalledWith(expect.stringContaining('/tasks/'));
  });

  it('offers a goal box when the project has no durable task', async () => {
    get.mockImplementation((url: string) => Promise.resolve({
      data: url === '/projects' ? [{ id: 'p1', name: 'demo' }] : emptyPayload,
    }));
    renderAt('/tasks?project=p1');

    await waitFor(() => expect(get).toHaveBeenCalledWith('/tasks/p1'));
    expect(screen.getByTestId('tasks-goal')).toBeInTheDocument();
    expect(screen.getByTestId('tasks-start')).toBeInTheDocument();
    expect(screen.queryByTestId('tasks-resume-1')).toBeNull();
  });

  it('posts the goal when a task is started', async () => {
    get.mockImplementation((url: string) => Promise.resolve({
      data: url === '/projects' ? [{ id: 'p1', name: 'demo' }] : emptyPayload,
    }));
    post.mockResolvedValue({ data: { ok: true } });
    renderAt('/tasks?project=p1');

    await waitFor(() => expect(screen.getByTestId('tasks-goal')).toBeInTheDocument());
    fireEvent.change(screen.getByTestId('tasks-goal'), {
      target: { value: 'migrate all 47 endpoints' },
    });
    fireEvent.click(screen.getByTestId('tasks-start'));

    await waitFor(() => expect(post).toHaveBeenCalledWith(
      '/tasks/p1', { goal: 'migrate all 47 endpoints' }));
  });

  it('shows the saved round and resumes with a tick count', async () => {
    get.mockImplementation((url: string) => Promise.resolve({
      data: url === '/projects' ? [{ id: 'p1', name: 'demo' }] : durablePayload,
    }));
    post.mockResolvedValue({ data: { ok: true, code: 0 } });
    renderAt('/tasks?project=p1');

    await waitFor(() => expect(screen.getByTestId('tasks-resume-1')).toBeInTheDocument());
    expect(screen.getByText('migrate all 47 endpoints')).toBeInTheDocument();
    expect(screen.getByText('round 3 did some work')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('tasks-resume-5'));
    await waitFor(() => expect(post).toHaveBeenCalledWith(
      '/tasks/p1/resume', { ticks: 5 }));
  });

  it('lists background subagents and autonomous jobs', async () => {
    get.mockImplementation((url: string) => Promise.resolve({
      data: url === '/projects' ? [{ id: 'p1', name: 'demo' }] : durablePayload,
    }));
    renderAt('/tasks?project=p1');

    await waitFor(() => expect(screen.getByText('sub-77')).toBeInTheDocument());
    expect(screen.getByText('watch the docs')).toBeInTheDocument();
  });
});
