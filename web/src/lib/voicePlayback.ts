/**
 * Speaking a reply.
 *
 * Two engines, tried in order:
 *   1. the app's own server (`/api/voice/speak`) — the neural voice set, which
 *      is the point of "pick any voice": ~300 names, not the handful an OS ships;
 *   2. the browser's Web Speech API — whatever the machine already has.
 *
 * The second is not a nicety. It is what keeps voice mode usable when the app
 * was built without the synthesis engine (a binary from an older release, a
 * container without the extra) or when the machine is offline. A silent audio
 * clip is not a fallback, so the mock engine is treated as "no engine".
 */
import { create } from 'zustand';
import { distillForSpeech, synthesizeSpeech } from '../api/client';

/** Which engine actually produced the audio. Surfaced by the status line. */
export type SpokenBy = 'server' | 'browser' | 'silent';

export interface SpeakRequest {
  /** Voice name, e.g. `zh-CN-XiaoxiaoNeural`. Empty = let the server choose. */
  voice?: string;
  /** Percent, -90..200. */
  rate?: number;
  /** Hertz, -100..100. */
  pitch?: number;
  volume?: number;
  maxChars?: number;
  /** From the settings drawer. `auto` tries the server first. */
  engine?: 'auto' | 'server' | 'browser';
}

let currentAudio: HTMLAudioElement | null = null;
let currentUrl: string | null = null;

/** UI state: true while anything is being spoken, so we can offer a stop. */
export const useVoicePlayback = create<{
  speaking: boolean;
  spokenBy: SpokenBy | null;
  setState: (speaking: boolean, spokenBy?: SpokenBy | null) => void;
}>((set) => ({
  speaking: false,
  spokenBy: null,
  setState: (speaking, spokenBy = null) => set({ speaking, spokenBy }),
}));

export function stopSpeaking(): void {
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }
  if (currentUrl) {
    URL.revokeObjectURL(currentUrl);
    currentUrl = null;
  }
  if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
    window.speechSynthesis.cancel();
  }
  useVoicePlayback.getState().setState(false, null);
}

function playBlob(blob: Blob): Promise<void> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    currentAudio = audio;
    currentUrl = url;
    const done = () => {
      URL.revokeObjectURL(url);
      if (currentUrl === url) currentUrl = null;
      if (currentAudio === audio) currentAudio = null;
      resolve();
    };
    audio.onended = done;
    audio.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error('audio playback failed'));
    };
    audio.play().catch(reject);
  });
}

/** Every voice the browser/OS offers, shaped like the server's list. */
export function browserVoices(): Array<{
  name: string; locale: string; gender: string; friendly: string;
}> {
  if (typeof window === 'undefined' || !('speechSynthesis' in window)) return [];
  return window.speechSynthesis.getVoices().map((v) => ({
    name: v.name,
    locale: v.lang || '',
    gender: '',
    friendly: v.name,
  }));
}

/**
 * Match a requested voice (a server name such as `zh-CN-XiaoxiaoNeural`) to a
 * browser voice, falling back to the locale so a Chinese reply is at least read
 * by a Chinese voice rather than the system default English one.
 */
function pickBrowserVoice(
  wanted: string, locale: string,
): SpeechSynthesisVoice | undefined {
  const voices = window.speechSynthesis.getVoices();
  if (!voices.length) return undefined;
  const exact = voices.find((v) => v.name === wanted);
  if (exact) return exact;
  const stem = wanted.split('-').slice(0, 2).join('-');
  const byStem = voices.find((v) => v.name.startsWith(stem));
  if (byStem) return byStem;
  if (locale) {
    const byLocale = voices.find((v) => v.lang.toLowerCase()
      .startsWith(locale.toLowerCase().split('-')[0]));
    if (byLocale) return byLocale;
  }
  return voices[0];
}

/** Does `text` read as Chinese/Japanese/Korean? Used to pick a default voice. */
function isCjk(text: string): boolean {
  return /[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef\uac00-\ud7af]/.test(text);
}

function speakWithBrowser(
  text: string, wanted: string, rate: number, pitch: number, volume: number,
): Promise<void> {
  return new Promise((resolve) => {
    if (typeof window === 'undefined' || !('speechSynthesis' in window)) {
      resolve();
      return;
    }
    const u = new SpeechSynthesisUtterance(text);
    const locale = wanted ? wanted.split('-').slice(0, 2).join('-') : '';
    const voice = pickBrowserVoice(wanted, locale);
    if (voice) u.voice = voice;
    // The sliders are server-style percentages around 0; Web Speech wants a
    // plain multiplier around 1.
    u.rate = Math.min(2, Math.max(0.5, 1 + rate / 100));
    u.pitch = Math.min(2, Math.max(0, 1 + pitch / 100));
    u.volume = Math.min(1, Math.max(0, 1 + volume / 100));
    u.onend = () => resolve();
    u.onerror = () => resolve();
    window.speechSynthesis.speak(u);
  });
}

/**
 * Speak `text` aloud. Resolves once playback finishes (or immediately when
 * there is nothing to say), so callers can chain without leaking promises.
 */
export async function speak(text: string, opts: SpeakRequest = {}): Promise<SpokenBy> {
  const body = (text || '').trim();
  if (!body) return 'silent';

  stopSpeaking();
  const store = useVoicePlayback.getState();
  const engine = opts.engine ?? 'auto';
  const wanted = opts.voice || (isCjk(body) ? 'zh-CN-XiaoxiaoNeural' : '');
  const rate = opts.rate ?? 0;
  const pitch = opts.pitch ?? 0;
  const volume = opts.volume ?? 0;

  if (engine !== 'browser') {
    try {
      const result = await synthesizeSpeech(body, {
        voice: wanted, rate, pitch, volume, max_chars: opts.maxChars,
      });
      if (result === null) return 'silent';           // nothing speakable
      if (result.engine && result.engine !== 'mock') { // a real engine answered
        try {
          store.setState(true, 'server');
          await playBlob(result.blob);
          return 'server';
        } catch {
          // The audio element refused to start. Autoplay policy does exactly
          // this before anything has been played from the origin, and a reply
          // that is silently dropped is the failure this module exists to
          // prevent -- so fall through to the browser voice instead of going
          // quiet. The user still hears the answer, just in the other voice.
        } finally {
          store.setState(false, null);
        }
      }
      // A mock engine returns silence — fall through to the browser instead of
      // playing it, which would look like voice mode being broken.
    } catch {
      // Server down or the endpoint missing: the browser still works.
    }
  }

  // Browser path. Ask the server to distil first so we read the same text the
  // server engine would have; if the server cannot help, read what we were
  // given rather than saying nothing.
  let spoken = body;
  try {
    const d = await distillForSpeech(body, opts.maxChars);
    if (d?.spoken) spoken = d.spoken;
  } catch {
    /* offline: read the raw reply */
  }
  if (!spoken.trim()) return 'silent';
  if (typeof window === 'undefined' || !('speechSynthesis' in window)) return 'silent';

  store.setState(true, 'browser');
  await speakWithBrowser(spoken, wanted, rate, pitch, volume);
  store.setState(false, null);
  return 'browser';
}
