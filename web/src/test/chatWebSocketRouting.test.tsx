/** Tests for the Chat page's WebSocket message routing.
 *
 * The backend's WebSocket sends:
 *   { type: "activity", message: { topic, sender, content, ... } }
 *
 * The Chat page must unwrap the envelope and dispatch on the inner
 * topic. Earlier code read `data.topic` directly, which never
 * exists at the top level — so the chat thread never received any
 * agent activity and looked "frozen" after the user sent a message.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import { ConfigProvider, App as AntdApp } from 'antd';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

// Mock the api client. The Chat page uses api.get for initial loads;
// we stub it so those don't try to hit a real backend.
vi.mock('../api/client', () => {
  const noop = vi.fn((_url?: string) => Promise.resolve({ data: {} }));
  return {
    default: {
      get: vi.fn((url?: string) => {
        if (typeof url === 'string' && url.includes('/loop')) {
          return Promise.resolve({ data: {
            running: false, round: 0, last_score: 0, last_approve: false,
            user_stopped: false, no_progress_count: 0,
            history: [],
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
        if (typeof url === 'string' && url.includes('/sessions')
            && !url.match(/sessions\/[^/]+\/rounds/)) {
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
import type { Project } from '../types';

const fakeProject: Project = {
  id: 'p1', name: 'demo', description: '', workspace: '/w',
  work_dir: '/w', status: 'active',
  task_count: 0, agent_count: 0, created_at: 1.0,
};

describe('Chat WebSocket message routing', () => {
  let messageHandler: ((data: any) => void) | null = null;

  beforeEach(async () => {
    useChatStore.getState().reset();
    useChatStore.getState().setProjects([fakeProject]);
    useChatStore.getState().setCurrentProject(fakeProject);
    // Capture the registered WS message handler.
    const { onWebSocketMessage } = await import('../api/client');
    (onWebSocketMessage as any).mockImplementation((cb: any) => {
      messageHandler = cb;
      return () => { messageHandler = null; };
    });
  });

  const setup = async () => {
    messageHandler = null;
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
    // Let effects run so the WS handler is registered.
    await waitFor(() => expect(messageHandler).not.toBeNull());
  };

  it('renders Chat page', async () => {
    await setup();
    // The empty-state hint shows when no session is active.
    expect(screen.getByText(/Auto router/i)).toBeInTheDocument();
  });

  it('agent.thinking activity adds a Coder bubble to the thread', async () => {
    await setup();
    expect(useChatStore.getState().currentMessages.length).toBe(0);
    await act(async () => {
      messageHandler!({
        type: 'activity',
        message: {
          id: 'm1', sender: 'coder', receiver: 'user',
          topic: 'agent.thinking',
          content: 'let me analyze the file',
          msg_type: 'text', timestamp: 1.0, metadata: {},
        },
      });
    });
    const msgs = useChatStore.getState().currentMessages;
    expect(msgs.length).toBe(1);
    expect(msgs[0].sender).toBe('coder');
    expect(msgs[0].topic).toBe('agent.thinking');
  });

  it('agent.response does not duplicate the streaming bubble', async () => {
    await setup();
    await act(async () => {
      messageHandler!({
        type: 'activity',
        message: {
          id: 'm2', sender: 'coder', receiver: 'user',
          topic: 'agent.response',
          content: 'final answer: refactored to use dataclasses',
          msg_type: 'text', timestamp: 2.0, metadata: {},
        },
      });
    });
    const msgs = useChatStore.getState().currentMessages;
    // R38.6.3: agent.response is deliberately skipped by the handler — the
    // stream.chunk bubble already carries the same text, and appending here
    // rendered every reply two or three times.
    expect(msgs.some((m) => m.topic === 'agent.response')).toBe(false);
  });

  it('tool.call and tool.result are both surfaced', async () => {
    await setup();
    await act(async () => {
      messageHandler!({
        type: 'activity',
        message: {
          id: 't1', sender: 'coder', topic: 'tool.call',
          content: 'file_read(a.py)', metadata: { tool: 'file_read',
                                                   args: { path: 'a.py' } },
        },
      });
      messageHandler!({
        type: 'activity',
        message: {
          id: 't2', sender: 'coder', topic: 'tool.result',
          content: '...file contents...',
          metadata: { tool: 'file_read', success: true, output: '...',
                      duration_ms: 12 },
        },
      });
    });
    const msgs = useChatStore.getState().currentMessages;
    expect(msgs.some((m) => m.topic === 'tool.call')).toBe(true);
    expect(msgs.some((m) => m.topic === 'tool.result')).toBe(true);
  });

  it('loop.coder_started triggers a session start (sets sessionId + fetches)', async () => {
    // The handler closes over `currentProject` from the render pass, so the
    // project must be selected before the component mounts.
    useChatStore.getState().setProjects([{
      id: 'p1', name: 'demo', description: '', workspace: '/w',
      work_dir: '/w', status: 'active', task_count: 0, agent_count: 0,
      created_at: 1.0,
    } as any]);
    useChatStore.getState().setCurrentProject({
      id: 'p1', name: 'demo', description: '', workspace: '/w',
      work_dir: '/w', status: 'active', task_count: 0, agent_count: 0,
      created_at: 1.0,
    } as any);
    await setup();
    // We have to capture api.get calls. The mock above provides them.
    await act(async () => {
      messageHandler!({
        type: 'activity',
        message: {
          id: 's1', sender: 'orchestrator',
          topic: 'loop.coder_started',
          content: '',
          metadata: { session_id: 'sess-abc123' },
        },
      });
    });
    // The handler should set currentSessionId to the new session
    // (via setCurrentSessionId('sess-abc123') from the r.data.session_id).
    expect(useChatStore.getState().currentSessionId).toBe('sess-abc123');
  });

  it('init / agent_update envelopes are ignored (no bubble added)', async () => {
    await setup();
    await act(async () => {
      messageHandler!({ type: 'init', agents: [], messages: [] });
      messageHandler!({ type: 'agent_update', agents: [] });
    });
    const msgs = useChatStore.getState().currentMessages;
    expect(msgs.length).toBe(0);
  });

  it('top-level data.topic (which never exists) is ignored — the bug we fixed', async () => {
    // BEFORE THE FIX, this would have been matched by the
    // `if (t === 'agent.message')` branch because `t = data.topic || data.type`
    // and data.topic didn't exist so t was ''. After the fix,
    // we dispatch on data.message.topic, so a flat-top-level
    // topic is ignored.
    await setup();
    await act(async () => {
      messageHandler!({
        topic: 'agent.message',  // <-- top-level, no envelope
        content: 'orphan',
      });
    });
    const msgs = useChatStore.getState().currentMessages;
    expect(msgs.length).toBe(0);
  });
});
