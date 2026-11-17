"""
Generates placeholder PWA icons. Run from the project root:
    pip install pillow
    python scripts/make_placeholder_icons.py
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "static" / "icons"
TEAL = (13, 148, 136)
WHITE = (255, 255, 255)


def make_icon(size, filename):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = size // 5
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=TEAL)
    font = ImageFont.load_default(size=int(size * 0.6))
    draw.text((size / 2, size / 2), "H", font=font, fill=WHITE, anchor="mm")
    img.save(OUT / filename, "PNG")
    print(f"Wrote {OUT / filename}")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    make_icon(192, "icon-192.png")
    make_icon(512, "icon-512.png")
    make_icon(180, "apple-touch-icon.png")
