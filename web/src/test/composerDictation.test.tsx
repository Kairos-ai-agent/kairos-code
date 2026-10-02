/**
 * The microphone button in the composer.
 *
 * Two things are worth holding down here. The button must not exist where no
 * recogniser does (Firefox, and jsdom's bare window) -- a mic that cannot
 * listen is worse than no mic. And it must go dark when the recogniser stops
 * *by itself*, which it does on silence or a refused permission: a button that
 * stays lit after that is a button the user cannot turn off.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { App, ConfigProvider } from 'antd';

import ChatComposer from '../components/ChatComposer';
import { useSettingsStore } from '../stores/settingsStore';

vi.mock('../api/client', () => ({
  default: { post: vi.fn(), delete: vi.fn() },
}));

const Wrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <ConfigProvider><App>{children}</App></ConfigProvider>
);

type Handler = ((event: unknown) => void) | null;

interface FakeResult {
  length: number;
  isFinal: boolean;
  [index: number]: { transcript: string };
}

class FakeRecognition {
  static last: FakeRecognition | null = null;

  lang = '';
  continuous = false;
  interimResults = false;
  onresult: Handler = null;
  onerror: Handler = null;
  onend: (() => void) | null = null;
  started = false;

  constructor() {
    FakeRecognition.last = this;
  }

  start = () => { this.started = true; };

  stop = () => {
    // Both engines call onend after a stop, so the fake does too.
    this.onend?.();
  };

  abort = () => {};

  /** Speak a phrase, final or interim, the way a recogniser reports it. */
  say(phrases: string[], isFinal: boolean) {
    const results = phrases.map((transcript) => ({
      length: 1, isFinal, 0: { transcript },
    })) as unknown as FakeResult[];
    this.onresult?.({ resultIndex: 0, results });
  }
}

const installRecogniser = () => {
  (window as unknown as { SpeechRecognition?: unknown }).SpeechRecognition =
    FakeRecognition;
};

describe('composer dictation', () => {
  beforeEach(() => {
    FakeRecognition.last = null;
    delete (window as unknown as { SpeechRecognition?: unknown }).SpeechRecognition;
  });

  afterEach(() => {
    delete (window as unknown as { SpeechRecognition?: unknown }).SpeechRecognition;
  });

  it('has no mic button at all when the browser cannot listen', () => {
    render(<ChatComposer onSubmit={vi.fn()} />, { wrapper: Wrapper });
    expect(screen.queryByTestId('composer-mic')).not.toBeInTheDocument();
  });

  it('dictates into the message box once a recogniser exists', () => {
    installRecogniser();
    render(<ChatComposer onSubmit={vi.fn()} />, { wrapper: Wrapper });

    const mic = screen.getByTestId('composer-mic');
    fireEvent.click(mic);

    expect(FakeRecognition.last).not.toBeNull();
    expect(FakeRecognition.last?.started).toBe(true);
    expect(FakeRecognition.last?.lang).not.toBe('');
    // while listening, the button is the way to stop
    expect(mic.getAttribute('aria-label')).toBe('Stop dictating');

    act(() => { FakeRecognition.last?.say(['hello there'], true); });
    expect(screen.getByRole('textbox')).toHaveValue('hello there');
  });

  it('goes dark when the recogniser ends on its own', () => {
    installRecogniser();
    render(<ChatComposer onSubmit={vi.fn()} />, { wrapper: Wrapper });

    const mic = screen.getByTestId('composer-mic');
    fireEvent.click(mic);
    expect(mic.getAttribute('aria-label')).toBe('Stop dictating');

    // Silence, or a refused permission: no stop() was ever called.
    act(() => { FakeRecognition.last?.onend?.(); });
    expect(mic.getAttribute('aria-label')).toBe('Dictate');
  });


describe('hands-free voice mode', () => {
  const setVoiceMode = (on: boolean) => {
    useSettingsStore.setState((s) => ({ voice: { ...s.voice, voiceMode: on } }));
  };

  beforeEach(() => {
    installRecogniser();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    setVoiceMode(false);
  });

  it('opens the mic by itself whenever the agent is idle', () => {
    setVoiceMode(true);
    render(<ChatComposer onSubmit={vi.fn()} />, { wrapper: Wrapper });
    expect(FakeRecognition.last?.started).toBe(true);
    expect(screen.getByTestId('composer-mic').getAttribute('aria-label'))
      .toBe('Stop dictating');
  });

  it('sends by itself after five seconds of quiet, and empties the box', async () => {
    setVoiceMode(true);
    const onSubmit = vi.fn(() => Promise.resolve());
    render(<ChatComposer onSubmit={onSubmit} />, { wrapper: Wrapper });

    act(() => { FakeRecognition.last?.say(['book a table for two'], true); });
    expect(screen.getByRole('textbox')).toHaveValue('book a table for two');

    await act(async () => { await vi.advanceTimersByTimeAsync(5100); });

    expect(onSubmit).toHaveBeenCalledWith('book a table for two', []);
    // The spoken text must not linger: the next turn starts from a clean box.
    expect(screen.getByRole('textbox')).toHaveValue('');
  });

  it('stays quiet while the agent is working, then listens again', () => {
    setVoiceMode(true);
    const { rerender } = render(
      <ChatComposer onSubmit={vi.fn()} busy />, { wrapper: Wrapper });
    // Nothing may listen while the agent is replying -- the recogniser would
    // otherwise transcribe the agent's own voice and send it back.
    expect(FakeRecognition.last).toBeNull();
    expect(screen.getByTestId('composer-mic')).toBeDisabled();

    rerender(<ChatComposer onSubmit={vi.fn()} busy={false} />);
    expect(FakeRecognition.last?.started).toBe(true);
  });

  it('does not auto-send the phrase when voice mode is off', async () => {
    const onSubmit = vi.fn(() => Promise.resolve());
    render(<ChatComposer onSubmit={onSubmit} />, { wrapper: Wrapper });
    fireEvent.click(screen.getByTestId('composer-mic'));
    act(() => { FakeRecognition.last?.say(['hello'], true); });

    await act(async () => { await vi.advanceTimersByTimeAsync(5100); });
    // Push-to-talk: the user decides when it is done, so the words stay put.
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole('textbox')).toHaveValue('hello');
  });
});
});
