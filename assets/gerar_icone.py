from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent / "icone.ico"
SIZES = [16, 24, 32, 48, 64, 128, 256]

BG = (18, 16, 30, 255)
BG_EDGE = (168, 85, 247, 90)
TOP = (200, 132, 252, 255)
LEFT = (168, 85, 247, 255)
RIGHT = (109, 40, 217, 255)
EDGE = (233, 213, 255, 235)

def render(canvas: int) -> Image.Image:
    img = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    pad = canvas * 0.02
    radius = canvas * 0.235
    d.rounded_rectangle(
        [pad, pad, canvas - pad, canvas - pad],
        radius=radius,
        fill=BG,
        outline=BG_EDGE,
        width=max(2, int(canvas * 0.012)),
    )

    cx = cy = canvas / 2
    r = canvas * 0.30
    sx = r * math.sin(math.radians(60))
    hy = r / 2

    p_top = (cx, cy - r)
    p_ur = (cx + sx, cy - hy)
    p_lr = (cx + sx, cy + hy)
    p_bot = (cx, cy + r)
    p_ll = (cx - sx, cy + hy)
    p_ul = (cx - sx, cy - hy)
    p_c = (cx, cy)

    d.polygon([p_ul, p_top, p_ur, p_c], fill=TOP)
    d.polygon([p_ul, p_c, p_bot, p_ll], fill=LEFT)
    d.polygon([p_ur, p_lr, p_bot, p_c], fill=RIGHT)

    w = max(2, int(canvas * 0.018))
    d.line([p_top, p_ur, p_lr, p_bot, p_ll, p_ul, p_top], fill=EDGE, width=w, joint="curve")
    d.line([p_ul, p_c], fill=EDGE, width=w)
    d.line([p_ur, p_c], fill=EDGE, width=w)
    d.line([p_bot, p_c], fill=EDGE, width=w)
    return img

def main() -> None:
    master = render(1024)
    frames = [master.resize((s, s), Image.LANCZOS) for s in reversed(SIZES)]
    frames[0].save(OUT, format="ICO", sizes=[(s, s) for s in SIZES], append_images=frames[1:])
    print(f"OK -> {OUT}  ({OUT.stat().st_size} bytes)  tamanhos={SIZES}")

if __name__ == "__main__":
    main()
