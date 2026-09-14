import { describe, expect, it } from 'vitest';

import { matchPreset } from '../llm/presets';

/**
 * A preset is a *choice the user makes*, not something to be re-derived from whatever
 * happens to be in the fields. Reported behaviour: the user selected a custom URL, the
 * built-in endpoint list then snapped back to "DeepSeek", and chat no longer worked —
 * because the drawer infers the preset from (endpointUrl, model) on every edit, and the
 * inference is far too eager: a model *name* alone beats an explicit endpoint, and any
 * host that merely *contains* a preset's host matches it.
 */
describe('matchPreset', () => {
  it('recognises a genuine preset endpoint', () => {
    expect(matchPreset('https://api.deepseek.com/v1/chat/completions', 'deepseek-chat'))
      .toBe('deepseek');
    expect(matchPreset('https://api.openai.com/v1/chat/completions', 'gpt-4o'))
      .toBe('openai');
  });

  it('does not infer a provider from the model name alone', () => {
    // The reported case: custom URL + a model called deepseek-* must stay custom.
    expect(matchPreset('https://my-gateway.example.com/v1/chat/completions', 'deepseek-chat'))
      .toBe('custom');
    expect(matchPreset('https://llm.internal/v1', 'qwen-max')).toBe('custom');
  });

  it('does not treat a host that merely contains a preset host as that preset', () => {
    expect(matchPreset('https://api.deepseek.com.mirror.example/v1/chat/completions', 'x'))
      .toBe('custom');
    expect(matchPreset('https://notapi.openai.com.evil.example/v1', 'gpt-4o'))
      .toBe('custom');
  });

  it('falls back to the OpenAI default when nothing is configured', () => {
    expect(matchPreset('', '')).toBe('openai');
  });
});
