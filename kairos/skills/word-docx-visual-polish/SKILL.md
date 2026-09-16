---
name: "word-docx-visual-polish"
description: "Polish an existing Word .docx for visual density and professionalism WITHOUT changing any text. Triggers when user uploads a .docx and asks to 美化/优化排版/配色/排版 or complains about 空白太多/不够紧凑. Covers two pa"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\.archive\\word-docx-visual-polish\\SKILL.md"
---
# Word docx — Visual Polish (no-text-change)

Polish an existing `.docx` for visual density and professionalism without altering any text. Two passes + verification loop.

## When to use this skill

Trigger when **all** of these hold:
- User has an existing `.docx` (often a Chinese tech manual, product spec, internal SOP)
- User asks to beautify / 优化排版 / 调整配色 / 美化样式
- User states or implies that **text must not change** ("不要更改任何文字内容", "只调样式不改字", "排版/配色美化" without content edits)

Do NOT use this skill for:
- Markdown → docx conversion (use `docx-format` skill instead)
- Substantive rewrites / restructuring / content additions
- Generating a docx from scratch

## Workflow

### Step 1 — Inventory the docx first (always)

Before touching anything, run a scan pass to count: paragraph styles (Heading 1/2, Normal, List Bullet/Number), tables (rows × cols + first-cell text), font/size distributions, page margins, and **phantom empty paragraphs after tables** (see `scripts/scan_docx.py`).

The output drives every later decision. If you skip this, you will guess wrong on colors/sizes and break the design.

### Step 2 — Beautify pass (color / font / table styling)

Apply in this order so later passes don't override earlier ones:

1. **Force explicit H1/H2 size + color.** Heading styles often inherit from the theme — explicit values are non-negotiable for cross-machine consistency. Typical: `Pt(18)` for H1, `Pt(14)` for H2, brand navy `#1F3864`. Add a left accent border (`pBdr`) for the "industrial manual" look:
   ```python
   add_paragraph_border(p, 'left', color=COLOR_H1, sz='36')  # 4.5pt thick
   ```
2. **Set Chinese vs English fonts separately.** Always set both:
   ```python
   rFonts.set(qn('w:ascii'), EN_FONT)         # Times New Roman / Arial
   rFonts.set(qn('w:eastAsia'), CN_FONT)      # 微软雅黑 / 思源黑体 / 宋体
   ```
   Setting only `run.font.name` misses CJK chars — they fall back to default and look mismatched.
3. **Table headers**: solid brand bg + white bold text (10pt). Detect first row automatically.
4. **Zebra striping**: alternate `FFFFFF` and `#F2F2F2` on data rows via `tcPr/shd`.
5. **Alert boxes** (1×1 single-cell tables with text like "重要说明"/"危险"/"驾驶提示"/"首次接线重点"/"不可擅自"/"核心结论"/"使用边界"): classify by first-cell keyword, apply:
   - "危险" / "警告" → red bg `#C00000`, white text, thick border
   - everything else → navy bg `#1F3864`, white text, thick border
6. **Body text**: bump from ~9.0/9.5pt (default in many Chinese tech docs) to 10.5pt for readability. Color `#262626` (softer than pure black).

### Step 3 — Compact pass (whitespace reduction)

The biggest density wins come from this pass:

1. **Delete phantom empty paragraphs after tables.** Scan `<w:body>` for `<w:p>` elements whose `<w:t>` is empty AND that come right after a `<w:tbl>`. Delete them. In one 36-page Chinese tech manual we found 43 of these — removing them saved 6 pages without any visual loss.
2. **Shrink page margins** to `0.6cm` on all sides (1cm is common but rarely the limit).
3. **Tighten paragraph spacing**:
   - H1: `space_before=Pt(8)`, `space_after=Pt(4)`, `line_spacing=1.15`
   - H2: `space_before=Pt(4)`, `space_after=Pt(2)`, `line_spacing=1.25`
   - Body: `space_after=Pt(2)`, `line_spacing=1.3`
   - List items: `space_after=Pt(1)`, `line_spacing=1.25`
   - Table cell paragraphs: `space_before=Pt(0)`, `space_after=Pt(0)`
4. **Force `keep_with_next=True`** on every H1 and H2 — prevents orphan titles at page bottom.

### Step 4 — Visual verification with Word COM (CRITICAL)

**Do NOT use mammoth → HTML → Chrome headless for visual review.** Mammoth silently drops these OXML features:
- `<w:pBdr>` (paragraph borders) → accent bars vanish
- `<w:shd>` (cell shading) → table headers + alert boxes go white
- Custom table styles

You will see a "no improvement" preview and conclude your script is broken. It is not — mammoth is the wrong tool for this.

**Use `docx2pdf` (Word COM automation) instead:**

```bash
python -m pip install --user docx2pdf pywin32
```

```python
import docx2pdf
docx2pdf.convert('input.docx', 'output.pdf')  # ~5-10s per page on Windows
```

Requires Word installed at `C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE` (default Win10/11 path).

Then extract per-page PNGs with PyMuPDF for `vision_analyze`:

```python
import fitz
doc = fitz.open('output.pdf')
for pn in [0, 1, 5, 10]:  # cover, TOC, key body pages
    doc[pn].get_pixmap(dpi=110).save(f'p{pn+1}.png')
```

See `scripts/render_and_extract.py` for a one-shot command.

### Step 5 — Iterate based on vision feedback

Vision feedback is **directional, not absolute**. Common patterns:

| Vision says | Action |
|---|---|
| "X is too tight / needs breathing room" | small relax (H2 `space_after` `2pt → 4pt`) |
| "X is too loose / too much blank" | tighten margins/spacing or delete more phantom paragraphs |
| "Table header doesn't show blue / border doesn't show" | preview path is wrong → switch to docx2pdf |
| "Color X doesn't match brand" | adjust hex in your constants |
| "Bottom of page empty" for content-light sections | accept it — see Pitfall 4 |

Don't react to every nit. Two or three rounds usually converge.

## Pitfalls (lesson of the day)

1. **`doc.tables[i]` re-indexes after you move tables.** Always use `body.index(table_elem)` to find the current position after a `move` operation, or you will move the wrong table. (Caught this twice in one session when moving "重要说明" — `doc.tables[0]` was actually the 资料表, not the alert box.)
2. **Heading styles inherit from theme — they have no explicit size/color.** Forcing them is mandatory.
3. **Phantom empty paragraphs after tables are ubiquitous in Chinese tech docs.** Always scan + delete. Single biggest density win.
4. **Content-light sections will always have bottom whitespace** (e.g., "适用范围" + a 4-row table fills 30% of a page). Format changes cannot solve this — only adding content can, which violates the no-text-change constraint. Accept it; explain it to the user honestly.
5. **Vision feedback can be self-contradictory.** "Too much blank" + "needs more breathing" on the same page is normal. Use judgment; iterate based on overall coherence, not every nit.
6. **Tables moved via `target_p._p.addprevious(table_elem)` may break Word's pagination** if moved mid-table-chunk. Always verify the page that contains the moved table after re-rendering.
7. **Don't change text — period.** No punctuation, no sentence reorder, no character substitution. If the user later asks "just fix this one typo", that's a different session/task, not this skill.

## Output expectations

After both passes + verification, the deliverable is:
- A new `.docx` (typically `<original>_美化版.docx` or `<original>_紧凑版.docx`)
- A verification PDF (`<original>_review.pdf`) for the user to spot-check

Keep the original `.docx` untouched. Never write back over the source — always write to a sibling path.

## Support files

- `scripts/scan_docx.py` — full docx inventory + phantom paragraph detection. Run before designing.
- `scripts/render_and_extract.py` — docx2pdf + per-page PNG extraction for vision review.

See each file's header for usage.