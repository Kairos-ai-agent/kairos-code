/**
 * R38.8 — the chat thread shows the agent's process, and keeps it.
 * R38.13 — the process now belongs to a *turn*: the rows for one question, its
 * tool calls and their results live together inside one collapsible block, so
 * "what did it do to answer this?" is a single question with a single answer.
 *
 *  1. an ``agent.thinking`` event is a step in that block, not a reply;
 *  2. a tool call and its result are ONE step (the result is that call's, not a
 *     second event), and a long result stays behind a click;
 *  3. a status row reports what the agent is doing right now.
 *
 * Note: a folded block does not render its rows at all, so a test that inspects
 * steps opens it first — see `openProcess` below.
 *
 * The persistence half (the topics surviving a refresh) is backend-side and
 * covered by tests/test_chat_topics.py.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, act, fireEvent } from '@testing-library/react';
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

/** Expand the process block if the turn has finished (it folds itself away). */
function openProcess(container: HTMLElement): void {
  const toggle = container.querySelector('[data-testid="process-toggle"]');
  if (toggle?.getAttribute('aria-expanded') === 'false') {
    fireEvent.click(toggle as HTMLElement);
  }
}

const steps = (container: HTMLElement) =>
  Array.from(container.querySelectorAll('[data-testid="process-step"]'));

describe('chat process display (R38.8)', () => {
  beforeEach(() => { useChatStore.setState({ liveStatus: null }); });

  it('renders agent.thinking as a step in the process, not as a reply', () => {
    const { container } = render(<ChatThread messages={[msg({
      topic: 'agent.thinking',
      content: 'Let me look at the failing test first.',
    })]} />);
    openProcess(container);
    expect(container.querySelector('[data-testid="process-block"]')).toBeTruthy();
    expect(steps(container)).toHaveLength(1);
    expect(screen.getByText('Let me look at the failing test first.')).toBeTruthy();
    // An ordinary assistant reply would carry the avatar label; a thinking row
    // must not, or the reasoning reads as an answer.
    expect(screen.queryByText('Kairos')).toBeNull();
  });

  it('pairs a tool call with its own result, and keeps a long result behind a click', () => {
    const { container } = render(<ChatThread messages={[
      msg({ topic: 'tool.call', metadata: { tool: 'read_file', turn: 1 },
            content: 'Calling read_file({"path":"a.py"})' }),
      msg({ topic: 'tool.result', metadata: { tool: 'read_file', turn: 1 },
            timestamp: 1700000001, content: 'x'.repeat(420) }),
    ]} />);
    openProcess(container);
    // One step, not two: the result belongs to the call and must not be
    // mistaken for an event of its own.
    expect(steps(container)).toHaveLength(1);
    expect(screen.getByText('read_file')).toBeTruthy();
    expect(screen.getByText('{"path":"a.py"}')).toBeTruthy();
    // The four-hundredth character of a file read is not worth the room.
    const body = screen.queryByText('x'.repeat(420));
    expect(body).toBeNull();
    // ...but it is one click away.
    fireEvent.click(screen.getByText('read_file'));
    expect(screen.getByText('x'.repeat(420))).toBeTruthy();
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
