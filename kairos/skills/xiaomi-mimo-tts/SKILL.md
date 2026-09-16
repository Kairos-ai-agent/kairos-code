---
name: "xiaomi-mimo-tts"
description: "Xiaomi MiMo V2.5 TTS API integration. Covers all 3 models (standard, voice-design, voice-clone), correct request format, voice list, and pitfalls. Use when building TTS features, voice synthesis, or i"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\software-development\\gen-api-integration\\references\\xiaomi-mimo-tts\\SKILL.md"
---
# Xiaomi MiMo V2.5 TTS API

## Critical: API Format (NOT OpenAI-compatible!)

MiMo TTS uses the **chat completions** endpoint, NOT `/v1/audio/speech`.

### Endpoint
```
POST https://api.xiaomimimo.com/v1/chat/completions
POST https://token-plan-cn.xiaomimimo.com/v1/chat/completions  (Token Plan)
```

### Authentication
```
Header: api-key: YOUR_KEY
NOT: Authorization: Bearer YOUR_KEY
```

### Models
| Model | Description |
|-------|-------------|
| `mimo-v2.5-tts` | Standard TTS with preset voices. Supports singing mode. |
| `mimo-v2.5-tts-voicedesign` | Create custom voices from text description. NO singing, NO preset voices, NO cloning. |
| `mimo-v2.5-tts-voiceclone` | Clone voice from reference audio. NO singing, NO preset voices, NO design. |

### Preset Voices (for mimo-v2.5-tts ONLY)
| Voice ID | Language | Gender |
|----------|----------|--------|
| mimo_default | varies by cluster (CN=冰糖) | - |
| 冰糖 | Chinese | Female |
| 茉莉 | Chinese | Female |
| 苏打 | Chinese | Male |
| 白桦 | Chinese | Male |
| Mia | English | Female |
| Chloe | English | Female |
| Milo | English | Male |
| Dean | English | Male |

## Message Format (CRITICAL — ALL models use the same role convention)

**For ALL three models:**
- `assistant` message = **text to be spoken** (MUST be in assistant role)
- `user` message = **voice/style description** (optional for standard, required for voicedesign)

The official docs explicitly state: "语音合成的目标文本需填写在 role 为 assistant 的消息中，不可放在 user 角色的消息内"

### Standard TTS
```json
{
  "model": "mimo-v2.5-tts",
  "messages": [
    {"role": "user", "content": "温暖自然的语调"},
    {"role": "assistant", "content": "要朗读的文本内容"}
  ],
  "audio": {"format": "wav", "voice": "冰糖"}
### Voice Design
```json
{
  "model": "mimo-v2.5-tts-voicedesign",
  "messages": [
    {"role": "user", "content": "20岁甜美女声，温柔自然"},
    {"role": "assistant", "content": "要朗读的文本内容"}
  ],
  "audio": {"format": "wav", "optimize_text_preview": true}
}
```
- `user` = voice description (REQUIRED for voicedesign)
- `assistant` = text to speak (MUST be in assistant role per official docs)
- `audio.optimize_text_preview: true` can auto-generate fitting text if assistant omitted
- **DO NOT include `audio.voice`** — API returns 400

### Voice Clone
```json
{
  "model": "mimo-v2.5-tts-voiceclone",
  "messages": [
    {"role": "user", "content": ""},
    {"role": "assistant", "content": "要朗读的文本内容"}
  ],
  "audio": {
    "format": "wav",
    "voice": "data:audio/mpeg;base64,BASE64_AUDIO_DATA"
  }
}
```
- `audio.voice` = reference audio as **full data URL**: `data:{MIME};base64,$BASE64_AUDIO`
- Max 10MB base64 encoded
- Supports mp3 and wav reference audio
- `user` message can be empty for basic cloning, or add style instructions
- **Text to speak MUST be in assistant role** (same as all other models)

## Style Control

**Full tag catalog:** See `references/style-tags-catalog.md` for complete list of all supported style tags, audio tags, and inline tags.

Two methods:
Direct style description in the `user` content. Supports:
- Multi-style transitions: 播报→低语→嘶吼
- Mixed emotions: 压抑的愤怒, 带着哽咽的笑意
- Fine-grained control: paragraph → sentence → word → character level
- Director mode: 角色 + 场景 + 指导 三维度描述

### Audio Tags (in assistant message)
Embed tags directly in the text to speak:
- Style tags at start: `(开心)你好啊`, `(东北话)哎呀妈呀`
- Inline tags: `(深呼吸)呼……冷静`, `[笑]哈哈哈`
- Supported emotions: 开心/悲伤/愤怒/恐惧/惊讶/兴奋/委屈/平静/冷漠
- Voice traits: 磁性/醇厚/清亮/空灵/甜美/沙哑
- Dialects: 东北话/四川话/河南话/粤语
- Audio effects: 吸气/深呼吸/叹气/笑/抽泣/哽咽/颤抖

## Response Format
```json
{
  "choices": [{
    "message": {
      "audio": {
        "data": "base64_encoded_wav_audio"
      }
    }
  }]
}
```
Convert: `atob(data)` → `Uint8Array` → `new Blob([bytes], {type: "audio/wav"})`

## Streaming
Currently in compatibility mode (returns full result in streaming format). Use `pcm16` format for streaming, then convert to wav at 24kHz sample rate.

## Pitfalls

1. **Text MUST be in assistant role** — putting text-to-speak in user role will cause the user to describe a voice instead of speaking
2. **Voice design model rejects `audio.voice`** — always omit for voicedesign, returns 400 otherwise
3. **Voice clone uses `audio.voice`** with data URL prefix, NOT `audio.ref_audio` with stripped base64
4. **Auth header is `api-key`**, not `Authorization: Bearer`
5. **Endpoint is `/v1/chat/completions`**, not `/v1/audio/speech`
6. **Preset voice names are Chinese** — 冰糖/茉莉/苏打/白桦, not Alloy/Echo/Fable
7. **Style tags go in assistant content**, natural language style goes in user content
8. **Store audio as data URLs** (`FileReader.readAsDataURL`), NOT blob URLs (`URL.createObjectURL`), so they work as voice clone references. Blob URLs (`blob:http://...`) are INVALID for the API.
9. **Voice clone `audio.voice` needs full data URL** with MIME prefix: `data:audio/wav;base64,...`. Stripped base64 does NOT work.

### Dialect Control — Model-Specific Behavior

| Model | Dialect Method | Example |
|-------|---------------|---------|
| `mimo-v2.5-tts` | Audio tag in assistant: `(东北话)文本` | `(粤语)呢个真係好正啊` |
| `mimo-v2.5-tts-voicedesign` | Write in user voice description | `"四川话口音，20岁甜美女声"` |
| `mimo-v2.5-tts-voiceclone` | Audio tag in assistant: `(东北话)文本` | Same as standard |

**Voicedesign does NOT support audio tags** — `(方言)` in assistant content is ignored. Must describe the dialect in the user message as part of voice characteristics.

**UI implication:** Hide language/dialect selector when model is voicedesign or voiceclone. Show only for standard TTS.

**Dialect reliability (2026-06):** Even with correct format, the model may not produce audible dialect differences on Mandarin text. Dialect tags work best when text is already written in the target dialect style (e.g. "哎呀妈呀" for 东北话). Test with official example texts first to verify model support.
