---
name: "landing-page-generator"
description: "Professional high-end Landing Page generation tool. Creates visually stunning, Awwwards-level web pages with cinematic hero sections, modern animations, and automatic deployment. Trigger keywords: lan"
priority: 0.5
imported-from: "minimax"
source-path: "C:\\Users\\leohu\\.minimax\\skills\\landing-page-generator\\SKILL.md"
---
# Landing Page Generator

## Overview

A professional Landing Page generation assistant that creates visually striking, "Awwwards-level" web experiences. It handles the full pipeline: gathering requirements, generating a design spec, producing visual assets (cinematic hero videos and images), writing clean HTML/CSS/JS code, validating with Playwright, and deploying the final page — delivering a live URL to the user.

## Workflow

### Step 1: Gather User Requirements

Collect the following from the user:

1. **Project theme / brand** — name, identity, brand assets
2. **Page purpose** — product launch, brand showcase, event promotion, etc.
3. **Target audience** — demographics, expectations
4. **Style preference** (optional) — mood, references, aesthetic direction
5. **Content materials** — copy, brand guidelines, logos, images, etc.

If the user provides only a brief description, expand it into a rich, specific design spec yourself. Do not ask excessive clarifying questions — make expert creative decisions and proceed.

### Step 2: Create Design Spec (`spec.md`)

Create a `spec.md` file as the strict blueprint for all subsequent work.

The spec must include:

1. **Expanded Prompt**:
   - Project Context
   - Hero Section design (Video-First — see Hero Section rules below)
   - Typography plan
   - Layout structure
   - Motion / animation plan

2. **Tech Strategy**:
   - Stack choices
   - Asset protocol

3. **Design System**:
   - **Color Palette** — MUST comply with the Color Constraint (see below)
   - **Typography** — scale and font pairing
   - **Layout** — grid system and spacing

### Step 3: Generate Visual Assets

Based on the spec, generate all required visual assets. **Prioritize the Hero Video.**

**For Videos (PRIORITY):**
- Resolution: 1080p (1920×1080) minimum
- Enhance prompts with cinematic keywords: "Shot on Arri Alexa, 35mm lens, f/1.8 shallow depth of field, slow-motion, cinematic lighting, hyper-realistic, 8k resolution"
- Add specific camera gear and focal lengths (e.g., "Close-up texture shot, 85mm lens", "Wide angle landscape, 16mm lens")
- Use `gen_videos` tool
- Store in: `project_name/videos/`

**For Images:**
- Use `gen_images` tool
- Store in: `project_name/imgs/`

**Directory Structure:**
```
project_name/
├── imgs/
├── videos/
└── index.html
```

### Step 4: Generate Code (HTML/CSS/JS)

- Read `spec.md` and implement requirements exactly
- Write clean, semantic HTML5 with modern CSS
- Mobile-first responsive design
- Minimize external dependencies; inline critical CSS

**Path Safety (Zero Tolerance for Broken Links):**

1. **Exact Match**: Before writing HTML, verify the exact filenames generated in Step 3. Do NOT hallucinate filenames.
2. **Relative Paths**: Use simple, relative paths (e.g., `./videos/hero_loop.mp4`).
3. **Fallback Strategy**:
   - Images: `loading="lazy"` AND `onerror="this.style.display='none'"`
   - Videos: NO static image overlays or poster images blocking the video. Set a background color on the video container as fallback.

### Step 5: Pre-Deployment Validation (Playwright Protocol)

Validate build quality before deployment.

1. **Setup**: `playwright install chromium 2>&1`
2. **Validation** — Run Python/Playwright to verify:
   - No 404 errors on media: check all `<img>` src and `<video>` sources resolve to 200 OK
   - No console errors
   - Page renders correctly
3. **Correction**: If ANY media file returns a 404, fix the path in the HTML immediately and re-validate.

### Step 6: Deploy (MANDATORY)

- Deploy using the `deploy` tool with the project directory as `dist_dir`
- **Never** use `python -m http.server` or `npx serve` — only the deploy tool
- Provide the deployed URL in the final response

### Step 7: Deliver to User

Present to the user:
- The live deployment URL
- Project source files (optional, on request)

---

## Color Constraint (NON-NEGOTIABLE)

**NO BLUE OR PURPLE HUES.**

| Forbidden | Allowed |
|-----------|---------|
| Standard Tech Blue | Earth tones (terracotta, sand, clay) |
| Indigo | Monochrome (Black / White / Greyscale) |
| Violet | Warm tones (Orange, Red, Cream, Gold) |
| Blurple | Nature Greens (olive, forest, sage) |
| Neon Purple | Rich neutrals (charcoal, taupe, ivory) |

**Reasoning**: The aesthetic must strictly avoid the "generic SaaS blue" look. Every page must feel bespoke and high-end.

Always verify the color palette in `spec.md` before proceeding to code generation.

---

## Hero Section Rules (CRITICAL)

The Hero Section is EVERYTHING. First impression determines success. Deliver overwhelming visual impact.

### Default Strategy: Video-Led Hero

- The hero video is the **single most important asset**
- Resolution: 1080p (1920×1080) minimum — do not accept low-resolution or pixelated outputs
- The video must be atmospheric and looping, serving as a powerful backdrop for massive typography
- Video should autoplay (muted) seamlessly
- Do NOT include a static placeholder `<img>` or poster image that blocks or delays the video experience
- Use a solid background color matching the video's tone as the only technical fallback

### Acceptable Hero Patterns

| Pattern | Description |
|---------|-------------|
| **Video-Led** (Preferred) | Muted autoplay background, high-end cinematic feel, floating headline |
| **Image-Led** | 100vh hero image, text overlays with blend modes |
| **Type-Led** | Giant typography IS the hero |
| **Split-Screen** | 50/50 dramatic tension |

### NEVER Do This in the Hero

- Small, contained hero images with excessive padding
- Static placeholder images obstructing the video
- Generic stock photos without artistic treatment
- Blue or Purple color schemes
- Broken image icons or 404 errors

---

## Code Quality Standards

### HTML/CSS
- Semantic HTML5 structure
- Mobile-first responsive approach
- Modern CSS (Grid, Flexbox, custom properties)
- Inline critical CSS for performance
- Clean, readable, well-commented code

### Animation & Interaction
- Use modern component/animation libraries where appropriate
- Aim for "wow" features: parallax, scroll-triggered animations, smooth transitions
- Keep performance in mind — no janky animations

### Asset Handling
- All media paths must exactly match generated filenames
- Images: use `loading="lazy"` and `onerror="this.style.display='none'"`
- Videos: background-color fallback on container, no poster images blocking autoplay
- Use relative paths only (e.g., `./imgs/photo.jpg`)

---

## Output Checklist

Before delivering to the user, verify every item:

1. `spec.md` generated (confirmed NO blue/purple in palette)
2. Visual assets generated (hero video is 1080p & cinematic)
3. HTML/CSS/JS code generated
4. Path integrity check: all media paths match generated files exactly
5. Playwright validation passed (no 404s, no console errors)
6. Deployed via `deploy` tool
7. Deployment URL provided to user

---

## Common Mistakes to Avoid

- Using blue or purple colors anywhere in the design
- Generating low-resolution hero videos (below 1080p)
- Hallucinating filenames that don't match actually generated assets
- Placing a static poster image over the hero video
- Using `python -m http.server` or `npx serve` instead of the deploy tool
- Skipping the Playwright validation step
- Using generic stock imagery without artistic treatment
- Creating small, padded hero sections instead of full-viewport impact
- Forgetting to verify all media paths resolve to 200 OK before deployment

## File & Output Conventions

- **Project directory**: `project_name/` (derived from brand/project name)
- **Images directory**: `project_name/imgs/`
- **Videos directory**: `project_name/videos/`
- **Main page**: `project_name/index.html`
- **Design spec**: `project_name/spec.md` (internal, not deployed)
- **Deployment**: via `deploy` tool with `dist_dir` set to the project directory
- **Final deliverable**: live URL + source files on request
