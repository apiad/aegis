"""Draw the app icons: an amber hexagon outline on Ink's background.

    uv run --with pillow python scripts/make_icons.py

Pillow is not a dependency of aegis; the PNGs are committed. The maskable icon
keeps the hexagon inside the central 80%, the safe zone a launcher may crop to.
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parents[1] / "src" / "aegis" / "client" / "icons"
BG, FG = "#11100e", "#e0a872"


def draw(size: int, scale: float) -> Image.Image:
    im = Image.new("RGB", (size, size), BG)
    r = size * scale / 2
    c = size / 2
    pts = [
        (
            c + r * math.cos(math.radians(60 * k - 90)),
            c + r * math.sin(math.radians(60 * k - 90)),
        )
        for k in range(6)
    ]
    ImageDraw.Draw(im).polygon(pts, outline=FG, width=max(2, size // 22))
    return im


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    draw(192, 0.72).save(OUT / "aegis-192.png")
    draw(512, 0.72).save(OUT / "aegis-512.png")
    draw(512, 0.56).save(OUT / "aegis-maskable-512.png")


if __name__ == "__main__":
    main()
