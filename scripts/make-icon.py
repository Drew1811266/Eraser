"""Generate the app icon.

A build artifact, not a design file: keeping it in a script means the icon is
reproducible from the repository and nobody has to hunt for a binary when the
brand colour changes.

    python scripts/make-icon.py      # writes desktop/build/icon.ico (+ .png)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "desktop" / "build"
SIZE = 512
SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]

TOP = (94, 157, 255)
BOTTOM = (47, 107, 216)


def rounded_gradient(size: int, radius: int) -> Image.Image:
    """A vertical gradient clipped to a rounded square."""
    gradient = Image.new("RGBA", (size, size))
    for y in range(size):
        mix = y / max(1, size - 1)
        colour = tuple(round(TOP[i] + (BOTTOM[i] - TOP[i]) * mix) for i in range(3))
        for x in range(size):
            gradient.putpixel((x, y), (*colour, 255))

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(gradient, (0, 0), mask)
    return out


def draw_eraser(size: int) -> Image.Image:
    """A tilted eraser block with its shadow band, the way the tool reads."""
    image = rounded_gradient(size, radius=round(size * 0.22))
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    body = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    body_draw = ImageDraw.Draw(body)
    left = round(size * 0.24)
    top = round(size * 0.34)
    width = round(size * 0.52)
    height = round(size * 0.30)
    radius = round(size * 0.05)
    body_draw.rounded_rectangle((left, top, left + width, top + height), radius=radius, fill=(255, 255, 255, 255))
    # The rubber tip, a slightly greyer band on the lower third.
    band_top = top + round(height * 0.62)
    body_draw.rounded_rectangle(
        (left, band_top, left + width, top + height), radius=radius, fill=(214, 228, 255, 255)
    )
    body = body.rotate(-32, resample=Image.BICUBIC, center=(size / 2, size / 2))
    layer.alpha_composite(body)

    # Two "erased" specks, so the icon reads as removing something.
    draw.ellipse(
        (round(size * 0.60), round(size * 0.62), round(size * 0.70), round(size * 0.72)),
        fill=(255, 255, 255, 210),
    )
    draw.ellipse(
        (round(size * 0.70), round(size * 0.52), round(size * 0.76), round(size * 0.58)),
        fill=(255, 255, 255, 150),
    )
    image.alpha_composite(layer)
    return image


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    master = draw_eraser(SIZE)
    master.save(OUT_DIR / "icon.png")

    icon_sizes = [size for size in SIZES]
    master.save(OUT_DIR / "icon.ico", sizes=icon_sizes)
    print(f"wrote {OUT_DIR / 'icon.ico'}  ({', '.join(f'{w}x{h}' for w, h in icon_sizes)})")
    print(f"wrote {OUT_DIR / 'icon.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
