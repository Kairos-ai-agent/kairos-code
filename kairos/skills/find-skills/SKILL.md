---
name: "find-skills"
description: "Helps users discover and install agent skills when they ask questions like \"how do I do X\", \"find a skill for X\", \"is there a skill that can...\", or express interest in extending capabilities. This sk"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\find-skills\\SKILL.md"
---
# Find Skills

This skill helps you discover and install skills from the open agent skills ecosystem.

## When to Use This Skill

Use this skill when the user:

- Asks "how do I do X" where X might be a common task with an existing skill
- Says "find a skill for X" or "is there a skill for X"
- Asks "can you do X" where X is a specialized capability
- Expresses interest in extending agent capabilities
- Wants to search for tools, templates, or workflows
- Mentions they wish they had help with a specific domain (design, testing, deployment, etc.)

## What is the Skills CLI?

The Skills CLI (`npx skills`) is the package manager for the open agent skills ecosystem. Skills are modular packages that extend agent capabilities with specialized knowledge, workflows, and tools.

**Key commands:**

- `npx skills find [query]` - Search for skills interactively or by keyword
- `npx skills add <package>` - Install a skill from GitHub or other sources
- `npx skills check` - Check for skill updates
- `npx skills update` - Update all installed skills

**Browse skills at:** https://skills.sh/

## How to Help Users Find Skills

### Step 1: Understand What They Need

When a user asks for help with something, identify:

1. The domain (e.g., React, testing, design, deployment)
2. The specific task (e.g., writing tests, creating animations, reviewing PRs)
3. Whether this is a common enough task that a skill likely exists

### Step 2: Search for Skills

Run the find command with a relevant query:

```bash
npx skills find [query]
```

For example:

- User asks "how do I make my React app faster?" → `npx skills find react performance`
- User asks "can you help me with PR reviews?" → `npx skills find pr review`
- User asks "I need to create a changelog" → `npx skills find changelog`

The command will return results like:

```
Install with npx skills add <owner/repo@skill>

vercel-labs/agent-skills@vercel-react-best-practices
└ https://skills.sh/vercel-labs/agent-skills/vercel-react-best-practices
```

### Step 3: Present Options to the User

When you find relevant skills, present them to the user with:

1. The skill name and what it does
2. The install command they can run
3. A link to learn more at skills.sh

Example response:

```
I found a skill that might help! The "vercel-react-best-practices" skill provides
React and Next.js performance optimization guidelines from Vercel Engineering.

To install it:
npx skills add vercel-labs/agent-skills@vercel-react-best-practices

Learn more: https://skills.sh/vercel-labs/agent-skills/vercel-react-best-practices
```

### Step 4: Offer to Install

If the user wants to proceed, vet the complete skill package first, then install it:

```bash
npx skills add <owner/repo@skill> -g -y
```

The `-g` flag installs globally (user-level) and `-y` skips confirmation prompts.

For Hermes Agent, follow the exact-identifier, full-package review, registry/API fallback, and post-install verification workflow in [references/hermes-installation-and-verification.md](references/hermes-installation-and-verification.md). Do not infer success from process exit code alone; confirm that Hermes can list and load the installed skill.

## Common Skill Categories

When searching, consider these common categories:

| Category        | Example Queries                          |
| --------------- | ---------------------------------------- |
| Web Development | react, nextjs, typescript, css, tailwind |
| Testing         | testing, jest, playwright, e2e           |
| DevOps          | deploy, docker, kubernetes, ci-cd        |
| Documentation   | docs, readme, changelog, api-docs        |
| Code Quality    | review, lint, refactor, best-practices   |
| Design          | ui, ux, design-system, accessibility     |
| Productivity    | workflow, automation, git                |
| Creative / Media | video, filmmaking, directing, storyboard, music, art-direction, copywriting, image-generation |

## Pitfalls

- **`npx skills find` is incomplete for creative domains.** Film/video, music, art-direction, and other creative workflows are often missing from the index. Always fall back to direct probing (see above) when the domain is creative or hardware.
- **Don't conflate "art-director" with "film director".** A skill named `art-director` (e.g. `MachinesOfDesire/art-director`, `na1234shui/ui-art-director-skill`) is almost always about image/UI aesthetic direction, not film/video directing. For film directing, probe specifically for `s1dashu/director` first — it's the canonical end-to-end video director skill (Animated Explainer / Cinematic Drama / Storytime Animation / Visual Journalism modes).
- **Skills hosted on imini.ai are NOT on GitHub.** They are referenced via slug URLs (`imini.ai/agent/skills/<slug>`) and have their own `npx skills add` integration but are invisible to GitHub search.

## Tips for Effective Searches

1. **Use specific keywords**: "react testing" is better than just "testing"
2. **Try alternative terms**: If "deploy" doesn't work, try "deployment" or "ci-cd"
3. **Check popular sources**: Many skills come from `vercel-labs/agent-skills` or `ComposioHQ/awesome-claude-skills`

## When `npx skills find` Returns Nothing or Too Little

`npx skills find` indexes a subset of the ecosystem. Niche domains (film/video directing, music production, art direction, PCB design, embedded firmware, hardware, creative writing workflows, etc.) are often NOT in that index. Fall back to direct probing before declaring "no skill exists."

**Direct GitHub probe** — for a candidate `owner/repo`, fetch the README without installing:

```bash
curl -sL --max-time 25 https://raw.githubusercontent.com/owner/repo/main/README.md | head -150
curl -sL --max-time 25 https://raw.githubusercontent.com/owner/repo/main/SKILL.md
```

Look for the `Agent Skill` / `Codex Skill` / `Claude Skill` badge in the README, plus a `SKILL.md` link.

**Browse an author's full catalog** — `https://www.skills.sh/{owner}` lists every skill under that GitHub user/org, with install counts. Useful when one good skill surfaces an author worth exploring more.

**Check `imini.ai/agent/skills`** — 200+ creative/video/image skills hosted there (not on GitHub). Skill metadata is in `<script type="application/ld+json">` blocks inside the HTML page; fetch the page and grep:

```bash
curl -sL --max-time 20 "https://imini.ai/agent/skills/<skill-slug>" | grep -oE '"(skill_name|description|detail_markdown)"[^}]*'
```

Watch out for `web_extract` returning a DuckDuckGo "search-only backend" error on JS-heavy pages — fall back to `curl` in `terminal`.

For a deeper playbook on niche-domain search (film/music/art, embedded, devtools) including high-value repos to probe first, see [references/niche-domain-search.md](references/niche-domain-search.md).

## When No Skills Are Found

If no relevant skills exist:

1. Acknowledge that no existing skill was found
2. Offer to help with the task directly using your general capabilities
3. Suggest the user could create their own skill with `npx skills init`

Example:

```
I searched for skills related to "xyz" but didn't find any matches.
I can still help you with this task directly! Would you like me to proceed?

If this is something you do often, you could create your own skill:
npx skills init my-xyz-skill
```
