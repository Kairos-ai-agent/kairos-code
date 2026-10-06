/**
 * R41 — the loop runs hidden, and no "Running…" badge survives it.
 *
 * The bug these pin: `GET /loop` reports `running: true` for as long as the
 * loop *task* is alive, and every terminal topic (`loop.completed`,
 * `loop.finished`, …) is published from inside that still-unwinding task. The
 * page used to apply each refetch verbatim, so the badge stayed up for good
 * next to the final score.
 *
 * Two things are therefore pinned here:
 *   1. the chat page renders no loop running banner while the run is live
 *      (no `background-task` strip over the composer);
 *   2. a terminal event clears the badge, and a later refetch that still says
 *      `running: true` cannot bring it back.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react';
import { ConfigProvider, App as AntdApp } from 'antd';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

// Deliberately the worst case: the backend keeps reporting `running: true`,
// exactly as it does while the loop task is finishing.
vi.mock('../api/client', () => {
  const noop = vi.fn((_url?: string) => Promise.resolve({ data: {} }));
  return {
    default: {
      get: vi.fn((url?: string) => {
        if (typeof url === 'string' && url.includes('/loop')) {
          return Promise.resolve({ data: {
            running: true, round: 1, last_score: 100, last_approve: true,
            user_stopped: false, no_progress_count: 0, history: [],
            session_id: 'sess-abc12345',
          } });
        }
        if (typeof url === 'string' && url.includes('/plan')) {
          return Promise.resolve({ data: { pending: false, text: '',
                                           decision: null, round: 0 } });
        }
        if (typeof url === 'string' && url.includes('/ask')) {
          return Promise.resolve({ data: { pending: false, question: '',
                                           context: '', round: 0 } });
        }
        if (typeof url === 'string' && url.includes('/sessions')) {
          return Promise.resolve({ data: { sessions: [] } });
        }
        return noop(url);
      }),
      post: vi.fn(() => Promise.resolve({ data: {} })),
    },
    connectWebSocket: vi.fn(),
    onWebSocketMessage: vi.fn(() => () => {}),
    onWebSocketState: vi.fn(() => () => {}),
  };
});

import { useChatStore } from '../stores/chatStore';
import Chat from '../pages/Chat';

const SESSION = 'sess-abc12345';

const project = {
  id: 'p1', name: 'demo', description: '', workspace: '/w',
  work_dir: '/w', status: 'active', task_count: 0, agent_count: 0,
  created_at: 1.0,
} as any;

function activity(topic: string, metadata: Record<string, unknown> = {}) {
  return {
    type: 'activity',
    message: {
      id: `m-${topic}`, sender: 'orchestrator', topic,
      content: '', msg_type: 'result', timestamp: 1.0,
      metadata: { project_id: 'p1', session_id: SESSION, ...metadata },
    },
  };
}

describe('loop running indicator (R41)', () => {
  let handler: ((data: any) => void) | null = null;

  beforeEach(async () => {
    useChatStore.getState().reset();
    useChatStore.getState().setProjects([project]);
    useChatStore.getState().setCurrentProject(project);
    handler = null;
    const { onWebSocketMessage } = await import('../api/client');
    (onWebSocketMessage as any).mockImplementation((cb: any) => {
      handler = cb;
      return () => { handler = null; };
    });
  });

  const setup = async (initial = `/chat/${SESSION}`) => {
    render(
      <ConfigProvider>
        <AntdApp>
          <MemoryRouter initialEntries={[initial]}>
            <Routes>
              <Route path="/chat" element={<Chat />} />
              <Route path="/chat/:sessionId" element={<Chat />} />
            </Routes>
          </MemoryRouter>
        </AntdApp>
      </ConfigProvider>,
    );
    await waitFor(() => expect(handler).not.toBeNull());
  };

  const send = async (topic: string, metadata: Record<string, unknown> = {}) => {
    await act(async () => { handler!(activity(topic, metadata)); });
  };

  it('shows no running banner while the loop runs', async () => {
    await setup();
    await send('loop.coder_started');
    await waitFor(() => expect(screen.getByText('Running…')).toBeInTheDocument());
    // No strip above the composer: that is the banner the user asked to hide.
    expect(document.querySelector('[data-testid="background-task"]')).toBeNull();
    expect(document.querySelector('[data-testid="background-task-view"]')).toBeNull();
    expect(screen.queryByText(/页面会自动刷新|auto-refresh/i)).toBeNull();
  });

  it('clears the badge on loop.completed even though GET /loop still says running', async () => {
    await setup();
    await send('loop.coder_started');
    await waitFor(() => expect(screen.getByText('Running…')).toBeInTheDocument());

    await send('loop.completed', { score: 100 });
    await waitFor(() => expect(screen.queryByText('Running…')).toBeNull());
    // And the final score is still shown — the run's result is not the residue
    // the user complained about.
    expect(screen.getByText(/score 100/i)).toBeInTheDocument();
  });

  it('clears the badge on the backend terminal event (loop.ended)', async () => {
    await setup();
    await send('loop.coder_started');
    await waitFor(() => expect(screen.getByText('Running…')).toBeInTheDocument());

    await send('loop.ended', { status: 'stopped' });
    await waitFor(() => expect(screen.queryByText('Running…')).toBeNull());
  });

  it('does not resurrect the badge when the response is refetched afterwards', async () => {
    await setup();
    await send('loop.coder_started');
    await waitFor(() => expect(screen.getByText('Running…')).toBeInTheDocument());
    await send('loop.ended', { status: 'done' });
    await waitFor(() => expect(screen.queryByText('Running…')).toBeNull());

    // The Refresh button refetches /loop, which still answers `running: true`
    // (the fixture never relents). The run is over, so the badge must stay off.
    fireEvent.click(screen.getByRole('button', { name: /refresh/i }));
    await act(async () => { await Promise.resolve(); });
    await waitFor(() => expect(screen.queryByText('Running…')).toBeNull());
  });

  it('keeps the chat header on the session the user opened', async () => {
    await setup();
    await send('loop.coder_started');
    await waitFor(() => expect(screen.getByText('Running…')).toBeInTheDocument());
    // Not the loop's own session id — the user is on `sess-abc…`.
    expect(screen.getByText('Session sess-abc…')).toBeInTheDocument();
    expect(screen.queryByText(/Session sess-abc12345/)).toBeNull();
  });
});
