# -*- coding: utf-8 -*-
"""Формирует заготовку иконки приложения ``assets/app.ico``.

Это осознанная заготовка (простой значок «калькулятор» в фирменном зелёном
цвете), а не случайное изображение. Чтобы поставить свою иконку — замените
файл ``assets/app.ico`` (формат ICO, желательно размеры 16/32/48/256) и
пересоберите приложение. Скрипт нужен только для генерации заготовки.
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

ACCENT = (34, 160, 90, 255)      # --accent из style.css
ACCENT_DARK = (26, 128, 72, 255)
WHITE = (255, 255, 255, 255)
SCREEN = (223, 245, 231, 255)

SIZE = 1024
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "assets", "app.ico")


def build() -> str:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    margin = int(SIZE * 0.06)
    radius = int(SIZE * 0.20)
    d.rounded_rectangle([margin, margin, SIZE - margin, SIZE - margin],
                        radius=radius, fill=ACCENT)

    # Корпус калькулятора
    body = [int(SIZE * 0.22), int(SIZE * 0.16), int(SIZE * 0.78), int(SIZE * 0.84)]
    d.rounded_rectangle(body, radius=int(SIZE * 0.06), fill=WHITE)

    # Дисплей
    screen = [int(SIZE * 0.28), int(SIZE * 0.22), int(SIZE * 0.72), int(SIZE * 0.36)]
    d.rounded_rectangle(screen, radius=int(SIZE * 0.02), fill=SCREEN)
    d.rounded_rectangle([int(SIZE * 0.31), int(SIZE * 0.28),
                         int(SIZE * 0.69), int(SIZE * 0.30)],
                        radius=int(SIZE * 0.01), fill=ACCENT_DARK)

    # Клавиши 3×3
    cols, rows = 3, 3
    x0, y0 = int(SIZE * 0.28), int(SIZE * 0.42)
    x1, y1 = int(SIZE * 0.72), int(SIZE * 0.78)
    gap = int(SIZE * 0.025)
    key_w = (x1 - x0 - gap * (cols - 1)) // cols
    key_h = (y1 - y0 - gap * (rows - 1)) // rows
    for r in range(rows):
        for c in range(cols):
            kx = x0 + c * (key_w + gap)
            ky = y0 + r * (key_h + gap)
            d.rounded_rectangle([kx, ky, kx + key_w, ky + key_h],
                                radius=int(SIZE * 0.015), fill=ACCENT_DARK)

    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    img.save(OUT, format="ICO", sizes=sizes)
    return OUT


if __name__ == "__main__":
    path = build()
    print("Иконка создана:", path)
