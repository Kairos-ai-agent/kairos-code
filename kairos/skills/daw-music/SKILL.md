---
name: "daw-music"
description: "|"
priority: 0.5
version: "2.0.0"
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\openclaw-imports\\daw-music\\SKILL.md"
---
# DAW & Music Composition

## DAW Selection

```
┌─────────────────────────────────────────────────────────────┐
│                    DAW COMPARISON                            │
├─────────────────────────────────────────────────────────────┤
│  REAPER:                                                     │
│  Price: $60  │  Best for: Game audio, flexibility          │
│  Pros: Lightweight, customizable, great for stems          │
├─────────────────────────────────────────────────────────────┤
│  ABLETON LIVE:                                               │
│  Price: $99-749  │  Best for: Electronic, live performance │
│  Pros: Session view, great for layered music               │
├─────────────────────────────────────────────────────────────┤
│  FL STUDIO:                                                  │
│  Price: $99-499  │  Best for: Electronic, hip-hop          │
│  Pros: Pattern-based, lifetime updates                      │
├─────────────────────────────────────────────────────────────┤
│  LOGIC PRO:                                                  │
│  Price: $199  │  Best for: Film, orchestral (Mac only)     │
│  Pros: Great instruments, Apple ecosystem                   │
└─────────────────────────────────────────────────────────────┘
```

## Game Music Structure

```
INTERACTIVE MUSIC LAYERS:
┌─────────────────────────────────────────────────────────────┐
│  Layer 1 (Base):     Percussion/rhythm - always playing    │
│  Layer 2 (Harmony):  Pads/strings - calm moments           │
│  Layer 3 (Melody):   Main theme - exploration              │
│  Layer 4 (Tension):  Dissonance - approaching danger       │
│  Layer 5 (Combat):   Intense - full orchestra/drums        │
└─────────────────────────────────────────────────────────────┘

MUSIC STATE TRANSITIONS:
┌─────────────────────────────────────────────────────────────┐
│  [EXPLORATION] → detect enemy → [TENSION]                   │
│       ↑                            ↓                        │
│       └── no enemy ←───────── [COMBAT] ←── engage          │
│                                    ↓                        │
│                            enemy defeated                   │
│                                    ↓                        │
│                              [VICTORY]                      │
│                                    ↓                        │
│                            → [EXPLORATION]                  │
└─────────────────────────────────────────────────────────────┘
```

## Composition Workflow

```
GAME MUSIC PRODUCTION PIPELINE:
┌─────────────────────────────────────────────────────────────┐
│  1. CONCEPT:                                                 │
│     • Reference tracks                                      │
│     • Mood boards                                           │
│     • Discuss with team                                     │
├─────────────────────────────────────────────────────────────┤
│  2. SKETCH:                                                  │
│     • Quick mockup (piano/synth)                           │
│     • Establish tempo, key, theme                          │
│     • 30-second demo for approval                          │
├─────────────────────────────────────────────────────────────┤
│  3. PRODUCTION:                                              │
│     • Full instrumentation                                  │
│     • Create stems for interactivity                        │
│     • Multiple intensity versions                          │
├─────────────────────────────────────────────────────────────┤
│  4. MIXING:                                                  │
│     • Balance levels                                        │
│     • EQ and compression                                    │
│     • Leave headroom for SFX                               │
├─────────────────────────────────────────────────────────────┤
│  5. EXPORT:                                                  │
│     • Loop points (seamless)                               │
│     • Stems for middleware                                  │
│     • Multiple quality versions                            │
└─────────────────────────────────────────────────────────────┘
```

## Seamless Loop Creation

```
LOOP TECHNIQUES:
┌─────────────────────────────────────────────────────────────┐
│  1. MEASURE-ALIGNED:                                         │
│     • Start/end on exact bar boundaries                    │
│     • Ensure reverb tails don't extend past end            │
│     • Crossfade at loop point if needed                    │
├─────────────────────────────────────────────────────────────┤
│  2. INTRO + LOOP:                                            │
│     • Separate intro section                               │
│     • Main loop starts after intro                         │
│     • Mark loop start/end points in metadata               │
├─────────────────────────────────────────────────────────────┤
│  3. STINGER TRANSITIONS:                                     │
│     • Short musical phrases for state changes              │
│     • Victory fanfares                                      │
│     • Death/failure sounds                                  │
└─────────────────────────────────────────────────────────────┘
```

## Export Settings

| Format | Use Case | Settings |
|--------|----------|----------|
| WAV | Master/Stems | 48kHz, 24-bit |
| OGG | Game runtime | 128-192 kbps |
| MP3 | Backup/Preview | 320 kbps |
| MIDI | Interactive | Standard MIDI |

## 🔧 Troubleshooting

```
┌─────────────────────────────────────────────────────────────┐
│ PROBLEM: Loop has audible click/pop                         │
├─────────────────────────────────────────────────────────────┤
│ SOLUTIONS:                                                   │
│ → Ensure loop points are at zero-crossing                  │
│ → Add small crossfade (5-10ms)                              │
│ → Check reverb/delay tails                                  │
│ → Verify exact sample alignment                             │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ PROBLEM: Music doesn't fit game mood                        │
├─────────────────────────────────────────────────────────────┤
│ SOLUTIONS:                                                   │
│ → Create reference playlist with team                       │
│ → Start with temp music to establish mood                   │
│ → Get feedback on sketches before full production          │
│ → Consider genre conventions                                │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ PROBLEM: Transitions between music states jarring           │
├─────────────────────────────────────────────────────────────┤
│ SOLUTIONS:                                                   │
│ → Use same key/tempo across states                          │
│ → Add transition stingers                                   │
│ → Crossfade on musical boundaries (bars)                    │
│ → Use stems to gradually add/remove layers                 │
└─────────────────────────────────────────────────────────────┘
```

## Integration Workflow

```
MIDDLEWARE INTEGRATION:
┌─────────────────────────────────────────────────────────────┐
│  DAW → Export Stems → Wwise/FMOD → Game Engine             │
│                                                              │
│  Stems typically:                                            │
│  • Percussion                                               │
│  • Bass                                                     │
│  • Harmony                                                  │
│  • Melody                                                   │
│  • FX/Stingers                                              │
└─────────────────────────────────────────────────────────────┘
```

---

**Use this skill**: When composing game music, implementing audio systems, or creating interactive soundtracks.
