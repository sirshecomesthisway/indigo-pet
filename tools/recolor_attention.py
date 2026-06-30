"""Post-process attention_needed frames so their palette matches idle.png.

Two fixes applied:

1. Body recolor: hot-pink (#F85878 family) -> dusty pink (#E07088 family).
   We multiply saturation and value down but PRESERVE hue (idle and v1
   both sit at hue ~347deg; the difference is purely S/V, not H). Only
   pink-hue pixels are touched; yellow flag/sparkles and dark pupils
   are unaffected.

2. Eye sclera: Gemini drew transparent holes around the pupils instead
   of real cream-white pixels (verified by pixel sampling -- the
   "white" you see in an image viewer is the viewer's background
   showing through the transparent eye sockets, NOT actual sclera).
   Fix: flood-fill from the canvas corners to mark "external transparent",
   then every transparent pixel NOT reachable from outside is an INTERNAL
   hole (eye socket) and gets filled with idle's cream #F0F0E0.

Idempotent: re-running on already-processed frames is safe.

Usage: python tools/recolor_attention.py
"""

from __future__ import annotations

import colorsys
from collections import deque
from pathlib import Path

from PIL import Image

SPRITES_DIR = Path(__file__).resolve().parent.parent / "src" / "squid_pet" / "frontend" / "sprites"
FRAMES = ["attention_needed.png"] + [f"attention_needed_{i}.png" for i in (1, 2, 3, 4)]

TARGET_CREAM = (240, 240, 224)   # #F0F0E0 -- idle.png's sclera color

# Hue band for "pink body" pixels (degrees). idle hue ~347, v1 hue ~348,
# wide enough to catch shaded variants but narrow enough to exclude
# yellow flag (~52 deg) and any orange-ish edges.
PINK_HUE_LO = 320.0
PINK_HUE_HI = 360.0
PINK_SAT_MIN = 0.30  # ignore near-grey pixels

# Multipliers derived from comparing idle.png to attention v1:
#   idle body avg HSV: H=347, S=0.50, V=0.88
#   v1   body avg HSV: H=348, S=0.65, V=0.97
# So: S_mult = 0.50/0.65 = 0.77, V_mult = 0.88/0.97 = 0.91
S_MULT = 0.77
V_MULT = 0.91

# Alpha threshold below which a pixel counts as "transparent" for
# flood-fill purposes. Anti-aliased edges with low alpha are treated
# as inside the body to avoid bleeding cream through outline pixels.
ALPHA_TRANSPARENT_MAX = 50


def rgb_to_hsv_deg(r: int, g: int, b: int) -> tuple[float, float, float]:
    h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
    return h * 360.0, s, v


def hsv_to_rgb_int(h_deg: float, s: float, v: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb(h_deg / 360.0, s, v)
    return int(round(r * 255)), int(round(g * 255)), int(round(b * 255))


def is_pink(r: int, g: int, b: int) -> bool:
    h, s, _ = rgb_to_hsv_deg(r, g, b)
    if s < PINK_SAT_MIN:
        return False
    return PINK_HUE_LO <= h <= PINK_HUE_HI


def recolor_body(img: Image.Image) -> Image.Image:
    """Pull saturation + value down on all pink-hue pixels, keeping
    their original hue so the body matches idle's dusty pink palette."""
    out = img.copy()
    px_in = img.load()
    px_out = out.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px_in[x, y]
            if a < 50 or not is_pink(r, g, b):
                continue
            h_deg, s, v = rgb_to_hsv_deg(r, g, b)
            nr, ng, nb = hsv_to_rgb_int(h_deg, s * S_MULT, v * V_MULT)
            px_out[x, y] = (nr, ng, nb, a)
    return out


def fill_internal_holes(img: Image.Image, fill_rgb: tuple[int, int, int]) -> tuple[Image.Image, int]:
    """Find all transparent pixels NOT reachable by flood-fill from the
    image corners, and fill them with fill_rgb at full alpha.

    Why corners: an external transparent pixel can always be reached by
    walking through transparent neighbors back to the canvas border.
    Eye-socket holes are surrounded by opaque body pixels on all sides,
    so they're unreachable from the corners.
    """
    w, h = img.size
    px_in = img.load()
    # Boolean mask: True = transparent pixel (alpha <= threshold)
    transparent = [[False] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            if px_in[x, y][3] <= ALPHA_TRANSPARENT_MAX:
                transparent[y][x] = True

    # BFS flood-fill from all 4 corners through transparent pixels only.
    reached = [[False] * w for _ in range(h)]
    queue: deque[tuple[int, int]] = deque()
    for sx, sy in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        if transparent[sy][sx]:
            queue.append((sx, sy))
            reached[sy][sx] = True
    # 4-connectivity is sufficient for this kind of cutout
    while queue:
        x, y = queue.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and transparent[ny][nx] and not reached[ny][nx]:
                reached[ny][nx] = True
                queue.append((nx, ny))

    # Paint internal-hole pixels (transparent but unreached) with cream.
    out = img.copy()
    px_out = out.load()
    filled = 0
    for y in range(h):
        for x in range(w):
            if transparent[y][x] and not reached[y][x]:
                px_out[x, y] = (*fill_rgb, 255)
                filled += 1
    return out, filled


def process(path: Path) -> None:
    print(f"  processing {path.name} ...", end=" ", flush=True)
    img = Image.open(path).convert("RGBA")
    img = recolor_body(img)
    img, filled = fill_internal_holes(img, TARGET_CREAM)
    img.save(path)
    print(f"done (filled {filled} hole pixels with cream)")


def main() -> None:
    print(f"recoloring {len(FRAMES)} frames in {SPRITES_DIR}")
    for name in FRAMES:
        p = SPRITES_DIR / name
        if not p.exists():
            print(f"  SKIP {name} (not found)")
            continue
        process(p)
    print("done.")


if __name__ == "__main__":
    main()
