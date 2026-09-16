---
name: "seo-geo-optimizer"
description: "SEO & GEO optimization expert. Use when user needs keyword research, content optimization, SEO audits, competitor analysis, technical SEO checks, AI citation optimization (GEO), Schema markup generati"
priority: 0.5
imported-from: "minimax"
source-path: "minimax/skills/seo-geo-optimizer/SKILL.md"
---
# SEO & GEO Optimization Expert

> **Copyright 2024-2025 Aaron He Zhu** | Source: https://github.com/aaron-he-zhu/seo-geo-claude-skills | Licensed under Apache License 2.0: http://www.apache.org/licenses/LICENSE-2.0

> **SEO** 让你在搜索结果中获得排名。**GEO** 让你被AI系统（ChatGPT、Perplexity、Google AI Overviews）引用。本技能同时涵盖这两个领域。

## Overview

This is a comprehensive SEO (Search Engine Optimization) and GEO (Generative Engine Optimization) skill that covers the full optimization workflow: research, build, optimize, and monitor. It helps users improve website visibility in both traditional search engines and AI systems.

## User Goal Quick Navigation

| User Goal | Start With | Then |
|---------|---------|---------|
| Starting from scratch | Keyword Research → Competitor Analysis | → SEO Content Writing |
| Writing new content | Keyword Research | → SEO Content Writing + GEO Content Optimization |
| Improving existing content | On-Page SEO Audit | → Content Refresh or SEO Content Writing |
| Fixing technical issues | Technical SEO Check | → Fix issues by priority |
| Assessing domain authority | Domain Authority Audit | → Backlink Analysis |
| Full quality assessment | Content Quality Audit | → 120-item comprehensive report |
| Building entity/brand | Entity Optimization | → Schema Markup + GEO Content Optimization |
| Optimizing meta tags | Meta Tags Optimization | → A/B test variations |
| Adding structured data | Schema Markup Generation | → Validation and testing |

## Recommended Skill Combinations

These combinations work best together:
- **Keyword Research** + **Content Gap Analysis** → Complete content strategy
- **SEO Content Writing** + **GEO Content Optimization** → Dual-optimized content
- **On-Page SEO Audit** + **Technical SEO Check** → Complete website audit
- **Content Quality Audit** + **Content Refresh** → Data-driven content updates
- **Schema Markup** + **Meta Tags Optimization** → Complete SERP presence

## Workflow

### Step 1: Understand Requirements

Before starting any task, confirm:
- Target keywords or topics
- Target audience
- Business goals (traffic/leads/sales)
- Current website state (new/established)
- Geographic targeting needs

### Step 2: Execute Analysis

Based on the task type, follow the appropriate section below.

### Step 3: Provide Recommendations
- All recommendations must be based on specific data
- Prioritize by impact: fix highest-impact issues first
- Give specific action steps

### Step 4: Quality Check
- Use CORE-EEAT framework for content quality
- Use CITE framework for domain authority
- Ensure compliance with latest search engine guidelines

### Output Format

All reports should include:
1. **Executive Summary** — Key findings overview
2. **Detailed Analysis** — Data-backed deep analysis
3. **Priority Recommendations** — Action items sorted by importance
4. **Next Steps** — Specific follow-up actions

---

## Quality Frameworks

### CORE-EEAT Content Quality Benchmark (80 items, 8 dimensions)

- **C**ontextual Clarity — Intent alignment, audience targeting
- **O**rganization — Structure, navigation, readability
- **R**eferenceability — Citability, data presentation
- **E**xclusivity — Original research, unique insights
- **Exp**erience — First-hand knowledge, practical advice
- **Exp**ertise — Depth, accuracy, technical capability
- **A**uthority — Credentials, recognition, citations
- **T**rust — Transparency, disclosure, accuracy

**Score Calculation**:
- GEO Score = (C + O + R + E) / 4
- SEO Score = (Exp + Ept + A + T) / 4
- Weighted Score = Σ (dimension_score × content_type_weight)

**Rating Scale**: 90-100 Excellent | 75-89 Good | 60-74 Medium | 40-59 Low | 0-39 Poor

### CITE Domain Authority Score (40 items, 4 dimensions)

- **C**itation — Backlink profile, media mentions
- **I**mpact — Publishing record, brand recognition
- **T**rust — Legal compliance, security, policies
- **E**ntity — Knowledge graph presence, consistency

### Search Intent Classification

| Intent Type | Signal Words | Content Type |
|---------|--------|---------|
| Informational | what, how, why, guide, learn | Blog posts, guides |
| Navigational | brand name, login, official site | Homepage, product pages |
| Commercial | best, review, vs, compare | Comparison articles, reviews |
| Transactional | buy, price, discount, purchase | Product pages, pricing pages |

---

## 1. Keyword Research

> Triggers: "find keywords", "keyword research", "what should I write about", "identify ranking opportunities", "topic ideas", "what are people searching for", "which keywords should I target", "give me keyword ideas"

This section discovers, analyzes, and prioritizes keywords for SEO and GEO content strategies. It identifies high-value opportunities based on search volume, competition, intent, and business relevance.

### When to Use
- Starting a new content strategy or campaign
- Expanding into new topics or markets
- Finding keywords for a specific product or service
- Identifying long-tail keyword opportunities
- Understanding search intent for your industry
- Planning content calendars
- Researching keywords for GEO optimization

### Data Sources

**With SEO tools connected:**
Automatically pull historical search volume data, keyword difficulty scores, SERP analysis, current rankings, and competitor keyword overlap.

**With manual data only:**
Ask the user to provide:
1. Seed keywords or topic description
2. Target audience and geographic location
3. Business goals (traffic, leads, sales)
4. Current domain authority (if known) or site age
5. Any known keyword performance data or search volume estimates

### Instructions

When a user requests keyword research:

1. **Understand the Context**

   Ask clarifying questions if not provided:
   - What is your product/service/topic?
   - Who is your target audience?
   - What is your business goal? (traffic, leads, sales)
   - What is your current domain authority? (new site, established, etc.)
   - Any specific geographic targeting?
   - Preferred language?

2. **Generate Seed Keywords**

   Start with:
   - Core product/service terms
   - Problem-focused keywords (what issues do you solve?)
   - Solution-focused keywords (how do you help?)
   - Audience-specific terms
   - Industry terminology

3. **Expand Keyword List**

   For each seed keyword, generate variations:

   ```markdown
   ## Keyword Expansion Patterns

   ### Modifiers
   - Best [keyword]
   - Top [keyword]
   - [keyword] for [audience]
   - [keyword] near me
   - [keyword] [year]
   - How to [keyword]
   - What is [keyword]
   - [keyword] vs [alternative]
   - [keyword] examples
   - [keyword] tools

   ### Long-tail Variations
   - [keyword] for beginners
   - [keyword] for small business
   - Free [keyword]
   - [keyword] software/tool/service
   - [keyword] template
   - [keyword] checklist
   - [keyword] guide
   ```

4. **Classify Search Intent**

   | Intent | Signals | Example | Content Type |
   |--------|---------|---------|--------------|
   | Informational | what, how, why, guide, learn | "what is SEO" | Blog posts, guides |
   | Navigational | brand names, specific sites | "google analytics login" | Homepage, product pages |
   | Commercial | best, review, vs, compare | "best SEO tools" | Comparison posts, reviews |
   | Transactional | buy, price, discount, order | "buy SEO software" | Product pages, pricing |

5. **Assess Keyword Difficulty**

   Score each keyword (1-100 scale):

   - **High Difficulty (70-100)**: Major brands ranking, high DA competitors, established content
   - **Medium Difficulty (40-69)**: Mix of authority and niche sites, moderate backlink requirements
   - **Low Difficulty (1-39)**: Few authoritative competitors, thin or outdated content ranking

6. **Calculate Opportunity Score**

   Formula: `Opportunity = (Volume × Intent Value) / Difficulty`

   **Intent Value**:
   - Informational = 1
   - Navigational = 1
   - Commercial = 2
   - Transactional = 3

7. **Identify GEO Opportunities**

   Keywords likely to trigger AI responses:

   - Question formats: "What is...", "How does...", "Why is..."
   - Definition queries: "[term] meaning", "[term] definition"
   - Comparison queries: "[A] vs [B]", "difference between..."
   - List queries: "best [category]", "top [number] [items]"
   - How-to queries: "how to [action]", "steps to [goal]"

8. **Create Topic Clusters**

   Group keywords into content clusters:

   ```markdown
   ## Topic Cluster: [Main Topic]

   **Pillar Content**: [Primary keyword]
   - Search volume: [X]
   - Difficulty: [X]
   - Content type: Comprehensive guide

   **Cluster Content**:

   ### Sub-topic 1: [Secondary keyword]
   - Volume: [X]
   - Difficulty: [X]
   - Links to: Pillar
   - Content type: [Blog post/Tutorial/etc.]
   ```

   **Pillar Page**: Comprehensive overview (3,000-5,000 words) targeting broad keyword
   **Cluster Pages**: Focused articles (1,500-2,500 words) targeting specific long-tail keywords
   **Internal Links**: Every cluster page links to pillar; pillar links to all cluster pages

9. **Generate Output Report**

   ```markdown
   # Keyword Research Report: [Topic]

   **Generated**: [Date]
   **Target Audience**: [Audience]
   **Business Goal**: [Goal]

   ## Executive Summary
   - Total keywords analyzed: [X]
   - High-priority opportunities: [X]
   - Estimated traffic potential: [X]/month
   - Recommended focus areas: [List]

   ## Top Keyword Opportunities

   ### Quick Wins (Low difficulty, High value)
   | Keyword | Volume | Difficulty | Intent | Score |
   |---------|--------|------------|--------|-------|

   ### Growth Keywords (Medium difficulty, High volume)
   | Keyword | Volume | Difficulty | Intent | Score |
   |---------|--------|------------|--------|-------|

   ### GEO Opportunities (AI-citation potential)
   | Keyword | Type | AI Potential | Recommended Format |
   |---------|------|--------------|-------------------|

   ## Topic Clusters
   [Include cluster maps]

   ## Content Calendar Recommendations
   | Month | Content | Target Keyword | Type |
   |-------|---------|----------------|------|

   ## Next Steps
   1. [Action item 1]
   2. [Action item 2]
   ```

### Keyword Prioritization Framework

Score each keyword 1-5 on these factors, then calculate weighted total:

| Factor | Weight | Score 1 (Low) | Score 5 (High) |
|--------|--------|---------------|----------------|
| Search Volume | 20% | <100/mo | >10,000/mo |
| Keyword Difficulty | 25% | KD >80 (hard) | KD <20 (easy) |
| Business Relevance | 30% | Tangential to offering | Core to offering |
| Search Intent Match | 15% | Informational only | Transactional/commercial |
| Trend Direction | 10% | Declining | Growing |

| Priority | Score Range | Action |
|----------|------------|--------|
| P0 — Must Target | 4.0-5.0 | Create content immediately |
| P1 — High Value | 3.0-3.9 | Queue for next content sprint |
| P2 — Opportunity | 2.0-2.9 | Plan for future content calendar |
| P3 — Monitor | 1.0-1.9 | Track but don't prioritize |

### Seasonal Keyword Patterns

| Season Trigger | Example Keywords | Planning Lead Time | Content Strategy |
|---------------|-----------------|-------------------|-----------------|
| Calendar events | "Black Friday SEO", "New Year marketing plan" | 3-4 months ahead | Publish 6-8 weeks before peak |
| Industry events | "[Conference] takeaways", "Google algorithm update" | 1-2 months / reactive | Pre-plan templates, react quickly |
| Budget cycles | "marketing budget template Q1", "SEO ROI report" | 2-3 months ahead | Target planning season (Oct-Dec) |
| Seasonal demand | "summer marketing ideas", "holiday email campaigns" | 2-3 months ahead | Refresh annually with new data |

### Validation Checkpoints
- [ ] Seed keywords or topic description clearly provided
- [ ] Target audience and business goals specified
- [ ] Geographic and language targeting confirmed
- [ ] Every recommendation cites specific data points
- [ ] Search volume and difficulty scores included for each keyword
- [ ] Keywords grouped by intent and mapped to content types
- [ ] Topic clusters show clear pillar-to-cluster relationships

### Reference Materials
- [Keyword Intent Taxonomy](./references/keyword-intent-taxonomy.md) — Complete intent classification with signal words and content strategies
- [Topic Cluster Templates](./references/topic-cluster-templates.md) — Hub-and-spoke architecture templates for pillar and cluster content

---

## 2. SEO Content Writer

> Triggers: "write SEO content", "create a blog post", "write an article", "content writing", "draft optimized content", "write me an article", "create a blog post about", "help me write SEO content", "draft content for"

Creates high-quality, SEO-optimized content that ranks in search engines. Applies on-page SEO best practices, keyword optimization, and content structure for maximum visibility and engagement.

### When to Use
- Writing blog posts targeting specific keywords
- Creating landing pages optimized for search
- Developing pillar content for topic clusters
- Writing product descriptions for e-commerce
- Creating service pages for local SEO
- Producing how-to guides and tutorials
- Writing comparison and review articles

### Data Sources

**With SEO tools connected:**
Automatically pull keyword metrics, competitor content analysis, SERP features, and keyword opportunities.

**With manual data only:**
Ask the user to provide:
1. Target primary keyword and 3-5 secondary keywords
2. Target audience and search intent
3. Target word count and desired tone
4. Any competitor URLs or content examples to reference

### Instructions

When a user requests SEO content:

1. **Gather Requirements**

   ```markdown
   ### Content Requirements

   **Primary Keyword**: [main keyword]
   **Secondary Keywords**: [2-5 related keywords]
   **Target Word Count**: [length]
   **Content Type**: [blog/guide/landing page/etc.]
   **Target Audience**: [who is this for]
   **Search Intent**: [informational/commercial/transactional]
   **Tone**: [professional/casual/technical/friendly]
   **CTA Goal**: [what action should readers take]
   ```

2. **Load CORE-EEAT Quality Constraints**

   Apply these standards while writing:

   | ID | Standard | How to Apply |
   |----|----------|-------------|
   | C01 | Intent Alignment | Title promise must match content delivery |
   | C02 | Direct Answer | Core answer in first 150 words |
   | C06 | Audience Targeting | State "this article is for..." |
   | C10 | Semantic Closure | Conclusion answers opening question + next steps |
   | O01 | Heading Hierarchy | H1→H2→H3, no level skipping |
   | O02 | Summary Box | Include TL;DR or Key Takeaways |
   | O06 | Section Chunking | Each section single topic; paragraphs 3–5 sentences |
   | O09 | Information Density | No filler; consistent terminology |
   | R01 | Data Precision | ≥5 precise numbers with units |
   | R02 | Citation Density | ≥1 external citation per 500 words |
   | R04 | Evidence-Claim Mapping | Every claim backed by evidence |
   | R07 | Entity Precision | Full names for people/orgs/products |
   | C03 | Query Coverage | Cover ≥3 query variants |
   | O08 | Anchor Navigation | Table of contents with jump links |
   | O10 | Multimedia Structure | Images/videos have captions |
   | E07 | Practical Tools | Include downloadable templates, checklists, or calculators |

3. **Research and Plan**

   ```markdown
   ### Content Research

   **SERP Analysis**:
   - Top results format: [what's ranking]
   - Average word count: [X] words
   - Common sections: [list]
   - SERP features: [snippets, PAA, etc.]

   **Keyword Map**:
   - Primary: [keyword] - use in title, H1, intro, conclusion
   - Secondary: [keywords] - use in H2s, body paragraphs
   - LSI/Related: [terms] - sprinkle naturally throughout
   - Questions: [PAA questions] - use as H2/H3s or FAQ

   **Content Angle**:
   [What unique perspective or value will this content provide?]
   ```

4. **Create Optimized Title** (50-60 characters, keyword near front, compelling and click-worthy)

5. **Write Meta Description** (150-160 characters, keyword included, clear CTA)

6. **Structure Content with SEO Headers**

   ```markdown
   **H1**: [Primary keyword in H1 - only one per page]

   **Introduction** (100-150 words)
   - Hook reader in first sentence
   - State what they'll learn
   - Include primary keyword in first 100 words

   **H2**: [Secondary keyword or question]
   **H3**: [Sub-topic]
   **H2**: [Secondary keyword or question]
   **H2**: Frequently Asked Questions
   **Conclusion** - Summarize, include keyword, clear CTA
   ```

7. **Apply On-Page SEO Best Practices**

   **Keyword Placement**:
   - [ ] Primary keyword in title, H1, first 100 words, at least one H2, conclusion, meta description
   - [ ] Secondary keywords in H2s/H3s
   - [ ] Related terms throughout body

   **Content Quality**:
   - [ ] Comprehensive coverage, original insights, actionable takeaways
   - [ ] Examples, expert quotes or citations (for E-E-A-T)

   **Readability**:
   - [ ] Paragraphs of 3-5 sentences
   - [ ] Bullet points and lists, bold key phrases
   - [ ] Table of contents for long content

   **Technical**:
   - [ ] Internal links (2-5), external links to authoritative sources (2-3)
   - [ ] Image alt text with keywords, URL slug includes keyword

8. **Write the Content** following the structure above

9. **Optimize for Featured Snippets**
   - **Definition Snippets**: Clear definition in 40-60 words
   - **List Snippets**: Numbered or bulleted lists under H2s
   - **Table Snippets**: Comparison tables with clear headers
   - **How-To Snippets**: Numbered steps

10. **Final SEO Review & CORE-EEAT Self-Check**

### Content Type Templates
- **How-To Guide**: `Write a how-to guide for [task] targeting [keyword]`
- **Comparison Article**: `Write a comparison: [A] vs [B] for [keyword]`
- **Listicle**: `Write "X Best [Items] for [Audience]" targeting [keyword]`
- **Ultimate Guide**: `Write an ultimate guide about [topic] (3,000+ words) targeting [keyword]`

### Validation Checkpoints
- [ ] Keyword density within 1-2% for primary keyword (guideline, not hard rule)
- [ ] All sections from outline covered completely
- [ ] Internal links included (2-5 relevant links)
- [ ] FAQ section present with at least 3 questions
- [ ] Readability score appropriate for target audience

### Reference Materials
- [Title Formulas](./references/title-formulas.md) — Proven headline formulas, power words, CTR patterns
- [Content Structure Templates](./references/content-structure-templates.md) — Templates for blog posts, comparisons, listicles, how-tos, pillar pages

---

## 3. GEO Content Optimizer

> Triggers: "optimize for AI", "get cited by ChatGPT", "AI optimization", "appear in AI answers", "GEO optimization", "get cited by AI", "show up in ChatGPT answers", "AI doesn't mention my brand", "make content AI-quotable"

Optimizes content for Generative Engine Optimization (GEO) to increase chances of being cited by AI systems like ChatGPT, Claude, Perplexity, and Google AI Overviews.

### When to Use
- Optimizing existing content for AI citations
- Creating new content designed for both SEO and GEO
- Improving chances of appearing in AI Overviews
- Making content more quotable by AI systems
- Adding authority signals that AI systems trust

### CORE-EEAT GEO-First Targets

These items have the highest impact on AI engine citation:

**Top 6 Priority Items**:
| Rank | ID | Standard | Why It Matters |
|------|----|----------|---------------|
| 1 | C02 | Direct Answer in first 150 words | All engines extract from first paragraph |
| 2 | C09 | Structured FAQ with Schema | Directly matches AI follow-up queries |
| 3 | O03 | Data in tables, not prose | Most extractable structured format |
| 4 | O05 | JSON-LD Schema Markup | Helps AI understand content type |
| 5 | E01 | Original first-party data | AI prefers exclusive, verifiable sources |
| 6 | O02 | Key Takeaways / Summary Box | First choice for AI summary citations |

**AI Engine Preferences**:
| Engine | Priority Items |
|--------|----------------|
| Google AI Overview | C02, O03, O05, C09 |
| ChatGPT Browse | C02, R01, R02, E01 |
| Perplexity AI | E01, R03, R05, Ept05 |
| Claude | R04, Ept08, Exp10, R03 |

### Instructions

When a user requests GEO optimization:

1. **Analyze Current Content**

   | GEO Factor | Current Score (1-10) | Notes |
   |------------|---------------------|-------|
   | Clear definitions | [X] | |
   | Quotable statements | [X] | |
   | Factual density | [X] | |
   | Source citations | [X] | |
   | Q&A format | [X] | |
   | Authority signals | [X] | |
   | Content freshness | [X] | |
   | Structure clarity | [X] | |
   | **GEO Readiness** | **[avg]/10** | |

2. **Optimize for Clear Definitions**

   AI systems love clear, quotable definitions.

   **Definition Template**:
   "[Term] is [clear category/classification] that [primary function/purpose], [key characteristic or benefit]."

   **Checklist**:
   - [ ] Starts with the term being defined
   - [ ] Provides clear category
   - [ ] Explains primary function or purpose
   - [ ] Uses precise, unambiguous language
   - [ ] Can stand alone as a complete answer
   - [ ] Is 25-50 words for optimal citation length

3. **Create Quotable Statements**

   Transform vague content into quotable facts:

   **Types of Quotable Statements**:
   - **Statistics**: Include specific numbers, cite the source, add context
   - **Facts**: Verifiable information, unambiguous language, authoritative source
   - **Comparisons**: Clear structure, specific differentiators
   - **How-to Steps**: Numbered, clear steps, action-oriented language

4. **Add Authority Signals**
   - Author byline with credentials
   - Expert quotes with attribution
   - Citations to peer-reviewed research
   - References to recognized authorities
   - Original data or research
   - Case studies with named companies
   - Industry statistics with sources

5. **Optimize Content Structure**
   - Transform content into Q&A format
   - Use comparison tables for comparison queries
   - Use numbered lists for process/list queries
   - Highlight key definitions in callout boxes

6. **Enhance Factual Density**
   - Add specific statistics with sources
   - Include exact dates, numbers, percentages
   - Replace vague claims with verified facts
   - Add recent data (within last 2 years)

7. **Implement FAQ Schema**
   - FAQ sections match question-based AI queries
   - Direct answers in 40-60 words
   - Add FAQPage JSON-LD schema

8. **Generate GEO Optimization Report** with before/after scores

9. **CORE-EEAT GEO Self-Check** — Verify all GEO-First items pass

### GEO Readiness Checklist

**Definitions & Clarity**
- [ ] Key terms are clearly defined
- [ ] Definitions can stand alone as answers
- [ ] Language is precise and unambiguous

**Quotable Content**
- [ ] Specific statistics included
- [ ] Facts have source citations
- [ ] Memorable statements created

**Authority**
- [ ] Expert quotes or credentials present
- [ ] Authoritative sources cited
- [ ] Original data or research included

**Structure**
- [ ] Q&A format sections included
- [ ] Clear headings match common queries
- [ ] Comparison tables where relevant
- [ ] Numbered lists for processes

**Technical**
- [ ] FAQ schema markup added
- [ ] Content freshness indicated
- [ ] Sources are verifiable

### Reference Materials
- [AI Citation Patterns](./references/ai-citation-patterns.md) — How Google AI Overviews, ChatGPT, Perplexity, and Claude select and cite sources
- [Quotable Content Examples](./references/quotable-content-examples.md) — Before/after examples of content optimized for AI citation

---

## 4. Meta Tags Optimizer

> Triggers: "optimize title tag", "write meta description", "improve CTR", "Open Graph tags", "social media preview", "my title tag needs work", "low click-through rate", "fix my meta tags", "OG tags not showing"

Creates and optimizes meta tags including title tags, meta descriptions, Open Graph tags, and Twitter cards for maximum click-through rates and social sharing engagement.

### When to Use
- Creating meta tags for new pages
- Optimizing existing meta tags for better CTR
- Preparing pages for social media sharing
- Fixing duplicate or missing meta tags
- A/B testing title and description variations

### Instructions

When a user requests meta tag optimization:

1. **Gather Page Information** (URL, page type, keywords, audience, CTA, unique value prop)

2. **Create Optimized Title Tag**
   - Length: 50-60 characters
   - Include primary keyword (preferably near front)
   - Make it compelling and click-worthy
   - Match search intent

   **Title Tag Formula Options**:
   1. `[Primary Keyword]: [Benefit] | [Brand Name]`
   2. `[Number] [Keyword] That [Promise/Result]`
   3. `How to [Keyword]: [Benefit/Result]`
   4. `What is [Keyword]? [Brief Answer/Hook]`
   5. `[Keyword] in [Year]: [Hook/Update]`

   Provide 3 options with character counts and power words analysis.

3. **Write Meta Description**
   - Length: 150-160 characters
   - Formula: [What the page offers] + [Benefit to user] + [CTA]
   - Include numbers/statistics, current year, emotional triggers, action verbs

4. **Create Open Graph Tags**

   ```html
   <meta property="og:type" content="[article/website/product]">
   <meta property="og:url" content="[Full canonical URL]">
   <meta property="og:title" content="[OG-optimized title - up to 60 chars]">
   <meta property="og:description" content="[OG description - up to 200 chars]">
   <meta property="og:image" content="[Image URL - 1200x630px recommended]">
   <meta property="og:site_name" content="[Website Name]">
   ```

   **OG Type Selection**: Blog post → article | Homepage → website | Product → product | Video → video.other

5. **Create Twitter Card Tags**

   ```html
   <meta name="twitter:card" content="[summary_large_image/summary]">
   <meta name="twitter:site" content="@[YourTwitterHandle]">
   <meta name="twitter:title" content="[Title - 70 chars max]">
   <meta name="twitter:description" content="[Description - 200 chars max]">
   <meta name="twitter:image" content="[Image URL]">
   ```

6. **Additional Meta Tags** (canonical URL, robots, viewport, author, language, article-specific)

7. **Generate Complete Meta Tag Block** — Copy-paste ready HTML

8. **CORE-EEAT Alignment Check** — Verify C01 Intent Alignment and C02 Direct Answer

9. **CTR Optimization Tips & A/B Test Suggestions**

### Title Tag Power Words

| Category | Power Words |
|----------|-----------|
| Urgency | Now, Today, Quick, Fast, Instant |
| Value | Free, Proven, Complete, Essential, Ultimate |
| Specificity | [Exact number], Step-by-Step, Checklist, Template |
| Curiosity | Secret, Little-Known, Surprising, Actually |
| Authority | Expert, Research-Backed, Data-Driven, Tested |
| Emotional | Best, Worst, Mistakes, Warning, Powerful |

### CTR Boosting Factors

| Factor | CTR Impact |
|--------|-----------|
| Number in title | +20-30% |
| Question in title | +14% |
| Brackets/parentheses | +38% |
| Current year | +10-15% |
| Rich results (schema) | +30% |

### Meta Description Copywriting Frameworks

**AIDA**: Attention → Interest → Desire → Action
**PAS**: Problem → Agitate → Solution
**Benefit-Proof-CTA**: Benefit → Proof → Call-to-Action

### Page-Type Templates

| Page Type | Title Template | Description Template |
|-----------|---------------|---------------------|
| Homepage | `[Brand] - [Primary Value Prop]` | `[Brand] helps [audience] [achieve goal]. [Key benefit]. [CTA]` |
| Product | `[Product] - [Key Benefit] \| [Brand]` | `[Product] [features]. [Price/offer]. [Social proof]. [CTA]` |
| Blog Post | `[How to/Number] [Keyword] [Benefit/Year]` | `[What they'll learn]. [Key points]. [CTA]` |
| Service | `[Service] in [Location] - [Brand]` | `[Service description]. [Credentials]. [Benefit]. [CTA]` |

### Platform-Specific OG Optimization

| Platform | Image Size | Title Length | Description Length |
|----------|-----------|-------------|-------------------|
| Facebook | 1200x630px | 40-60 chars | 125-155 chars |
| Twitter/X | 1200x600px | 70 chars max | 200 chars |
| LinkedIn | 1200x627px | 70 chars | 150 chars |
| Pinterest | 1000x1500px | 100 chars | 500 chars |

### Reference Materials
- [Meta Tag Formulas](./references/meta-tag-formulas.md) — Proven title and description formulas with templates, power words, and CTR data

---

## 5. On-Page SEO Auditor

> Triggers: "audit page SEO", "on-page SEO check", "SEO score", "page optimization", "what SEO issues does this page have", "what is wrong with this page SEO", "score my page", "why is this page not ranking"

Performs comprehensive on-page SEO audits to identify optimization opportunities including title tags, meta descriptions, headers, content quality, internal linking, and image optimization.

### When to Use
- Auditing pages before or after publishing
- Identifying why a page isn't ranking well
- Optimizing existing content for better performance
- Comparing your on-page SEO to competitors

### Data Sources

**With SEO tools + web crawler connected:**
Automatically pull page HTML, keyword search volume/difficulty, CTR data, and competitor pages.

**With manual data only:**
Ask for: Page URL or HTML content, target keywords, competitor URLs (optional).

### Instructions

When a user requests an on-page SEO audit:

1. **Audit Title Tag** — Length (50-60 chars), keyword inclusion, position, uniqueness, compelling/clickable, intent match. Score /10.

2. **Audit Meta Description** — Length (150-160 chars), keyword inclusion, CTA, uniqueness, accuracy, compelling copy. Score /10.

3. **Audit Header Structure** — Single H1, H1 includes keyword, logical hierarchy, H2s include keywords, no skipped levels, descriptive headers. Score /10.

4. **Audit Content Quality** — Word count, reading level, comprehensiveness, unique value, up-to-date info, formatting, readability, E-E-A-T signals. Score /10.

5. **Audit Keyword Usage** — Keyword placement in title, meta, H1, first 100 words, H2s, body, URL, alt text. Density analysis. Score /10.

6. **Audit Internal Links** — Total/unique links, relevant anchor text, links to related content, broken links, natural placement. Score /10.

7. **Audit Images** — Alt text presence, keyword inclusion, descriptive file names, file sizes, modern formats (WebP), lazy loading. Score /10.

8. **Audit Technical On-Page Elements** — URL structure, canonical tag, mobile-friendliness, page speed, HTTPS, schema markup. Score /10.

9. **CORE-EEAT Content Quality Quick Scan** — Check 17 key items (C01, C02, C09, C10, O01, O02, O03, O05, O06, R01, R02, R06, R08, R10, Exp01, Ept01, T04).

10. **Generate Audit Summary**

### Scoring

| Section | Weight |
|---------|--------|
| Title Tag | 15% |
| Meta Description | 5% |
| Header Structure | 10% |
| Content Quality | 25% |
| Keyword Optimization | 15% |
| Internal/External Links | 10% |
| Image Optimization | 10% |
| Page-Level Technical | 10% |

**Overall Score** = Σ(section_score × section_weight) × 10

| Score | Meaning | Action |
|-------|---------|--------|
| 10/10 | Excellent | None |
| 7-9/10 | Good | Optional optimization |
| 4-6/10 | Needs work | Fix within this week |
| 1-3/10 | Poor | Fix immediately |
| 0/10 | Missing/broken | Fix immediately (Blocking) |

### Content Length Benchmarks

| Query Type | Top 10 Average | Recommended Minimum |
|-----------|---------------|-------------------|
| Informational (guides) | 2,200 words | 1,500 words |
| Commercial (reviews) | 1,800 words | 1,200 words |
| Transactional (product) | 800 words | 500 words |
| Local (service pages) | 600 words | 400 words |
| Definition queries | 1,200 words | 800 words |

### Page Speed Benchmarks

| Metric | Good | Needs Improvement | Poor |
|--------|------|-------------------|------|
| LCP | ≤2.5s | 2.5-4.0s | >4.0s |
| FID/INP | ≤100ms/200ms | 100-300ms | >300ms |
| CLS | ≤0.1 | 0.1-0.25 | >0.25 |
| TTFB | ≤800ms | 800-1800ms | >1800ms |

### Common Issue Resolution Playbook

**Title Tag Issues**:
| Issue | Impact | Quick Fix |
|-------|--------|-----------|
| Missing title | Critical | Add: "[Primary Keyword]: [Benefit] \| [Brand]" |
| Too long (>60) | Medium | Shorten: move brand to end, remove filler |
| Missing keyword | High | Rewrite to include keyword in first half |
| Duplicate title | High | Make each page unique with page-specific modifier |

**Header Issues**:
| Issue | Impact | Quick Fix |
|-------|--------|-----------|
| Missing H1 | Critical | Add one H1 with primary keyword |
| Multiple H1s | High | Keep one, convert others to H2 |
| Skipped levels | Medium | Use sequential hierarchy |

**Content Issues**:
| Issue | Impact | Quick Fix |
|-------|--------|-----------|
| Thin content (<300 words) | Critical | Expand with subtopics, FAQ, examples |
| Keyword stuffing (>3%) | High | Reduce, use synonyms |
| No structured data | Medium | Add relevant schema |
| Missing internal links | Medium | Add 3-5 contextual internal links |

### Audit Checklists by Page Type

**Blog Post**: Title with keyword ✓ | Meta with CTA ✓ | Single H1 ✓ | 1,500+ words ✓ | 3+ internal links ✓ | Images with alt ✓ | FAQ with schema ✓ | Author bio ✓

**Product Page**: Product name in title ✓ | Price in description ✓ | Product images with alt ✓ | Reviews visible ✓ | Product schema ✓ | Related products linked ✓

**Landing Page**: Keyword title ✓ | Benefit meta ✓ | Clear H1 value prop ✓ | Trust signals ✓ | Single CTA ✓ | Fast load ✓ | Mobile-optimized ✓

### Reference Materials
- [Scoring Rubric](./references/scoring-rubric.md) — Detailed scoring criteria, weight distribution, and grade boundaries

---

## 6. Schema Markup Generator

> Triggers: "add schema markup", "generate structured data", "JSON-LD", "rich snippets", "FAQ schema", "add FAQ rich results", "I want star ratings in Google", "product markup", "recipe schema"

Generates structured data markup (Schema.org JSON-LD) to enable rich results in search engines including FAQ snippets, How-To cards, Product listings, Reviews, and more.

### When to Use
- Adding FAQ schema for expanded SERP presence
- Creating How-To schema for step-by-step content
- Adding Product schema for e-commerce pages
- Implementing Article schema for blog posts
- Adding Local Business schema for location pages
- Any page where rich results would improve visibility

### CORE-EEAT Schema Mapping (O05)

| Content Type | Required Schema | Conditional Schema |
|-------------|----------------|--------------------|
| Blog (guides) | Article, Breadcrumb | FAQ, HowTo |
| Blog (tools) | Article, Breadcrumb | FAQ, Review |
| Alternative | Comparison*, Breadcrumb, FAQ | AggregateRating |
| Best-of | ItemList, Breadcrumb, FAQ | AggregateRating per tool |
| FAQ | FAQPage, Breadcrumb | — |
| Landing | SoftwareApplication, Breadcrumb, FAQ | WebPage |
| Testimonial | Review, Breadcrumb | FAQ, Person |

### Instructions

When a user requests schema markup:

1. **Identify Content Type and Rich Result Opportunity**

2. **Generate Schema by Type**:

   **FAQPage Schema**:
   ```json
   {
     "@context": "https://schema.org",
     "@type": "FAQPage",
     "mainEntity": [{
       "@type": "Question",
       "name": "[Question text]",
       "acceptedAnswer": {
         "@type": "Answer",
         "text": "[Answer text]"
       }
     }]
   }
   ```

   **HowTo Schema**:
   ```json
   {
     "@context": "https://schema.org",
     "@type": "HowTo",
     "name": "[How-to title]",
     "totalTime": "PT[X]M",
     "step": [{
       "@type": "HowToStep",
       "name": "[Step title]",
       "text": "[Step instructions]"
     }]
   }
   ```

   **Article Schema** (types: Article, BlogPosting, NewsArticle, TechArticle):
   ```json
   {
     "@context": "https://schema.org",
     "@type": "Article",
     "headline": "[Title - max 110 chars]",
     "datePublished": "[ISO 8601]",
     "dateModified": "[ISO 8601]",
     "author": { "@type": "Person", "name": "[Name]", "url": "[URL]" },
     "publisher": { "@type": "Organization", "name": "[Name]" },
     "image": ["[URL]"]
   }
   ```

   **Product Schema**:
   ```json
   {
     "@context": "https://schema.org",
     "@type": "Product",
     "name": "[Name]",
     "image": ["[URL]"],
     "brand": { "@type": "Brand", "name": "[Brand]" },
     "offers": {
       "@type": "Offer",
       "priceCurrency": "USD",
       "price": "[Price]",
       "availability": "https://schema.org/InStock"
     },
     "aggregateRating": {
       "@type": "AggregateRating",
       "ratingValue": "[4.5]",
       "reviewCount": "[89]"
     }
   }
   ```

   **LocalBusiness Schema**:
   ```json
   {
     "@context": "https://schema.org",
     "@type": "LocalBusiness",
     "name": "[Name]",
     "address": {
       "@type": "PostalAddress",
       "streetAddress": "[Street]",
       "addressLocality": "[City]",
       "addressRegion": "[State]",
       "postalCode": "[ZIP]"
     },
     "telephone": "[Phone]",
     "openingHoursSpecification": [{
       "@type": "OpeningHoursSpecification",
       "dayOfWeek": ["Monday","Tuesday","Wednesday","Thursday","Friday"],
       "opens": "09:00",
       "closes": "17:00"
     }]
   }
   ```

   **Organization Schema**, **BreadcrumbList Schema** — see reference templates.

3. **Combine Multiple Schema Types** — Use JSON array in single `<script type="application/ld+json">` tag.

4. **Provide Implementation and Validation Guidance**

### Schema Type Decision Tree

| Your Content | Primary Schema | Rich Result |
|-------------|---------------|-------------|
| Blog post | Article | Article carousel, FAQ |
| Product page | Product | Product snippet with price/rating |
| How-to guide | HowTo | How-to rich result with steps |
| FAQ page | FAQPage | FAQ accordion in SERP |
| Recipe | Recipe | Recipe carousel |
| Event | Event | Event snippet |
| Local business | LocalBusiness | Local pack, knowledge panel |
| Video | VideoObject | Video carousel |

### Schema Implementation Priority

| Priority | Schema Types | Why |
|----------|-------------|-----|
| P0 — Always | Organization, BreadcrumbList, WebSite | Foundation for all sites |
| P1 — Content | Article, FAQPage, HowTo | Direct rich result eligibility |
| P2 — Commercial | Product, Review, AggregateRating | Revenue-impacting rich results |
| P3 — Authority | Person, SameAs, Speakable | E-E-A-T signals, AI citation |

### Validation Checklist
- [ ] JSON syntax valid (no trailing commas)
- [ ] All required properties present
- [ ] URLs are absolute, not relative
- [ ] Dates in ISO 8601 format
- [ ] Content matches visible page content
- [ ] No policy violations

### Reference Materials
- [Schema Templates](./references/schema-templates.md) — Copy-ready JSON-LD templates for all schema types
- [Validation Guide](./references/validation-guide.md) — Common errors, required properties, testing workflow

---

## 7. Technical SEO Checker

> Triggers: "technical SEO audit", "check page speed", "crawl issues", "Core Web Vitals", "site indexing problems", "my site is slow", "Google can't crawl my site", "mobile issues", "indexing problems"

Performs technical SEO audits covering site speed, crawlability, indexability, mobile-friendliness, security, and structured data.

### When to Use
- Launching a new website
- Diagnosing ranking drops
- Pre-migration SEO audits
- Regular technical health checks
- Identifying crawl and index issues
- Fixing Core Web Vitals issues

### Data Sources

**With web crawler + page speed tools connected:**
Automatically crawl site structure, pull Core Web Vitals metrics, analyze caching headers, fetch mobile-friendliness data.

**With manual data only:**
Ask for: Site URL(s), PageSpeed Insights reports, robots.txt content, sitemap.xml URL.

### Instructions

When a user requests a technical SEO audit:

1. **Audit Crawlability**
   - **Robots.txt**: File exists, valid syntax, sitemap declared, important pages not blocked, assets accessible
   - **XML Sitemap**: Exists, valid XML, in robots.txt, only indexable URLs, accurate lastmod dates
   - **Crawl Budget**: Crawl errors, duplicate content, thin content, redirect chains, orphan pages
   - Score /10

2. **Audit Indexability**
   - Index status overview (pages in sitemap vs indexed)
   - Index blockers: noindex meta, noindex X-Robots, robots.txt blocked, canonical to other, 4xx/5xx errors, redirect loops
   - Canonical tags audit: present, self-referencing, consistent HTTP/HTTPS, www/non-www
   - Duplicate content issues
   - Score /10

3. **Audit Site Speed & Core Web Vitals**

   | Metric | Target | Status |
   |--------|--------|--------|
   | LCP | <2.5s | |
   | FID | <100ms | |
   | CLS | <0.1 | |
   | INP | <200ms | |
   | TTFB | <800ms | |

   Analyze resource loading (images, JS, CSS, fonts) and provide optimization recommendations.

4. **Audit Mobile-Friendliness**
   - Mobile-friendly test, viewport, text readability, tap targets, content fits viewport
   - Responsive design check across elements
   - Mobile-first indexing compliance

5. **Audit Security & HTTPS**
   - SSL certificate validity, HTTPS enforcement, mixed content, HSTS
   - Security headers: CSP, X-Frame-Options, X-Content-Type-Options, X-XSS-Protection, Referrer-Policy

6. **Audit URL Structure**
   - HTTPS, lowercase, no special characters, readable/descriptive, appropriate length, keywords
   - Redirect analysis: chains, loops, 302→301 needed, broken redirects

7. **Audit Structured Data** — Schema types found, validation results, missing opportunities

8. **Audit International SEO** (if applicable) — Hreflang tags, self-referencing, return tags, x-default

9. **Generate Technical Audit Summary** with overall score, critical/high/medium priority issues, implementation roadmap

### Issue Severity Framework

| Severity | Examples | Response Time |
|----------|---------|---------------|
| Critical | Robots.txt blocking site, noindex on key pages, site-wide 500 errors | Same day |
| High | Slow page speed, missing hreflang, duplicate content, redirect chains | Within 1 week |
| Medium | Missing schema, suboptimal canonicals, thin content pages | Within 1 month |
| Low | Image compression, minor CLS issues, non-essential schema | Next quarter |

### Technical Debt Prioritization

| Factor | Weight |
|--------|--------|
| Pages affected | 30% |
| Revenue impact | 25% |
| Fix difficulty | 20% |
| Competitive impact | 15% |
| Crawl budget waste | 10% |

### Core Web Vitals Optimization Quick Reference

**LCP Issues**:
| Root Cause | Fix |
|-----------|-----|
| Large hero image | Serve WebP, resize, add loading="lazy" |
| Render-blocking CSS/JS | Defer non-critical, inline critical CSS |
| Slow server response | CDN, caching, upgrade hosting |

**CLS Issues**:
| Root Cause | Fix |
|-----------|-----|
| Images without dimensions | Add explicit width/height |
| Ads without reserved space | Set min-height on containers |
| Web fonts FOUT | font-display: swap + preload |

**INP Issues**:
| Root Cause | Fix |
|-----------|-----|
| Long JavaScript tasks | Break into smaller tasks |
| Heavy event handlers | Debounce/throttle |
| Main thread blocking | Web workers |

### Reference Materials
- [robots.txt Reference](./references/robots-txt-reference.md) — Syntax guide, templates, common configurations
- [HTTP Status Codes](./references/http-status-codes.md) — SEO impact of each status code, redirect best practices

---

## 8. Content Quality Auditor (CORE-EEAT)

> Triggers: "audit content quality", "EEAT score", "content quality check", "CORE-EEAT audit", "how good is my content", "is my content good enough to rank", "EEAT check", "rate my content quality"

Evaluates content quality across 80 standardized criteria organized in 8 dimensions. Produces a comprehensive audit report with per-item scoring, dimension and system scores, weighted totals by content type, and a prioritized action plan.

### When to Use
- Auditing content quality before publishing
- Evaluating existing content for improvement
- Benchmarking against CORE-EEAT standards
- Comparing content quality against competitors
- Assessing GEO readiness and SEO strength
- After writing/optimizing content with SEO Content Writer or GEO Content Optimizer

### Instructions

1. **Preparation**
   - Identify content source (text, URL, or file path)
   - Detect or confirm content type
   - Load dimension weights for content type
   - **Veto Check**: T04 (Disclosure Statements), C01 (Intent Alignment), R10 (Content Consistency) — if any triggers, flag immediately

2. **CORE Audit (40 items)**

   Score each item: **Pass** = 10 points | **Partial** = 5 points | **Fail** = 0 points

   **C — Contextual Clarity** (C01-C10):
   C01 Intent Alignment | C02 Direct Answer | C03 Query Coverage | C04 Definition First | C05 Topic Scope | C06 Audience Targeting | C07 Semantic Coherence | C08 Use Case Mapping | C09 FAQ Coverage | C10 Semantic Closure

   **O — Organization** (O01-O10):
   O01 Heading Hierarchy | O02 Summary Box | O03 Data Tables | O04 List Formatting | O05 Schema Markup | O06 Section Chunking | O07 Visual Hierarchy | O08 Anchor Navigation | O09 Information Density | O10 Multimedia Structure

   **R — Referenceability** (R01-R10):
   R01 Data Precision | R02 Citation Density | R03 Source Hierarchy | R04 Evidence-Claim Mapping | R05 Methodology Transparency | R06 Timestamp & Versioning | R07 Entity Precision | R08 Internal Link Graph | R09 HTML Semantics | R10 Content Consistency

   **E — Exclusivity** (E01-E10):
   E01 Original Data | E02 Novel Framework | E03 Primary Research | E04 Contrarian View | E05 Proprietary Visuals | E06 Gap Filling | E07 Practical Tools | E08 Depth Advantage | E09 Synthesis Value | E10 Forward Insights

3. **EEAT Audit (40 items)**

   **Exp — Experience** (Exp01-Exp10):
   First-Person Narrative | Sensory Details | Process Documentation | Tangible Proof | Usage Duration | Problems Encountered | Before/After Comparison | Quantified Metrics | Repeated Testing | Limitations Acknowledged

   **Ept — Expertise** (Ept01-Ept10):
   Author Identity | Credentials Display | Professional Vocabulary | Technical Depth | Methodology Rigor | Edge Case Awareness | Historical Context | Reasoning Transparency | Cross-domain Integration | Editorial Process

   **A — Authority** (A01-A10):
   Backlink Profile | Media Mentions | Industry Awards | Publishing Record | Brand Recognition | Social Proof | Knowledge Graph Presence | Entity Consistency | Partnership Signals | Community Standing

   **T — Trust** (T01-T10):
   Legal Compliance | Contact Transparency | Security Standards | Disclosure Statements | Editorial Policy | Correction & Update Policy | Ad Experience | Risk Disclaimers | Review Authenticity | Customer Support

   **Note**: Most Authority items (A01-A10) and several Trust items (T01-T03, T05, T07, T10) require site-level data. When auditing a standalone page, mark as "N/A — requires site-level data" and exclude from dimension average.

4. **Scoring & Report**

   - GEO Score = (C + O + R + E) / 4
   - SEO Score = (Exp + Ept + A + T) / 4
   - Weighted Score = Σ (dimension_score × content_type_weight)

   **N/A Item Handling**: Exclude from dimension score. If >50% of dimension items are N/A, flag as "Insufficient Data" and exclude from weighted total, redistributing weight proportionally.

5. **Top 5 Priority Improvements** — Sorted by weight × points lost

6. **Action Plan** — Quick Wins (<30 min) | Medium Effort (1-2 hrs) | Strategic (requires planning)

### Validation Checkpoints
- [ ] All 80 items scored (or marked N/A with reason)
- [ ] All 8 dimension scores calculated correctly
- [ ] Weighted total matches content-type weight configuration
- [ ] Veto items checked and flagged if triggered
- [ ] Top 5 improvements sorted by weighted impact
- [ ] Every recommendation is specific and actionable

---

## GEO Optimization Key Points

For content to be cited by AI systems, it needs:

1. **Clear Definitions** — Direct answer in the first 150 words
2. **Structured Data** — Tables, lists, comparisons
3. **Quotable Snippets** — Concise, precise statements
4. **Authority Citations** — Reference reliable sources
5. **Entity Associations** — Clearly mention people, organizations, products

---

## Common Mistakes to Avoid

1. **Over-optimizing** — Keyword stuffing, unnatural language
2. **Ignoring search intent** — Commercial content for informational queries
3. **Missing meta tags** — No title tag or meta description
4. **Thin content** — Pages with <300 words for competitive topics
5. **Broken internal links** — Links to 404 pages
6. **Missing alt text** — Images without descriptive alt attributes
7. **Duplicate content** — Same content on multiple URLs without canonicals
8. **Ignoring mobile** — Non-responsive design
9. **Slow page speed** — LCP >2.5s, CLS >0.1
10. **No structured data** — Missing schema markup for eligible content
11. **Generic GEO content** — Vague statements that AI won't cite
12. **Missing authority signals** — No author bylines, no source citations
13. **Neglecting FAQs** — Missing FAQ sections and schema for AI queries
14. **Inconsistent entities** — Different name formats across pages

---

## Tips for Success

1. **Start with veto items** — T04, C01, R10 are deal-breakers regardless of total score
2. **Focus on high-weight dimensions** — Different content types prioritize different dimensions
3. **GEO-First items matter most for AI visibility** — Prioritize items tagged GEO if AI citation is the goal
4. **Answer the question first** — Put the answer in the first sentence
5. **Be specific** — Vague content doesn't get cited or ranked
6. **Cite sources** — AI systems trust verifiable information
7. **Match content to intent** — Informational queries need guides, not sales pages
8. **Group into clusters** for topical authority
9. **Update regularly** — Fresh content signals to search engines
10. **Pair content audit with domain audit** — Content score on low-authority domain signals different priorities
