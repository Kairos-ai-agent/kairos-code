---
name: "fund-manager"
description: "基金日常管理：基金实时估值、行业涨幅、集合竞价数据整理、涨跌幅预警、持仓增删。当用户提及以下内容时触发：基金收益、基金日报、实时估值、盘中估值、更新基金、基金净值、行业涨幅、行业表现、集合竞价、竞价数据、添加持仓、删除持仓、基金预警、涨跌幅提醒、复盘总结。也适用于：帮我看看基金、今天基金怎么样、更新一下收益、行业表现如何、早盘数据、今天复盘一下。"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\fund-manager\\SKILL.md"
---
# 基金日常管理

管理用户的基金持仓，提供实时估值、行业涨幅、集合竞价整理和预警功能。所有输出同时提供 xlsx + png 图片两个版本，保存到 `/workspace/output/`。

## 持仓信息

读取 `references/portfolio.md` 获取当前持仓列表。修改持仓时同步更新该文件和所有相关脚本。

## 核心功能

### 1. 基金实时估值

**盘中实时估值**（交易时段使用）：
- 数据源：天天基金API，详见 `references/datasources.md`
- 对每只基金获取 gszzl（估值涨幅%），计算 当日收益 = 持仓金额 × 涨幅%
- 按涨幅从高到低排序

**晚间确认净值**（收盘后使用）：
- 运行 `scripts/update_fund_nav.py`，使用 akshare 获取基金公司最终净值
- 生成 xlsx 报表

**图片生成**：
- 运行 `scripts/gen_fund_image.py` 生成支付宝APP风格卡片图
- 风格：白色卡片、顶部总资产+总收益、三列（当日涨幅/当日收益/持有金额）、红涨绿跌
- 注意：gen_fund_image.py 中的 funds 列表为静态数据，盘中更新时需用实时数据动态生成

**定时任务设置**：
```
盘中估值：11:28, 13:58, 14:28, 14:58（天天基金实时估值）
晚间净值：19:58, 20:58（akshare基金公司净值）
```

### 2. 行业涨幅（复盘总结时自动输出）

当用户说"复盘总结"时，自动生成行业涨幅报表。

运行 `scripts/update_industry_heatmap.py` 获取申万一级31个行业数据。

**列**：当日涨幅、近1月、近3月、近6月、今年以来、近7年、成立以来、PE(TTM)、行业温度

**行业温度**：多周期动量评分（0-100），基于各周期涨跌方向加权。

**xlsx样式**：涨幅列用红涨绿跌色阶（`references/datasources.md` 中的 heat_color 算法），PE列按估值高低用暖色标注，行业温度用红/黄/绿三档。行业温度列用纯数值显示，不加百分号。

转图片：`python scripts/xlsx_to_image.py input.xlsx output.png`

**定时**：每晚 20:28

### 3. 集合竞价数据

用户提供早盘截图时，提取数据生成 xlsx：
- 列：序号、代码、名称、最新价、涨幅%、涨跌、DDE决策、总量、现量、买入价、卖出价、涨速%
- E列（涨幅%）和F列（涨跌）用红涨绿跌色阶，其余列白底黑字无底色
- 标题蓝底白字表头

### 4. 涨跌幅预警

运行 `scripts/fund_alert.py` 检测是否有基金估值涨跌幅超过±3%。

**定时**：工作日 9:00-14:59 每15分钟（`*/15 9-14 * * 1-5`）。有触发时提醒用户，无触发时静默。

### 5. 添加/删除持仓

**添加**：
1. 通过 akshare 获取当日净值作为买入价
2. 更新 `scripts/update_fund_nav.py`、`scripts/fund_alert.py`、`references/portfolio.md`
3. 重新生成实时估值

**删除**：
1. 从上述文件中移除
2. 重新生成日报

## 输出规范

- 所有报表同时输出 xlsx 和 png，用 show_file 展示两个版本
- 中国股市惯例：红涨绿跌
- 图片使用 NotoSansCJK 中文字体
- xlsx 使用 openpyxl，图片使用 PIL/Pillow
- 依赖：akshare、openpyxl、Pillow（均需 pip install）
