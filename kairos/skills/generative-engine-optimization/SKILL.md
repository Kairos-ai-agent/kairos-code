---
name: "generative-engine-optimization"
description: "Generate Engine Optimization (生成式引擎优化) — optimize content for AI search engines (豆包, DeepSeek, 元宝, Kimi, 通义千问). Covers JSON-LD structured data, FAQPage schema, natural language question expansion, met"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/research/generative-engine-optimization/SKILL.md"
---
# Generative Engine Optimization (GEO)

## When to Use

User asks about:
- "GEO", "生成式引擎优化", "AI搜索优化"
- "让豆包/DeepSeek/Kimi引用我们的内容"
- "内容被AI搜索收录"
- "结构化数据优化"
- "FAQPage Schema"
- Making content visible in AI-generated answers

## Core Principle

GEO ≠ SEO. Traditional SEO optimizes for **ranking pages** in search results. GEO optimizes for **being cited in AI-generated answers**. AI search engines (豆包, DeepSeek, Kimi, 元宝, 通义千问) work like this:

> User query → **Semantic understanding** → **Knowledge retrieval (RAG)** → **Content synthesis** → **Cited answer**

GEO makes your content **easier to retrieve**, **more trusted**, and **more likely to be selected** as an answer source.

## GEO vs SEO — Core Differences

| Dimension | Traditional SEO | GEO |
|-----------|----------------|-----|
| Goal | Rank at top of search results | Be cited in AI-generated answers |
| Core | Keywords + backlinks + PageRank | Structured data + information density + authority signals |
| Content form | Flowing articles | Conclusion-first + data comparisons + tables + FAQ |
| Keyword strategy | Natural keyword distribution | Semantic coverage + entity consistency + natural questions |
| Technical focus | Core Web Vitals, sitemap | JSON-LD Schema, FAQPage, Article |
| Keyword stuffing | Can be penalized | Ineffective (AI uses semantic understanding) |

## What AI Search Engines Prioritize

| Factor | Impact | How to Implement |
|--------|--------|-----------------|
| **Structured data (JSON-LD)** | Highest | FAQPage, Article, WebSite, HowTo, Product schemas |
| **Natural language Q&A** | High | FAQ section with question-form H2/H3 headings |
| **Data & statistics** | 30-100%+ visibility lift | Numbers, comparison tables, cost calculations |
| **Authority signals** | High | Citations, data sources, expert attribution |
| **Freshness** | High weight for last 3 months | Regular content updates |
| **Clear content hierarchy** | Medium | H1→H2→H3, lists, tables, bold key points |
| **Multi-modal** | Increasing | Image ALT text, video captions, Schema markup |

## Step 1: Diagnostic — Test Current AI Visibility

Before optimizing, ask the key questions in 3-5 AI search platforms:
- "AI写作工具推荐"
- "2026年买什么手机好"
- "15万SUV推荐"
- (domain-specific questions)

Record: Is your site cited? Position in results? Which sources are cited instead?

## Step 2: Add Structured Data (JSON-LD)

### FAQPage Schema (Highest ROI for GEO)

Insert a `<script type="application/ld+json">` block in `<head>`:

```json
{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {
      "@type": "Question",
      "name": "2026年买什么手机性价比最高？",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "不同预算推荐不同：1500元以内Redmi Note 14 Pro..."
      }
    }
  ]
}
```

**Critical rules:**
- Questions MUST be in **natural language** — exactly what a user would type into AI search
- Answers MUST contain **data, numbers, and specific recommendations** (not vague opinions)
- Match each FAQ question to a real article or data point on your site
- Limit to 8-12 questions per FAQPage (covers key queries without bloat)

### WebSite Schema

```json
{
  "@context": "https://schema.org",
  "@type": "WebSite",
  "name": "Site Name",
  "url": "https://example.com",
  "inLanguage": "zh-CN",
  "potentialAction": {
    "@type": "SearchAction",
    "target": "https://example.com/search?q={search_term_string}",
    "query-input": "required name=search_term_string"
  }
}
```

### Other Schema Types
- **Article** — for each content page (headline, date, author, description)
- **BreadcrumbList** — for navigation hierarchy
- **ItemList** — for category pages and rankings

## Step 3: Expand FAQ Section on the Page

The on-page FAQ section should mirror (but not duplicate) the JSON-LD:

```
<div class="faq-item">
  <div class="faq-q" onclick="toggleFaq(this)">
    <span>2026年买什么手机性价比最高？</span>
    <span class="faq-icon">+</span>
  </div>
  <div class="faq-a">不同预算推荐不同：1500元以内Redmi Note 14 Pro...</div>
</div>
```

**Question design rules (GEO-focused):**
- Each question = a **real query** people type into AI search
- Cover the **top 8-12 questions** across your content domains
- Questions should span **multiple categories** to increase surface area
- Each answer should reference a **specific article** or section on your site

## Step 4: Add Meta Tags for Social + AI Sharing

```
<meta name="description" content="...">
<meta property="og:title" content="...">
<meta property="og:description" content="...">
<meta property="og:type" content="website">
<meta property="og:locale" content="zh_CN">
<meta property="og:url" content="https://example.com">
<meta name="twitter:card" content="summary_large_image">
```

## Step 5: Content Structure for AI Readability

Each article should follow this structure (GEO-optimized):

1. **H1**: Keyword-rich title with year/scope (e.g., "2026年15-20万SUV终极选购指南")
2. **First 100 words**: Direct answer to the core question
3. **H2 sections**: Use natural question format ("为什么重要", "怎么选", "优缺点对比")
4. **Data tables**: Comparison tables with numbers, prices, ratings
5. **Lists**: Bullet-point pros/cons, numbered steps
6. **FAQ bottom**: 3-5 article-specific questions
7. **避坑指南/Conclusion**: Data-backed summary

## Pitfalls

- ❌ **Keyword stuffing** — AI uses semantic understanding, not word frequency. Natural language beats keyword density.
- ❌ **Vague content without data** — "不错" "很好" are meaningless to AI. Always pair claims with numbers.
- ❌ **Inaccessible format** — If content is behind login, paywall, or JS-rendered without server-side fallback, AI can't index it.
- ❌ **FAQ questions that don't match real user queries** — "我们的产品怎么样" is not a search query. Use actual user questions.
- ❌ **JSON-LD that doesn't match on-page content** — Schema mismatch confuses both AI and search engines. Keep them aligned.
- ❌ **SPA hash routing** (`/#/article`) — AI crawlers can't index hash fragments. Use History API (`/article/slug`) for all content URLs.
- ❌ **Duplicate or thin content** — AI penalizes low-effort content in citations. Each page needs unique value.

## Verification

After GEO implementation:
1. ✅ JSON-LD validates at schema.org/validator or Google Rich Results Test
2. ✅ FAQ questions are real natural-language queries (not "FAQ 1", "FAQ 2")
3. ✅ Each FAQ answer contains data, numbers, or specific recommendations
4. ✅ Page renders without JS errors — AI crawlers may not execute JS
5. ✅ Sitemap submitted to search engines
6. ✅ Content has proper H1→H2→H3 hierarchy

## Chinese AI Search Platform Differences

| Platform | Parent Company | Content Preference | Source Characteristics |
|----------|---------------|-------------------|----------------------|
| 豆包 | ByteDance | Douyin/Toutiao ecosystem | Prefers short-video platform content |
| DeepSeek | DeepSeek | Tech communities, GitHub | Logic-focused, prefers structured data |
| 元宝 | Tencent | WeChat Official Accounts | WeChat ecosystem high weight |
| Kimi | Moonshot AI | Long articles, papers, web | Supports ultra-long context, academic |
| 通义千问 | Alibaba | E-commerce, business content | Prefers Ali ecosystem data |

### Template Literal Pitfalls (Chinese SPAs)

When embedding GEO FAQ content in JavaScript template literals (common in SPAs):

**Unescaped backticks in article `b:` fields:**
```javascript
// ❌ Wrong — inner backticks close the template literal
b: `## 提示\n例如：`a cat...`\n`

// ✅ Correct — escape inner backticks
b: `## 提示\n例如：\`a cat...\`\n`
```

**`split('\\n')` vs `split('\n')` in template literal data:**
```javascript
// ❌ Bug — looking for literal \\n but data has real newlines
var ls = c.split('\\\\n');

// ✅ Fix — match real newlines
var ls = c.split('\\n');
```

**Shell `$` interpretation:** When using `python -c "..."` in terminal, double-quoted `$` is interpreted as variable reference. Use single quotes or escape `\$`.

## Related

See `references/geo-website-implementation.md` for a real-world example of applying GEO to a static SPA review site (AI TopList / test-toplist.com).
