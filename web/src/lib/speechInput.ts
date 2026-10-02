/**
 * Dictation for the chat composer.
 *
 * Speech *synthesis* goes to the server (kairos/voice.py, a real engine).
 * Recognition has no server counterpart here -- sttProvider is "mock" and no
 * engine is installed -- so this uses the browser's own recogniser, which is
 * present in the Chromium kernel the desktop build ships. Nothing is recorded
 * or uploaded by this app: the words arrive as text and land in the box the
 * user is already typing in.
 *
 * Available in Chrome/Edge. `isSpeechInputSupported()` is false elsewhere, and
 * the composer hides the button rather than showing a control that cannot run.
 *
 * The silence timer is what makes hands-free use possible: a phrase is finished
 * when nothing new has been heard for a while, not when the user reaches for
 * the mouse.
 */

interface SpeechAlternative {
  readonly transcript: string;
}
interface SpeechResult {
  readonly length: number;
  readonly isFinal: boolean;
  [index: number]: SpeechAlternative;
}
interface SpeechResultList {
  readonly length: number;
  [index: number]: SpeechResult;
}
interface SpeechResultEvent {
  readonly resultIndex: number;
  readonly results: SpeechResultList;
}
interface SpeechErrorEvent {
  readonly error: string;
}
interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: SpeechResultEvent) => void) | null;
  onerror: ((event: SpeechErrorEvent) => void) | null;
  onend: (() => void) | null;
}
type RecognizerCtor = new () => SpeechRecognitionLike;

function recognizerCtor(): RecognizerCtor | null {
  const w = window as unknown as {
    SpeechRecognition?: RecognizerCtor;
    webkitSpeechRecognition?: RecognizerCtor;
  };
  return w.SpeechRecognition || w.webkitSpeechRecognition || null;
}

export function isSpeechInputSupported(): boolean {
  return recognizerCtor() !== null;
}

/**
 * The UI language widened to a tag a recogniser understands. The recogniser
 * wants a region ("zh-CN"), the app stores a language ("zh").
 */
export function speechInputLanguage(uiLang: string): string {
  const withRegion: Record<string, string> = {
    zh: 'zh-CN', en: 'en-US', ja: 'ja-JP', ko: 'ko-KR', de: 'de-DE',
    fr: 'fr-FR', es: 'es-ES', pt: 'pt-BR', ru: 'ru-RU', it: 'it-IT',
    ar: 'ar-SA', hi: 'hi-IN', vi: 'vi-VN', th: 'th-TH', id: 'id-ID',
    tr: 'tr-TR', nl: 'nl-NL', pl: 'pl-PL', uk: 'uk-UA', sv: 'sv-SE',
  };
  if (!uiLang) return 'en-US';
  return withRegion[uiLang] || (uiLang.includes('-') ? uiLang : 'en-US');
}

export interface Dictation {
  /** Stop quietly -- the caller is taking the mic away (unmount, agent busy). */
  stop(): void;
  /** Stop and treat what was heard as finished: the user pressed "send". */
  finishNow(): void;
}

/**
 * Listen until stopped, or until the speaker has been quiet for `silenceMs`.
 *
 * `onText` gets the full transcript so far on every update, interim guesses
 * included, so the box fills as the user speaks. `onSettle` fires exactly once
 * with the finished transcript -- on silence, on `finishNow()`, or because the
 * recogniser ended by itself (it does that on "no speech" and after a long
 * quiet spell; a phrase must not be lost to it). `onEnd` fires once after that,
 * and carries an error code when the microphone was refused or died: callers
 * must not restart listening then, or a denied mic turns into a restart loop.
 *
 * Returns null when the browser has no recogniser, or when one is already
 * listening in this window.
 */
export function startDictation(options: {
  language: string;
  /** Quiet time that ends a phrase. 0 disables it (push-to-talk). */
  silenceMs?: number;
  onText: (transcript: string) => void;
  onSettle?: (transcript: string) => void;
  onEnd: (error?: string) => void;
}): Dictation | null {
  const Ctor = recognizerCtor();
  if (!Ctor) return null;

  const recognition = new Ctor();
  recognition.lang = speechInputLanguage(options.language);
  recognition.continuous = true;
  recognition.interimResults = true;

  const silenceMs = options.silenceMs ?? 0;
  let settledText = '';
  let interimText = '';
  let timer: number | undefined;
  let ended = false;
  let alreadySettled = false;
  let quiet = false; // stop() was called: whatever was heard is not a phrase
  let lastError: string | undefined;

  const transcript = () => (settledText + interimText).trim();

  const clearTimer = () => {
    if (timer !== undefined) {
      window.clearTimeout(timer);
      timer = undefined;
    }
  };

  const settle = () => {
    if (alreadySettled) return;
    alreadySettled = true;
    clearTimer();
    options.onSettle?.(transcript());
  };

  const finish = () => {
    if (ended) return;
    ended = true;
    clearTimer();
    options.onEnd(lastError);
  };

  const armSilenceTimer = () => {
    if (!silenceMs) return;
    clearTimer();
    timer = window.setTimeout(() => {
      settle();
      try {
        recognition.stop();
      } catch {
        finish();
      }
    }, silenceMs);
  };

  recognition.onresult = (event) => {
    let interim = '';
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const result = event.results[i];
      const phrase = result[0] ? result[0].transcript : '';
      if (result.isFinal) settledText += phrase;
      else interim += phrase;
    }
    interimText = interim;
    options.onText(transcript());
    armSilenceTimer();
  };
  // Both engines follow an error with onend, so there is a single exit from the
  // listening state; the code is remembered so the caller can stop retrying.
  recognition.onerror = (event) => {
    lastError = (event && event.error) || 'error';
  };
  recognition.onend = () => {
    if (!quiet) settle();
    finish();
  };

  try {
    recognition.start();
  } catch {
    return null; // a recogniser is already running in this window
  }
  return {
    stop: () => {
      quiet = true;
      try {
        recognition.stop();
      } catch {
        finish();
      }
    },
    finishNow: () => {
      try {
        recognition.stop();
      } catch {
        settle();
        finish();
      }
    },
  };
}
