/**
 * R38.8 — the chat thread shows the agent's process, and keeps it.
 *
 * Three behaviours, all from the user's ask:
 *  1. an ``agent.thinking`` event is its own collapsible row, not a reply;
 *  2. a tool *result* collapses behind a one-line summary while the call stays open;
 *  3. a status row reports what the agent is doing right now.
 *
 * The persistence half (the topics surviving a refresh) is backend-side and
 * covered by tests/test_chat_topics.py.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import React from 'react';

// Mock the theme hook so the component doesn't depend on the real antd provider.
vi.mock('../hooks/useThemeTokens', () => ({
  useThemeTokens: () => ({
    brand: '#1677ff', border: '#d9d9d9',
    labelPrimary: '#000', labelSecondary: '#666', labelTertiary: '#999',
    bgLay1: '#fafafa', bgElevated: '#fff',
    agentBubble: '#fff', agentBubbleBorder: '#eee',
    toolBubble: '#f6f6f6', userBubble: '#f0f0f0',
  }),
}));

import ChatThread from '../components/ChatThread';
import { useChatStore } from '../stores/chatStore';
import type { Message } from '../types';

let seq = 0;
const msg = (over: Partial<Message>): Message => ({
  id: `m${++seq}`, sender: 'agent', receiver: 'user', topic: 'agent.chat',
  content: '', msg_type: 'text', timestamp: 1700000000, metadata: {},
  ...over,
});

describe('chat process display (R38.8)', () => {
  beforeEach(() => { useChatStore.setState({ liveStatus: null }); });

  it('renders agent.thinking as a collapsible row, not as a reply', () => {
    render(<ChatThread messages={[msg({
      topic: 'agent.thinking',
      content: 'Let me look at the failing test first.',
    })]} />);
    expect(screen.getByTestId('collapsible-body')).toBeTruthy();
    expect(screen.getByText('Let me look at the failing test first.')).toBeTruthy();
    // An ordinary assistant reply would carry the avatar label; a thinking row must not.
    expect(screen.queryByText('Kairos')).toBeNull();
  });

  it('keeps the tool call open and collapses the result behind a summary', () => {
    render(<ChatThread messages={[
      msg({ topic: 'tool.call', metadata: { tool: 'read_file' },
            content: '{"path":"a.py"}' }),
      msg({ topic: 'tool.result', metadata: { tool: 'read_file' },
            content: 'x'.repeat(420) }),
    ]} />);
    const details = screen.getAllByTestId('collapsible-body') as unknown as HTMLDetailsElement[];
    // Only the result collapses — the call is the part the user is watching for.
    expect(details).toHaveLength(1);
    expect(details[0].open).toBe(false);
    expect(screen.getByText('{"path":"a.py"}')).toBeTruthy();
    // Both rows carry the tool name: the call ("tool · read_file") and the
    // collapsed result ("result · read_file").
    expect(screen.getAllByText(/read_file/)).toHaveLength(2);
  });

  it('shows a live status row while a tool runs, and nothing when idle', () => {
    render(<ChatThread messages={[msg({ content: 'hi' })]} />);
    expect(screen.queryByTestId('live-status')).toBeNull();

    act(() => {
      useChatStore.setState({ liveStatus: { kind: 'tool', detail: 'read_file' } });
    });
    expect(screen.getByTestId('live-status').textContent).toContain('read_file');

    act(() => { useChatStore.setState({ liveStatus: null }); });
    expect(screen.queryByTestId('live-status')).toBeNull();
  });
});
