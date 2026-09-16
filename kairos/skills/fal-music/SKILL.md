---
name: "fal-music"
description: "Generate music and audio using AI. Use when the user asks to create, generate, or make music, songs, audio, melodies, tracks, or beats. Supports text-to-music with multiple models (Stable Audio, Beato"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\openclaw-imports\\fal-music\\SKILL.md"
---
# FAL Music - AI Music Generation

Generate music using FAL.ai — access to Stable Audio Open, Stable Audio 2.5, and Beatoven Maestro through a single API.

## Setup

Uses the same FAL API key as fal-video-skill. If not already configured:

```bash
python3 ~/.claude/skills/fal-music-skill/fal_music_skill.py config YOUR_API_KEY
```

Or set environment variable:
```bash
export FAL_KEY="your_api_key"
```

## Commands

### Generate Music

```bash
python3 ~/.claude/skills/fal-music-skill/fal_music_skill.py generate "your prompt" [OPTIONS]
```

**Arguments:**
- `PROMPT` - Music description (e.g., "dark synthwave with driving bass and arpeggiated synths")
- `--model, -m` - Model to use (default: stable-audio-25)
- `--duration, -d` - Duration in seconds (default: 60)
- `--steps, -s` - Inference/refinement steps
- `--guidance, -g` - Guidance scale (stable-audio-25 only)
- `--creativity` - Creativity level 1-20 (beatoven only)
- `--negative-prompt, -n` - What to avoid (beatoven only)
- `--seed` - Random seed for reproducibility
- `--output, -o` - Output file path
- `--timeout, -t` - Timeout in seconds (default: 300)

**Examples:**
```bash
# Synthwave track
python3 fal_music_skill.py generate "dark synthwave instrumental, driving bassline, analog arpeggios, 120 BPM" -d 90

# Ambient music with Beatoven
python3 fal_music_skill.py generate "ambient electronic, atmospheric pads, gentle evolving textures" -m beatoven -d 120

# Short loop with Stable Audio Open
python3 fal_music_skill.py generate "128 BPM techno drum loop with acid bass" -m stable-audio -d 30

# Custom output path
python3 fal_music_skill.py generate "epic orchestral trailer music" -o ~/music/trailer.wav
```

### List Models

```bash
python3 ~/.claude/skills/fal-music-skill/fal_music_skill.py models
```

### Check Config

```bash
python3 ~/.claude/skills/fal-music-skill/fal_music_skill.py config
```

## Available Models

| Model | Quality | Max Duration | Cost | Best For |
|-------|---------|-------------|------|----------|
| `stable-audio` | Good | 47s | Free | Loops, SFX, short clips |
| `stable-audio-25` | Excellent | 190s | ~$0.20/gen | Full tracks, high quality |
| `beatoven` | Best | 150s | Varies | Full songs, compositions |

## Output

Audio files saved to `~/.claude/skills/fal-music-skill/output/` by default.

JSON output includes:
```json
{
  "status": "success",
  "model": "stable-audio-25",
  "file": "/path/to/audio.wav",
  "url": "https://fal.media/...",
  "duration": 60,
  "prompt": "..."
}
```

## Prompt Tips

Good prompts for music generation:
- Include genre: "synthwave", "ambient", "techno", "orchestral"
- Specify tempo: "120 BPM", "slow tempo", "driving rhythm"
- Mention instruments: "analog synths", "electric guitar", "piano"
- Describe mood: "dark", "uplifting", "melancholic", "energetic"
- Add "instrumental" to avoid vocals

## Requirements

```
requests>=2.28.0
```

## Security Notes

- API key stored in config.json with 600 permissions
- Falls back to fal-video-skill config if own config not set
- Audio files downloaded locally, URLs expire after ~24 hours

#music #ai #fal #audio #generation
