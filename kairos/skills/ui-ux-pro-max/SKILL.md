---
name: "ui-ux-pro-max"
description: "UI/UX design intelligence and implementation guidance for building polished interfaces. Use when the user asks for UI design, UX flows, information architecture, visual style direction, design systems"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\ui-ux-pro-max\\SKILL.md"
---
Follow these steps to deliver high-quality UI/UX output with minimal back-and-forth.

## 1) Triage
Ask only what you must to avoid wrong work:
- Target platform: web / iOS / Android / desktop
- Stack (if code changes): React/Next/Vue/Svelte, CSS/Tailwind, component library
- Goal and constraints: conversion, speed, brand vibe, accessibility level (WCAG AA?)
- What you have: screenshot, Figma, repo, URL, user journey

If the user says "全部都要" (design + UX + code + design system), treat it as four deliverables and ship in that order.

## 2) Produce Deliverables (pick what fits)
Always be concrete: name components, states, spacing, typography, and interactions.

- **UI concept + layout**: Provide a clear visual direction, grid, typography, color system, key screens/sections.
- **UX flow**: Map the user journey, critical paths, error/empty/loading states, edge cases.
- **Design system**: Tokens (color/typography/spacing/radius/shadow), component rules, accessibility notes.
- **Implementation plan**: Exact file-level edits, component breakdown, and acceptance criteria.

## 3) Use Bundled Assets
This skill bundles data you can cite for inspiration/standards.

- **Design intelligence data**: Read from `skills/ui-ux-pro-max/assets/data/` when you need palettes, patterns, or UI/UX heuristics.
- **Upstream reference**: If you need more phrasing/examples, consult `skills/ui-ux-pro-max/references/upstream-skill-content.md`.
- **Dark card SVG buttons**: For dark teal card-style mode/feature selector buttons with SVG icons and glow effects, see `skills/ui-ux-pro-max/references/dark-card-svg-buttons.md`. Includes full CSS, color palette, and SVG icon patterns.

## 4) Optional Script (Design System Generator)
If you need to quickly generate tokens and page-specific overrides, use the bundled script:

```bash
python3 skills/ui-ux-pro-max/scripts/design_system.py --help
```

Prefer running it when the user wants a structured token output (ASCII-friendly).
## Output Standards

- Default to ASCII-only tokens/variables unless the project already uses Unicode.
- Include: spacing scale, type scale, 2-3 font pair options, color tokens, component states.
- Always cover: empty/loading/error, keyboard navigation, focus states, contrast.

## Tool vs. Product Hierarchy

When designing the layout for any tool-app (anything whose purpose is to **produce something** — generate an output, send a message, create an artifact, run a calculation, etc.), distinguish:

- **The Product** — the thing the app exists to make. This is the content the user came here for. Goes front-and-center in the main view.
- **The Configuration Tools** — the settings the user adjusts to shape the product. These are *means*, not ends. They live in modals, side panels, drawers, dropdowns, or behind a clear "设置" / "⚙" button.

### Rule

> The main view = the Product. Tools = secondary surfaces (modal, side, drawer).

Don't promote a tool to main view just because it's convenient to render. A calendar, a config form, a settings panel — all tools. The user came to **see/do the thing the app produces**, not to see all knobs and inputs.

### Symptoms you're violating this

- The "main page" is dominated by the configuration surface (a full-page calendar, a tabbed settings form, a giant dropdown).
- The actual product appears as a small panel at the top or right, or only after interacting with the config.
- Each click on the main view feels like clicking a settings knob, not like interacting with the product.
- User feedback: "this page is too big / it's confusing / the calendar / form / etc. shouldn't be the main view."

### How to recover

1. Identify **what the user actually came here to make or see**. Make that the main view.
2. Move every config surface into a modal, drawer, side panel, or behind a clear button.
3. The main view should answer the user's intent at a glance — "here is your [thing], here are the controls to act on it, here are the stats."
4. Calendar grid / form / settings panel become a **tool** the user opens when needed, not the page they land on.

### Why this matters for scope

This is the **UI version of scope creep**. Building the tool bigger than the product is "the launcher feature eating the actual feature." Users don't tolerate it twice — once directed, they will not direct again.
