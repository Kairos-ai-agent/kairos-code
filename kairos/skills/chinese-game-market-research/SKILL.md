---
name: "chinese-game-market-research"
description: "Research the Chinese mobile game market — WeChat mini-games, mobile games, mini-programs, top products by category, top publishers, MAU data, IAP/IAA splits. Use when the user asks about Chinese gamin"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/research/chinese-game-market-research/SKILL.md"
---
# Chinese Game Market Research

## When to use

Load this skill when the user asks:

- "微信小游戏 top20 / 头部产品 / 头部品类"
- "Chinese mobile game market categories / MAU / trends"
- "WeChat mini-games vs 抖音小游戏 comparison"
- "Top game publishers in China"
- "SLG / 塔防 / 模拟经营 / 卡牌 / RPG 小游戏 [genre] ranking"
- Any question about the **Chinese** gaming market specifically (海外/海外市场 questions → use general `market-research` instead).

## Critical data sources (authoritative)

| Source | What it gives you | URL |
|--------|-------------------|-----|
| **QuestMobile** | MAU panel, user demographics, usage frequency | questmobile.com.cn (twice-yearly industry reports) |
| **DataEye-ADX + 引力引擎** | Monthly WeChat mini-game **畅销榜** (IAP Top100) + **畅玩榜** (IAA Top100) — joint ranking, the industry standard | ADX mini-game dashboard; published as monthly articles on news.qq.com / 36kr / DataEye public account |
| **GameLook** (gamelook.com.cn) | Analyst commentary + monthly Top 10 lists for both WeChat & 抖音 mini-games | gamelook.com.cn |
| **微信公开课 / 微信游戏官方** | MAU announcements at developer conferences (annual) — e.g. "微信小游戏 MAU 6.54亿" | Official WeChat Game public account |
| **阿拉丁指数** (Aladdin Index) | Older, broader mini-program panel — less current for games specifically | aldwx.com |

**Pitfall:** 阿拉丁指数 shows up a lot in search results but it's old data and less reliable for games. **Trust DataEye-ADX + 引力引擎 first** for current rankings; QuestMobile for platform-level MAU.

## Key terminology you MUST distinguish

| Term | Meaning | Why it matters |
|------|---------|----------------|
| **IAP** (应用内购买) | In-app purchase — players pay real money | Drives **畅销榜** (sales chart) |
| **IAA** (应用内广告) | In-app advertising — players watch ads for rewards | Drives **畅玩榜** (play chart) |
| **MAU** | Monthly active users | Platform-level metric |
| **混合变现** | Hybrid IAP + IAA | Most successful new games use this; pure-IAA mini-games peaked in 2023 |

A product's rank differs between the two charts. When reporting "top 20", ALWAYS clarify which chart and what time window.

## Output structure (use this template)

When answering "top X games" or "top categories":

```
## 一、最新头部产品 (按近 N 个月 X榜 Top 20 去重合并)
[Ranked table: 排位 / 游戏名 / 类型 / 厂商]

## 二、Top X 涉及的类型 (合并去重后共 N 大品类)
[Numbered list with sub-bullets for代表产品]

## 三、品类分布占比 (基于 Top 100 数据)
[Percentages or counts per category]

## 四、关键趋势总结 (2026 H1)
[3-5 bullet trends]

## 五、头部厂商 (按入榜款数)
[Ranked publisher list]
```

Always cite the source (e.g. "DataEye-ADX 5月月榜", "QuestMobile 2026上半年报告"). User wants **specific products with MAU/revenue numbers**, not abstract category descriptions.

## Verification checklist

Before delivering, verify each claim has a source:

1. ✓ Product names — exact spelling (向僵尸开炮, not 向僵尸开跑)
2. ✓ Publisher — confirm with cross-reference (大梦龙途 owns 向僵尸开炮, 三七互娱 owns 寻道大千)
3. ✓ Rank number — note the month and chart type
4. ✓ MAU/revenue — must come from QuestMobile or official WeChat announcements
5. ✓ Category classification — be consistent: SLG / 塔防 / 模拟经营 / 卡牌 / RPG / 休闲益智 / MMO / 棋牌

## Anti-patterns

- ❌ **Don't rank without specifying chart type** ("Top 1 game" is meaningless without 畅销榜 vs 畅玩榜 vs MAU)
- ❌ **Don't use 阿拉丁指数 for current WeChat mini-game rankings** — it's outdated and unreliable for games
- ❌ **Don't lump WeChat + 抖音 + QQ mini-games together** — they're different platforms with different #1 games (e.g. 抖音 #1 is 棋牌, WeChat #1 is SLG/塔防)
- ❌ **Don't claim MAU numbers older than 6 months as "current"** — query QuestMobile or WeChat official for the latest
- ❌ **Don't recommend building in this market without first reading the IAP/IAA split** — pure IAA peaked; hybrid is the norm now

## Category taxonomy (2026 H1 verified)

| Category | Top genre representatives | ARPU |
|----------|---------------------------|------|
| **SLG / 策略** | 三国：冰河时代、无尽冬日、奔奔王国 | 最高 |
| **塔防** | 向僵尸开炮、永远的蔚蓝星球、城主别慌张 | 中高 |
| **模拟经营** | QQ经典农场、疯狂水世界、我的花园世界 | 中 |
| **RPG / 卡牌** | 神器传说、寻道大千、英雄没有闪 | 中 |
| **休闲动作 / 益智** | 跃动小子、灵画师 (IAA: 羊了个羊：星球、抓大鹅) | 中低 |
| **MMO / 传奇** | 工具人传奇 (大退潮中，5月从14款跌至3款) | 中 |
| **棋牌** | 腾讯欢乐斗地主、微乐四川麻将 | 低但长尾长青 |
| **战棋** | 占城大师 (2026 新崛起) | 中 |

**Key trend (2026 H1):** 用户被教育成"准APP玩家" — 头部产品时长/付费都在向中重度迁移，SLG + 塔防 + 模拟经营是当前三大统治品类。

## Top publishers (2026 H1, by Top100 入榜款数)

| Rank | Publisher | In Top100 | Dominant category |
|------|-----------|-----------|-------------------|
| 1 | 大梦龙途 | 5 款 | 塔防之王 |
| 2 | 三七互娱 | 4 款 | RPG/放置 |
| 3 | 腾讯游戏 | 3 款 | 棋牌+农场IP |
| 4 | 在线途游 | 3 款 | SLG |
| 5 | 波克城市 | 2 款 | 模拟经营 |
| 6 | 点点互动 | 2 款 | SLG |
| 7 | 益世界 | 1+ 款 | 模拟经营 |

## User profile pattern

Per user memory:
- 已放弃 AI 生图 SaaS，转向海外 freelance (Upwork)
- 视觉敏感, 但此 skill 不涉及设计
- 喜欢具体数据 + 多方案对比，不要泛泛而谈
- 默认要把所有方案做出独立可交付版本

**When delivering research to this user:**
- Always include specific product names + MAU/ranks, not just category percentages
- End with offer to deep-dive one category (e.g. "要不要展开塔防/SLG/模拟经营？")

## Reference files

- `references/wechat-minigame-2026H1-snapshot.md` — Verified 2026 H1 snapshot with Top 20 products, category distribution, top publishers
- `references/dataeye-monthly-reports-index.md` — Index of recent DataEye + 引力引擎 monthly reports (1月/4月/5月/6月/8月)

See those files for raw session data.