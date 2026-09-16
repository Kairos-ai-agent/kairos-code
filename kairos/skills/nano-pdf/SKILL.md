---
name: "nano-pdf"
description: "Edit PDF text/typos/titles via nano-pdf CLI (NL prompts)."
priority: 0.5
version: "1.0.0"
imported-from: "hermes"
source-path: "hermes/skills/productivity/nano-pdf/SKILL.md"
---
# nano-pdf

Edit PDFs using natural-language instructions. Point it at a page and describe what to change.

## Prerequisites

```bash
# Install with uv (recommended — already available in Hermes)
uv pip install nano-pdf

# Or with pip
pip install nano-pdf
```

## Usage

```bash
nano-pdf edit <file.pdf> <page_number> "<instruction>"
```

## Examples

```bash
# Change a title on page 1
nano-pdf edit deck.pdf 1 "Change the title to 'Q3 Results' and fix the typo in the subtitle"

# Update a date on a specific page
nano-pdf edit report.pdf 3 "Update the date from January to February 2026"

# Fix content
nano-pdf edit contract.pdf 2 "Change the client name from 'Acme Corp' to 'Acme Industries'"
```

## Notes

- Page numbers may be 0-based or 1-based depending on version — if the edit hits the wrong page, retry with ±1
- Always verify the output PDF after editing (use `read_file` to check file size, or open it)
- The tool uses an LLM under the hood — requires an API key (check `nano-pdf --help` for config)
- Works well for text changes; complex layout modifications may need a different approach

## Pitfalls

### Windows: Missing system dependencies

On Windows, nano-pdf requires `tesseract` and `poppler`. If you get:
```
Error: Missing system dependencies: tesseract
```
Install with Chocolatey:
```bash
choco install poppler tesseract
```
(May need to restart terminal after installation.)

Alternatively, use `pip install pymupdf` (no system deps) — see below.

### CJK / Chinese text not rendering with pymupdf insert_text

`pymupdf.Page.insert_text()` with `fontfile=` for CJK fonts (SimHei, Microsoft YaHei, etc.) embeds the font but many PDF viewers still render the characters as garbled dots or missing glyphs. The text will appear as `3......502......4......` instead of readable Chinese.

**Workaround: Image-based editing with Pillow**

For CJK-heavy PDFs, especially WPS-generated documents with embedded images/complex layout, use this approach:

1. Render the target page as a high-DPI image with pymupdf:
```python
pix = page.get_pixmap(dpi=300)
img = Image.open(io.BytesIO(pix.tobytes("png")))
```

2. Find the text location by searching `page.get_text("dict")` blocks:
```python
for block in page.get_text("dict")["blocks"]:
    if "lines" in block:
        for line in block["lines"]:
            text = "".join([s["text"] for s in line["spans"]])
            if "target text" in text:
                bbox = line["bbox"]  # (x0, y0, x1, y1) from page top
```

3. Draw a white rectangle on the Pillow image to cover old text, then draw new text with Pillow's `ImageDraw.text()` using a Windows CJK font:
```python
from PIL import Image, ImageDraw, ImageFont
scale = 300 / 72  # DPI conversion
font = ImageFont.truetype("C:/Windows/Fonts/simhei.ttf", size=30)
draw = ImageDraw.Draw(img)
upper = int(bbox[1] * scale) - 15  # y0 from top
lower = int(bbox[3] * scale) + 30  # y1 + buffer
draw.rectangle([300, upper, 1900, lower], fill=(255, 255, 255))
draw.text((310, upper + 5), "新文字内容", fill=(0, 0, 0), font=font)
```

4. Replace the original page with the image:
```python
new_pix = pymupdf.Pixmap(img_bytes)
new_page = doc.new_page(-1, width=page.rect.width, height=page.rect.height)
new_page.insert_image(new_page.rect, pixmap=new_pix)
doc.delete_page(page_index)
doc.move_page(doc.page_count - 1, page_index)
```

**When to use image-based editing:**
- CJK Chinese/Japanese/Korean text in WPS/Word-generated PDFs
- PDFs where text extraction shows garbled content
- Documents with embedded images that must be preserved alongside text changes
- Any PDF where `insert_text(fontfile=...)` produces unreadable output

**When to avoid image-based editing:**
- The output file is 20-30x larger (each page becomes a high-res image)
- Text is no longer selectable/searchable (rasterized)
- You need to preserve the original vector quality for printing
