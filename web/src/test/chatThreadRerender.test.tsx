/**
 * A poll tick must cost nothing in the thread.
 *
 * The chat page re-renders every two seconds (it polls pending plan/ask state)
 * and everything under it used to re-render with it — every settled turn
 * re-walking its markdown twice a second for a payload that had not changed.
 * The memos on ChatThread and TurnView only help because the parent hands over
 * the same `messages` reference when nothing arrived: that is what this pins.
 */
import { describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/react';
import * as markdownModule from '../utils/markdown';
import ChatThread from '../components/ChatThread';
import type { Message } from '../types';

vi.mock('../utils/markdown', async (importOriginal) => {
  const actual = await importOriginal<typeof markdownModule>();
  return { ...actual, renderMarkdown: vi.fn(actual.renderMarkdown) };
});

const msg = (over: Partial<Message>): Message => ({
  id: 'm',
  sender: 'agent',
  receiver: 'user',
  topic: 'agent.chat',
  content: '',
  msg_type: 'text',
  timestamp: 1000,
  metadata: {},
  ...over,
} as Message);

const question = (text: string) =>
  msg({ id: `q-${text}`, sender: 'user', topic: 'user.message', content: text });

const reply = (text: string) =>
  msg({ id: `r-${text}`, topic: 'agent.chat', content: text });

/** How much markdown work the thread has done in total. */
function markdownWork(): number {
  const spy = markdownModule.renderMarkdown as unknown as {
    mock: { calls: unknown[][] };
  };
  return spy.mock.calls.length;
}

describe('the thread does not re-render on a poll tick', () => {
  it('does no new work when the parent re-renders with the same messages', () => {
    const messages = [question('do it'), reply('done')];
    const { rerender } = render(<ChatThread messages={messages} />);

    const before = markdownWork();
    expect(before).toBeGreaterThan(0);

    rerender(<ChatThread messages={messages} />);  // one 2s poll tick
    rerender(<ChatThread messages={messages} />);  // and the next one

    expect(markdownWork()).toBe(before);
  });

  it('still re-renders when a message actually arrives', () => {
    const messages = [question('do it'), reply('done')];
    const { rerender } = render(<ChatThread messages={messages} />);
    const before = markdownWork();

    rerender(<ChatThread messages={[...messages, reply('and more')]} />);

    expect(markdownWork()).toBeGreaterThan(before);
  });
});
