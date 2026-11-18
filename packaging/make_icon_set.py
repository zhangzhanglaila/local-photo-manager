"""Generate several minimal icon concepts, a contact sheet, and matching .ico files."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path("packaging") / "icons"
S = 512  # working size for each concept
BLUE = (32, 120, 210, 255)
DARKBLUE = (21, 84, 152, 255)
LIGHT = (236, 244, 255, 255)
WHITE = (255, 255, 255, 255)
AMBER = (255, 190, 70, 255)
GREY = (150, 160, 175, 255)


def _canvas(bg=BLUE, radius_ratio=0.22) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    m = int(S * 0.06)
    d.rounded_rectangle([m, m, S - m, S - m], radius=int(S * radius_ratio), fill=bg)
    return img, d


def concept_photo() -> Image.Image:
    """Single photo: rounded frame + mountain + sun."""
    img, d = _canvas()
    f = [int(S * 0.20), int(S * 0.26), int(S * 0.80), int(S * 0.74)]
    d.rounded_rectangle(f, radius=int(S * 0.06), fill=WHITE)
    inner = [f[0] + S // 28, f[1] + S // 28, f[2] - S // 28, f[3] - S // 28]
    d.rounded_rectangle(inner, radius=int(S * 0.04), fill=LIGHT)
    w, h = inner[2] - inner[0], inner[3] - inner[1]
    d.polygon([(inner[0], inner[3]), (inner[0] + w * 0.40, inner[1] + h * 0.42), (inner[0] + w * 0.66, inner[3])], fill=BLUE)
    d.polygon([(inner[0] + w * 0.42, inner[3]), (inner[0] + w * 0.70, inner[1] + h * 0.58), (inner[2], inner[3])], fill=DARKBLUE)
    r = S // 26
    cx, cy = inner[0] + w * 0.74, inner[1] + h * 0.28
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=AMBER)
    return img


def concept_stack() -> Image.Image:
    """Two offset photos: a library, not a single picture."""
    img, d = _canvas()
    back = [int(S * 0.28), int(S * 0.18), int(S * 0.78), int(S * 0.62)]
    d.rounded_rectangle(back, radius=int(S * 0.05), fill=(255, 255, 255, 150))
    front = [int(S * 0.18), int(S * 0.32), int(S * 0.68), int(S * 0.76)]
    d.rounded_rectangle(front, radius=int(S * 0.05), fill=WHITE)
    inner = [front[0] + S // 30, front[1] + S // 30, front[2] - S // 30, front[3] - S // 30]
    d.rounded_rectangle(inner, radius=int(S * 0.03), fill=LIGHT)
    w, h = inner[2] - inner[0], inner[3] - inner[1]
    d.polygon([(inner[0], inner[3]), (inner[0] + w * 0.45, inner[1] + h * 0.45), (inner[2], inner[3])], fill=BLUE)
    r = S // 30
    d.ellipse([inner[2] - r * 2.2, inner[1] + r * 0.6, inner[2] - r * 0.2, inner[1] + r * 2.6], fill=AMBER)
    return img


def concept_timeline() -> Image.Image:
    """Vertical rail with ticks and a dot: the timeline scrubber."""
    img, d = _canvas()
    x = int(S * 0.42)
    d.rounded_rectangle([x - S // 60, int(S * 0.16), x + S // 60, int(S * 0.84)], radius=S // 60, fill=WHITE)
    for i in range(5):
        y = int(S * 0.24 + i * S * 0.13)
        d.rounded_rectangle([x + S // 16, y, x + S // 4, y + S // 34], radius=S // 68, fill=(255, 255, 255, 190))
    cy = int(S * 0.44)
    r = int(S * 0.10)
    d.ellipse([x - r, cy - r, x + r, cy + r], fill=AMBER)
    return img


def concept_aperture() -> Image.Image:
    """Camera aperture: suggests photography, very simple at small sizes."""
    img, d = _canvas()
    cx = cy = S // 2
    outer = int(S * 0.30)
    d.ellipse([cx - outer, cy - outer, cx + outer, cy + outer], outline=WHITE, width=int(S * 0.045))
    for i in range(6):
        import math

        a0 = math.radians(i * 60 + 15)
        a1 = a0 + math.radians(55)
        r0, r1 = int(S * 0.05), int(S * 0.245)
        d.polygon(
            [
                (cx + r0 * math.cos(a0), cy + r0 * math.sin(a0)),
                (cx + r1 * math.cos(a0), cy + r1 * math.sin(a0)),
                (cx + r1 * math.cos(a1), cy + r1 * math.sin(a1)),
            ],
            fill=(255, 255, 255, 120),
        )
    d.ellipse([cx - S // 22, cy - S // 22, cx + S // 22, cy + S // 22], fill=AMBER)
    return img


def concept_search() -> Image.Image:
    """Magnifier over a photo: browse/search the library."""
    img, d = _canvas()
    f = [int(S * 0.16), int(S * 0.20), int(S * 0.68), int(S * 0.64)]
    d.rounded_rectangle(f, radius=int(S * 0.05), fill=WHITE)
    inner = [f[0] + S // 32, f[1] + S // 32, f[2] - S // 32, f[3] - S // 32]
    d.rounded_rectangle(inner, radius=int(S * 0.03), fill=LIGHT)
    w, h = inner[2] - inner[0], inner[3] - inner[1]
    d.polygon([(inner[0], inner[3]), (inner[0] + w * 0.5, inner[1] + h * 0.5), (inner[2], inner[3])], fill=BLUE)
    cx, cy, r = int(S * 0.66), int(S * 0.66), int(S * 0.16)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=AMBER, width=int(S * 0.05))
    d.line([cx + r * 0.7, cy + r * 0.7, cx + r * 1.5, cy + r * 1.5], fill=AMBER, width=int(S * 0.055))
    return img


CONCEPTS = {
    "A-photo": concept_photo,
    "B-stack": concept_stack,
    "C-timeline": concept_timeline,
    "D-aperture": concept_aperture,
    "E-search": concept_search,
}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sizes = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]
    made: list[tuple[str, Image.Image]] = []
    for name, fn in CONCEPTS.items():
        big = fn()
        big.save(OUT_DIR / f"{name}.png", format="PNG")
        big.save(OUT_DIR / f"{name}.ico", format="ICO", sizes=sizes)
        made.append((name, big))

    # Contact sheet: one row, labelled, on a neutral background.
    pad, cell = 24, 256
    sheet = Image.new("RGB", (pad + len(made) * (cell + pad), cell + 64), (245, 246, 248))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arial.ttf", 22)
    except Exception:
        font = ImageFont.load_default()
    for i, (name, img) in enumerate(made):
        x = pad + i * (cell + pad)
        sheet.paste(img.resize((cell, cell), Image.LANCZOS), (x, pad), img.resize((cell, cell), Image.LANCZOS))
        draw.text((x + 6, cell + pad + 8), name, fill=(40, 45, 55), font=font)
    sheet.save(OUT_DIR / "contact-sheet.png", format="PNG")
    print("wrote", OUT_DIR / "contact-sheet.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
