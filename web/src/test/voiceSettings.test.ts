/**
 * The voice settings that are more than a field assignment:
 *
 *   * voice mode is "write for the ear *and* speak it", so switching it on has
 *     to switch the speaking setting on with it — otherwise the user picks
 *     voice mode and hears nothing;
 *   * v1 shipped a hard-coded English voice, which reads a Chinese reply in
 *     English. The upgrade has to drop that default without touching a voice
 *     the user actually chose.
 */
import { describe, it, expect, beforeEach } from 'vitest';

import { useSettingsStore } from '../stores/settingsStore';

const migrate = () =>
  (useSettingsStore.persist.getOptions() as unknown as {
    migrate: (state: any, version: number) => any;
  }).migrate;

beforeEach(() => {
  useSettingsStore.setState({
    voice: {
      ttsProvider: 'edge',
      ttsVoice: 'en-US-AriaNeural',
      sttProvider: 'mock',
      sttLanguage: 'en',
      autoPlay: false,
      voiceMode: false,
      rate: 0,
      pitch: 0,
      volume: 0,
      engine: 'auto',
    },
  });
});

describe('setVoice', () => {
  it('switching voice mode on also switches the speaking switch on', () => {
    useSettingsStore.getState().setVoice({ voiceMode: true });
    const voice = useSettingsStore.getState().voice;
    expect(voice.voiceMode).toBe(true);
    expect(voice.autoPlay).toBe(true);
  });

  it('switching voice mode off leaves the speaking switch where it was', () => {
    useSettingsStore.getState().setVoice({ voiceMode: true });
    useSettingsStore.getState().setVoice({ voiceMode: false });
    expect(useSettingsStore.getState().voice.autoPlay).toBe(true);
  });

  it('keeps unrelated fields intact', () => {
    useSettingsStore.getState().setVoice({ rate: 30 });
    const voice = useSettingsStore.getState().voice;
    expect(voice.rate).toBe(30);
    expect(voice.ttsVoice).toBe('en-US-AriaNeural');
    expect(voice.engine).toBe('auto');
  });
});

describe('v1 -> v2 migration', () => {
  it('drops the old default English voice so the reply language decides', () => {
    const persisted = migrate()({ voice: { ttsVoice: 'en-US-AriaNeural' } }, 1);
    expect(persisted.voice.ttsVoice).toBe('');
  });

  it('leaves a voice the user actually chose alone', () => {
    const persisted = migrate()({ voice: { ttsVoice: 'zh-CN-YunxiNeural' } }, 1);
    expect(persisted.voice.ttsVoice).toBe('zh-CN-YunxiNeural');
  });

  it('does not touch a payload that is already current', () => {
    const payload = { voice: { ttsVoice: 'en-US-AriaNeural' } };
    expect(migrate()(payload, 2).voice.ttsVoice).toBe('en-US-AriaNeural');
  });

  it('survives a payload with no voice block at all', () => {
    expect(() => migrate()({}, 1)).not.toThrow();
  });
});
