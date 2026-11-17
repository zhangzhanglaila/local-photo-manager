"""Generate the application icon (multi-size .ico) for the packaged build."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path("packaging") / "app.ico"
PNG_OUT = Path("packaging") / "app.png"
SIZE = 1024


def _rounded(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = size // 16
    radius = size // 5

    # Background: a soft vertical gradient inside a rounded square.
    bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad = ImageDraw.Draw(bg)
    for y in range(size):
        t = y / max(1, size - 1)
        r = int(64 + (24 - 64) * t)
        g = int(132 + (90 - 132) * t)
        b = int(214 + (168 - 214) * t)
        grad.line([(0, y), (size, y)], fill=(r, g, b, 255))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [margin, margin, size - margin, size - margin], radius=radius, fill=255
    )
    img.paste(bg, (0, 0), mask)

    # Photo frame.
    frame = [
        int(size * 0.24),
        int(size * 0.30),
        int(size * 0.76),
        int(size * 0.70),
    ]
    draw.rounded_rectangle(frame, radius=size // 24, fill=(255, 255, 255, 240))
    inner = [
        frame[0] + size // 40,
        frame[1] + size // 40,
        frame[2] - size // 40,
        frame[3] - size // 40,
    ]
    draw.rounded_rectangle(inner, radius=size // 40, fill=(236, 244, 255, 255))

    # Mountain + sun inside the frame.
    draw.polygon(
        [
            (inner[0], inner[3]),
            (inner[0] + (inner[2] - inner[0]) * 0.38, inner[1] + (inner[3] - inner[1]) * 0.45),
            (inner[0] + (inner[2] - inner[0]) * 0.62, inner[3]),
        ],
        fill=(74, 144, 217, 255),
    )
    draw.polygon(
        [
            (inner[0] + (inner[2] - inner[0]) * 0.45, inner[3]),
            (inner[0] + (inner[2] - inner[0]) * 0.70, inner[1] + (inner[3] - inner[1]) * 0.58),
            (inner[2], inner[3]),
        ],
        fill=(52, 116, 186, 255),
    )
    sun_r = size // 22
    sun_cx = inner[0] + (inner[2] - inner[0]) * 0.74
    sun_cy = inner[1] + (inner[3] - inner[1]) * 0.30
    draw.ellipse(
        [sun_cx - sun_r, sun_cy - sun_r, sun_cx + sun_r, sun_cy + sun_r],
        fill=(255, 198, 74, 255),
    )
    return img


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    big = _rounded(SIZE)
    big.save(PNG_OUT, format="PNG")
    sizes = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]
    big.save(OUT, format="ICO", sizes=sizes)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes) and {PNG_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
