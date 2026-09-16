---
name: "image-compression"
description: "Compress JPEG/PNG images to a target file size (KB/MB) using Python/Pillow. Includes binary-search quality tuning, metadata stripping, progressive JPEG, and Windows-friendly paths."
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/media/image-compression/SKILL.md"
---
# Image Compression

Compress images to a target file size using Python/Pillow. Works on any OS with Python 3 + Pillow installed.

## When to Use

- User asks: "compress this image to X KB/MB"
- User asks: "reduce image file size"
- User sends an image that needs to be smaller for upload/sharing
- Image is too large (>100MB) — warn user, Pillow may OOM; suggest using CLI tools instead

## Preferred Approach: Pillow Binary Search (for JPEG)

Python + Pillow is available on every system without extra installs. Use binary search to find the optimal JPEG quality that hits the target size.

```python
from PIL import Image

img = Image.open(input_path)
target_size = target_kb * 1024
output_path = "compressed.jpg"

# First pass: strip metadata + optimize (lossless gain)
img.save(output_path, "JPEG", quality=95, optimize=True, progressive=True)

# Binary search for target size
lo, hi = 5, 95
best_q = 95  # fallback
best_diff = float('inf')

while lo <= hi:
    q = (lo + hi) // 2
    img.save(output_path, "JPEG", quality=q, optimize=True, progressive=True)
    size = os.path.getsize(output_path)
    diff = abs(size - target_size)
    if diff < best_diff:
        best_q = q
        best_diff = diff
    if size > target_size:
        hi = q - 1
    else:
        lo = q + 1

# Final save with best quality
img.save(output_path, "JPEG", quality=best_q, optimize=True, progressive=True)
```

## PNG Compression

PNG is trickier — Pillow's `optimize=True` is lossless but may not reduce enough. For aggressive PNG compression:
- Convert to PNG-8 (palette mode) if <=256 colors: `img.quantize(colors=256).save(...)`
- For larger reductions, suggest the user install `pngquant` or `oxipng`
- As a fallback, convert to JPEG if the user allows it

## Script Template

For request-response flow, write a self-contained Python script:

1. Read the image with `Image.open()`
2. Print original size & dimensions
3. Try `quality=95, optimize=True` first (lossless metadata strip)
4. Binary search if still over target
5. Print final quality, size, and output path

## Pitfalls

- **Pillow saves larger than original** at high quality (quality=95 with optimize can be bigger than the source JPEG). This is expected — the binary search will find a lower quality.
- **Transparency loss**: JPEG doesn't support alpha. If source PNG has transparency and user wants JPEG, warn them first.
- **Very large images** (>10000px or >100MB): Pillow loads into memory. Consider resizing first or using `--resize` / thumbnail approach.
- **EXIF orientation**: Pillow respects EXIF by default. If the image appears rotated wrong, use `ImageOps.exif_transpose(img)`.

## On Windows

- File paths: use raw strings `r"~"` or escaped backslashes
- MEDIA: paths work in Hermes to send images back to the user
- Output to user's Desktop: `r"~\Desktop\compressed.jpg"`
