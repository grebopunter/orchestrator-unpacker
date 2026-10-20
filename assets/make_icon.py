"""Draws assets/dop-extract.ico and .png (needs Pillow). Run once; the
outputs are committed, so building the app doesn't need Pillow."""
import math
import os
from PIL import Image, ImageDraw

S = 1024
here = os.path.dirname(os.path.abspath(__file__))
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
# background: rounded square with a vertical gradient
grad = Image.new("RGBA", (S, S))
gd = ImageDraw.Draw(grad)
top, bot = (40, 52, 110), (18, 120, 140)
for y in range(S):
    t = y / (S - 1)
    gd.line([(0, y), (S, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bot)) + (255,))
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([40, 40, S - 40, S - 40], radius=200, fill=255)
img.paste(grad, (0, 0), mask)
d = ImageDraw.Draw(img)
# piano-roll notes (MIDI) in the upper half
notes = [(150, 230, 330), (300, 300, 470), (480, 200, 620), (600, 270, 760),
         (700, 340, 880), (220, 380, 420), (520, 400, 640)]
for x0, y, x1 in notes:
    d.rounded_rectangle([x0, y, x1, y + 52], radius=22, fill=(255, 214, 102, 255))
# waveform (audio) in the lower half
cx, cy, w = 150, 700, 724
pts_top, pts_bot = [], []
for i in range(0, w + 1, 6):
    x = cx + i
    env = 0.25 + 0.75 * abs(math.sin(i / w * math.pi * 2.5)) * (1 - 0.35 * i / w)
    a = 150 * env * (0.65 + 0.35 * math.sin(i * 0.21))
    pts_top.append((x, cy - a))
    pts_bot.append((x, cy + a))
d.polygon(pts_top + pts_bot[::-1], fill=(255, 255, 255, 240))
d.line([(cx, cy), (cx + w, cy)], fill=(40, 52, 110, 255), width=10)
img.save(os.path.join(here, "dop-extract.png"))
img.save(os.path.join(here, "dop-extract.ico"),
         sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
