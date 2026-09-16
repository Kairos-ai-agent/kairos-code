---
name: "pdf-invoice-parser"
description: "Extract structured data from Chinese tax invoices (增值税发票) PDFs using pdfplumber + regex. No external OCR API needed. Use when processing Chinese invoices, extracting invoice fields from PDF, or building invoice automation."
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/data-science/pdf-invoice-parser/SKILL.md"
---
# Chinese Invoice PDF Parser

Extract structured data from Chinese tax invoices (增值税专用发票/普通发票) using `pdfplumber` for text extraction and regex for field parsing. No external API required.

## Prerequisites

```bash
pip install pdfplumber requests
# For image invoices, set up vision API credentials:
#   XIAOMI_API_KEY=***   XIAOMI_API_BASE=https://api.xiaomimimo.com  # default if unset
```

## Invoice Source Types

### 1. PDF invoices → pdfplumber text extraction

Works with standard 电子发票 PDFs. Text extraction via pdfplumber, then regex parsing.

⚠️ PITFALL: pdfplumber may split long fields across lines. A spec like `H-NS16-赛光-ROHS2.0` can appear as two lines. Handle continuation lines.

### 2. Image invoices (JPG/PNG) → Vision API OCR

For image files, the OCR endpoint uses a vision-capable LLM (e.g. `mimo-v2.5`) to recognize the invoice and return structured JSON. Set `XIAOMI_API_KEY` environment variable with your API key.

**API URL construction**: The `XIAOMI_API_BASE` should NOT include `/v1` (e.g. `https://api.xiaomimimo.com` not `https://api.xiaomimimo.com/v1`). The code strips any trailing `/v1` before appending `/v1/chat/completions`:

```python
base = api_base.rstrip('/').replace('/v1','').replace('/v1/','')
vision_url = f"{base}/v1/chat/completions"
```

**Vision API prompt** asks for JSON with fields: invoice_no, invoice_date, total_amount, tax_amount, amount_with_tax, seller_name, buyer_name, items[].

The returned JSON is passed to `parse_invoice_text()` which detects JSON first via `json.loads()` (before attempting regex).

## Core Technique

Chinese electronic invoices (电子发票) have a fixed layout. pdfplumber extracts text line by line, but the two-column layout means buyer/seller info appears on the same line split by position.

## Key Parsing Patterns

### Invoice Number (发票号码)
18-20 digit number, usually on its own line. Must exclude 纳税人识别号 lines:
```python
for line in lines:
    m = re.search(r'(\d{18,20})', line)
    if m and '识别号' not in line:
        data['invoice_no'] = m.group(1)
        break
```

### Invoice Date (开票日期)
Format: `2024年06月30日`
```python
m = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', text)
```

### Buyer/Seller Names (购买方/销售方)

**Traditional format (纸质票/普票):**
Both names appear on the SAME line. Use `re.finditer` to find all company names:
```python
for line in lines:
    if '公司' in line:
        comps = list(re.finditer(r'[\u4e00-\u9fff（）\(\)]+公司', line))
        if len(comps) >= 2:
            data['buyer_name'] = comps[0].group(0).strip()
            data['seller_name'] = comps[1].group(0).strip()
            break
```

**Electronic invoice format (数电票/全电发票):**
The format is `购 名称：XXX 销 名称：YYY` on the same line, with "购" and "销" as abbreviations:

**⚠️ CRITICAL — JSON-first parsing**: Invoice images processed via vision API return JSON (e.g. `{"seller_name":"某某公司","buyer_name":"杭州赛光","invoice_no":"..."}`). The `parse_invoice_text` function MUST check for JSON FIRST using `json.loads()` before attempting regex — regex patterns won't match JSON field names. Extract the `{...}` block from the LLM response via `re.search(r'\{[\s\S]*\}', text)`.

**⚠️ CRITICAL — Buyer/Seller Regex**: PDF text extraction often inserts spaces between "销售方" and "名称/信息" (e.g. `销售方 名称：某某公司` or `销售方信息名称：某某公司`). The `_name_pat` must include `\s*` before and after the optional 名/称, plus `(?:信息)?` for the "销售方信息" format:

```python
# Handle: 销售方名称： / 销售方 名称： / 销售方信息名称： / 销售方信息： / 销售方 称： / 销售方：
_name_pat = r'(?:信息)?\s*(?:名\s*)?称?\s*[：:\s]+(.+?)(?:\s{2,}|\n|$|(?<=\S)\s(?=[\u4e00-\u9fff\u3400-\u4dbf]))'
m = re.search(r'销售方' + _name_pat, text)
```

The CJK lookahead `(?<=\S)\s(?=[\u4e00-\u9fff\u3400-\u4dbf])` stops name capture at a single space followed by a Chinese character (next field), solving the problem of single-space field separation.

**⚠️ CRITICAL — "名称：XXXX" fallback**: If neither `销售方名称` nor `购买方名称` patterns match, use a fallback matching Chinese company names:
```python
m = re.search(r'名\s*称[：:\s]+([\u4e00-\u9fff\u3400-\u4dbf][\u4e00-\u9fff\u3400-\u4dbf\w\-（()）]+)', text)
```
The FIRST match goes to seller_name. A second different name goes to buyer_name. If they're the same, dedup logic re-separates with individual patterns.

**⚠️ PITFALL: Seller==Buyer dedup**: If combo regex `购...销...` assigns the same name to both fields, re-separate:
```python
if data['seller_name'] == data['buyer_name']:
    m_s = re.search(r'销售方(?:名\s*)?称?[：:\s]+([^\s]{2,30}?)', text)
    m_b = re.search(r'购买方(?:名\s*)?称?[：:\s]+([^\s]{2,30}?)', text)
    if m_s: data['seller_name'] = m_s.group(1).strip()[:50]
    if m_b: data['buyer_name'] = m_b.group(1).strip()[:50]
```

```python
# Combined format first (most common for 数电票)
m = re.search(r'购\s*(?:名\s*)?称[：:\s]*(.+?)\s+销\s*(?:名\s*)?称[：:\s]*(.+?)(?:\s{2,}|\n|$)', text)
if m:
    data['buyer_name'] = m.group(1).strip()[:50]
    data['seller_name'] = m.group(2).strip()[:50]
else:
    # Traditional format fallback with flexible whitespace
    _name_pat = r'\s*(?:名\s*)?称?\s*[：:\s]+(.+?)(?:\s{2,}|\n|$|(?<=\S)\s(?=[\u4e00-\u9fff\u3400-\u4dbf]))'
    m = re.search(r'销售方' + _name_pat, text)
    if m: data['seller_name'] = m.group(1).strip()[:50]
    m = re.search(r'购买方' + _name_pat, text)
    if m: data['buyer_name'] = m.group(1).strip()[:50]
```

⚠️ PITFALL: Do NOT use midpoint split (`line[:mid]`/`line[mid:]`) — it cuts company names in half.
⚠️ PITFALL: 数电票 uses "购 称" not "购买方", and the regex must handle optional "名" character: `购\s*(?:名\s*)?称`.
⚠️ PITFALL: Try the combined "购...销..." format FIRST, because the individual "购" regex can match the entire line (no `\s{2,}` delimiter).

### Amounts (金额/税额/价税合计)
The "合 计" line has spaces between characters. Match with `\s+`:
```python
# 金额合计 (first number after 合 计)
m = re.search(r'合\s+计\s+[￥¥]?\s*([\d,]+\.?\d*)', text)
# 税额合计 (second number)
m = re.search(r'合\s+计\s+[￥¥\s\d,.]+[￥¥]\s*([\d,]+\.?\d*)', text)
# 价税合计 (小写)
m = re.search(r'小写[）)][￥¥]?\s*([\d,]+\.?\d*)', text)
```

**Tax amount fallback:** If `税额` label isn't found, match the number after a percentage:
```python
m = re.search(r'税\s*额[：:\s]*[￥¥]?\s*([\d,]+\.?\d*)', text)
if not m:
    # Match "13% 2122.57" format
    m = re.search(r'\d+%\s+([\d,]+\.?\d*)', text)
if m: data['tax_amount'] = m.group(1).replace(',', '')
```

⚠️ PITFALL: `合\s*计[￥¥]?` does NOT match `合 计 ¥` — there are spaces between 合 and 计 AND between 计 and ¥. Use `合\s+计\s+[￥¥]?`.

### Line Items (明细行)

**Standard format:**
Format: `*分类*项目名称 规格型号 单位 数量 单价 金额 税率% 税额`

Real examples:
- `*集成电路*整机 CS21XX烧写器 只 1 442.48 442.48 13% 57.52`
- `*集成电路*手势识别模块 H-NS16-赛光-ROHS2.0 pcs 550 6.6371681415929 3650.44 13% 474.56`

**Alternative approach (position-based parsing):**
When the `*分类*` prefix is not present, parse by position — extract all numbers, identify tax_rate by `%` suffix, then assign remaining numbers as quantity/unit_price/amount/tax_amount:
```python
nums = []
num_indices = []
for idx, p in enumerate(parts):
    clean = p.replace(',','').replace('％','%')
    if re.match(r'^\d+\.?\d*%?$', clean):
        nums.append(clean)
        num_indices.append(idx)

# Extract tax_rate from percentage
rate_idx = None
for i, n in enumerate(nums):
    if n.endswith('%'):
        rate_idx = i
        break
if rate_idx is not None:
    item['tax_rate'] = float(nums[rate_idx].rstrip('%'))
    nums = nums[:rate_idx] + nums[rate_idx+1:]

# Assign remaining numbers: quantity, unit_price, amount, tax_amount
if len(nums) >= 4:
    item['quantity'] = float(nums[0])
    item['unit_price'] = float(nums[1])
    item['amount'] = float(nums[2])
    item['tax_amount'] = float(nums[3])
```

⚠️ PITFALL: The old regex `r'[*＊]\s*([\u4e00-\u9fff\w*＊]+)\s+...'` is WRONG — it doesn't match the actual format. Use the two-stage approach below.

⚠️ PITFALL: Units can be Chinese (只/台/个) OR English (pcs). The unit regex must include `[a-zA-Z]`.

⚠️ PITFALL: pdfplumber may split long specs across lines. `H-NS16-赛光-ROHS2.0` can become two lines: `H-NS16-赛光-` and `ROHS2.0`. Handle continuation lines.

**Step 1: Match `*分类*` prefix, then parse the rest:**
```python
m = re.match(r'[*＊]([^*＊]+)[*＊](.+)', line)
if m:
    cat = m.group(1).strip()
    rest = m.group(2).strip()
```

**Step 2: Handle continuation lines (spec split across PDF lines):**
```python
# If next line looks like a spec continuation (has letters/hyphens, not a keyword)
next_line_for_spec = ''
if i + 1 < len(lines):
    nl = lines[i + 1].strip()
    if (nl and
        not re.match(r'[*＊合价备开收复下载国统全]', nl) and
        re.search(r'[A-Za-z\-]', nl) and
        not re.match(r'^[\d,.]+$', nl)):
        next_line_for_spec = nl
```

**Step 3: Match numeric fields from the END of the rest string:**
```python
m2 = re.search(
    r'\s+([\u4e00-\u9fff_a-zA-Z]+)\s+([\d.]+)\s+([\d,.]+)\s+([\d,.]+)\s+(\d+%)\s+([\d,.]+)\s*$',
    rest
)
if m2:
    unit = m2.group(1)        # 只, pcs, 台, etc.
    qty = m2.group(2)
    unit_price = m2.group(3).replace(',', '')
    amount = m2.group(4).replace(',', '')
    tax_rate = m2.group(5).replace('%', '')
    tax_amount = m2.group(6).replace(',', '')
```

**Step 4: Separate 项目名称 from 规格型号:**
```python
desc_part = rest[:m2.start()].strip()
m3 = re.match(r'([\u4e00-\u9fff]+)\s*(.*)', desc_part)
if m3 and m3.group(2).strip():
    description = m3.group(1).strip()  # 整机, 手势识别模块
    spec = m3.group(2).strip()         # CS21XX烧写器, H-NS16-赛光-
else:
    description = desc_part
    spec = ''

# If spec ends with hyphen and continuation line exists, append it
if spec.endswith('-') and next_line_for_spec:
    spec = spec + next_line_for_spec  # H-NS16-赛光-ROHS2.0
    i += 1  # skip the continuation line
```

**Output fields:**
- `category`: 分类 (集成电路, etc.)
- `description`: 项目名称 (整机, 手势识别模块)
- `spec`: 规格型号 (CS21XX烧写器, H-NS16-赛光-ROHS2.0)
- `unit`: 单位 (只, pcs)
- `quantity`, `unit_price`, `amount`, `tax_rate`, `tax_amount`

## Full Parser Function

See `references/full_parser.py` for the complete working implementation.

## Integration with Flask

```python
from flask import request, jsonify
import pdfplumber, os, uuid

@app.route('/api/invoices/ocr', methods=['POST'])
def api_invoice_ocr():
    f = request.files['file']
    fname = f'{uuid.uuid4().hex}.pdf'
    fpath = os.path.join(upload_dir, fname)
    f.save(fpath)
    with pdfplumber.open(fpath) as pdf:
        text = '\n'.join(p.extract_text() or '' for p in pdf.pages)
    data = _parse_invoice_text(text)
    return jsonify({'success': True, 'data': data})
```

## Known Limitations

- Works best with standard 增值税电子专用发票 format
- Scanned PDFs (image-based) need OCR — pdfplumber only extracts embedded text
- Multi-page invoices: concatenate all pages before parsing
- Some invoices have slightly different layouts — test with actual samples

⚠️ PITFALL: pdfplumber's `extract_text()` may split long fields across lines. A spec like `H-NS16-赛光-ROHS2.0` can appear as two lines: `H-NS16-赛光-` and `ROHS2.0`. The parser handles this via continuation-line detection — if the next line has letters/hyphens and isn't a keyword, it's appended to the spec when the spec ends with `-`. If you see truncated specs, check whether the continuation logic is triggering.

⚠️ PITFALL: The "合 计" line has SPACES between characters (`合 计 ¥442.48`). Regex must use `合\s+计\s+[￥¥]?` — NOT `合计[￥¥]?`.
