---
name: "autoglm-stock-analysis"
description: "股票分析技能（AutoGLM 版）。当用户提到任何股票、证券、基金、市场行情分析需求时必须使用此技能，包括：分析某只股票、研究公司基本面、查财报数据、评估买入卖出时机、对比多只股票、分析行业板块、解读财经新闻对股价影响等。只要涉及股票投资研究、行情解读、估值判断或综合分析，都应触发此技能。获取股票信息时，可同时组合使用 `websearch`、`open-link`、`yfinance` 三种方式"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/autoglm-stock-analysis/SKILL.md"
---
# autoglm-stock-analysis

利用 `websearch.py`、`open-link.py` 和 `yfinance` 三种方式协同完成股票信息采集、网页内容提取与实时行情校验，输出结构化研究报告。凡是股票 K 线图、K 线图、周 K、日 K、月 K 等图表分析，均不在本 skill 内处理，一律交给 `autoglm-image-recognition`。

---

## 工具调用方式

### 1. 搜索 — `websearch.py`

```bash
python websearch.py "搜索词"
```

返回 JSON，关键字段：`result[].title` / `result[].snippet` / `result[].url`

**示例：**
```bash
python websearch.py "贵州茅台 2024年报 业绩"
python websearch.py "AAPL 最新财报 营收"
python websearch.py "新能源汽车板块 今日行情"
```

---

### 2. 打开网页 — `open-link.py`

```bash
python open-link.py "https://example.com"
```

返回 JSON，关键字段：`content` 或 `text`（页面正文）

**推荐数据源：**

| 场景 | URL 示例 |
|------|---------|
| A股行情（东方财富-沪市） | `https://quote.eastmoney.com/sh600519.html` |
| A股行情（东方财富-深市） | `https://quote.eastmoney.com/sz000001.html` |
| 美股行情（东方财富） | `https://quote.eastmoney.com/us/TSLA.html` |
| 港股行情（东方财富） | `https://quote.eastmoney.com/hk/00700.html` |
| 雪球股票页 | `https://xueqiu.com/S/SH600519` |
| 港股（雪球） | `https://xueqiu.com/S/00700` |
| 美股（Yahoo Finance） | `https://finance.yahoo.com/quote/AAPL` |

> A股代码规则：沪市 `SH` 前缀，深市 `SZ` 前缀，如 `SH600519`（茅台）

---

### 3. 实时行情 — `yfinance`

当需要获取实时股价、涨跌幅、成交量、52 周区间、市值等基础行情数据时，可直接使用 `yfinance` Python SDK。

```bash
python -c "import yfinance as yf; t = yf.Ticker('AAPL'); print(t.fast_info)"
```

**常见场景：**
- 美股、港股的实时或近实时价格查询
- 补充报告中的当前价格、成交量、日内区间、52 周区间等字段
- 快速校验搜索结果中的行情数据

**示例：**
```bash
python -c "import yfinance as yf; t = yf.Ticker('600519.SS'); print({'symbol': '600519.SS', 'lastPrice': t.fast_info.get('lastPrice'), 'dayHigh': t.fast_info.get('dayHigh'), 'dayLow': t.fast_info.get('dayLow')})"
python -c "import yfinance as yf; t = yf.Ticker('000001.SZ'); print({'symbol': '000001.SZ', 'lastPrice': t.fast_info.get('lastPrice'), 'lastVolume': t.fast_info.get('lastVolume')})"
python -c "import yfinance as yf; t = yf.Ticker('AAPL'); print({'lastPrice': t.fast_info.get('lastPrice'), 'dayHigh': t.fast_info.get('dayHigh'), 'dayLow': t.fast_info.get('dayLow'), 'lastVolume': t.fast_info.get('lastVolume')})"
python -c "import yfinance as yf; t = yf.Ticker('TSLA'); print({'symbol': 'TSLA', 'marketCap': t.info.get('marketCap'), 'currency': t.info.get('currency')})"
python -c "import yfinance as yf; t = yf.Ticker('0700.HK'); print(t.fast_info)"
python -c "import yfinance as yf; t = yf.Ticker('9988.HK'); print({'symbol': '9988.HK', 'lastPrice': t.fast_info.get('lastPrice'), 'fiftyTwoWeekHigh': t.fast_info.get('fiftyTwoWeekHigh'), 'fiftyTwoWeekLow': t.fast_info.get('fiftyTwoWeekLow')})"
```

> 代码格式示例：
> A股上交所通常用 `.SS`，如 `600519.SS`
> A股深交所通常用 `.SZ`，如 `000001.SZ`
> 美股直接使用代码，如 `AAPL`、`TSLA`
> 港股通常用 `.HK`，如 `0700.HK`、`9988.HK`

---

## 分析流程

### 第一步：判断分析类型

默认原则：可以同时使用 `websearch`、`open-link`、`yfinance` 三种方式获取用户要求的股票信息。优先用 `websearch` 找入口和最新线索，用 `open-link` 读取关键页面正文，用 `yfinance` 获取或校验实时行情数据；必要时交叉验证后再输出结论。如果 `yfinance` 所需 ticker 不明确，先搜索并确认代码，再继续执行行情抓取和分析。

职责边界：如果用户需求涉及股票 K 线图、K 线图、周 K、日 K、月 K 或其他股票图表识别与描述，不在本 skill 内处理，直接交给 `autoglm-image-recognition`。

| 用户意图 | 执行策略 |
|---------|---------|
| 快速行情查询 | `yfinance` 获取实时股价 + `websearch` 补充背景，必要时 `open-link` 读取行情页正文 |
| 基本面分析 | `websearch` 搜索财报与新闻 + `open-link` 读取财报页正文 + `yfinance` 补充价格和市值等行情 |
| 技术面分析 | `websearch` 搜索行情解读 + `open-link` 读取行情页正文 + `yfinance` 补充近期价格数据 |
| 综合研究报告 | 组合使用三种方式，分别覆盖实时行情、正文信息、新闻动态 |
| 多股对比 | 对每只股票分别结合 `yfinance`、`websearch`、`open-link` 获取数据后制作对比表 |

---

### 第二步：信息采集

**建议组合方式（默认优先采用）：**
```bash
python -c "import yfinance as yf; t = yf.Ticker('AAPL'); print(t.fast_info)"
python websearch.py "AAPL 最新财报 营收 近期新闻"
python open-link.py "https://finance.yahoo.com/quote/AAPL"
```

**ticker 缺失时的补救流程：**
```bash
python websearch.py "[公司名/股票名] 股票代码 ticker Yahoo Finance"
python websearch.py "[公司名/股票名] 东方财富 雪球 Yahoo Finance 代码"
python open-link.py "[从搜索结果中选出的权威行情页或资料页 URL]"
python -c "import yfinance as yf; t = yf.Ticker('[确认后的ticker]'); print(t.fast_info)"
```

执行要求：
- 如果用户只给公司名、品牌名、中文简称，先补齐可用于 `yfinance` 的 ticker，再继续后续分析。
- 优先从 Yahoo Finance、雪球、东方财富等页面确认代码与交易所后缀是否一致。
- A 股注意区分 `.SS` 与 `.SZ`，港股注意补全 `.HK`，美股通常直接使用原始 ticker。
- 若搜索结果出现多个可能代码，必须先用 `open-link` 打开 1~2 个权威页面核对公司名称、交易所和代码，再决定使用哪个 ticker。

**基础搜索：**
```bash
python websearch.py "[股票名] 今日股价 行情"
python websearch.py "[股票名] 最新财报 业绩 营收"
python websearch.py "[股票名] 近期新闻 利好利空"
```

**实时行情：**
```bash
python -c "import yfinance as yf; t = yf.Ticker('AAPL'); print(t.fast_info)"
```

用户提到实时股价、最新价格、盘中行情时，优先执行 `yfinance`；但仍可同时结合 `websearch.py` 和 `open-link.py` 补充相关新闻、公告、行情页正文和市场背景。

**正文与深度数据：**
```bash
python open-link.py "https://xueqiu.com/S/SH600519"
python open-link.py "https://finance.yahoo.com/quote/AAPL"
python open-link.py "https://finance.yahoo.com/quote/AAPL/financials"
python websearch.py "[股票名] 分析师评级 目标价 2024"
```

### 第三步：输出报告

```markdown
## [股票名称]（[代码]）分析报告
> 数据时间：[当前日期]

### 📊 基本信息
- **当前价格**：XX 元 | 涨跌幅：+X.XX%
- **市值**：XXX 亿 | **PE**：XX | **PB**：XX
- **所属行业**：XX | **概念板块**：XX

### 🏢 基本面分析
- **核心业务**：...
- **最新财务**：营收 XX 亿（同比 +XX%）/ 净利润 XX 亿（同比 +XX%）/ 毛利率 XX%
- **竞争优势**：...
- **近期事件**：...

### 📈 技术面分析
- **趋势判断**：上升 / 下降 / 震荡
- **关键价位**：支撑位 XX 元 / 压力位 XX 元
- **均线状态**：多头排列 / 空头排列 / 纠缠
- **量价关系**：...
- **指标信号**：MACD ... / RSI ... / KDJ ...

### 📰 市场情绪
- 最新新闻：...
- 机构观点：...

### ⚖️ 风险提示
1. ...
2. ...

### 💡 综合评估
| 维度 | 评分 | 说明 |
|------|------|------|
| 基本面 | ⭐⭐⭐⭐ | ... |
| 技术面 | ⭐⭐⭐ | ... |
| 市场情绪 | ⭐⭐⭐ | ... |

**适合投资者类型**：价值型 / 成长型 / 短线

---
⚠️ 免责声明：本分析基于公开信息整理，仅供参考，不构成投资建议。股市有风险，投资需谨慎。
```

---

## 特殊场景

### 场景A：对比多只股票
```bash
python websearch.py "贵州茅台 PE PB ROE 2024"
python websearch.py "五粮液 PE PB ROE 2024"
```
输出对比表格，维度：估值（PE/PB）、成长性（营收/利润增速）、盈利质量（ROE/毛利率）

### 场景B：行业板块分析
```bash
python websearch.py "[行业名] 板块 景气度 2024"
python websearch.py "[行业名] 龙头股 排名"
```
判断行业景气周期位置，列出 3~5 只代表标的简要对比

### 场景C：快速问答
仅 1 次 websearch，给出 3~5 句核心判断 + 免责声明，无需完整报告格式。

---

## 注意事项

1. **脚本路径**：所有脚本与 SKILL.md 同级，直接 `python websearch.py` 调用
2. **JSON 解析**：脚本输出为 JSON，提取 `result`、`content`、`text` 字段使用
3. **组合使用**：可以同时使用 `websearch`、`open-link`、`yfinance` 三种方式获取和交叉校验股票信息，不必只选一种
4. **实时行情**：如果用户明确提到实时股价、最新价格、盘中行情等需求，优先使用 `yfinance` 获取数据，再结合 `websearch` 和 `open-link` 补充信息
5. **ticker 兜底**：如果缺少 `yfinance` 所需股票代码、交易所后缀或 ticker，先执行 `websearch` 搜索代码，再用 `open-link` 打开权威页面确认，确认后继续 `yfinance`
6. **K线图分析**：如果用户需求是股票 K 线图、K 线图、周 K、日 K、月 K 等图表分析，一律交给 `autoglm-image-recognition` 完成
7. **搜索时效**：搜索词加上年份（如"2024"）确保结果最新
8. **open-link 失败**：若返回空，改用 websearch 搜索该页面摘要
9. **免责声明**：每次报告末尾必须包含投资风险声明
