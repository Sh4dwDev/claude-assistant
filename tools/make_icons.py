"""Generates the app icons (a thin purple ring with a dot) with no dependencies.
Run:  python tools/make_icons.py"""
import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "hub" / "static" / "icons"
BG = (10, 10, 12)
ACCENT = (154, 134, 255)


def png(path: Path, size: int, scale: float = 1.0, bg=True, mono=False) -> None:
    c = size / 2
    r_ring, w_ring, r_dot = size * 0.26 * scale, size * 0.035 * scale, size * 0.085 * scale
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            # 4x supersampling for smooth edges
            cov_ring = cov_dot = 0
            for sy in (0.125, 0.375, 0.625, 0.875):
                for sx in (0.125, 0.375, 0.625, 0.875):
                    d = math.hypot(x + sx - c, y + sy - c)
                    if abs(d - r_ring) <= w_ring / 2:
                        cov_ring += 1
                    if d <= r_dot:
                        cov_dot += 1
            a = min(16, cov_ring + cov_dot) / 16
            fg = (255, 255, 255) if mono else ACCENT
            if bg:
                px = tuple(round(BG[i] * (1 - a) + fg[i] * a) for i in range(3)) + (255,)
            else:
                px = fg + (round(a * 255),)
            row += bytes(px)
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) \
        + chunk(b"IDAT", raw) + chunk(b"IEND", b"")
    path.write_bytes(data)
    print("wrote", path.name)


SVG = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="14" fill="rgb{BG}"/>
<circle cx="32" cy="32" r="16.6" fill="none" stroke="rgb{ACCENT}" stroke-width="2.4"/>
<circle cx="32" cy="32" r="5.4" fill="rgb{ACCENT}"/>
</svg>
"""

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    png(OUT / "icon-192.png", 192)
    png(OUT / "icon-512.png", 512)
    png(OUT / "icon-maskable-512.png", 512, scale=0.8)
    png(OUT / "apple-touch-icon.png", 180)
    png(OUT / "badge-96.png", 96, scale=1.4, bg=False, mono=True)
    (OUT / "icon.svg").write_text(SVG.replace("rgb(", "rgb(").replace(", ", ","), encoding="utf-8")
    print("wrote icon.svg")
