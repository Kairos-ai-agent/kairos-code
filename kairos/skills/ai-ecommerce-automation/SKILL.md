---
name: "ai-ecommerce-automation"
description: "AI Agent驱动的跨境电商自动化。覆盖平台选型（TikTok Shop/Shopee/Shopify）、一件代发供应商对接（CJ Dropshipping/1688）、Agent自动化架构设计、API集成、选品/上架/履约/客服全流程。当用户讨论跨境电商、dropshipping、一件代发、AI电商运营、平台开店时使用。"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/productivity/ai-ecommerce-automation/SKILL.md"
---
# AI Agent 跨境电商自动化

用AI Agent自动化跨境电商全流程：选品→上架→营销→履约→客服。

## 核心架构

```
┌─────────────────────────────────────┐
│        Orchestrator Agent            │ ← LLM决策中心
│    (DeepSeek/GPT + Memory + Plan)   │
└──────┬──────────┬──────────┬────────┘
       │          │          │
  ┌────▼────┐ ┌──▼─────┐ ┌─▼──────────┐
  │选品Agent│ │运营Agent│ │履约Agent   │
  └────┬────┘ └──┬─────┘ └─┬──────────┘
       │         │          │
  ┌────▼─────────▼──────────▼──────────┐
  │          Tool Layer                 │
  │ • 平台API (TikTok/Shopify)         │
  │ • 供应商API (CJ Dropshipping)      │
  │ • 浏览器自动化 (Playwright)        │
  │ • 支付 (Stripe/PayPal)             │
  └─────────────────────────────────────┘
```

## 平台选型决策

| 平台 | 月费 | 佣金 | API友好度 | 适合场景 |
|------|------|------|----------|---------|
| **TikTok Shop** | ¥0 | 2-8% | ⭐⭐⭐⭐ | 短视频带货、社交电商 |
| **Shopee** | ¥0 | 2-6% | ⭐⭐⭐ | 东南亚市场 |
| **Shopify** | $39/月 | 2.9%+$0.3 | ⭐⭐⭐⭐⭐ | 品牌独立站 |

**零成本首选**: TikTok Shop（免月费、免开店费、仅成交扣佣）

## 供应商平台

| 平台 | 特点 | API |
|------|------|-----|
| **CJ Dropshipping** | 全球直发、支持贴牌、完整REST API | ✅ 免费 |
| **1688** | 价格最低、SKU最多 | ⚠️ 需爬虫 |
| **DSers** | Shopify官方插件、1688集成 | ✅ 免费版 |

## 用户偏好

- **零投入启动**: 用户明确要求不需要前期资金投入的方案
- **一件代发优先**: 对接dropshipping供应商降低操作复杂度
- **Agent自动化**: 所有环节尽量由Agent自动完成

## 关键踩坑

- **CJ API Key 获取**: 新版CJ网站改版后，My CJ后台找不到"授权"菜单。正确路径: 访问 `developers.cjdropshipping.com` → 左侧菜单「获取token」→ 看到 API Key 格式 `CJxxxxx@api@xxxxx`。不要通过 cjdropshipping.com 主站找API Key。
- **CJ API 认证流程**: 旧文档说用 email+password 获取 access token 是错的。正确方式: POST `/v1/authentication/getAccessToken` body 仅需 `{"apiKey": "CJxxxxx@api@xxxxx"}`，返回 accessToken（15天有效）+ refreshToken（180天有效）。API 域名用 `developers.cjdropshipping.com`，不是 `.cn` 域名。
- **TikTok Shop 注册类型选择**: 中国主体选「注册商家自运营 POP（中国主体）」，不是全托管也不是品牌托管。
- **CJ店铺授权选择**: 注册CJ时选「TikTok Shop Non-US」对应东南亚跨境店。
- **营业执照经营范围**: 科技/电子类执照可直接卖电子产品；卖服装/日用品需先变更经营范围。

## 参考文件

- `references/tiktok-shop-cj-dropshipping.md` — TikTok Shop入驻条件、CJ API接口文档、对接架构、成本分析
- `references/tiktok-shop-registration-walkthrough.md` — 实际注册流程截图级指引、CJ API Key获取的正确路径
