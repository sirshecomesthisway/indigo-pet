"""Align a set of sprite frames to match a reference sprite's body anchor.

The problem this solves: when AI-generated sprites are dropped in as-is,
the body drifts between frames AND drifts from the canonical idle/celebrating
position. This script:

  1. Measures the BODY (not flag/tentacle) bounding box in the reference.
  2. Measures the body bounding box in each frame.
  3. Scales each frame so its body width matches the reference body width.
  4. Pads to the reference canvas size, anchoring body-bottom-center to
     the reference body-bottom-center.

Body detection heuristic: find the widest row of non-transparent pixels --
that is the body's widest band (the mantle). Then walk down from there to
find the bottom of opaque content (ignoring above the widest band, which
is where the flag and raised tentacles live).

Usage:
    uv run --no-project python tools/align_to_reference.py \\
        --reference src/squid_pet/frontend/sprites/idle.png \\
        --output-dir src/squid_pet/frontend/sprites \\
        --output-prefix attention_needed \\
        FRAME1 FRAME2 FRAME3 FRAME4
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Tuple

from PIL import Image


ALPHA_THRESHOLD = 16


def opaque_mask_rows(img: Image.Image) -> List[Tuple[int, int, int]]:
    """Return list of (y, x_min, x_max) for each row containing opaque pixels."""
    assert img.mode == "RGBA"
    w, h = img.size
    px = img.load()
    rows: List[Tuple[int, int, int]] = []
    for y in range(h):
        x_min, x_max = w, -1
        for x in range(w):
            if px[x, y][3] > ALPHA_THRESHOLD:
                if x < x_min:
                    x_min = x
                if x > x_max:
                    x_max = x
        if x_max >= 0:
            rows.append((y, x_min, x_max))
    return rows


def body_anchor(img: Image.Image) -> Tuple[int, int, int]:
    """Return (body_width, body_bottom_center_x, body_bottom_y)."""
    rows = opaque_mask_rows(img)
    if not rows:
        raise ValueError("image has no opaque pixels")

    widest_y = max(rows, key=lambda r: r[2] - r[1])[0]
    widest_width = max(r[2] - r[1] for r in rows)
    body_min_width = int(widest_width * 0.30)

    body_rows = [r for r in rows if r[0] >= widest_y and (r[2] - r[1]) >= body_min_width]
    if not body_rows:
        body_rows = [r for r in rows if r[0] >= widest_y]

    bottom_y, bx_min, bx_max = body_rows[-1]
    body_bottom_center_x = (bx_min + bx_max) // 2

    return widest_width, body_bottom_center_x, bottom_y


def frame_overhead(img: Image.Image) -> int:
    """Vertical extent above the body anchor (i.e. raised arm / flag region)."""
    rows = opaque_mask_rows(img)
    if not rows:
        return 0
    _, _, anchor_y = body_anchor(img)
    top_y = rows[0][0]
    return anchor_y - top_y


def compute_shared_scale(
    frames: List[Image.Image],
    ref_anchor_y: int,
    margin_px: int = 8,
) -> float:
    """Pick a single scale that lets the tallest frame fit above the anchor."""
    max_overhead = max(frame_overhead(f) for f in frames)
    if max_overhead == 0:
        return 1.0
    available = max(1, ref_anchor_y - margin_px)
    return available / max_overhead


def align_frame(
    frame: Image.Image,
    ref_canvas_size: Tuple[int, int],
    ref_anchor: Tuple[int, int],
    scale: float,
) -> Image.Image:
    """Scale + reposition frame to match the reference's body anchor."""
    new_w = max(1, int(round(frame.width * scale)))
    new_h = max(1, int(round(frame.height * scale)))
    scaled = frame.resize((new_w, new_h), Image.Resampling.NEAREST)

    _, sf_anchor_x, sf_anchor_y = body_anchor(scaled)

    paste_x = ref_anchor[0] - sf_anchor_x
    paste_y = ref_anchor[1] - sf_anchor_y

    canvas = Image.new("RGBA", ref_canvas_size, (0, 0, 0, 0))
    canvas.paste(scaled, (paste_x, paste_y), scaled)
    return canvas


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--output-prefix", required=True)
    parser.add_argument("frames", nargs="+", type=Path)
    args = parser.parse_args()

    ref = Image.open(args.reference).convert("RGBA")
    ref_body_width, ref_x, ref_y = body_anchor(ref)
    print(f"Reference: {args.reference.name}  size={ref.size}")
    print(f"   body width={ref_body_width}  anchor=({ref_x}, {ref_y})")

    args.output_dir.mkdir(parents=True, exist_ok=True)

    frames = [Image.open(p).convert("RGBA") for p in args.frames]
    shared_scale = compute_shared_scale(frames, ref_y)
    print(f"Shared scale across {len(frames)} frames: {shared_scale:.3f}x")
    sample_body_width = int(body_anchor(frames[0])[0] * shared_scale)
    print(f"   scaled body width ~= {sample_body_width}  (reference body width = {ref_body_width})")

    for i, (frame_path, frame) in enumerate(zip(args.frames, frames), start=1):
        fbw, fx, fy = body_anchor(frame)
        print(f"Frame {i}: {frame_path.name}  body_w={fbw}  anchor=({fx},{fy})  overhead={frame_overhead(frame)}")

        aligned = align_frame(frame, ref.size, (ref_x, ref_y), shared_scale)

        out_path = args.output_dir / f"{args.output_prefix}_{i}.png"
        aligned.save(out_path, optimize=True)
        print(f"   wrote {out_path}")

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
