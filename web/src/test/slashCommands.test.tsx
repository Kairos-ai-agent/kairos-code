/**
 * `/` completion: the list must only ever show commands the backend runs, and
 * Enter must still send a normal message when no list is open.
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { App as AntdApp } from 'antd';
import ChatComposer from '../components/ChatComposer';
import {
  knownSlashCommand, matchSlashCommands, slashTokenAt, type SlashCommand,
} from '../utils/slash';

const COMMANDS: SlashCommand[] = [
  { name: 'goal', help: 'set the goal' },
  { name: 'compact', help: 'how compaction works' },
  { name: 'remember', help: 'remember a key' },
];

function renderComposer(onSubmit = vi.fn()) {
  render(
    <AntdApp>
      <ChatComposer onSubmit={onSubmit} slashCommands={COMMANDS} />
    </AntdApp>,
  );
  return { box: screen.getByRole('textbox'), onSubmit };
}

// ------------------------------------------------------------ pure matching

describe('slashTokenAt', () => {
  it('finds the token at the start of the input', () => {
    expect(slashTokenAt('/com', 4)).toEqual({ query: 'com', start: 0, end: 4 });
  });

  it('finds a token after whitespace', () => {
    expect(slashTokenAt('do it /co', 9))
      .toEqual({ query: 'co', start: 6, end: 9 });
  });

  it('ignores a slash inside a word — a URL is not a command', () => {
    expect(slashTokenAt('https://exa', 11)).toBeNull();
    expect(slashTokenAt('see src/utils/x', 14)).toBeNull();
  });

  it('finds nothing once the token is finished', () => {
    expect(slashTokenAt('/goal now', 9)).toBeNull();
  });
});

describe('matchSlashCommands', () => {
  it('matches by prefix, case-insensitively', () => {
    expect(matchSlashCommands(COMMANDS, 'com').map((c) => c.name))
      .toEqual(['compact']);
    expect(matchSlashCommands(COMMANDS, 'RE').map((c) => c.name))
      .toEqual(['remember']);
  });

  it('offers everything for a bare slash', () => {
    expect(matchSlashCommands(COMMANDS, '')).toHaveLength(3);
  });
});

describe('knownSlashCommand', () => {
  it('accepts an exact command name, with or without an argument', () => {
    expect(knownSlashCommand('/goal', COMMANDS)).toEqual({ name: 'goal', arg: '' });
    expect(knownSlashCommand('/goal ship it', COMMANDS))
      .toEqual({ name: 'goal', arg: 'ship it' });
  });

  it('refuses anything the backend would not run', () => {
    // A path, a partial command and an unknown word all stay ordinary messages.
    expect(knownSlashCommand('/etc/hosts', COMMANDS)).toBeNull();
    expect(knownSlashCommand('/comp', COMMANDS)).toBeNull();
    expect(knownSlashCommand('/nope', COMMANDS)).toBeNull();
    expect(knownSlashCommand('hello', COMMANDS)).toBeNull();
  });

  it('offers nothing when the backend listed nothing', () => {
    expect(knownSlashCommand('/goal', [])).toBeNull();
  });
});

// ------------------------------------------------------------ the composer

describe('the composer slash list', () => {
  it('offers matching commands while you type and accepts one', () => {
    const { box, onSubmit } = renderComposer();

    fireEvent.change(box, { target: { value: '/com', selectionStart: 4 } });

    expect(screen.getByTestId('composer-slash-list')).toBeTruthy();
    expect(screen.getByText('/compact')).toBeTruthy();
    expect(screen.queryByText('/goal')).toBeNull();

    fireEvent.keyDown(box, { key: 'Enter' });

    // Enter picked the command instead of sending the half-typed one.
    expect(onSubmit).not.toHaveBeenCalled();
    expect((box as HTMLTextAreaElement).value).toBe('/compact ');
  });

  it('still sends a normal message when no list is open', () => {
    const { box, onSubmit } = renderComposer();

    fireEvent.change(box, { target: { value: 'do the thing' } });
    expect(screen.queryByTestId('composer-slash-list')).toBeNull();

    fireEvent.keyDown(box, { key: 'Enter' });

    expect(onSubmit).toHaveBeenCalled();
  });

  it('closes on Escape without touching the text', () => {
    const { box } = renderComposer();

    fireEvent.change(box, { target: { value: '/go', selectionStart: 3 } });
    expect(screen.getByTestId('composer-slash-list')).toBeTruthy();

    fireEvent.keyDown(box, { key: 'Escape' });

    expect(screen.queryByTestId('composer-slash-list')).toBeNull();
    expect((box as HTMLTextAreaElement).value).toBe('/go');
  });

  it('shows nothing at all when the backend listed no commands', () => {
    render(
      <AntdApp>
        <ChatComposer onSubmit={vi.fn()} slashCommands={[]} />
      </AntdApp>,
    );
    fireEvent.change(screen.getByRole('textbox'),
                    { target: { value: '/go', selectionStart: 3 } });
    expect(screen.queryByTestId('composer-slash-list')).toBeNull();
  });
});
