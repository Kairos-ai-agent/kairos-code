/**
 * Two user-reported gaps, pinned here.
 *
 *   1. A sub-agent (``spawn_subagent``) was a line in the tool list like any
 *      other, so neither "it is running" nor "it has finished" was visible. It
 *      now gets its own card: the delegated task, a clock that ticks while the
 *      child works, an unmistakable finished/failed state, and the
 *      background-handle case.
 *   2. The model's streaming reasoning was either dumped whole or not shown at
 *      all. It is now a single, left-clipped line that follows the tail of the
 *      stream and folds away the moment the answer starts.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import React from 'react';

// The component reads colors from this hook; stub it so no antd provider is
// needed (same shape as the other chat-thread tests).
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
  content: '', msg_type: 'text', timestamp: 1000, metadata: {},
  ...over,
});

const question = (text: string) =>
  msg({ sender: 'user', topic: 'user.message', content: text });

const reply = (text: string) => msg({ topic: 'agent.chat', content: text });

const spawnCall = (ts: number, args: Record<string, unknown>) => msg({
  topic: 'tool.call', timestamp: ts,
  content: `Calling spawn_subagent(${JSON.stringify(args)})`,
  metadata: { tool: 'spawn_subagent', turn: 1 },
});

const spawnResult = (ts: number, body: string, success = true) => msg({
  topic: 'tool.result', timestamp: ts, content: body,
  metadata: { tool: 'spawn_subagent', turn: 1, success },
});

const cards = () =>
  Array.from(document.querySelectorAll('[data-testid="subagent-card"]')) as HTMLElement[];
const part = (card: HTMLElement, testid: string) =>
  card.querySelector(`[data-testid="${testid}"]`) as HTMLElement | null;

describe('sub-agent visibility', () => {
  beforeEach(() => { act(() => { useChatStore.setState({ liveStatus: null }); }); });

  it('shows a running card with the delegated task, then a visible "done"', () => {
    const task = 'explore the repo for dead code and report the file paths';
    const { rerender } = render(
      <ChatThread messages={[question('go'), spawnCall(1000, { task })]} />,
    );

    let card = cards()[0];
    expect(card).toBeTruthy();
    // Running is a state of its own, not "a row with no duration".
    expect(card.getAttribute('data-state')).toBe('running');
    expect(part(card, 'subagent-state')!.textContent).toContain('Running');
    expect(part(card, 'subagent-task')!.textContent).toBe(task);
    expect(part(card, 'subagent-elapsed')!.textContent).toContain('elapsed');

    rerender(
      <ChatThread messages={[
        question('go'),
        spawnCall(1000, { task }),
        spawnResult(1004, 'Found 3 unused modules: a.py, b.py, c.py'),
        reply('done'),
      ]} />,
    );

    card = cards()[0];
    // The turn is over, so the process block folded itself away — and the card
    // is STILL visible: "did the sub-agent finish?" must not need an unfold.
    expect(screen.getByTestId('process-toggle').getAttribute('aria-expanded')).toBe('false');
    expect(card.getAttribute('data-state')).toBe('done');
    expect(part(card, 'subagent-state')!.textContent).toContain('Done');
    // 1004 - 1000 = 4s frozen on the card.
    expect(part(card, 'subagent-elapsed')!.textContent).toContain('4');
    // The report is one click away.
    expect(part(card, 'subagent-report')).toBeTruthy();
    // And it is NOT also a row in the ordinary tool list.
    expect(screen.queryAllByTestId('process-step').length).toBe(0);
  });

  it('renders subagent_status / subagent_result as their own cards too', () => {
    render(
      <ChatThread messages={[
        question('go'),
        msg({ topic: 'tool.call', timestamp: 1000,
              content: 'Calling subagent_status({"handle":"h-abc123"})',
              metadata: { tool: 'subagent_status', turn: 1 } }),
        msg({ topic: 'tool.result', timestamp: 1001,
              content: 'handle: h-abc123\nstatus: completed\nfinished: yes',
              metadata: { tool: 'subagent_status', turn: 1, success: true } }),
      ]} />,
    );
    const card = cards()[0];
    expect(card.getAttribute('data-state')).toBe('done');
    expect(part(card, 'subagent-state')!.textContent).toContain('Done');
    expect(part(card, 'subagent-handle')!.textContent).toContain('h-abc123');
    expect(screen.queryAllByTestId('process-step').length).toBe(0);
  });

  it('marks a failed sub-agent and shows the reason', () => {
    render(
      <ChatThread messages={[
        question('go'),
        spawnCall(1000, { task: 'something' }),
        spawnResult(1002, 'spawn_subagent: subagent crashed: boom', false),
      ]} />,
    );
    const card = cards()[0];
    expect(card.getAttribute('data-state')).toBe('failed');
    expect(part(card, 'subagent-state')!.textContent).toContain('Failed');
    expect(part(card, 'subagent-reason')!.textContent).toContain('boom');
  });

  it('shows the background handle and how to collect the result', () => {
    render(
      <ChatThread messages={[
        question('go'),
        spawnCall(1000, { task: 'index the docs', background: true }),
        spawnResult(1001,
          'Sub-agent spawned in background. handle=h-abc123 child=p1.sub_9f. '
          + 'Poll it with the subagent_status tool, then read the report with '
          + 'subagent_result.'),
      ]} />,
    );
    const card = cards()[0];
    // The child keeps running after the spawn returns, so the card stays a
    // background card rather than claiming "done".
    expect(card.getAttribute('data-state')).toBe('background');
    const handle = part(card, 'subagent-handle')!.textContent || '';
    expect(handle).toContain('handle');
    expect(handle).toContain('h-abc123');
    expect(part(card, 'subagent-hint')!.textContent).toContain('subagent_status');
  });

  it('keeps parallel sub-agents on their own rows', () => {
    render(
      <ChatThread messages={[
        question('go'),
        spawnCall(1000, { task: 'first task' }),
        spawnCall(1000, { task: 'second task' }),
      ]} />,
    );
    const list = cards();
    expect(list.length).toBe(2);
    expect(part(list[0], 'subagent-task')!.textContent).toBe('first task');
    expect(part(list[1], 'subagent-task')!.textContent).toBe('second task');
  });

  it('ticks the elapsed seconds while it runs', () => {
    vi.useFakeTimers();
    try {
      const started = Date.now() / 1000;
      render(<ChatThread messages={[question('go'), spawnCall(started, { task: 'a' })]} />);
      const before = screen.getByTestId('subagent-elapsed').textContent;
      act(() => { vi.advanceTimersByTime(3000); });
      const after = screen.getByTestId('subagent-elapsed').textContent;
      expect(after).not.toBe(before);
      expect(after).toContain('3');
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('live thinking line', () => {
  const HEAD = 'start-of-reasoning ' + 'x'.repeat(200);
  const TAIL = 'final insight about the bug';
  const streaming = () => msg({
    sender: 'coder', topic: 'stream.chunk', timestamp: 1000,
    content: `<think>${HEAD}${TAIL}`,
  });

  beforeEach(() => { act(() => { useChatStore.setState({ liveStatus: { kind: 'thinking' } }); }); });
  afterEach(() => { act(() => { useChatStore.setState({ liveStatus: null }); }); });

  it('renders exactly one clipped line holding only the tail of the stream', () => {
    render(<ChatThread messages={[question('go'), streaming()]} />);

    expect(screen.getAllByTestId('thinking-line').length).toBe(1);
    const text = screen.getByTestId('thinking-line-text') as HTMLElement;
    expect(text.style.whiteSpace).toBe('nowrap');
    expect(text.style.overflow).toBe('hidden');
    expect(text.textContent).toContain(TAIL);
    expect(text.textContent).not.toContain('start-of-reasoning');
    // Bounded to the last ~140 characters.
    expect((text.textContent || '').length).toBeLessThanOrEqual(140);
    // The blinking marker is there, and it is three dots.
    expect(
      screen.getByTestId('thinking-dots').querySelectorAll('span').length,
    ).toBe(3);
  });

  it('folds away once the answer starts', () => {
    const { rerender } = render(<ChatThread messages={[question('go'), streaming()]} />);
    expect(screen.queryByTestId('thinking-line')).toBeTruthy();

    rerender(<ChatThread messages={[question('go'), msg({
      sender: 'coder', topic: 'stream.chunk', timestamp: 1001,
      content: `<think>${HEAD}${TAIL}</think> here is the actual answer`,
    })]} />);
    expect(screen.queryByTestId('thinking-line')).toBeNull();
    // The generic status row is what remains while that reply streams.
    expect(screen.getByTestId('live-status')).toBeTruthy();
  });

  it('folds away when a tool starts running', () => {
    render(<ChatThread messages={[question('go'), streaming()]} />);
    expect(screen.queryByTestId('thinking-line')).toBeTruthy();

    act(() => {
      useChatStore.setState({ liveStatus: { kind: 'tool', detail: 'read_file' } });
    });
    expect(screen.queryByTestId('thinking-line')).toBeNull();
    expect(screen.getByTestId('live-status').textContent).toContain('read_file');
  });

  it('returns to the process fold — with its step count — once the turn is done', () => {
    act(() => { useChatStore.setState({ liveStatus: null }); });
    render(
      <ChatThread messages={[
        question('go'),
        msg({ topic: 'tool.call', timestamp: 1000,
              content: 'Calling read_file({})',
              metadata: { tool: 'read_file', turn: 1 } }),
        msg({ topic: 'tool.result', timestamp: 1001, content: 'ok',
              metadata: { tool: 'read_file', turn: 1, success: true } }),
        reply('done'),
      ]} />,
    );
    expect(screen.queryByTestId('thinking-line')).toBeNull();
    expect(screen.getByTestId('process-toggle').textContent).toContain('1 step');
  });
});
