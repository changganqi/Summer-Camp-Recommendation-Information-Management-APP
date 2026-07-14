from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
WIDTH = 1920
HEIGHT = 1080


def add_texture(image: Image.Image, amount: int = 10) -> Image.Image:
    noise = Image.effect_noise(image.size, 7).convert("L")
    noise = ImageOps.colorize(noise, (105, 118, 118), (255, 255, 255)).convert("RGBA")
    noise.putalpha(amount)
    return Image.alpha_composite(image.convert("RGBA"), noise)


def make_mist() -> Image.Image:
    image = Image.new("RGBA", (WIDTH, HEIGHT), "#edf7f5")
    draw = ImageDraw.Draw(image)
    for y in range(HEIGHT):
        ratio = y / max(1, HEIGHT - 1)
        color = (
            int(242 - 22 * ratio),
            int(249 - 15 * ratio),
            int(247 - 13 * ratio),
            255,
        )
        draw.line((0, y, WIDTH, y), fill=color)

    layers = [
        (int(HEIGHT * 0.48), "#cbded9", 82, 0.009),
        (int(HEIGHT * 0.60), "#b8d0ca", 66, 0.012),
        (int(HEIGHT * 0.70), "#9dbbb3", 48, 0.016),
    ]
    for baseline, color, amplitude, frequency in layers:
        points = [(0, HEIGHT)]
        for x in range(0, WIDTH + 16, 16):
            ridge = baseline + math.sin(x * frequency) * amplitude + math.sin(x * frequency * 0.41) * amplitude * 0.35
            points.append((x, int(ridge)))
        points.extend([(WIDTH, HEIGHT), (0, HEIGHT)])
        draw.polygon(points, fill=color)

    for y in range(int(HEIGHT * 0.74), HEIGHT, 14):
        draw.line((0, y, WIDTH, y), fill=(235, 245, 242, 150), width=2)
    for x in range(80, WIDTH, 170):
        waterline = int(HEIGHT * 0.76)
        draw.arc((x, waterline, x + 120, waterline + 54), 190, 344, fill=(225, 239, 235, 130), width=2)
    return add_texture(image.filter(ImageFilter.GaussianBlur(0.7)), 9)


def make_night() -> Image.Image:
    image = Image.new("RGBA", (WIDTH, HEIGHT), "#17222d")
    draw = ImageDraw.Draw(image)
    for y in range(HEIGHT):
        ratio = y / max(1, HEIGHT - 1)
        color = (int(20 + 10 * ratio), int(31 + 16 * ratio), int(42 + 21 * ratio), 255)
        draw.line((0, y, WIDTH, y), fill=color)

    grid = (82, 111, 132, 44)
    for x in range(0, WIDTH, 64):
        draw.line((x, 0, x, HEIGHT), fill=grid)
    for y in range(18, HEIGHT, 42):
        draw.line((0, y, WIDTH, y), fill=grid)

    random.seed(20260710)
    stars: list[tuple[int, int]] = []
    for _ in range(52):
        x = random.randrange(20, WIDTH - 20)
        y = random.randrange(12, HEIGHT - 18)
        stars.append((x, y))
        radius = 1 if random.random() < 0.82 else 2
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(184, 211, 225, 115))
    for index in range(0, len(stars) - 2, 5):
        first = stars[index]
        second = stars[index + 1]
        draw.line((*first, *second), fill=(119, 158, 181, 52), width=1)
    return add_texture(image, 7)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    make_mist().convert("RGB").save(ASSETS / "theme_mist.png", quality=92)
    make_night().convert("RGB").save(ASSETS / "theme_night.png", quality=92)
    print("theme backgrounds ready")


if __name__ == "__main__":
    main()
