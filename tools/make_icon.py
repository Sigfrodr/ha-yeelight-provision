"""Génère brand/icon.png et icon@2x.png (ampoule + ondes Wi-Fi)."""
import math
from pathlib import Path

from PIL import Image, ImageDraw

S = 2048  # dessin sur-échantillonné puis réduit (anticrénelage)
OUT = Path(__file__).resolve().parent.parent / "custom_components" / "yeelight_provision" / "brand"
BLUE = (42, 120, 214, 255)
YELLOW = (247, 170, 40, 255)
GREY = (110, 117, 128, 255)

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# Globe de l'ampoule : un disque prolongé par un col trapézoïdal
cx, cy, r = S * 0.34, S * 0.55, S * 0.22
d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=YELLOW)
neck_top = cy + r * 0.62
neck_bottom = cy + r * 1.25
d.polygon(
    [
        (cx - r * 0.78, neck_top),
        (cx + r * 0.78, neck_top),
        (cx + r * 0.42, neck_bottom),
        (cx - r * 0.42, neck_bottom),
    ],
    fill=YELLOW,
)

# Culot : trois bagues grises
ring_h = r * 0.16
for i in range(3):
    top = neck_bottom + r * 0.06 + i * (ring_h + r * 0.06)
    half = r * (0.44 - 0.06 * i)
    d.rounded_rectangle((cx - half, top, cx + half, top + ring_h), radius=ring_h / 2, fill=GREY)

# Ondes Wi-Fi en haut à droite, centrées sur le globe
ox, oy = cx + r * 0.35, cy - r * 0.35
width = int(S * 0.055)
for k, radius in enumerate((r * 1.15, r * 1.55, r * 1.95)):
    box = (ox - radius, oy - radius, ox + radius, oy + radius)
    d.arc(box, start=-80, end=-10, fill=BLUE, width=width)
    # extrémités arrondies
    for angle in (-80, -10):
        a = math.radians(angle)
        x = ox + (radius - width / 2) * math.cos(a)
        y = oy + (radius - width / 2) * math.sin(a)
        d.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=BLUE)

img = img.crop(img.getbbox())
side = max(img.size)
square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))

OUT.mkdir(parents=True, exist_ok=True)
square.resize((256, 256), Image.LANCZOS).save(OUT / "icon.png", optimize=True)
square.resize((512, 512), Image.LANCZOS).save(OUT / "icon@2x.png", optimize=True)
print("ok", sorted(p.name for p in OUT.iterdir()))
