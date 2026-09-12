"""Generate icon sizes from the master K logo for the Kairos UI.

Usage:
    python web/public/branding/_build_icons.py <master-logo-image>

The master logo is NOT in the repo (it is a design source file), so the path
is passed in instead of being hardcoded to the author's machine.
"""
import sys
from pathlib import Path
from PIL import Image, ImageOps

if len(sys.argv) < 2:
    raise SystemExit(__doc__.strip().splitlines()[-1].strip())
SRC = Path(sys.argv[1]).expanduser()
if not SRC.is_file():
    raise SystemExit(f"master logo not found: {SRC}")
OUT_DIR = Path(__file__).resolve().parent
OUT_DIR.mkdir(parents=True, exist_ok=True)

# The source is 1456x1456 (square). Crop the rounded shape if
# possible. PIL opens the JPEG and we just resize — the icon is
# already circular so we don't need to do additional masking.
img = Image.open(SRC).convert("RGBA")

# Square-crop (in case the source isn't quite square)
w, h = img.size
side = min(w, h)
img = ImageOps.fit(img, (side, side), method=Image.LANCZOS, centering=(0.5, 0.5))

# Generate PNG sizes
for size, name in [
    (16, "favicon-16.png"),
    (32, "favicon-32.png"),
    (48, "favicon-48.png"),
    (64, "kairos-icon-64.png"),
    (128, "kairos-icon-128.png"),
    (256, "kairos-icon-256.png"),
    (512, "kairos-icon-512.png"),
]:
    resized = img.resize((size, size), Image.LANCZOS)
    out_path = OUT_DIR / name
    resized.save(out_path, "PNG", optimize=True)
    print(f"wrote {name}: {size}x{size}  ({out_path.stat().st_size} bytes)")

# Also save a small "favicon.ico" multi-size ico for legacy
# browsers (AntD's setup may not need it but covers all cases)
favicon_ico = OUT_DIR / "favicon.ico"
favicon_ico_base = img.resize((64, 64), Image.LANCZOS)
favicon_ico_base.save(
    favicon_ico,
    format="ICO",
    sizes=[(16, 16), (32, 32), (48, 48), (64, 64)],
)
print(f"wrote favicon.ico: multi-size  ({favicon_ico.stat().st_size} bytes)")

# Save the canonical 64x64 as favicon.png for the index.html
# <link rel="icon" type="image/png"> tag.
img.resize((64, 64), Image.LANCZOS).save(OUT_DIR / "favicon.png", "PNG", optimize=True)
print(f"wrote favicon.png: 64x64  ({(OUT_DIR / 'favicon.png').stat().st_size} bytes)")
