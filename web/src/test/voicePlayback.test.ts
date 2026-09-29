/**
 * Voice playback.
 *
 * The interesting behaviour is the fallback chain, so these tests spend most of
 * their effort there: a server with a real engine, a server whose engine is the
 * silent placeholder, and a server that is not there at all. The last two must
 * end up in the browser, because "voice mode is on but nothing is audible" is
 * the exact failure this module exists to prevent.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const synthesizeSpeech = vi.fn();
const distillForSpeech = vi.fn();

vi.mock('../api/client', () => ({
  synthesizeSpeech: (...args: unknown[]) => synthesizeSpeech(...args),
  distillForSpeech: (...args: unknown[]) => distillForSpeech(...args),
}));

import {
  speak, stopSpeaking, browserVoices, useVoicePlayback,
} from '../lib/voicePlayback';

// ---------------------------------------------------------------- test doubles

class FakeAudio {
  static instances: FakeAudio[] = [];
  src: string;
  played = false;
  paused = false;
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(src: string) {
    this.src = src;
    FakeAudio.instances.push(this);
  }

  play() {
    this.played = true;
    return Promise.resolve();
  }

  pause() {
    this.paused = true;
  }

  /** Simulate playback finishing. */
  finish() {
    this.onended?.();
  }
}

type Utterance = {
  text: string;
  rate: number;
  pitch: number;
  volume: number;
  voice: unknown;
  onend: (() => void) | null;
};

const spoken: Utterance[] = [];

/** jsdom has neither of these, so the Web Speech API is stubbed wholesale. */
class FakeUtterance {
  text: string;
  rate = 1;
  pitch = 1;
  volume = 1;
  voice: unknown = null;
  onend: (() => void) | null = null;

  constructor(text: string) {
    this.text = text;
  }
}

function installBrowserSpeech(voices: Array<{ name: string; lang: string }> = []) {
  const list = voices.map((v) => ({ name: v.name, lang: v.lang }));
  (globalThis as any).SpeechSynthesisUtterance = FakeUtterance;
  (window as any).speechSynthesis = {
    getVoices: () => list,
    speak: (u: Utterance) => {
      spoken.push(u);
      u.onend?.();
    },
    cancel: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  };
}

afterEach(() => {
  delete (window as any).speechSynthesis;
  delete (globalThis as any).SpeechSynthesisUtterance;
  delete (globalThis as any).Audio;
});

/** Wait for a condition instead of guessing at the number of microtasks. */
async function until(predicate: () => boolean, ms = 500): Promise<boolean> {
  const started = Date.now();
  while (!predicate() && Date.now() - started < ms) {
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  return predicate();
}

beforeEach(() => {
  vi.clearAllMocks();
  spoken.length = 0;
  FakeAudio.instances = [];
  (globalThis as any).Audio = FakeAudio;
  (URL as any).createObjectURL = vi.fn(() => 'blob:demo');
  (URL as any).revokeObjectURL = vi.fn();
  distillForSpeech.mockResolvedValue({ spoken: 'distilled', original_chars: 20, spoken_chars: 9 });
  installBrowserSpeech([{ name: 'Microsoft Huihui', lang: 'zh-CN' }]);
  useVoicePlayback.getState().setState(false, null);
});

// ---------------------------------------------------------------------- tests

describe('speak', () => {
  it('uses the server engine and plays the audio it returns', async () => {
    synthesizeSpeech.mockResolvedValue({
      blob: new Blob(['x']), engine: 'edge', spokenChars: 5,
    });

    const played = speak('你好', { voice: 'zh-CN-XiaoxiaoNeural' });
    // Wait for the audio element to exist rather than counting microtasks.
    expect(await until(() => FakeAudio.instances.length > 0)).toBe(true);
    FakeAudio.instances[0].finish();

    expect(await played).toBe('server');
    expect(FakeAudio.instances[0].played).toBe(true);
    expect(spoken).toHaveLength(0);         // the browser was not needed
    expect(useVoicePlayback.getState().speaking).toBe(false);
  });

  it('treats the mock engine as no engine and falls back to the browser', async () => {
    // A mock engine answers 200 with silence. Playing it would look like voice
    // mode being broken, so the browser's voices have to take over.
    synthesizeSpeech.mockResolvedValue({
      blob: new Blob(['silence']), engine: 'mock', spokenChars: 5,
    });

    expect(await speak('你好')).toBe('browser');
    expect(spoken).toHaveLength(1);
    expect(spoken[0].text).toBe('distilled');
    expect(FakeAudio.instances).toHaveLength(0);
  });

  it('falls back to the browser when the server is unreachable', async () => {
    synthesizeSpeech.mockRejectedValue(new Error('connect ECONNREFUSED'));

    expect(await speak('你好')).toBe('browser');
    expect(spoken).toHaveLength(1);
  });

  it('reports silence when there is nothing speakable', async () => {
    synthesizeSpeech.mockResolvedValue(null);   // the route answered 204

    expect(await speak('```py\nprint(1)\n```')).toBe('silent');
    expect(spoken).toHaveLength(0);
  });

  it('skips the server entirely when the engine is set to browser', async () => {
    expect(await speak('你好', { engine: 'browser' })).toBe('browser');
    expect(synthesizeSpeech).not.toHaveBeenCalled();
  });

  it('maps the sliders onto the Web Speech API range', async () => {
    synthesizeSpeech.mockResolvedValue(null);
    await speak('你好', { engine: 'browser', rate: 20, pitch: -25, volume: 0 });

    // Server-style percentages around zero become multipliers around one.
    expect(spoken[0].rate).toBeCloseTo(1.2);
    expect(spoken[0].pitch).toBeCloseTo(0.75);
    expect(spoken[0].volume).toBeCloseTo(1);
  });

  it('still reads the raw text when even the distiller is unreachable', async () => {
    // The server is down: no audio, and no distiller either.
    synthesizeSpeech.mockRejectedValue(new Error('offline'));
    distillForSpeech.mockRejectedValue(new Error('offline'));

    expect(await speak('说点什么')).toBe('browser');
    expect(spoken[0].text).toBe('说点什么');
  });

  it('says nothing for an empty reply', async () => {
    expect(await speak('   ')).toBe('silent');
    expect(synthesizeSpeech).not.toHaveBeenCalled();
  });
});

describe('stopSpeaking', () => {
  it('pauses the audio element and clears the speaking state', () => {
    useVoicePlayback.getState().setState(true, 'server');
    const audio = new FakeAudio('blob:x');
    stopSpeaking();
    expect((window as any).speechSynthesis.cancel).toHaveBeenCalled();
    expect(useVoicePlayback.getState().speaking).toBe(false);
    expect(audio).toBeTruthy();
  });
});

describe('browserVoices', () => {
  it('shapes the platform voices like the server list', () => {
    installBrowserSpeech([{ name: 'Microsoft Huihui', lang: 'zh-CN' }]);
    expect(browserVoices()).toEqual([
      { name: 'Microsoft Huihui', locale: 'zh-CN', gender: '', friendly: 'Microsoft Huihui' },
    ]);
  });

  it('returns nothing when the browser has no speech support', () => {
    delete (window as any).speechSynthesis;
    expect(browserVoices()).toEqual([]);
  });
});
