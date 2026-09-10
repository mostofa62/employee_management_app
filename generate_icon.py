"""Generate logo.ico from logo.png for exe icon (transparent square, multi-size)."""
from PIL import Image
import os

SRC = "logo.png"
DST = "logo.ico"

if not os.path.isfile(SRC):
    raise SystemExit(f"{SRC} not found")

im = Image.open(SRC).convert("RGBA")
s = max(im.size)
bg = Image.new("RGBA", (s, s), (255, 255, 255, 0))
bg.paste(im, ((s - im.size[0]) // 2, (s - im.size[1]) // 2), im)
bg.save(DST, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(f"{DST} created: {os.path.getsize(DST)} bytes")
print(f"ICO sizes: {Image.open(DST).info.get('sizes')}")
