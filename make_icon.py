"""Genera assets/cutlab.ico dal disegno dell'icona (nessun file binario a mano).

    python make_icon.py
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "assets")
BG = (19, 18, 9)
ACCENT = (229, 164, 59)
TEXT = (245, 243, 236)

S = 1024


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # piastra arrotondata
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=int(S * 0.22), fill=BG)
    # due barre verticali = le due tracce video
    w = int(S * 0.055)
    x1 = int(S * 0.24)
    x2 = int(S * 0.40)
    top = int(S * 0.22)
    bot = int(S * 0.78)
    d.rounded_rectangle([x1, top, x1 + w, bot], radius=w // 2, fill=TEXT)
    d.rounded_rectangle([x2, top, x2 + w, bot], radius=w // 2, fill=TEXT)
    d.rounded_rectangle([x1, int(S * 0.47), x2 + w, int(S * 0.47) + w], radius=w // 2, fill=ACCENT)
    # la testa del "cursore" (la voce che si tiene)
    cx, cy, r = int(S * 0.70), int(S * 0.34), int(S * 0.075)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=ACCENT, width=int(S * 0.028))
    d.rounded_rectangle([cx - w // 2, cy + r, cx + w // 2, int(S * 0.80)],
                        radius=w // 2, fill=ACCENT)
    return img


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    img = draw()
    sizes = [16, 24, 32, 48, 64, 128, 256]
    img.save(os.path.join(OUT, "cutlab.ico"), sizes=[(s, s) for s in sizes])
    img.resize((256, 256), Image.LANCZOS).save(os.path.join(OUT, "cutlab-256.png"))
    print("scritti assets/cutlab.ico e cutlab-256.png")


if __name__ == "__main__":
    main()
