"""Sabi's logo: a chat bubble (you just talk) holding three rising bars (your talk becomes business
intelligence), with a gold spark on the tallest one (the insight). One set of measurements makes the
SVG and every app icon, so they always match.

    python scripts/make_logo.py        # writes web/icon.svg and web/icon-*.png
"""
from pathlib import Path

from PIL import Image, ImageDraw

WEB = Path(__file__).resolve().parents[1] / "web"
TOP, BOTTOM = (0xF2, 0x7A, 0x1A), (0xD4, 0x45, 0x00)      # Sabi orange, light to deep
CREAM, ORANGE, GOLD = (0xFF, 0xF8, 0xEB), (0xE8, 0x5D, 0x04), (0xFF, 0xC4, 0x3D)

# Everything on a 512 grid.
BUBBLE = (100, 108, 412, 352, 76)                 # x0, y0, x1, y1, corner radius
TAIL = [(150, 330), (124, 414), (226, 346)]       # bottom-left, pointing down like a chat bubble
BASE = 296
BARS = [(166, 246), (234, 206), (302, 156)]       # (x, top): 46 wide, rounded tops
BAR_W, BAR_R = 46, 14
SPARK = (360, 150, 30)                            # centre x, centre y, radius: a four-point star


def _star(cx, cy, r):
    k = r * 0.30    # how thin the arms are
    return [(cx, cy - r), (cx + k, cy - k), (cx + r, cy), (cx + k, cy + k),
            (cx, cy + r), (cx - k, cy + k), (cx - r, cy), (cx - k, cy - k)]


def svg():
    bx0, by0, bx1, by1, br = BUBBLE
    bars = "\n  ".join(
        f'<path d="M{x},{BASE} V{top + BAR_R} Q{x},{top} {x + BAR_R},{top} H{x + BAR_W - BAR_R} '
        f'Q{x + BAR_W},{top} {x + BAR_W},{top + BAR_R} V{BASE} Z" fill="#E85D04"/>' for x, top in BARS)
    star = " ".join(f"{x:.1f},{y:.1f}" for x, y in _star(*SPARK))
    tail = " ".join(f"{x},{y}" for x, y in TAIL)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <title>Sabi</title>
  <defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#F27A1A"/><stop offset="1" stop-color="#D44500"/></linearGradient></defs>
  <rect width="512" height="512" rx="112" fill="url(#g)"/>
  <rect x="{bx0}" y="{by0}" width="{bx1 - bx0}" height="{by1 - by0}" rx="{br}" fill="#FFF8EB"/>
  <polygon points="{tail}" fill="#FFF8EB"/>
  {bars}
  <polygon points="{star}" fill="#FFC43D"/>
</svg>
'''


def png(size, full_bleed=False, scale=1.0):
    """full_bleed: no rounded corners (the phone masks it). scale: shrink the mark into the safe zone."""
    s = 4 * size                                      # draw big, shrink: smooth edges
    k = s / 512
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    grad = Image.new("RGB", (1, s))
    for y in range(s):
        t = y / (s - 1)
        grad.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)))
    grad = grad.resize((s, s))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, s - 1, s - 1), radius=0 if full_bleed else round(112 * k), fill=255)
    img.paste(grad, (0, 0), mask)

    mark = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(mark)
    bx0, by0, bx1, by1, br = BUBBLE
    d.rounded_rectangle([bx0 * k, by0 * k, bx1 * k, by1 * k], radius=br * k, fill=CREAM)
    d.polygon([(x * k, y * k) for x, y in TAIL], fill=CREAM)
    for x, top in BARS:
        d.rounded_rectangle([x * k, top * k, (x + BAR_W) * k, (BASE + BAR_R) * k], radius=BAR_R * k, fill=ORANGE)
        d.rectangle([x * k, (BASE - 1) * k, (x + BAR_W) * k, (BASE + BAR_R + 2) * k], fill=CREAM)   # flat base
        d.rectangle([x * k, (top + BAR_R) * k, (x + BAR_W) * k, BASE * k], fill=ORANGE)
    d.polygon([(x * k, y * k) for x, y in _star(*SPARK)], fill=GOLD)
    if scale != 1.0:
        small = mark.resize((round(s * scale), round(s * scale)), Image.LANCZOS)
        mark = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        mark.paste(small, ((s - small.width) // 2, (s - small.height) // 2), small)
    img.alpha_composite(mark)
    return img.resize((size, size), Image.LANCZOS)


def main():
    (WEB / "icon.svg").write_text(svg(), encoding="utf-8")
    png(512).save(WEB / "icon-512.png")
    png(192).save(WEB / "icon-192.png")
    # iOS home screen: no transparency, it rounds the corners itself
    png(180, full_bleed=True).convert("RGB").save(WEB / "icon-180.png")
    # Android "maskable": the phone crops to a circle or squircle, so the mark sits in the middle 80%
    png(512, full_bleed=True, scale=0.8).save(WEB / "icon-maskable.png")
    print("logo written to web/")


if __name__ == "__main__":
    main()
