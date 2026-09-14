#!/usr/bin/env python3
"""
Generate the extension's toolbar icons.

Kept as source rather than committed binaries so the icon can be re-tinted by
editing two numbers. Run it from this folder:

    python make_icons.py
"""

import os
import struct
import zlib

BG = (47, 111, 235)        # same accent blue as the app
FG = (255, 255, 255)
SIZES = (16, 32, 48, 128)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")


def write_png(path, size, rows):
    raw = b"".join(b"\x00" + bytes(row) for row in rows)

    def chunk(tag, payload):
        body = tag + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    with open(path, "wb") as fh:
        fh.write(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b"")
        )


def in_rounded_square(x, y, n):
    r = n * 0.22
    if r <= x <= n - r or r <= y <= n - r:
        return 0 <= x <= n and 0 <= y <= n
    cx = r if x < r else n - r
    cy = r if y < r else n - r
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def in_arrow(x, y, n):
    # Shaft
    if 0.42 * n <= x <= 0.58 * n and 0.20 * n <= y <= 0.54 * n:
        return True
    # Head: a triangle narrowing to a point at 0.80 of the height
    top, tip = 0.50 * n, 0.80 * n
    if top <= y <= tip:
        half = 0.26 * n * (tip - y) / (tip - top)
        return abs(x - 0.5 * n) <= half
    return False


def build(n):
    rows = []
    for py in range(n):
        row = []
        for px in range(n):
            # Sample at pixel centres so edges land predictably.
            x, y = px + 0.5, py + 0.5
            if not in_rounded_square(x, y, n):
                row += [0, 0, 0, 0]
            elif in_arrow(x, y, n):
                row += [FG[0], FG[1], FG[2], 255]
            else:
                row += [BG[0], BG[1], BG[2], 255]
        rows.append(row)
    return rows


def write_ico(path, pngs):
    """Wrap PNG blobs in an .ico container (PNG-in-ICO is valid from Vista on).

    The packaged exe and the installer both want an .ico; this saves keeping a
    separate binary in the repo.
    """
    header = struct.pack("<HHH", 0, 1, len(pngs))
    entries = b""
    blobs = b""
    offset = 6 + 16 * len(pngs)
    for size, blob in pngs:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        blobs += blob
        offset += len(blob)
    with open(path, "wb") as fh:
        fh.write(header + entries + blobs)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    pngs = []
    for n in SIZES:
        path = os.path.join(OUT, f"icon{n}.png")
        write_png(path, n, build(n))
        print(f"wrote {path}")
        with open(path, "rb") as fh:
            pngs.append((n, fh.read()))
    ico = os.path.join(OUT, "icon.ico")
    write_ico(ico, pngs)
    print(f"wrote {ico}")
