---
name: "game-portrait-generator"
description: "Generate realistic, high-resolution studio portrait photos for game face module development. Use when users request game character face reference photos, frontal portraits, studio portrait photos, or"
priority: 0.5
imported-from: "minimax"
source-path: "minimax/skills/game-portrait-generator/SKILL.md"
---
# Game Portrait Generator

Generate studio-quality portrait photographs that meet game development standards based on user-provided parameters.

## Input Parameters

| Parameter | Description | Example |
|-----------|-------------|---------|
| `age` | Age (18-60 years) | 25 |
| `ethnicity` | Ethnicity | Caucasian, Asian, African, South Asian, Hispanic |
| `gender` | Gender | Male, Female |
| `reference_photo` | Reference photo (optional) | User-provided photo path |

## Image Specifications

- **Resolution**: High resolution (2K/4K)
- **Orientation**: Frontal view
- **Expression**: Neutral expression
- **Background**: Single-color light background (white or light gray)
- **Lighting**: Professional studio lighting, front sharp focus
- **Style**: No shadows, no makeup, no accessories

## Generation Prompt Template

```
A highly realistic, studio-quality portrait photograph of a [age]-year-old [ethnicity] [gender].
Frontal view, neutral expression, single-color light background (white or light gray).
Ultra sharp focus from the front, professional studio lighting with no shadows.
No makeup, no accessories, no jewelry, no glasses.
Clean, crisp, high-resolution image suitable for game character face module development.
Full face visible, symmetrical features, natural skin texture, detailed facial structure.
```

## Execution Steps

1. **Parse User Parameters**
   - Extract age, ethnicity, and gender information
   - Check for reference photo availability

2. **Build Generation Prompt**
   - Fill parameters according to template
   - Add necessary quality modifiers

3. **Generate Image**
   - Call image generation tool
   - Set appropriate aspect ratio (typically 3:4 or 1:1)

4. **Return Results**
   - Provide generated image path
   - Confirm image quality meets requirements

## Examples

**User Input**:
"Generate a 30-year-old Asian female game face photo"

**Generated Prompt**:
```
A highly realistic, studio-quality portrait photograph of a 30-year-old Asian female.
Frontal view, neutral expression, single-color light background (white or light gray).
Ultra sharp focus from the front, professional studio lighting with no shadows.
No makeup, no accessories, no jewelry, no glasses.
Clean, crisp, high-resolution image suitable for game character face module development.
Full face visible, symmetrical features, natural skin texture, detailed facial structure.
```

## Notes

- Age recommended between 18-60 years for best results
- Ethnicity uses standard classification: Caucasian, Asian, African, South Asian, Hispanic
- Gender options: Male, Female
- If reference photo is provided, prioritize generating based on reference features
