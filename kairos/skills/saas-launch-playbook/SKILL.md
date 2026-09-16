---
name: "saas-launch-playbook"
description: "Multi-channel cold-start launch playbook for small SaaS products — diagnose first (real user count, geo, sources, revenue), fix data foundation (add missing schema fields, capture request.cf.country),"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\saas-launch-playbook\\SKILL.md"
---
# SaaS Cold-Start Launch Playbook

Class-level workflow for a small SaaS product that has shipped but isn't growing. **Most complaints about "marketing isn't working" are actually complaints about "I can't see if marketing is working"** — diagnosis comes first, channels come second.

> **For the StoryForge AI worked example** (Aug 2026): `references/storyforge-launched-kit.md` — full 4-channel kit built off the Phase 1 diagnosis of 5 test users / 0 paid / no geo data. The reusable structure is in `references/launch-kit-template.md`.

## Phase 1: Diagnose (NEVER skip)

Before any marketing, query the actual state of the product. Do not rely on the user's intuition alone — "海外用户转化不好" is often "I have no users anywhere" once you check the DB.

### 1.1 Real user count + activity

```sql
SELECT COUNT(*) AS users, MIN(created_at) AS first, MAX(created_at) AS last FROM users;

-- Activity concentration — is 1 user doing 95% of the work?
SELECT user_id, COUNT(*) AS gens, MAX(created_at) AS last_gen
FROM generations GROUP BY user_id ORDER BY gens DESC LIMIT 10;
```

**If `users < 50` OR `MAX - MIN created_at > 30 days`, there are no real users.** Tell the user honestly: this is a cold-start problem, not a conversion problem. Skip to Phase 3.

### 1.2 Revenue signal

```sql
SELECT COUNT(*) AS paid_orders, COALESCE(SUM(amount_usd), 0) AS revenue
FROM orders WHERE status='completed';
```

If `$0` with any non-trivial user count, conversion is untestable (denominator too small). Don't suggest A/B tests; suggest Phase 2 + 3.

### 1.3 Geo / source / language signal

```sql
SELECT language, COUNT(*) AS n FROM users GROUP BY language;
SELECT oauth_provider, COUNT(*) AS n FROM users GROUP BY oauth_provider;
```

**If every row shows the schema default** (e.g. `language='en'` because that's the schema default, not because users picked English), there is **no real signal**. This is the gap Phase 2 fixes.

### 1.4 Deliverable: honest one-paragraph assessment

Write to the user verbatim:

> "Total 5 users, 0 paid orders, 100% activity from 1 account (admin), no country/source/language data captured. This is a cold-start problem, not a conversion problem. Marketing won't move these numbers until we (a) fix the data gap so we can see what works, and (b) put real traffic in front of the product."

This sentence transforms the conversation from "tell me a marketing hack" to "let me actually solve this".

## Phase 2: Fix Data Foundation

Before any channel, capture the signal that lets you measure what works. Every missing field you can identify should be captured going forward.

### 2.1 Country / geo

See `cloudflare-deployment` → "User GeoIP Capture via `request.cf`" for full code pattern. TL;DR:

```js
// top of handleRequest
const country = (request.cf && request.cf.country && request.cf.country !== 'XX')
  ? request.cf.country : '';
```

Add `country TEXT DEFAULT ''` to users. Bind into every user-creation INSERT. Filter `XX`. Backfill on next OAuth login via `CASE WHEN country = '' THEN ? ELSE country END`.

**Why Worker-side over Zone Analytics API**: `.deploy_token` typically lacks `analytics.read` scope (403). Worker capture is per-user (not per-request), token-independent, and survives CF cache changes.

### 2.2 Source / referrer

```js
const source = url.searchParams.get('utm_source') || request.headers.get('Referer') || 'direct';
```

Store in `users.source` or a `signup_sources(source, user_id, created_at)` table. Without this, every channel attribution is guesswork.

### 2.3 First-action timestamp

```sql
ALTER TABLE users ADD COLUMN first_generation_at INTEGER DEFAULT 0;
```

Knowing "signup → first generation" latency is the most actionable retention signal. Drop-off at 24h = bad onboarding. Drop-off at 60s = product friction.

### 2.4 Don't break production while adding fields

See `cloudflare-deployment` → "D1 schema migrations" pitfall. **`deploy_cf.py` only runs `CREATE TABLE IF NOT EXISTS`** — `ALTER TABLE ADD COLUMN` for new columns on existing tables requires a separate one-shot migration script run via CF API directly.

## Phase 3: Multi-Channel Launch Kit

The kit covers 4 channels with different cost/return profiles. **Pick 1-2, not all** — spreading thin kills the work.

| Channel | Cost | Return profile | When to use |
|---|---|---|---|
| Product Hunt | 1-2h prep | 100-500 UV launch day; near-zero if ranked bottom | Have a hunter, working free tier, can prep a Maker GIF |
| Reddit (3 threads) | 2-3h writing | 50-500 UV per thread if upvoted | Product has a real workflow angle, not "AI tool" alone |
| AI tool directories (8 sites) | 1-2h one-time | 5-50 UV/month passive for years | AI/SaaS product, low effort long-tail |
| SEO + 5 blog posts | 4-8h/week ongoing | <10 UV/day month 1; 50-200 UV/day by month 6 | Have 3+ months runway and can write |

### 3.1 Product Hunt (high effort, high spike)

- **When**: Wed or Thu, 8:30 AM PT (Pacific morning = global PH traffic peak)
- **Prerequisites**: hunter with 1k+ followers (DM 5-10 people 1 week ahead), 3-4 screenshot assets, working free tier
- **Critical**: post Maker comment WITHIN 5 MIN of launch or ranking tanks

Copy structure: tagline (60 chars) → description (260 chars) → Maker comment (long, with 3 pre-prepared FAQ replies for the predictable questions) → asset list (priority order: GIF > gallery > pricing > before/after).

Generic template: `references/launch-kit-template.md#product-hunt`.

### 3.2 Reddit (medium effort, mid spike)

**Reddit rules** (read first, instant bans if violated):
- Account must have comment history; not brand-new
- Lead with **value** (workflow, technique, story); brand mention only at the END as "I built a tool that does this, free if you want to try"
- Never cross-post same thread to multiple subreddits
- Engage with every comment in first 6 hours

**Subreddit selection**: pick by audience, not size. "AI image generator" → r/StableDiffusion, r/midjourney (over-saturated). "AI for animators" → r/animation, r/characterdesign (less saturated, more qualified).

Generic template: `references/launch-kit-template.md#reddit`.

### 3.3 AI Tool Directories (low effort, long tail)

One-time submission to 8 sites, then passive for years. Sites by priority:

1. **theresanaiforthat.com** — biggest AI directory
2. **futurepedia.io** — review takes 3-7 days
3. **topai.tools** — free, fast approval
4. **aitools.fyi** — free
5. **toolify.ai** — free
6. **aichiefs.com** — may want sponsored review ($$)
7. **openaitools** — free
8. **insidr.ai/ai-tools** — directory

**Common fields** (write once, paste 8 times): name, URL, tagline (140 chars), categories, short description (260 chars), long description (400 chars), pricing model, logo (512×512 PNG transparent), 3-4 screenshots.

Track in a table: site | date submitted | approval status | notes.

### 3.4 SEO + Content (high effort, slow burn)

**Pre-launch quick wins** (30 min, do today):
- Submit sitemap.xml to Google Search Console + Bing Webmaster
- Add Open Graph image (1200×630) for social shares
- Add Twitter Card meta tags
- Add JSON-LD `Product` schema on `/pricing`
- Confirm each page has unique `<title>` + `<meta description>`

**Blog cadence**: 1/week for 3 months minimum. Don't expect results before month 3.

**Target query types**:
- "[your product type] free" — bottom funnel, ready to try
- "[competitor] alternative" — mid funnel, evaluating
- "how to [use case your product solves]" — top funnel, learning

## Phase 4: Measure (30-day check-in)

After 2-3 weeks, query what Phase 2 set up:

```sql
-- Attribution by country
SELECT country, COUNT(*) AS users,
       SUM(CASE WHEN oauth_provider != '' THEN 1 ELSE 0 END) AS oauth_users,
       SUM(credits) AS total_credits
FROM users
WHERE created_at >= strftime('%s', 'now', '-30 days') * 1000
  AND country != ''
GROUP BY country
ORDER BY users DESC;

-- Signup → first-action latency (the activation metric)
SELECT
  AVG(first_generation_at - created_at) AS avg_latency_sec,
  COUNT(*) AS activated_users
FROM users WHERE first_generation_at > 0;
```

**Decision rule**: kill any channel that brings <5 signups in 30 days. Double down on the channel with the best free→paid conversion (not just signups — conversion).

## Pitfalls

- **Don't skip Phase 1.** Marketing without diagnosis is throwing darts blindfolded. 5 test users means zero real conversion to optimize — Phase 1's job is to make this explicit.
- **Don't write marketing copy until you've read the actual product positioning.** A "cinematic AI for character designers" angle is completely different from a generic "AI image generator" pitch. The latter attracts zero qualified users.
- **Don't submit to 30 directories at once.** Top 5 first, see what approves, then next batch. Mass submissions look spammy and get rejected in bulk.
- **Reddit hates hard launches.** First post can't be "check out my product" — instant ban. Soft launch only, with brand mention at the end.
- **Don't measure channel ROI before 30 days.** Reddit converts in 24 hours, SEO takes 90 days. Mixed windows make early data meaningless.
- **Don't ignore geographic data after collection.** "Country = CN" showing up means you need a Chinese landing page eventually; "Country = US" but $0 revenue means your pricing page doesn't convert US users. Data without action is just decoration.

## Related Skills

- `cloudflare-deployment` — User GeoIP capture pattern (Phase 2.1), D1 migration pitfalls (Phase 2.4)
- `market-research-1.0.0` — pre-launch positioning + competitor analysis (run before Phase 3)
- `seo-1.0.3` / `seo-content-writer-2.0.0` — Phase 3.4 deep dive
- `social-content-generator-0.1.0` — Reddit / X copy tone calibration

## References

- `references/launch-kit-template.md` — generic copy-paste structure for each of the 4 channels
- `references/storyforge-launched-kit.md` — full worked example for an AI image SaaS (StoryForge AI, Aug 2026)