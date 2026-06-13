"""
remove_bg.py — flood-fill alpha-removal for Squid's sprite art.

The originals shipped by the artwork tool have a solid (or near-solid)
background fill the entire canvas. This script walks in from the four
corners with a colour-tolerance flood-fill and sets matching pixels'
alpha channel to 0, leaving the foreground subject intact.

Recipe:
1. Open PNG, force RGBA mode.
2. Sample the 4 corner pixels (assumed to be background).
3. Run a flood-fill from each corner with a Euclidean-RGB tolerance.
4. For every visited pixel, set alpha = 0.
5. Save in place (and optionally back up the original first).

Usage:
    python tools/remove_bg.py path/to/sprite.png
    python tools/remove_bg.py sprites/idle.png --tolerance 25
    python tools/remove_bg.py sprites/*.png --backup-to sprites/_originals_with_bg
    python tools/remove_bg.py sprites/ --recursive

Verify after:
    python tools/remove_bg.py --verify sprites/idle.png
    (prints alpha values for each corner; all should be 0)
"""
from __future__ import annotations

import argparse
import sys
from collections import deque
from pathlib import Path
from typing import Iterable

try:
    from PIL import Image
except ImportError:
    sys.stderr.write(
        "ERROR: Pillow is required. Install with: uv pip install pillow\n"
    )
    sys.exit(2)


DEFAULT_TOLERANCE = 30   # Euclidean distance in RGB-space (0-441 max).
NEIGHBOUR_OFFSETS = ((-1, 0), (1, 0), (0, -1), (0, 1))


def _color_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """Euclidean distance in RGB-space (alpha ignored)."""
    return (
        (a[0] - b[0]) ** 2
        + (a[1] - b[1]) ** 2
        + (a[2] - b[2]) ** 2
    ) ** 0.5


def _flood_fill_alpha_zero(
    img: Image.Image,
    seed_xy: tuple[int, int],
    tolerance: float,
) -> int:
    """Flood-fill from `seed_xy`. Pixels whose RGB is within `tolerance` of
    the seed pixel get their alpha set to 0. Returns the number of pixels
    cleared."""
    pixels = img.load()
    w, h = img.size
    seed_rgb = pixels[seed_xy][:3]

    visited: set[tuple[int, int]] = set()
    cleared = 0
    queue: deque[tuple[int, int]] = deque([seed_xy])

    while queue:
        x, y = queue.popleft()
        if (x, y) in visited:
            continue
        if x < 0 or x >= w or y < 0 or y >= h:
            continue
        visited.add((x, y))

        r, g, b, _a = pixels[x, y]
        if _color_distance((r, g, b), seed_rgb) > tolerance:
            continue

        pixels[x, y] = (r, g, b, 0)
        cleared += 1

        for dx, dy in NEIGHBOUR_OFFSETS:
            queue.append((x + dx, y + dy))

    return cleared


def remove_bg_from_corners(
    img_path: Path,
    tolerance: float = DEFAULT_TOLERANCE,
    backup_dir: Path | None = None,
) -> tuple[int, int]:
    """Open `img_path`, flood-fill from all 4 corners, save in place.

    Returns (total_pixels_cleared, total_pixels)."""
    if backup_dir is not None:
        backup_dir.mkdir(parents=True, exist_ok=True)
        dest = backup_dir / img_path.name
        if not dest.exists():
            Image.open(img_path).save(dest)

    img = Image.open(img_path).convert("RGBA")
    w, h = img.size
    corners = ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))

    total_cleared = 0
    for corner in corners:
        total_cleared += _flood_fill_alpha_zero(img, corner, tolerance)

    img.save(img_path)
    return total_cleared, w * h


def verify_corners(img_path: Path) -> dict[str, int]:
    """Print + return alpha values at each corner. All should be 0 if the
    background was successfully removed."""
    img = Image.open(img_path).convert("RGBA")
    w, h = img.size
    corners = {
        "top-left":     (0, 0),
        "top-right":    (w - 1, 0),
        "bottom-left":  (0, h - 1),
        "bottom-right": (w - 1, h - 1),
    }
    pixels = img.load()
    result = {name: pixels[xy][3] for name, xy in corners.items()}
    return result


def _iter_pngs(paths: Iterable[Path], recursive: bool) -> Iterable[Path]:
    for p in paths:
        if p.is_dir():
            pattern = "**/*.png" if recursive else "*.png"
            yield from sorted(p.glob(pattern))
        else:
            yield p


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="remove_bg.py",
        description="Flood-fill alpha-removal for sprite PNGs.",
    )
    ap.add_argument("paths", nargs="+", type=Path,
                    help="PNG files or directories containing PNGs.")
    ap.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                    help=f"RGB Euclidean tolerance (default {DEFAULT_TOLERANCE}).")
    ap.add_argument("--backup-to", type=Path, default=None,
                    help="Copy each original PNG here before modifying.")
    ap.add_argument("--recursive", action="store_true",
                    help="Recurse into directory arguments.")
    ap.add_argument("--verify", action="store_true",
                    help="Don't modify -- just print corner alpha values.")
    args = ap.parse_args(argv)

    pngs = list(_iter_pngs(args.paths, args.recursive))
    if not pngs:
        sys.stderr.write("No PNG files found.\n")
        return 1

    if args.verify:
        for p in pngs:
            alphas = verify_corners(p)
            verdict = "OK" if all(a == 0 for a in alphas.values()) else "FAIL"
            print(f"[{verdict}] {p.name}: {alphas}")
        return 0

    for p in pngs:
        cleared, total = remove_bg_from_corners(
            p, tolerance=args.tolerance, backup_dir=args.backup_to,
        )
        pct = 100.0 * cleared / total if total else 0.0
        print(f"  {p.name}: cleared {cleared}/{total} px ({pct:.1f}%)")

    return 0


if __name__ == "__main__":
    sys.exit(_main())
