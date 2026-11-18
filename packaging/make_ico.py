"""Write a Windows-friendly .ico by hand.

Pillow's ICO writer produced files that PIL could read but the Windows shell refused
(pointed the shortcut at our .ico and Explorer still fell back to the generic document
icon, while a system DLL icon displayed fine). The shell expects classic DIB frames with
a proper BITMAPINFOHEADER (double height) plus a 1-bit AND mask; the 256px frame may be
PNG.

Usage: python packaging/make_ico.py <source.png> <out.ico>
"""

from __future__ import annotations

import io
import struct
import sys
from pathlib import Path

from PIL import Image

DIB_SIZES = (16, 24, 32, 48, 64, 128)
PNG_SIZE = 256


def _dib_frame(image: Image.Image, size: int) -> bytes:
    rgba = image.convert("RGBA").resize((size, size), Image.LANCZOS)
    pixels = rgba.load()

    # BITMAPINFOHEADER: height is doubled because the mask follows the colour data.
    header = struct.pack(
        "<IiiHHIIiiII",
        40,            # biSize
        size,          # biWidth
        size * 2,      # biHeight
        1,             # biPlanes
        32,            # biBitCount
        0,             # biCompression = BI_RGB
        size * size * 4,  # biSizeImage
        0, 0, 0, 0,
    )

    rows = []
    for y in range(size - 1, -1, -1):  # bottom-up
        row = bytearray()
        for x in range(size):
            r, g, b, a = pixels[x, y]
            row += bytes((b, g, r, a))
        rows.append(bytes(row))
    xor = b"".join(rows)

    # AND mask: 1 bit per pixel, rows padded to 32 bits, 0 = opaque.
    mask_row = ((size + 31) // 32) * 4
    mask = b"\x00" * (mask_row * size)

    return header + xor + mask


def _png_frame(image: Image.Image, size: int) -> bytes:
    buf = io.BytesIO()
    image.convert("RGBA").resize((size, size), Image.LANCZOS).save(buf, format="PNG")
    return buf.getvalue()


def build(source: Path, out: Path) -> None:
    image = Image.open(source)
    frames: list[tuple[int, bytes, bool]] = []
    for size in DIB_SIZES:
        frames.append((size, _dib_frame(image, size), False))
    frames.append((PNG_SIZE, _png_frame(image, PNG_SIZE), True))

    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries = bytearray()
    payload = bytearray()
    for size, data, is_png in frames:
        dimension = 0 if size >= 256 else size
        entries += struct.pack(
            "<BBBBHHII",
            dimension,
            dimension,
            0,
            0,
            1,
            32,
            len(data),
            offset + len(payload),
        )
        payload += data

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(header + bytes(entries) + bytes(payload))
    print(f"wrote {out} ({out.stat().st_size} bytes, {len(frames)} frames)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: make_ico.py <source.png> <out.ico>")
    build(Path(sys.argv[1]), Path(sys.argv[2]))
