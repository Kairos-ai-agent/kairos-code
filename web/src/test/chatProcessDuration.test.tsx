/**
 * The process fold used to report "0.0s" for every turn.
 *
 * The number was a SUM of the steps' own durations, and a step only has one
 * when a result closes it — i.e. tool calls only. A thinking step never does,
 * so a reasoning-heavy turn (or one that made no tool call at all) summed to
 * exactly 0 and the line read "0.0s" however long it ran.
 *
 * These tests pin the replacement: the total is the turn's own wall clock,
 * read off the events' timestamps (fake, never the real clock), and each row
 * shows its own span. A still-running step must still claim nothing.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';

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

const msg = (over: Partial<Message>): Message => ({
  id: Math.random().toString(36).slice(2),
  sender: 'agent', receiver: 'user', topic: 'agent.chat',
  content: '', msg_type: 'text', timestamp: 100, metadata: {},
  ...over,
} as Message);

const question = (ts: number, text = 'do it') =>
  msg({ sender: 'user', topic: 'user.message', content: text, timestamp: ts });

const thinking = (ts: number, text = 'considering') =>
  msg({ topic: 'agent.thinking', content: text, timestamp: ts });

const call = (tool: string, ts: number) =>
  msg({ topic: 'tool.call', timestamp: ts,
        content: `Calling ${tool}({})`, metadata: { tool, turn: 1 } });

const result = (tool: string, ts: number) =>
  msg({ topic: 'tool.result', timestamp: ts, content: 'ok',
        metadata: { tool, turn: 1, success: true } });

const reply = (ts: number, text = 'done') =>
  msg({ topic: 'agent.chat', content: text, timestamp: ts });

/** Expand the process block if it folded itself away. */
function openProcess(container: HTMLElement): void {
  const toggle = container.querySelector('[data-testid="process-toggle"]');
  if (toggle?.getAttribute('aria-expanded') === 'false') {
    fireEvent.click(toggle as HTMLElement);
  }
}

const summary = () => screen.getByTestId('process-toggle').textContent || '';
const rows = () => Array.from(document.querySelectorAll('[data-testid="process-step"]'));

describe('process duration is real elapsed time, not a sum of nothing', () => {
  it('reports the turn\'s wall clock for a tool-only turn', () => {
    useChatStore.setState({ liveStatus: null });
    render(<ChatThread messages={[
      question(100),
      call('read_file', 100),
      result('read_file', 104),
      reply(105),
    ]} />);
    // Question at 100, answer at 105 → 5.0s. Nothing here touches the clock.
    expect(summary()).toContain('5.0s');
  });

  it('reports real time for a turn made only of thinking steps', () => {
    // The regression: these steps carry no duration of their own, so the old
    // sum was 0 and the folded line read "0.0s" forever.
    useChatStore.setState({ liveStatus: null });
    render(<ChatThread messages={[
      question(100),
      thinking(100, 'first'),
      thinking(110, 'second'),
      thinking(112, 'third'),
      reply(130),
    ]} />);
    expect(summary()).toContain('30.0s');
    expect(summary()).not.toContain('· 0.0s');
  });

  it('gives every step its own duration when expanded', () => {
    useChatStore.setState({ liveStatus: null });
    const { container } = render(<ChatThread messages={[
      question(100),
      thinking(100, 'let me look at the file'),
      call('read_file', 104),
      result('read_file', 106),
      reply(107),
    ]} />);
    openProcess(container);
    const text = rows().map((r) => r.textContent || '');
    // The thinking step ran until the tool call started: 104 - 100 = 4s.
    expect(text[0]).toContain('4.0s');
    // ...and the tool call ran for 2s, exactly as before.
    expect(text[1]).toContain('2.0s');
  });

  it('still claims no duration for a step that has not returned', () => {
    useChatStore.setState({ liveStatus: null });
    render(<ChatThread messages={[question(100), call('slow_tool', 100)]} />);
    const step = rows()[0];
    expect(step.textContent).toContain('slow_tool');
    expect(step.textContent).not.toMatch(/\d+\.\d+s/);
  });
});
