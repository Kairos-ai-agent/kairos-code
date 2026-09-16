---
name: "stock-research-toolkit"
description: "Stock analysis toolkit. Use when picking, screening, or scoring stocks."
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\stock-research-toolkit\\SKILL.md"
---
# Stock Research Toolkit

Curated reference gathered 2026-08-30 for the user's stock-study project.
Updated as tools/frameworks prove useful.

## When to use this skill

- User asks to find, screen, recommend, or score stocks.
- User asks which GitHub tools exist for stock analysis.
- User asks about value-investing, moat, margin-of-safety frameworks.
- User wants to set up daily stock reports or portfolio tracking.
- User mentions A股 / 港股 / 美股 analysis, PE / ROE / moat / DCF.

## Tool Landscape (GitHub)

| Tier | Repo | Stars | Purpose |
|------|------|-------|---------|
| Daily push | ZhuLinsen/daily_stock_analysis | 49k★ | Multi-market (A/HK/US) AI daily push via GitHub Actions + Telegram/Discord |
| Single-stock deep | siddharthprakash1/Enhanced-Stock-Analysis-Tool | 6k★ | LangGraph multi-agent equity research w/ fact-check + PDF |
| Multi-factor | weirdapps/etorotrade | — | Analyst consensus + fundamental → BUY/SELL/HOLD signal |
| Multi-factor | naumankazi/Stock-Analyser | — | Composite score + moat + risk-weighted allocation |
| Multi-factor | yennj12 InvestSkill stock-screener | — | 7-axis score: quality/moat/growth/value/momentum/insider/risk |
| Data layer | OpenBB-finance/OpenBB | 63k★ | Normalized multi-provider data API (Python/Excel/AI) |
| Data layer | yfinance | — | Free Yahoo Finance OHLCV + fundamentals (lightest) |
| Portfolio | jonaswitt/portfolio-analyzer | — | Rebalance suggestions vs target weights |

## Value Investing Framework (Graham / Buffett / Klarman)

**When to buy = price << intrinsic value *and* business quality is high.**

Eight quality/value axes user must apply (each 0-10, weighted average = composite score):

1. **Quality of earnings** — Net income / revenue stable, accruals low, audit clean
2. **Moat (durable competitive advantage)** — Pick ≥1: brand / network effect / switching cost / cost advantage / regulatory license
3. **Margin of safety** — Buy at meaningful discount (15-40%) to conservative intrinsic value
4. **Margin profile** — Gross ≥40%, Operating ≥15%, ROIC ≥15%
5. **Balance sheet strength** — Debt/Equity ≤0.5, Current ratio ≥1.5, FCF positive
6. **Growth** — Revenue/earnings CAGR ≥5%/yr over 5y, organic not acquisition-driven
7. **Management** — Insider ownership, buybacks when cheap, capital allocation track record
8. **Reasonable valuation** — PE ≤20 (≤15 preferred), PB reasonable vs ROE (Graham number = √(22.5 × EPS × BVPS)), PEG ≤1.5

**Red flags / value traps** — declining ROIC, rising debt, working-capital deterioration, accounting changes, dividend cut, goodwill write-downs.

## Multi-Factor Scoring (quant approach)

Composite = w_value·value + w_quality·quality + w_momentum·momentum + w_growth·growth + w_safety·safety
where each axis is a z-score (or 0-100 percentile rank) within the screened universe.

Recommended weights (sensible default, user can override):
- value 0.25 (PE, PB, EV/EBITDA, FCF yield)
- quality 0.30 (ROE, ROIC, gross margin, accruals)
- momentum 0.20 (6m, 12m price return, relative strength vs sector)
- growth 0.15 (revenue CAGR, EPS CAGR)
- safety 0.10 (debt/equity, interest coverage, beta)

Output: ranked ticker list with sub-scores + composite + recommendation
(BUY ≥ 70 / HOLD 50-70 / SELL < 50).

## Data Sources by Market

| Market | Free API | Notes |
|--------|----------|-------|
| US | yfinance, OpenBB-yfinance, SEC EDGAR | Easiest |
| HK | yfinance (.HK ticker), AkShare | yfinance partial coverage |
| A股 | AkShare, Tushare (free tier), Baostock | Tushare needs token (free), AkShare no token |

## Python Recipes (proven)

### Pull price + fundamentals for one ticker (yfinance)
```python
import yfinance as yf
t = yf.Ticker("AAPL")
info = t.info              # dict of ~120 fundamentals
hist = t.history(period="2y")  # OHLCV dataframe
```

### Screen watchlist (yfinance)
```python
import yfinance as yf, pandas as pd
tickers = ["AAPL","MSFT","GOOGL","NVDA","BRK-B","JPM","V","UNH","XOM","TSLA"]
def metrics(sym):
    try:
        i = yf.Ticker(sym).info
        return {
            "ticker": sym,
            "pe":   i.get("trailingPE"),
            "pb":   i.get("priceToBook"),
            "roe":  i.get("returnOnEquity"),
            "rev_growth": i.get("revenueGrowth"),
            "de":   i.get("debtToEquity"),
            "fcf":  i.get("freeCashflow"),
            "mcap": i.get("marketCap"),
        }
    except Exception as e: return {"ticker": sym, "error": str(e)}
df = pd.DataFrame(metrics(s) for s in tickers)
```

### Pitfalls
- **yfinance rate limits** — pause ~1s between tickers; max ~100 per session.
- **Trailing PE meaningless when earnings = 0** — fall back to forward PE or skip.
- **ROE inflated by high debt** — always read alongside Debt/Equity (DuPont).
- **One-year momentum reversal** — 6-12 month window captures trend better.
- **Survivorship bias** — backtests over today's ticker list overstate returns. Use point-in-time data for real research.
- **Small-cap liquidity** — wide bid-ask can inflate apparent discount. Position size accordingly.
