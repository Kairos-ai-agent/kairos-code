/**
 * Chat project isolation — a reply from project B must never render into the
 * thread of the open project A.
 *
 * The bug these pin: the backend's WebSocket forwards EVERY project's activity
 * to every connected client, and the Chat page's activity handler used to
 * dispatch purely on the inner `topic` — it never looked at which project the
 * message belonged to. So a WeChat project's answer was appended into whatever
 * thread was on screen (and snapshotted into it on the next project switch).
 *
 * The project id is resolved from `metadata.project_id` first, then from the
 * sender's "<project>.lane" dot-prefix. A message with neither is "cannot
 * determine" and keeps the old behaviour (flows into the open thread).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, waitFor, act } from '@testing-library/react';
import { ConfigProvider, App as AntdApp } from 'antd';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

vi.mock('../api/client', () => {
  const noop = vi.fn((_url?: string) => Promise.resolve({ data: {} }));
  return {
    default: {
      get: vi.fn((url?: string) => {
        if (typeof url === 'string' && url.includes('/loop')) {
          return Promise.resolve({ data: {
            running: false, round: 0, last_score: 0, last_approve: false,
            user_stopped: false, no_progress_count: 0, history: [],
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
        if (typeof url === 'string' && url.includes('/chat-messages')) {
          return Promise.resolve({ data: { messages: [] } });
        }
        if (typeof url === 'string' && url.includes('/rounds')) {
          return Promise.resolve({ data: { rounds: [] } });
        }
        if (typeof url === 'string' && url.includes('/sessions')) {
          return Promise.resolve({ data: { sessions: [] } });
        }
        if (typeof url === 'string' && url.includes('/slash')) {
          return Promise.resolve({ data: { commands: [] } });
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
import type { Project } from '../types';

const P_OPEN = 'p1';     // the project the user is looking at
const P_OTHER = 'p2';    // a *different* project, same browser session

const openProject: Project = {
  id: P_OPEN, name: 'alpha', description: '', workspace: '/w',
  work_dir: '/w', status: 'active',
  task_count: 0, agent_count: 0, created_at: 1.0,
} as Project;

/** Build an `activity` envelope exactly like the backend emits. */
function activity(opts: {
  topic: string; sender: string; content?: string;
  metadata?: Record<string, unknown>;
}) {
  return {
    type: 'activity',
    message: {
      id: `m-${Math.random().toString(16).slice(2)}`,
      sender: opts.sender,
      receiver: 'user',
      topic: opts.topic,
      content: opts.content ?? '',
      msg_type: 'text',
      timestamp: 1.0,
      metadata: opts.metadata ?? {},
    },
  };
}

describe('Chat project isolation (no cross-project bleed)', () => {
  let handler: ((data: any) => void) | null = null;

  beforeEach(async () => {
    useChatStore.getState().reset();
    useChatStore.getState().setLiveStatus(null);
    useChatStore.getState().setProjects([openProject]);
    useChatStore.getState().setCurrentProject(openProject);
    handler = null;
    const { onWebSocketMessage } = await import('../api/client');
    (onWebSocketMessage as any).mockImplementation((cb: any) => {
      handler = cb;
      return () => { handler = null; };
    });
  });

  const setup = async () => {
    render(
      <ConfigProvider>
        <AntdApp>
          <MemoryRouter initialEntries={['/chat']}>
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

  const send = async (env: any) => {
    await act(async () => { handler!(env); });
  };

  const contents = () =>
    useChatStore.getState().currentMessages.map((m) => m.content);

  // ---- ① another project's events never reach the open thread -------------

  it('does NOT append a final reply from another project (metadata.project_id)', async () => {
    await setup();
    await send(activity({
      topic: 'agent.chat', sender: `${P_OTHER}.coder`,
      content: 'P2-SECRET-REPLY',
      metadata: { project_id: P_OTHER },
    }));
    expect(contents()).not.toContain('P2-SECRET-REPLY');
    expect(useChatStore.getState().currentMessages.length).toBe(0);
  });

  it('does NOT append a final reply from another project (sender dot-prefix only)', async () => {
    await setup();
    await send(activity({
      topic: 'agent.chat', sender: `${P_OTHER}.coder`,
      content: 'P2-SECRET-NOMETA', metadata: {},
    }));
    expect(contents()).not.toContain('P2-SECRET-NOMETA');
    expect(useChatStore.getState().currentMessages.length).toBe(0);
  });

  it('does NOT leak another project\'s streaming chunk into the open thread', async () => {
    await setup();
    await send(activity({
      topic: 'stream.chunk', sender: `${P_OTHER}.coder`,
      content: 'P2-STREAM',
      metadata: { project_id: P_OTHER },
    }));
    expect(contents()).not.toContain('P2-STREAM');
    expect(
      useChatStore.getState().currentMessages.some(
        (m) => m.topic === 'stream.chunk'),
    ).toBe(false);
  });

  it('does NOT leak another project\'s tool/live status into the status row', async () => {
    await setup();
    await send(activity({
      topic: 'tool.call', sender: `${P_OTHER}.coder`, content: 'file_read(x)',
      metadata: { project_id: P_OTHER, tool: 'file_read' },
    }));
    await send(activity({
      topic: 'agent.thinking', sender: `${P_OTHER}.coder`,
      content: 'P2 thinking tail',
      metadata: { project_id: P_OTHER },
    }));
    expect(useChatStore.getState().liveStatus).toBeNull();
  });

  it('does not persist another project\'s message into the current project\'s thread', async () => {
    await setup();
    await send(activity({
      topic: 'agent.chat', sender: `${P_OTHER}.coder`,
      content: 'P2-PERSIST', metadata: { project_id: P_OTHER },
    }));
    // Switching away snapshots currentMessages into messagesByProject[oldId];
    // the foreign reply must not be part of that snapshot.
    await act(async () => { useChatStore.getState().setCurrentProject(null); });
    const saved = useChatStore.getState().messagesByProject[P_OPEN] || [];
    expect(saved.map((m) => m.content)).not.toContain('P2-PERSIST');
    expect(saved.length).toBe(0);
  });

  // ---- ② the open project's own events keep working (regression) ---------

  it('still appends the open project\'s final reply (metadata.project_id)', async () => {
    await setup();
    await send(activity({
      topic: 'agent.chat', sender: `${P_OPEN}.coder`,
      content: 'P1-REPLY', metadata: { project_id: P_OPEN },
    }));
    expect(contents()).toContain('P1-REPLY');
  });

  it('still streams the open project\'s chunks', async () => {
    await setup();
    await send(activity({
      topic: 'stream.chunk', sender: `${P_OPEN}.coder`,
      content: 'P1-STREAM', metadata: { project_id: P_OPEN },
    }));
    expect(contents()).toContain('P1-STREAM');
  });

  it('still updates the status row for the open project', async () => {
    await setup();
    await send(activity({
      topic: 'tool.call', sender: `${P_OPEN}.coder`, content: 'file_read(x)',
      metadata: { project_id: P_OPEN, tool: 'file_read' },
    }));
    expect(useChatStore.getState().liveStatus?.kind).toBe('tool');
  });

  // ---- ③ no project signal at all ⇒ predictable, never a crash -----------

  it('renders a message with no project signal (local short-form sender)', async () => {
    await setup();
    await send(activity({
      topic: 'agent.chat', sender: 'coder', content: 'NO-PROJECT-INFO',
      metadata: {},
    }));
    expect(contents()).toContain('NO-PROJECT-INFO');
  });

  it('drops a foreign-tagged message even when no project is open', async () => {
    await setup();
    await act(async () => { useChatStore.getState().setCurrentProject(null); });
    await send(activity({
      topic: 'agent.chat', sender: `${P_OTHER}.coder`,
      content: 'P2-NO-OPEN', metadata: { project_id: P_OTHER },
    }));
    expect(contents()).not.toContain('P2-NO-OPEN');
  });

  it('does not crash on a malformed activity payload', async () => {
    await setup();
    await act(async () => {
      handler!({ type: 'activity', message: { topic: 'agent.chat' } });
      handler!({ type: 'activity', message: { topic: 'stream.chunk' } });
    });
    // Nothing thrown, thread stays consistent.
    expect(Array.isArray(useChatStore.getState().currentMessages)).toBe(true);
  });
});
