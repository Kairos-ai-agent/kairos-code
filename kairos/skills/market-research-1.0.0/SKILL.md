---
name: "market-research-1.0.0"
description: "Size markets, analyze competitors, and validate opportunities with practical frameworks and free data sources."
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/market-research-1.0.0/SKILL.md"
---
## Core Framework

**Before any research:** Clarify the question. "How big is the market?" is useless. "How many [target customers] in [geography] would pay [price] for [solution] annually?" is actionable.

## Market Sizing

| Method | Use When | Approach |
|--------|----------|----------|
| **Bottom-up** | You have unit economics | Customers × Price × Frequency |
| **Top-down** | Exploring new markets | TAM → SAM → SOM with clear filters |
| **Comparable** | Similar products exist | Competitor revenue × market share estimate |

**Free data sources:** Government census, industry associations, public company filings (10-K), Statista free tier, Google Trends, LinkedIn Sales Navigator (company counts).

**Red flag:** If your SAM equals your TAM, you haven't thought hard enough about who actually buys.

## Competitor Analysis

1. **Map the landscape:** Direct, indirect, potential entrants
2. **Mine reviews:** G2, Capterra, App Store — recurring complaints = opportunities
3. **Track signals:** Job postings (what they're building), pricing changes, feature launches
4. **Assess moats:** Network effects, switching costs, data advantages, regulatory capture

For detailed competitor frameworks, see `competitor-analysis.md`.

## Customer Validation

**Before building:** Talk to 20+ potential customers. Use Jobs-to-be-Done or Mom Test frameworks.

**Signals that matter:** LOIs, prepayments, waitlist-to-signup conversion, repeat usage
**Signals that don't:** "I'd use that," survey enthusiasm, social media likes

For interview scripts and survey templates, see `validation.md`.

## Common Traps

- **Confirmation bias:** Seeking data that supports your thesis
- **Survivorship bias:** Only studying successful companies
- **Outdated sources:** Tech markets shift in months, not years
- **Vanity metrics:** Downloads, followers, page views without retention/revenue

## Boundaries

- **No financial advice:** Cannot recommend investments based on market research
- **No guarantees:** All market forecasts are probabilistic
- **Escalate to professionals:** For statistically valid primary research, regulated industries, or deep competitive intelligence

## Real Public Data for Indie SaaS / Overseas Markets

When the question is "what type of website can attract overseas paying customers?" — the answer comes from **what real customers are paying real money to today**, not from brainstorming what *might* work.

### When the user says "evaluate, don't speculate"

This is a direct instruction. Default mode becomes **cited public evidence**:

1. **Recent (≤12 months) revenue dashboards** from named founders (best — concrete MRR/ARR)
2. **Active product pages with current pricing + user count** (good — confirms the market is paying)
3. **Verified-purchase reviews** with price context (good — reveals complaints = opportunities)
4. **Forum posts from named customers with proof** (okay — qualitative signal)
5. **SEO traffic estimates** (rough, easily wrong — cite source)
6. **Your gut** (least reliable — do not use as primary evidence)

If you cannot find tier-1 or tier-2 evidence for a niche, either the market doesn't exist yet (don't try), or you're too early to evaluate (find a comparable proxy).

### Where to find real revenue data (named sources)

| Source | What it gives you | When to use |
|--------|-------------------|-------------|
| **Indie Hackers** (indiehackers.com) | Real founder dashboards: MRR, traffic, churn | Validate that *someone* hit $1K / $10K / $100K MRR in this niche |
| **Acquire.com** (acquire.com) | Active sale listings with TTM revenue + multiple | Fair price for a SaaS like yours; what's actually selling |
| **MicroConf / Bootstrapped.fm** | Founder interviews with real numbers | Qualitative validation of "what's working this year" |
| **Gumroad Discover** (gumroad.com/discover) | Bestseller digital products | Validate digital-product niches (templates, brushes, presets) |
| **Product Hunt leaderboard** (producthunt.com/leaderboard) | Top launches by votes, weekly | What's getting attention this quarter |
| **G2 / Capterra / Trustpilot** | Verified reviews with pricing data | Recurring complaints = your opportunity |
| **Comparison sites** (theplanettools.ai, snap2pass.com, launchstackhub.com) | Side-by-side pricing + user counts | Real pricing tier data, head-to-head feature comparisons |
| **Public podcasts** (Acquire.com, Indie Hackers, MicroConf) | Founder interviews with revenue screenshots | "How I went from $0 to $X MRR in Y months" stories |

### Vertical > General (the only pattern that beats incumbents)

General tools ("AI image generator", "video editor", "landing page builder") compete with platform incumbents (Midjourney, CapCut, Webflow) and rarely survive solo. **Vertical tools that solve ONE specific use case better than a general tool can charge premium.**

| Vertical play | Real example | Real price |
|--------------|-------------|-----------|
| AI headshot for LinkedIn / corporate use | BetterPic, HeadshotPro, Aragon AI, Secta | $35–$99 one-time |
| Twitter scheduling for creators | TweetHunter ($15K→$25K MRR → $1.4M exit), Hypefury | $49–$99/month |
| Long video → short clips (AI-edited) | OpusClip (10M users), Submagic, Pictory | $19–$99/month |
| Procreate brushes for digital artists | MaxPacks (Gumroad public case) | $20–$50 one-time; 6-figure-salary income |
| Notion / Figma templates for specific workflow | Various indie creators | $5–$50 one-time |

**Why vertical works**: narrow use case + higher price + better results for THAT specific job than a general tool. The general tool has to compromise for breadth; the vertical tool has to optimize for depth.

### Working evaluation template (use this for "what should I build?" questions)

When a user asks "what type of site can attract overseas paying users?", the deliverable is **not** "10 ideas". The deliverable is a comparison table:

```
| Niche | Real competitor(s) | Real price | Real revenue signal | User-fit for THIS user | Verdict |
|-------|--------------------|-----------:|---------------------|------------------------|---------|
| ...   | ...                | ...        | ...                 | ...                    | ...     |
```

- **Real competitor(s)**: named products, not categories
- **Real price**: from the product page today, not "typical $X"
- **Real revenue signal**: Indie Hackers dashboard, Acquire.com listing, public interview, or "no public signal"
- **User-fit for THIS user**: based on their existing skills / assets / distribution
- **Verdict**: Recommended / Skip / Need more info

Always show evidence sources inline (link or quote). If you can't fill a row with public evidence, say so explicitly.

### What NOT to do

- **Don't propose ideas in the abstract.** "AI for X" with no proof anyone pays for AI in X.
- **Don't reason from "users would probably want this."** Reason from "users *are* paying for this, here's the proof."
- **Don't recycle 2023 ideas as if they're 2026 ideas.** AI tools market shifted massively in 2024–2026; GPT wrappers built in 2023 are mostly dead now.
- **Don't compare to unicorns.** "If only we could be like Midjourney" — Midjourney is the exception, not the model. Compare to indie bootstrapped SaaS in your range ($0–$50K MRR).

---

## Consultation vs Implementation — The Most Common Failure Mode

When a user asks a **strategic question** ("how can we attract X", "what should I build", "is this market worth it", "evaluate options"), the expected deliverable is **evaluation + comparison table + recommendation + offer to do next**, NOT implementation.

**Strategic question signals** (all of these = evaluation mode):
- "有没有其他办法" / "有什么别的办法"
- "你看怎么样" / "你评估下"
- "我们是不是该 / 应该不应该"
- "X 是不是没前途"
- "再找下" / "再想想"
- "先评估一下，不要乱臆想"

**Implementation question signals** (these = build mode):
- "做吧" / "开始做"
- "怎么实现" / "怎么部署"
- "帮我写" / "帮我改" / "帮我加"
- "用 X" / "改成 Y"

**The failure pattern** (seen in real sessions): user asks "网站吸引不到国外用户怎么办" → agent assumes this is an implementation request → runs database migrations, deploys code, writes a 15KB marketing kit with 4 channels of English copy. User pushes back: "我没看明白你要做什么".

**The correct pattern**: evaluate first, get sign-off, then implement one step at a time.

```
User: "我们网站吸引不到国外用户怎么办"
Agent: [diagnoses, gives comparison table, recommends 2-3 paths with effort/payoff]
       "要不要我先做 X？"  ← STOP HERE
       [wait for user to pick]
```

NOT:

```
User: "我们网站吸引不到国外用户怎么办"
Agent: [runs 5 DB queries, edits schema, deploys Worker, writes 15KB launch kit with 4 channels]
       "我已经做了 A, B, C, D, E. 下一步你想做哪个？"
```

**Hard rules during evaluation mode:**

1. **No code edits. No deploys. No file writes outside research artifacts.** Diagnostic queries are fine; code changes are not.
2. **No 15KB deliverable docs.** A comparison table + 2-3 line recommendation per option is enough.
3. **No premature "I went ahead and did X".** The user did not say "do it".
4. **End every strategic answer with a question.** "Want me to go deeper on X / start building X?" — not "I'll proceed with X".
5. **If the evidence says the market is dead, say so directly.** Don't soften it. The user profile pattern: "没什么前途" gets a direct "yes, this market is dead, here's the data, here are 3 alternative directions" — not a 2000-word defense of why it might still work.

**Transition to implementation mode** happens when:
- User picks one option from the comparison table, OR
- User explicitly says "do it" / "做吧" / "开始"
- Even then: implement ONE step, then check back, don't bundle 4 deliverables.

---

## Real failure example from a session (for calibration)

User asked: "网站吸引不了国外用户，怎么办？"
What I did wrong: assumed "implement", ran schema migration + Worker deploy + wrote 15KB launch kit covering Product Hunt / Reddit / AI tool directories / SEO, gave a "next step" question with 4 options.
What user said: "我没看明白你要做什么" (I don't understand what you're doing).
What I should have done: ran the diagnostic queries (✓ already did), said "your real problem is zero real traffic, not conversion — here's a comparison of 3-4 directions with public revenue data", asked which direction to dig deeper on, STOPPED.

Cost of the mistake: 30 minutes of work the user didn't ask for, trust erosion, plus a confused user who had to push back twice before I course-corrected.