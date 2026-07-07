from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"


def rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int, fill: str) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def make_icon(size: int) -> Image.Image:
    scale = size / 256

    def v(value: int) -> int:
        return round(value * scale)

    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    rounded(draw, (v(16), v(18), v(240), v(238)), v(42), "#10233f")
    rounded(draw, (v(34), v(46), v(222), v(218)), v(22), "#f8fbff")
    rounded(draw, (v(34), v(46), v(222), v(88)), v(22), "#2563eb")
    draw.rectangle((v(34), v(68), v(222), v(92)), fill="#2563eb")

    for x in (75, 181):
        rounded(draw, (v(x), v(28), v(x + 20), v(64)), v(8), "#dbeafe")

    grid_color = "#d8e2ef"
    for x in (82, 128, 174):
        draw.line((v(x), v(108), v(x), v(199)), fill=grid_color, width=max(1, v(3)))
    for y in (134, 162, 190):
        draw.line((v(54), v(y), v(202), v(y)), fill=grid_color, width=max(1, v(3)))

    rounded(draw, (v(56), v(112), v(118), v(128)), v(8), "#facc15")
    rounded(draw, (v(124), v(140), v(200), v(156)), v(8), "#ef4444")
    rounded(draw, (v(80), v(168), v(176), v(184)), v(8), "#22c55e")

    star = [
        (128, 38),
        (137, 60),
        (161, 60),
        (142, 74),
        (149, 98),
        (128, 84),
        (107, 98),
        (114, 74),
        (95, 60),
        (119, 60),
    ]
    draw.polygon([(v(x), v(y)) for x, y in star], fill="#facc15")

    return image


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [make_icon(size) for size in sizes]
    images[-1].save(ASSETS / "app.ico", sizes=[(size, size) for size in sizes], append_images=images[:-1])
    make_icon(256).save(ASSETS / "app.png")


if __name__ == "__main__":
    main()
