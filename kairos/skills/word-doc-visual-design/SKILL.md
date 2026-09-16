---
name: "word-doc-visual-design"
description: "Word 文档视觉设计与紧凑化打磨 — 从\"能动 docx\"升级到\"企业级 VI 物料水准\"，或对现有 docx 做不动文字的密度/专业度美化。涵盖：(1) 设计层：配色 token 系统、字号体系、栅格对齐、Logo 单色化、页眉页脚三层结构、装饰元素；(2) 打磨层：扫盘 → 美化 pass（强制 H1/H2 显式样式 + 表格 zebra + 警示色块）→ 紧凑化 pass（删除幽灵空段 +"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\word-doc-visual-design\\SKILL.md"
---
# Word 文档视觉设计

> 用户原话反馈："你不会设计？让你有logo，就直接放个logo？你有审美skill吗？"
> 这是首次直接反馈设计期望的会话沉淀。**下次接到"页眉加 Logo"/"美化"/"VI"/"企业级"等任务，必须先加载本 skill，不要直接动手。**

## 何时使用

**触发关键词**：
- 美化 Word 文档 + 涉及视觉/Logo/页眉/品牌识别
- "加 Logo 到页眉"、"页眉页脚设计"、"VI 应用"、"企业级排版"
- 工业品说明书、技术手册、企业文档的视觉升级
- "Ctrl+Shift+R 硬刷新后视觉确认"（用户验收标准）

**何时不要使用**：
- 纯格式修改（字号/字体/行距统一）→ 用 `docx-format`
- Web/UI 设计 → 用 `ui-ux-pro-max` 或 `frontend-design`
- Markdown → DOCX → 用 `docx-format` 的 md_to_docx 脚本

## 核心设计哲学

### 设计 token 思路（不是临时配色）

把设计决策表达成 **有名字的 token**，而不是临时变量：

```python
# 配色 token（与 docx-format 共享）
COLOR_PRIMARY = '1F3864'      # 藏青主色 — 标题、Logo 单色版、装饰线
COLOR_SECONDARY = '5F6B76'    # 次级灰 — 副标题、英文副行、弱化文本
COLOR_LIGHT = 'D7DEE5'        # 浅灰 — 分隔线
COLOR_ACCENT = 'ED7D31'       # 橙色 — 版本号、Logo 跳色（≤2 处使用）
COLOR_DANGER = 'C00000'       # 警示红
COLOR_INFO = '1F3864'         # 提示蓝

# 字号 token
SIZE_H1 = 18.0      # 主标题
SIZE_H2 = 14.0      # 副标题
SIZE_BODY = 10.5    # 正文
SIZE_HEADER_MAIN = 11.0   # 页眉主标题
SIZE_HEADER_SUB = 7.5    # 页眉英文副行
SIZE_FOOTER = 9.0       # 页脚主行
SIZE_FOOTER_SUB = 7.0   # 页脚英文副行
SIZE_VERSION = 10.0     # 版本号

# 装饰 token
BORDER_HEAVY = '12'   # 1.5pt 粗边框
BORDER_THIN = '4'     # 0.5pt 细边框
```

### 三层视觉结构（不是单行）

页眉/页脚/标题都应该按 **三层** 组织，而不是把所有内容塞一行：

**反例**（单行平铺）：
```
[Logo] XT 老年代步车无刷控制器 | 使用说明书                    Ver 3.2
```

**正例**（三层）：
```
[Logo] | XT 老年代步车无刷控制器 · 使用说明书              ▪ Ver 3.2
        BRUSHLESS MOTOR CONTROLLER · USER MANUAL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

层级 = 主行（核心信息）+ 副行（次要信息）+ 装饰行（视觉锚点）

### 强调色克制使用

橙、红、亮黄等强调色**只用 1-2 处**（如版本号、Logo 跳色点）。超过 3 处就廉价。

## 关键技巧清单

### 0. 先扫盘再动手（inventory pass）

**绝对不要跳过的一步**——动手美化之前先 `python scripts/scan_docx.py <file.docx>` 拿到完整画像：

```
段落总数 / 表格总数
样式分布 (Heading 1/2, Normal, List Bullet/Number)
字体+字号分布
幽灵空段数（紧跟 `<w:tbl>` 的空 `<w:p>` — 紧凑化的最大单一收益点）
页面 margins
前 5 个 H1 标题
前 15 个表格概览
```

**为什么**：拍脑袋定配色/字号/装饰会失败。"看起来简单"的中文技术文档扫盘后可能发现 60+ 段落是 Normal 样式、9 个不同字号混用、43 个幽灵空段。**所有后续决策都基于扫盘结果**。

脚本：`scripts/scan_docx.py`。

### 1. PIL Logo 单色化（必做）

直接贴原彩色 Logo 嵌页眉 = 业余。必须转单色 + 透明背景：

```python
from PIL import Image

def make_mono(img, target_rgb=(31, 56, 100)):
    """转单色 + 透明背景（白底 → 透明）"""
    img = img.convert('RGBA')
    pixels = img.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            if r > 240 and g > 240 and b > 240:  # 白底 → 透明
                pixels[x, y] = (0, 0, 0, 0)
            else:  # 按亮度映射到目标色
                luminance = (r * 0.299 + g * 0.587 + b * 0.114) / 255
                pixels[x, y] = (*target_rgb, a)
    return img

# 用法
mono = make_mono(Image.open('logo.png'), (31, 56, 100))
mono.save('logo_mono.png')
```

**收益**：
- 文件大小 7× 缩减（16KB → 2.3KB）
- 小尺寸下 Logo 细节不糊
- 视觉上更专业、可控

详见 `docx-format/references/visual-design-techniques.md` §二。

### 2. Word COM 渲染陷阱（必避）

`docx2pdf` 渲染时会丢失/破坏部分元素：

| 陷阱 | 现象 | 替代 |
|---|---|---|
| `▌ ▪ ◆ ※ ©` 等特殊 Unicode | PDF 里看不到 | 用 ASCII `\| / * > <` |
| `w:spacing val="50"`（2.5pt） | 英文每个字母被空格分开 | `val="5"` (0.25pt) 是上限 |
| `tabs.pos > 页面宽` | 文字跑到页面外被裁剪 | A4 = 10400dxa 是 right tab 上限 |
| 单色 Logo 仍糊 | PIL 单色化没去白底 | 检查 `r > 240` 判断 |

### 3. titlePg 让首页不显示页眉

很多 docx 已经有 `headerReference type="first"` 但**首页仍显示 default header**——根因是 sectPr 缺 `<w:titlePg>` 元素。

```python
sectPr = section._sectPr
if sectPr.find(qn('w:titlePg')) is None:
    titlePg = OxmlElement('w:titlePg')
    sectPr.find(qn('w:cols')).addprevious(titlePg)
```

详见 `docx-format/references/visual-design-techniques.md` §四。

### 4. docx tables 索引会漂移（身份确认）

`doc.tables[i]` 不一定是 body 里第 i 个表格。**永远先按关键字查找**：

```python
def find_table_by_keyword(doc, keyword):
    for i, t in enumerate(doc.tables):
        if t.rows and keyword in t.rows[0].cells[0].text:
            return i, t
    return None, None

idx, tbl = find_table_by_keyword(doc, '重要说明')
```

### 5. "幽灵空段"清理（紧凑化关键）

每个表格后经常跟着不显示内容但占垂直空间的"幽灵空段"。检测：

```python
body = doc.element.body
items = list(body)
to_remove = []
for idx, item in enumerate(items):
    if item.tag.split('}')[-1] == 'tbl':
        # 检查后面紧跟的空段
        if idx+1 < len(items) and items[idx+1].tag.split('}')[-1] == 'p':
            text = ''.join(t.text or '' for t in items[idx+1].iter(qn('w:t')))
            if not text.strip():
                to_remove.append(items[idx+1])
for elem in to_remove:
    elem.getparent().remove(elem)
```

实战效果：XT 案例 43 个表格都跟着幽灵空段，全清后 44 → 37 页，省 7 页。

## 打磨 pass（不动文字的密度+专业度美化）

**触发场景**：用户上传 docx 说"美化/优化排版/配色"或抱怨"空白太多/不够紧凑"且**明确不允许改文字**（"不要更改任何文字内容"）。设计层（token/Logo/页眉）的改动会引入新元素 → 用上面的"设计层"流程；只是密度和样式统一 → 用本节的"打磨层"。

**与设计层的差异**：
- 设计层：可能要加 Logo/页眉/装饰元素，文字不动
- 打磨层：**绝不引入新元素**，只改字号/颜色/间距/表格样式/删除幽灵空段

### 流程：扫盘 → 美化 pass → 紧凑化 pass → 视觉验证

#### 1) 美化 pass（color/font/table styling）

按以下顺序应用，避免后续 pass 覆盖前面的：

1. **强制显式 H1/H2 字号 + 颜色**：Heading 样式常继承自主题，跨机器不一致 → 必须显式赋值。典型 `Pt(18)` for H1, `Pt(14)` for H2, 品牌藏青 `#1F3864`。加左侧 accent border (`pBdr`) 走"工业手册"风格：
   ```python
   add_paragraph_border(p, 'left', color=COLOR_H1, sz='36')  # 4.5pt 厚
   ```
2. **中英字体分开设置**（必做）：
   ```python
   rFonts.set(qn('w:ascii'), EN_FONT)         # Times New Roman / Arial
   rFonts.set(qn('w:eastAsia'), CN_FONT)      # 微软雅黑 / 思源黑体 / 宋体
   ```
   只设 `run.font.name` 会漏 CJK 字符 → 退回默认字体看起来不协调。
3. **表头**：品牌色背景 + 白色加粗 10pt。自动检测首行。
4. **Zebra striping**：数据行交替 `#FFFFFF` 和 `#F2F2F2` via `tcPr/shd`。
5. **警示框**（1×1 单格表，内容含"重要说明"/"危险"/"驾驶提示"/"首次接线重点"/"不可擅自"/"核心结论"/"使用边界"）：按首格关键词分类配色：
   - "危险"/"警告" → 红底 `#C00000` + 白字 + 厚边框
   - 其他 → 藏青底 `#1F3864` + 白字 + 厚边框
6. **正文**：从 9.0/9.5pt（很多中文技术文档默认值）提到 10.5pt，颜色 `#262626`（比纯黑柔和）。

#### 2) 紧凑化 pass（whitespace reduction）

最大的密度收益来自这一步：

1. **删除表格后的幽灵空段**（最大单一收益）：扫 `<w:body>` 找紧跟 `<w:tbl>` 的空 `<w:p>` → 删除。一个 36 页中文技术手册通常有 30-50 个幽灵空段，全删省 6-7 页无视觉损失。脚本模板见上方"关键技巧 §5"。
2. **缩 margin**：四面缩到 `0.6cm`（1cm 常见但很少是上限）。
3. **紧 spacing**：
   - H1：`space_before=Pt(8)`, `space_after=Pt(4)`, `line_spacing=1.15`
   - H2：`space_before=Pt(4)`, `space_after=Pt(2)`, `line_spacing=1.25`
   - 正文：`space_after=Pt(2)`, `line_spacing=1.3`
   - 列表项：`space_after=Pt(1)`, `line_spacing=1.25`
   - 表格单元格段落：`space_before=Pt(0)`, `space_after=Pt(0)`
4. **强制 `keep_with_next=True`** on every H1/H2 — 防止标题单独留页底成孤儿。

#### 3) 视觉验证 with Word COM（critical）

**不要用 mammoth → HTML → Chrome headless 做视觉验收**。mammoth 会**静默丢失**这些 OXML 特性：

- `<w:pBdr>` (paragraph borders) → 装饰条消失
- `<w:shd>` (cell shading) → 表头 + 警示框变白
- 自定义 table styles

你会看到"没改进"的预览，然后以为脚本坏了——其实不是，**mammoth 用错了**。

**正确：用 `docx2pdf`（Word COM 自动化）**：

```bash
python -m pip install --user docx2pdf pywin32
```

```python
import docx2pdf
docx2pdf.convert('input.docx', 'output.pdf')  # ~5-10s/页 on Windows
```

要求本机装了 Word（默认 Win10/11 路径 `C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE`）。

然后用 PyMuPDF 提取关键页 PNG 给 `vision_analyze`：

```python
import fitz
doc = fitz.open('output.pdf')
for pn in [0, 1, 5, 10]:  # 封面/目录/关键正文页
    doc[pn].get_pixmap(dpi=110).save(f'p{pn+1}.png')
```

一站式脚本：`scripts/render_and_extract.py`。

#### 4) 基于 vision 反馈迭代

Vision 反馈是**方向性而非绝对**。常见模式：

| Vision 说 | 动作 |
|---|---|
| "X 太挤/需要呼吸空间" | 微松（H2 `space_after` `2pt → 4pt`） |
| "X 太松/空白太多" | 紧 margin/spacing 或再多删幽灵段 |
| "表头不显蓝/边框不见" | 预览路径错了 → 切到 docx2pdf |
| "X 颜色不符合品牌" | 调整 hex 常量 |
| 章节内容稀疏 → 页底空白 | 接受 — 见 pitfall 4 |

不要对每条 nit 都反应。两三轮通常收敛。

### 打磨层的 Pitfalls

1. **`doc.tables[i]` 移表后会重新索引**。移动表后用 `body.index(table_elem)` 找当前位置，否则会移动错表。（在移"重要说明"框时踩过两次 — `doc.tables[0]` 实际是资料表而不是警示框。）
2. **Heading 样式继承自主题 —— 没有显式 size/color**。必须强制赋值。
3. **幽灵空段在中文技术文档里到处都有**。永远扫+删。最大密度收益点。
4. **内容稀疏章节必有页底空白**（如"适用范围"+ 4 行表只填 30% 一页）。**格式改动解不了**——加内容才能解，但加内容违反"不动文字"约束。接受并诚实告知用户。
5. **Vision 反馈会自相矛盾**。"空白太多"+"需要呼吸空间"在同一页出现很正常。用判断；按整体协调性迭代，不要被每条 nit 牵着走。
6. **`target_p._p.addprevious(table_elem)` 移表可能破坏 Word 分页**（如果跨表分块）。重新渲染后务必验证移动后的表所在页是否正常。
7. **绝不动文字 —— 句号**。不改标点、不重排语序、不替换字符。用户后续说"改这一个错别字"是另一个 session/任务，不是本 skill。

## 标准流程

```
1. 加载本 skill（word-doc-visual-design）+ ui-ux-pro-max + docx-format
2. 用 docx-format 的 §Step 1 分析 docx 内部结构
3. 建立配色/字号/装饰 token（按本 skill 模板）
4. PIL 单色化 Logo（如有）
5. 按三层结构写 header/footer + 警示色块 + 紧凑化
6. 输出 _美化版.docx
7. 用 docx2pdf 转 PDF + fitz 提取关键页 PNG
8. vision 校验（关注：Logo 清晰度、视觉层次、配色克制、装饰元素是否到位）
9. 报告改动（H1=N, H2=N, 表格=N, 警示=N, 总页数=N）
```

## 用户视角的"什么算合格"

用户在硬刷新后看 docx 的视觉期望：

| 维度 | 业余 | 专业 |
|---|---|---|
| Logo | 直接贴原彩色 PNG 1.5cm | 单色化 + 透明背景 + 尺寸适配 1.1cm |
| 页眉布局 | 单行平铺 | 三层（主行+副行+装饰行） |
| 配色 | 黑 + 蓝/红随便用 | 主色 + 次级灰 + 强调色 三层 token |
| 装饰 | 单一横线 | 双下边框 + 装饰色块 + 竖线分隔 |
| 中英对照 | 无 | 主标题中文 + 副行英文（国际化） |
| 栅格 | 文字随便放 | tabs 精确对齐 + 8pt 网格 |
| 字体层级 | 字号一致 | 22/14/10/8/7 五级 token |

## 相关文档

- `references/brand-manual-rebuild.md` — 旧 PDF + 现有精排 DOCX 的品牌化技术说明书复用流程，含跨 run 替换、Word COM 导出、PDF 元数据与残留词验收
- `scripts/scan_docx.py` — 完整 docx 库存 + 幽灵空段检测。动手前先跑。
- `scripts/render_and_extract.py` — docx2pdf + 按页 PNG 提取，给 vision 验收用。
- `docx-format` skill — python-docx 格式修改技术细节
- `docx-format/references/visual-design-techniques.md` — PIL Logo + Word COM 陷阱 + 完整技术细节
- `docx-format/references/docx-beautify-playbook.md` — 美化任务完整 pipeline
- `ui-ux-pro-max` skill — UI/UX 设计智能（跨数字/印刷文档参考）
- `frontend-design` skill — 数字界面设计（视觉层次/配色 token）

## 注意事项

1. **不动文字**：用户硬约束"不要更改任何文字内容"，文字改一个字都是越界
2. **永远用 docx2pdf 预览**：mammoth 丢失 pBdr/shd/tcBorders，vision 看 mammoth PDF 会给"样式没应用"的误导反馈
3. **新文件输出**：永远 `_美化版.docx`，不覆盖原文件
4. **页眉页脚会增加总页数**：装饰占用垂直空间（XT 案例 37 → 39 页），用户如果同时要求"少空白+页眉页脚"需要告知 trade-off
5. **UI 设计灵感可迁移**：design tokens、栅格对齐、视觉层次的思路从 UI 设计跨界而来，但**介质不同**（印刷文档 vs 屏幕），需考虑 DPI、固定尺寸、颜色模型等差异