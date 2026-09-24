/**
 * Turn grouping is the behaviour the user asked for: "调用工具或者正在思考等等
 * 都可详细展示过程". These tests pin the three things that make it trustworthy:
 *
 *   1. a tool call is paired with its OWN result, so the duration shown is that
 *      call's and not the next one's;
 *   2. a call with no result yet reads as running and claims no duration;
 *   3. a finished turn folds its process away, while a running turn stays open.
 *
 * Note for future edits: a folded block does not render its step rows at all,
 * so tests that inspect steps must open it first — that is the helper below,
 * not a workaround.
 */
import { describe, expect, it } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ChatThread from '../components/ChatThread';
import type { Message } from '../types';

const msg = (over: Partial<Message>): Message => ({
  id: Math.random().toString(36).slice(2),
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
  msg({ sender: 'user', topic: 'user.message', content: text });

const reply = (text: string) => msg({ topic: 'agent.chat', content: text });

const call = (tool: string, ts: number, args = '{}') => msg({
  topic: 'tool.call',
  content: `Calling ${tool}(${args})`,
  timestamp: ts,
  metadata: { tool, turn: 1, args: JSON.parse(args) },
});

const result = (tool: string, ts: number, body = 'ok') => msg({
  topic: 'tool.result',
  content: body,
  timestamp: ts,
  metadata: { tool, turn: 1 },
});

/** Expand the process block if the turn has finished (it folds itself away). */
function openProcess(container: HTMLElement): void {
  const toggle = container.querySelector('[data-testid="process-toggle"]');
  if (toggle?.getAttribute('aria-expanded') === 'false') {
    fireEvent.click(toggle as HTMLElement);
  }
}

describe('ChatThread turn grouping', () => {
  it('puts the process between the question and the answer', () => {
    const { container } = render(
      <ChatThread messages={[
        question('do it'),
        call('read_file', 1001),
        result('read_file', 1004),
        reply('done'),
      ]} />,
    );
    const turns = container.querySelectorAll('[data-testid="chat-turn"]');
    expect(turns.length).toBe(1);
    expect(screen.getByTestId('process-block')).toBeTruthy();
    expect(turns[0].textContent).toContain('do it');
    expect(turns[0].textContent).toContain('done');
  });

  it('shows the tool name, its outcome and its own duration', () => {
    const { container } = render(
      <ChatThread messages={[
        question('do it'),
        call('read_file', 1001),
        result('read_file', 1004),
        reply('done'),
      ]} />,
    );
    openProcess(container);
    const step = screen.getAllByTestId('process-step')[0];
    expect(step.textContent).toContain('read_file');
    // 1004 - 1001 = 3s
    expect(step.textContent).toContain('3.0s');
  });

  it('pairs each result with its own call when the same tool repeats', () => {
    const { container } = render(
      <ChatThread messages={[
        question('do it twice'),
        call('grep', 1000),
        result('grep', 1001),
        call('grep', 1005),
        result('grep', 1010),
        reply('done'),
      ]} />,
    );
    openProcess(container);
    const steps = screen.getAllByTestId('process-step');
    expect(steps.length).toBe(2);
    expect(steps[0].textContent).toContain('1.0s');
    expect(steps[1].textContent).toContain('5.0s');
  });

  it('leaves a call with no result marked as running, not as finished', () => {
    const { container } = render(
      <ChatThread messages={[question('go'), call('slow_tool', 1000)]} />,
    );
    const step = screen.getAllByTestId('process-step')[0];
    expect(step.textContent).toContain('slow_tool');
    // No duration may be claimed for a call that has not returned.
    expect(step.textContent).not.toMatch(/\d+\.\d+s/);
    // And a running turn's block starts open.
    const toggle = container.querySelector('[data-testid="process-toggle"]');
    expect(toggle?.getAttribute('aria-expanded')).toBe('true');
  });

  it('counts steps and marks failures in the summary row', () => {
    const failed = msg({
      topic: 'tool.result', content: 'boom', timestamp: 1002,
      metadata: { tool: 'run_tests', turn: 1, ok: false },
    });
    render(
      <ChatThread messages={[
        question('go'),
        call('run_tests', 1001),
        failed,
        reply('I could not run the tests'),
      ]} />,
    );
    // The summary row stays visible while folded, so no expansion is needed.
    // One call + its result is ONE step (the result belongs to the call), and
    // the failure is reported separately.
    const toggle = screen.getByTestId('process-toggle');
    expect(toggle.textContent).toContain('1 step');
    expect(toggle.textContent).toContain('1 failed');
  });

  it('folds a finished turn’s process away', () => {
    const { container } = render(
      <ChatThread messages={[
        question('go'), call('t', 1000), result('t', 1001), reply('done'),
      ]} />,
    );
    const toggle = container.querySelector('[data-testid="process-toggle"]');
    expect(toggle?.getAttribute('aria-expanded')).toBe('false');
  });

  it('renders a markdown reply as markup, not as literal asterisks', () => {
    const { container } = render(
      <ChatThread messages={[question('q'), reply('**bold** and `code`')]} />,
    );
    expect(container.querySelector('strong')?.textContent).toBe('bold');
    expect(container.querySelector('code')?.textContent).toBe('code');
  });

  it('keeps a message that is neither question, step nor reply visible', () => {
    const { container } = render(
      <ChatThread messages={[
        question('q'),
        msg({ sender: 'reviewer', topic: 'review.result', content: 'looks fine' }),
        reply('done'),
      ]} />,
    );
    expect(container.textContent).toContain('looks fine');
  });

  it('treats a thinking event as a step of the process', () => {
    const { container } = render(
      <ChatThread messages={[
        question('q'),
        msg({ topic: 'agent.thinking', content: 'let me consider', timestamp: 1001 }),
        reply('done'),
      ]} />,
    );
    openProcess(container);
    const step = screen.getAllByTestId('process-step')[0];
    expect(step.textContent).toContain('let me consider');
  });
});

/**
 * A reply is the agent's words, not a letterhead. A name printed above it is
 * noise to skip on every turn, and in a two-agent pipeline it is worse than
 * noise: "审查员" over a paragraph invites the reader to file the text under a
 * person instead of judging it. The verdict is the part that carries meaning,
 * so the verdict stays and the name goes.
 */
describe('ChatThread reply speaker', () => {
  it('prints no role name above an agent reply', () => {
    render(<ChatThread messages={[question('hi'), reply('hello')]} />);
    expect(screen.queryByText('Kairos')).toBeNull();
  });

  it('prints no role name above a reviewer reply, and keeps the verdict', () => {
    render(
      <ChatThread messages={[
        question('hi'),
        msg({ sender: 'reviewer', topic: 'review.result', content: 'looks fine',
              metadata: { score: 92, approve: true } }),
      ]} />,
    );
    expect(screen.queryByText('审查员')).toBeNull();
    expect(screen.queryByText('Reviewer')).toBeNull();
    // What has to survive is the judgement, not the badge: no name, still a score.
    expect(screen.getByText(/92/)).toBeTruthy();
    expect(screen.getByText('looks fine')).toBeTruthy();
  });

  it('leaves no role name anywhere in a mixed thread', () => {
    const { container } = render(
      <ChatThread messages={[
        question('q'),
        msg({ topic: 'agent.thinking', content: 'let me consider', timestamp: 1001 }),
        call('read_file', 1002),
        result('read_file', 1005),
        // A reply tagged with a stage id: the header slot must not print it.
        msg({ topic: 'coder.summary', content: 'done' }),
        msg({ sender: 'reviewer', topic: 'review.result', content: 'ok',
              metadata: { score: 80 } }),
      ]} />,
    );
    const text = container.textContent || '';
    for (const name of ['Kairos', '审查员', 'Reviewer', 'Coder', '编码者']) {
      expect(text).not.toContain(name);
    }
    // Nor the pipeline's internal stage ids: /trace is where attribution lives,
    // and a reply that announces its own stage is the same complaint in a rawer
    // form. Step rows keep theirs — "which step is this" is a real question there.
    expect(text).not.toContain('coder.summary');
    expect(text).not.toContain('review.result');
    expect(text).toContain('done');
  });
});
