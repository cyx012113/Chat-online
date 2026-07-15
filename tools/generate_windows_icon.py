"""Generate the multi-resolution Windows icon used by PyInstaller."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def render_icon(size: int = 256) -> Image.Image:
    scale = size / 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def box(coordinates: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        return tuple(round(value * scale) for value in coordinates)

    draw.rounded_rectangle(box((5, 7, 59, 50)), radius=round(11 * scale), fill="#2563EB")
    draw.rounded_rectangle(box((31, 30, 58, 54)), radius=round(8 * scale), fill="#16803C")
    draw.rounded_rectangle(box((15, 20, 40, 24)), radius=round(2 * scale), fill="#FFFFFF")
    draw.rounded_rectangle(box((15, 29, 33, 33)), radius=round(2 * scale), fill="#FFFFFF")
    return image


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    render_icon().save(
        args.output,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

