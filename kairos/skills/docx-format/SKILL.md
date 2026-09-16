---
name: "docx-format"
description: "使用 python-docx 精确读取、分析、修改 Word 文档（.docx）格式。当用户需要分析文档格式、批量修改格式、统一排版规范、处理中英文混排字体、修改交叉引用样式时使用此 skill。"
priority: 0.5
imported-from: "claude"
source-path: "claude/skills/docx-format/SKILL.md"
---
# Word 文档格式处理

使用 python-docx 库精确操作 Word 文档格式，适用于格式分析、格式规范化、批量修改。

## 核心规则

**被调用时立即执行**：

1. **确认文件路径**：询问用户 Word 文档的完整路径
2. **明确需求**：确认是分析格式还是修改格式，目标规范是什么。**关键**：用户说"美化/排版/配色优化"时，硬约束通常是"**不更改任何文字内容**"——只动字号/颜色/边框/底色/缩进/行距，先确认这条
3. **加载设计参考 skill**（重要）：任务涉及"美化/视觉设计/页眉页脚/Logo"时，**主动加载 `word-doc-visual-design` + `ui-ux-pro-max`**——不要只会基本格式。原话："你不会设计？让你有logo，就直接放个logo？你有审美skill吗？"
4. **选择模板**：根据需求选择合适的代码模板或脚本
5. **生成脚本**：创建独立的 Python 脚本文件
6. **执行验证**：见 §预览验证 pipeline

**强制性约束**：

- ✓ **必须处理** `doc.paragraphs` 和 `doc.tables`（文档正文常在表格内）
- ✓ **必须分设** 中英文字体（使用 `qn('w:eastAsia')`）
- ✓ **必须询问** 用户文件路径和输出路径
- ✓ **美化任务必须** 永远写到新文件（`_美化版.docx` / `_optimized.docx`），不动原文件
- ✓ **美化任务必须** 用 Word COM (`docx2pdf`) 预览，不要信 mammoth
- ✗ **禁止遗漏** 表格内容处理（常见错误）
- ✗ **禁止** "美化"任务中修改、删除、合并、重排文字

**依赖安装**（Windows + Python 3.13 共存环境）：
```bash
python3 -m pip install --user python-docx
# 预览用 Word COM：
python3 -m pip install --user docx2pdf pywin32
```
> ⚠ `uv run --with python-docx python3 script.py` 在当前 Hermes 环境（Windows + Python 3.13 via WindowsApps + uv）下**不工作**（ModuleNotFoundError）。用 `pip install --user` 装到 WindowsApps python3，再用 `python3 script.py` 调用。

## 何时使用

**触发场景**：
- 分析 Word 文档格式（字体/字号/缩进/行距）
- 批量修改文档格式
- 统一排版规范（学术论文、报告等）
- 处理中英文混排字体
- 修改交叉引用/参考文献样式

**触发关键词**：修改 Word 格式、统一字体、调整缩进、分析文档格式、参考文献格式

## 决策树

```
用户需求是什么？
├─ 不清楚当前格式 → 使用 scripts/analyze.py
├─ 应用公文标准 → 使用 scripts/format_official.py
├─ 应用学术标准 → 使用 scripts/format_academic.py
├─ 统一正文格式 → 使用【快速模板：批量格式化】
├─ 修改参考文献 → 使用【快速模板：参考文献处理】
├─ Markdown → DOCX → 使用 scripts/md_to_docx.py
├─ 美化/排版/配色优化（不动文字）→ §美化任务 pipeline（重点看这里）
└─ 自定义需求 → 基于【基础模板】组合
```

## 美化任务 pipeline（重要！）

"美化说明书/美化文档/排版优化/配色优化"——这是最常见的 docx 任务，且通常硬约束**不更改任何文字内容**。完整流程：

### Step 1. 分析 docx 内部结构（只读）
```python
from docx import Document
from collections import Counter

doc = Document(src)
print(f'段落: {len(doc.paragraphs)}, 表格: {len(doc.tables)}')

# 段落样式分布
for s, c in Counter(p.style.name for p in doc.paragraphs).most_common(10):
    print(f'  {s}: {c}')

# 字号分布（关键：找到正文偏小的字号）
sizes = Counter()
for p in doc.paragraphs:
    for r in p.runs:
        if r.font.size: sizes[r.font.size.pt] += 1
print('字号:', sizes.most_common(5))

# H1/H2 标题清单（确认层级数量）
for p in doc.paragraphs:
    if p.style.name == 'Heading 1' and p.text.strip():
        print(f'  H1: {p.text[:50]}')

# 警示类表格识别（首格含 危险/警告/重要/提示/核心/不可擅自 等）
KEYWORDS = ['危险','警告','重要说明','不可擅自','核心结论','首次接线重点',
            '驾驶提示','使用边界','提示次数']
for i, t in enumerate(doc.tables):
    if t.rows and any(k in t.rows[0].cells[0].text for k in KEYWORDS):
        print(f'  警示表{i}: {t.rows[0].cells[0].text[:30]}')
```

### Step 2. 设计美化方案（基于现有标题/表格/字体识别结果）

**常见改动维度**：
| 元素 | 维度 | 典型改动 |
|---|---|---|
| H1 | 字号/颜色/装饰 | 18pt 藏青 #1F3864 + 加粗 + 左粗竖线 (4.5pt) + 下划线 |
| H2 | 字号/颜色/装饰 | 14pt 藏青 #2E4E7E + 加粗 + 左细竖线 (2.25pt) |
| 正文 | 字号/行距/段距 | 9.0/9.5pt → 10.5pt（可读性关键提升）；行距 1.4；段后 6pt |
| 表格表头 | 底色/字色/字号 | 藏青 #1F3864 底 + 白字加粗 10pt |
| 数据行 | 斑马纹 | 奇行 #FFFFFF，偶行 #F2F2F2 |
| 边框 | 颜色/线宽 | 浅灰 #D0D0D0 0.5pt |
| 警示表（红）| 危险/警告类 | #C00000 红底 + #7F0000 边框 + 白字 |
| 警示表（蓝）| 提示/重要/核心类 | #1F3864 蓝底 + #14264A 边框 + 白字 |

**配色推荐**（藏青蓝 + 橙，工业/技术文档经典）：
```python
COLOR_PRIMARY = '1F3864'      # 深藏青蓝
COLOR_ACCENT = 'ED7D31'       # 橙色
COLOR_DANGER_BG = 'C00000'    # 警示红
COLOR_INFO_BG = '1F3864'      # 提示蓝
COLOR_TABLE_HEADER_BG = '1F3864'
COLOR_ZEBRA = 'F2F2F2'
COLOR_BORDER = 'D0D0D0'
```

### Step 3. OXML 辅助函数（python-docx 没封装的关键操作）

基础模板里的 `set_font` 只覆盖 run 级别。**美化必须用到这些 OXML 操作**——见 §OXML 辅助函数。

### Step 4. 写脚本 → 跑 → 输出新文件

输出**永远写到新文件**：
```python
SRC = '原文件.docx'
DST = '原文件_美化版.docx'   # 不覆盖原文件
doc = Document(SRC)
# ... 美化操作 ...
doc.save(DST)
```

### Step 5. 预览验证（**关键**）

**❌ 不要信 mammoth → HTML → Chrome PDF**：
- mammoth 只支持有限的样式（粗体、斜体、上下标）
- **会丢失**：段落边框 (pBdr)、单元格底色 (tcShd)、单元格边框 (tcBorders)、斑马纹
- vision_analyze 看 mammoth 预览会给出**误导性反馈**（"标题没颜色/表格没斑马纹"），但实际 docx 里样式是对的

**✅ 用 docx2pdf (Word COM) 预览**：
```python
import docx2pdf
docx2pdf.convert('美化版.docx', '预览.pdf')   # 用真实 Word 渲染
```
前提：装了 Word (`/c/Program Files/Microsoft Office/root/Office16/WINWORD.EXE`) + `pywin32` + `docx2pdf`

**第三方案**（如果没装 Word）：用 Edge/Chrome headless 把 mammoth 转的 HTML 转 PDF 做粗看，但**视觉验证以 docx 在 Word 里的真实表现为准**——告诉用户 Ctrl+Shift+R 后在 Word 里看。

## OXML 辅助函数（python-docx 没封装的操作）

python-docx 的高级 API 只覆盖段落/表格/字体基础。**美化必须用 OXML 直接操作**：

```python
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

def set_cell_shading(cell, hex_color):
    """单元格底色"""
    tcPr = cell._tc.get_or_add_tcPr()
    for shd in tcPr.findall(qn('w:shd')):
        tcPr.remove(shd)
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), hex_color)
    tcPr.append(shd)

def set_cell_borders(cell, color='D0D0D0', sz='4'):
    """单元格四边边框（sz 单位 1/8 pt，4 = 0.5pt）"""
    tcPr = cell._tc.get_or_add_tcPr()
    for b in tcPr.findall(qn('w:tcBorders')):
        tcPr.remove(b)
    borders = OxmlElement('w:tcBorders')
    for edge in ('top', 'left', 'bottom', 'right'):
        el = OxmlElement(f'w:{edge}')
        el.set(qn('w:val'), 'single')
        el.set(qn('w:sz'), sz)
        el.set(qn('w:space'), '0')
        el.set(qn('w:color'), color)
        borders.append(el)
    tcPr.append(borders)

def set_cell_valign(cell, val='center'):
    """单元格垂直对齐"""
    tcPr = cell._tc.get_or_add_tcPr()
    for v in tcPr.findall(qn('w:vAlign')):
        tcPr.remove(v)
    va = OxmlElement('w:vAlign')
    va.set(qn('w:val'), val)
    tcPr.append(va)

def add_paragraph_border(paragraph, side='left', color='1F3864', sz='24'):
    """段落单边边框（sz 单位 1/8 pt，24 = 3pt，36 = 4.5pt 粗竖线）
    side: left / right / top / bottom
    """
    pPr = paragraph._p.get_or_add_pPr()
    pBdr = pPr.find(qn('w:pBdr'))
    if pBdr is None:
        pBdr = OxmlElement('w:pBdr')
        pPr.append(pBdr)
    for b in pBdr.findall(qn(f'w:{side}')):
        pBdr.remove(b)
    el = OxmlElement(f'w:{side}')
    el.set(qn('w:val'), 'single')
    el.set(qn('w:sz'), sz)
    el.set(qn('w:space'), '4')
    el.set(qn('w:color'), color)
    pBdr.append(el)

def remove_paragraph_borders(paragraph):
    """清空段落所有边框（用于重设）"""
    pPr = paragraph._p.get_or_add_pPr()
    pBdr = pPr.find(qn('w:pBdr'))
    if pBdr is not None:
        pPr.remove(pBdr)

def set_paragraph_shading(paragraph, hex_color):
    """段落底色（用于标题底色块）"""
    pPr = paragraph._p.get_or_add_pPr()
    for shd in pPr.findall(qn('w:shd')):
        pPr.remove(shd)
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), hex_color)
    pPr.append(shd)
```

**关键易错点**：
- 边框宽度单位是 **1/8 pt**：`sz='4'` = 0.5pt，`sz='24'` = 3pt，`sz='36'` = 4.5pt
- 段落边框会让文字缩进被吃掉，记得同时设 `paragraph.paragraph_format.left_indent = Pt(8)` 让出竖线位置
- 表格 `tcPr` 是 cell 的属性容器，所有 cell-level OXML 操作都要先 `get_or_add_tcPr()`
- `set_cell_borders` 会**覆盖**已有边框，先 remove 再 add

## 警示色块表格处理模式

技术文档里**大量**使用 1×1 单格做警示框（"危险"/"重要说明"/"驾驶提示"/"首次接线重点"等）。识别和处理模式：

```python
ALERT_PATTERNS = {
    'danger': ['危险', '警告'],
    'info':   ['重要说明', '不可擅自', '核心结论', '首次接线重点',
               '驾驶提示', '使用边界', '提示次数', '试车'],
}

def classify_alert(first_cell_text):
    """根据首格文字归类"""
    text = first_cell_text.strip()
    for cls, kws in ALERT_PATTERNS.items():
        for kw in kws:
            if text.startswith(kw) or kw in text[:30]:
                return cls, kw
    return None, None

def style_alert_box(table, cls, keyword):
    """把整张表变成警示色块"""
    if cls == 'danger':
        bg, fg, border = 'C00000', 'FFFFFF', '7F0000'
    else:
        bg, fg, border = '1F3864', 'FFFFFF', '14264A'
    for row in table.rows:
        for cell in row.cells:
            set_cell_shading(cell, bg)
            set_cell_borders(cell, color=border, sz='12')  # 1.5pt 粗边框
            set_cell_valign(cell, 'center')
            for p in cell.paragraphs:
                p.paragraph_format.left_indent = Pt(4)
                p.paragraph_format.right_indent = Pt(4)
                p.paragraph_format.line_spacing = 1.45
                for run in p.runs:
                    run.font.size = Pt(10.5)
                    run.font.color.rgb = RGBColor.from_string(fg)
```

**安全色标规范**（ISO 3864 / IEC）：
- 红 = 危险（最高警示）
- 橙 = 警告
- 黄 = 注意
- 蓝 = 提示/强制
- 绿 = 安全/通过

## 快速开始

### 基础模板（基础参考）

```python
from docx import Document
from docx.shared import Pt
from docx.oxml.ns import qn

doc = Document('input.docx')

def set_font(run, cn='宋体', en='Times New Roman', size=10.5):
    """设置中英文字体"""
    run.font.name = en
    run._element.rPr.rFonts.set(qn('w:eastAsia'), cn)
    run.font.size = Pt(size)

def process_all_paragraphs(doc, process_func):
    """遍历所有段落（包括表格内）"""
    for para in doc.paragraphs:
        process_func(para)

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    process_func(para)

# 使用示例
def format_para(para):
    para.paragraph_format.first_line_indent = Pt(21)
    for run in para.runs:
        set_font(run, cn='宋体', en='Times New Roman', size=10.5)

process_all_paragraphs(doc, format_para)
doc.save('output.docx')
```

### 快速模板：批量格式化

```python
from docx import Document
from docx.shared import Pt
from docx.oxml.ns import qn

doc = Document('input.docx')

for para in doc.paragraphs:
    if len(para.text.strip()) > 30:
        para.paragraph_format.first_line_indent = Pt(21)
        para.paragraph_format.line_spacing = 1.5
        for run in para.runs:
            run.font.name = 'Times New Roman'
            run._element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
            run.font.size = Pt(10.5)

for table in doc.tables:
    for row in table.rows:
        for cell in row.cells:
            for para in cell.paragraphs:
                if len(para.text.strip()) > 30:
                    para.paragraph_format.first_line_indent = Pt(21)
                    for run in para.runs:
                        run.font.name = 'Times New Roman'
                        run._element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
                        run.font.size = Pt(10.5)

doc.save('output.docx')
```

### 快速模板：参考文献处理

```python
from docx import Document
from docx.shared import Pt, RGBColor
import re

doc = Document('input.docx')
ref_pattern = re.compile(r'\[\d+\]')

for para in doc.paragraphs:
    if para.text.strip().startswith('[') and ']' in para.text[:5]:
        para.paragraph_format.first_line_indent = Pt(-21)
        para.paragraph_format.left_indent = Pt(21)

    for run in para.runs:
        if ref_pattern.search(run.text):
            run.font.color.rgb = RGBColor(0, 0, 255)

doc.save('output.docx')
```

## 执行检查清单

**生成脚本前**：
- [ ] 已确认用户提供的文件路径
- [ ] 已明确目标格式规范
- [ ] 已选择合适的模板或脚本

**脚本中必须包含**：
- [ ] 同时处理 `doc.paragraphs` 和 `doc.tables`
- [ ] 使用 `qn('w:eastAsia')` 设置中文字体
- [ ] 正确的输入/输出文件路径

**执行前提醒用户**：
- [ ] 备份原文件或使用不同的输出文件名
- [ ] 确认文件未被其他程序打开

**执行命令**：
```bash
python3 script.py
```
> 前提：`python3 -m pip install --user python-docx` 已执行。如果还要做 Word 预览：`python3 -m pip install --user docx2pdf pywin32`

## 常见错误

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| 修改未生效 | 遗漏表格内容 | 检查是否处理了 `doc.tables` |
| 中文字体不对 | 未用 `qn('w:eastAsia')` | 必须单独设置中文字体 |
| 文件打开失败 | 路径错误或文件被占用 | 检查路径，关闭 Word |
| `ModuleNotFoundError: docx` 用 `uv run` | uv run --with 在本环境失效 | 用 `python3 -m pip install --user python-docx` 装到 WindowsApps python3 |
| 美化后 vision 反馈"样式没应用" | mammoth 预览丢失 pBdr/shd/tcBorders | 用 `docx2pdf` (Word COM) 重渲，docx 里样式其实是对的 |
| `msedge --print-to-pdf=out.pdf` 拒绝访问 | 写当前目录权限问题 | 用 `/tmp/out.pdf` 或绝对路径 |
| 段落边框画了但文字被压 | 边框占用空间没让出 | 加 `paragraph_format.left_indent = Pt(8)` |
| 警示表"危险"用了蓝底 | 配色不规范 | 红=危险/橙=警告/黄=注意/蓝=提示/绿=安全；悄悄改正 |
| 改了文字被用户骂 | 没听"不动文字"约束 | **永远只改样式**，文字改一个字都是越界 |
| 页眉页脚加了 ▌ ▪ 等装饰字符，PDF 里不显示 | Word COM 渲染丢失部分 Unicode 字符（`▌ ▪ ◆` 等 box-drawing/symbol 类） | 用 ASCII 替代（`|` `/` `*`），或用真实色块段落 |
| 英文副行每个字母都被空格分开（如 "H A N G Z H O U"） | `w:spacing val="50"` 设太大了（单位 1/20 pt，50 = 2.5pt 已经过大） | `w:spacing val` ≤ 5（0.25pt），需要时加宽字距用 `val="10"` |
| 页眉 Logo 太大或颜色糊 | 直接用了原彩色 PNG，缩到小尺寸后细节丢失 | 用 PIL 转**单色 + 透明背景**版（见 references/visual-design-techniques.md） |
| 加了 first header 但首页仍显示 | docx 有 `headerReference type="first"` 但 sectPr 缺 `<w:titlePg>` 元素 | 在 sectPr 里显式 `OxmlElement('w:titlePg')` 插到 cols 之前 |
| 设计被用户说"没审美 / 不会设计" | 只动了基础格式，没考虑视觉层次、装饰元素、配色系统、栅格对齐 | 加载 `word-doc-visual-design` skill，按 design system tokens 思路重做 |

## 相关文档

- **references/docx-beautify-playbook.md** — 美化任务完整 playbook（实战案例 + 配色清单 + 警示色块关键词表 + vision 反馈校验清单）
- **references/visual-design-techniques.md** — 页眉页脚设计 + PIL Logo 单色化 + 视觉层次方法论
- **templates/docx-beautify-template.py** — 美化脚本模板，可直接复制修改
- **scripts/verify_docx_styles.py** — 验证 docx 样式是否应用（独立运行，输出 H1/H2/表格/警示块样式报告）
- **STANDARDS.md** - 默认格式标准（公文/学术论文）和参数速查表
- **EXAMPLES.md** - 详细示例代码（多级标题、表格、图片、页眉页脚等）
- **scripts/** - 预置工具脚本（分析、公文格式化、学术格式化）

## Markdown 转 DOCX

直接读取 Markdown 文档并按格式要求写入 DOCX，无需 pandoc。

### 基础模板

```python
from docx import Document
from docx.shared import Pt
from docx.oxml.ns import qn
from docx.enum.text import WD_ALIGN_PARAGRAPH
import re

doc = Document()

def add_heading(doc, text, level):
    """添加标题"""
    para = doc.add_paragraph(text)
    if level == 1:
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in para.runs:
            run.font.name = 'Arial'
            run._element.rPr.rFonts.set(qn('w:eastAsia'), '黑体')
            run.font.size = Pt(15)
            run.font.bold = True
    elif level == 2:
        para.alignment = WD_ALIGN_PARAGRAPH.LEFT
        for run in para.runs:
            run.font.name = 'Arial'
            run._element.rPr.rFonts.set(qn('w:eastAsia'), '黑体')
            run.font.size = Pt(14)
            run.font.bold = True
    elif level == 3:
        para.alignment = WD_ALIGN_PARAGRAPH.LEFT
        for run in para.runs:
            run.font.name = 'Arial'
            run._element.rPr.rFonts.set(qn('w:eastAsia'), '黑体')
            run.font.size = Pt(12)
            run.font.bold = True

def add_paragraph(doc, text):
    """添加正文段落"""
    para = doc.add_paragraph(text)
    para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    para.paragraph_format.first_line_indent = Pt(24)
    para.paragraph_format.line_spacing = 1.5
    for run in para.runs:
        run.font.name = 'Times New Roman'
        run._element.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
        run.font.size = Pt(12)

# 读取 Markdown
with open('input.md', 'r', encoding='utf-8') as f:
    for line in f:
        line = line.rstrip()
        if not line:
            continue

        # 标题
        if line.startswith('# '):
            add_heading(doc, line[2:], 1)
        elif line.startswith('## '):
            add_heading(doc, line[3:], 2)
        elif line.startswith('### '):
            add_heading(doc, line[4:], 3)
        # 正文
        else:
            add_paragraph(doc, line)

doc.save('output.docx')
```

**使用预置脚本**：
```bash
python3 .claude/skills/docx-format/scripts/md_to_docx.py input.md output.docx
```

## 注意事项

1. **只支持 .docx**：不支持旧版 .doc
2. **表格内容**：学术论文、报告等正文常在表格内，必须处理
3. **单位转换**：`font.size` 返回 EMU，需转换（÷ 914400 × 72）
4. **备份原文件**：修改前建议备份
5. **Markdown 转换**：使用 pandoc 转换后再格式化
6. **美化任务额外提醒**：永远写到 `_美化版.docx`，永远用 `docx2pdf` 预览，永远只改样式
