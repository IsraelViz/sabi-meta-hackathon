"""Put the flyer together with Pillow. Every word and number on the flyer is
drawn by code from the books: image models still garble prices, and a wrong
price on a public post costs the trader real money."""
import io
import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.normalize.amounts import naira

SIZE = (1080, 1350)          # Instagram portrait, 4:5
INK = (33, 24, 16)
CREAM = (255, 248, 235)
ACCENT = (232, 93, 4)


# Fonts that have the ₦ sign, tried in order. FLYER_FONT overrides.
FONT_CANDIDATES = [
    os.environ.get("FLYER_FONT", ""),
    "DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "NotoSans-Bold.ttf", "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
]


def has_glyph(font, char):
    """True if the font draws `char` differently from a character no font has (the 'tofu' box)."""
    def ink(c):
        img = Image.new("L", (font.size * 2, font.size * 2), 0)
        ImageDraw.Draw(img).text((4, 4), c, font=font, fill=255)
        return img.tobytes()
    return ink(char) != ink("\U0010FFFD")


@lru_cache(maxsize=None)
def _font_path():
    for path in FONT_CANDIDATES:
        if not path:
            continue
        try:
            if has_glyph(ImageFont.truetype(path, 40), "₦"):
                return path
        except OSError:
            continue
    return None


@lru_cache(maxsize=None)
def _font(size):
    path = _font_path()
    return ImageFont.truetype(path, size) if path else ImageFont.load_default(size=size)


def price_label(kobo):
    """'₦12,500' when the font can draw the naira sign, else 'N12,500'. Never a broken box."""
    text = naira(kobo)
    return text if has_glyph(_font(64), "₦") else "N" + text[1:]


def _fit_text(draw, text, max_width, start_size, min_size=28):
    size = start_size
    while size > min_size:
        f = _font(size)
        if draw.textlength(text, font=f) <= max_width:
            return f
        size -= 4
    return _font(min_size)


def _cover(bg, size):
    """Scale and centre-crop the background to fill the canvas."""
    w, h = size
    scale = max(w / bg.width, h / bg.height)
    bg = bg.resize((round(bg.width * scale), round(bg.height * scale)))
    left, top = (bg.width - w) // 2, (bg.height - h) // 2
    return bg.crop((left, top, left + w, top + h))


def _pill(draw, xy, text, font, fill, ink):
    x, y = xy
    pad_x, pad_y = 36, 18
    tw = draw.textlength(text, font=font)
    box = (x, y, x + tw + 2 * pad_x, y + font.size + 2 * pad_y)
    draw.rounded_rectangle(box, radius=(box[3] - box[1]) // 2, fill=fill)
    draw.text((x + pad_x, (box[1] + box[3]) // 2), text, font=font, fill=ink, anchor="lm")
    return box


def compose(background, cutout, *, shop_name, product_name, price_kobo, unit, whatsapp=None):
    canvas = _cover(background.convert("RGB"), SIZE).convert("RGBA")
    w, h = SIZE

    # Product: fit inside the middle area, keep its proportions, add a soft shadow.
    area_w, area_h = int(w * 0.72), int(h * 0.50)
    scale = min(area_w / cutout.width, area_h / cutout.height)
    product = cutout.convert("RGBA").resize((max(1, round(cutout.width * scale)), max(1, round(cutout.height * scale))))
    px, py = (w - product.width) // 2, int(h * 0.20) + (area_h - product.height) // 2
    # Blur on a padded canvas so the shadow fades out instead of stopping at a hard edge.
    pad = 60
    alpha = Image.new("L", (product.width + 2 * pad, product.height + 2 * pad), 0)
    alpha.paste(product.getchannel("A").point(lambda a: a * 0.45), (pad, pad))
    shadow = Image.new("RGBA", alpha.size, (0, 0, 0, 0))
    shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(18)))
    canvas.alpha_composite(shadow, (px + 14 - pad, py + 22 - pad))
    canvas.alpha_composite(product, (px, py))

    draw = ImageDraw.Draw(canvas)
    # Top band: shop name
    draw.rectangle((0, 0, w, 130), fill=(*CREAM, 235))
    name_font = _fit_text(draw, shop_name, w - 120, 64)
    draw.text((w // 2, 65), shop_name, font=name_font, fill=INK, anchor="mm")

    # Bottom card: product, price, how to order
    card_top = int(h * 0.74)
    draw.rounded_rectangle((40, card_top, w - 40, h - 40), radius=36, fill=(*CREAM, 245))
    title_font = _fit_text(draw, product_name, w - 160, 60)
    draw.text((80, card_top + 36), product_name, font=title_font, fill=INK)
    price_text = f"{price_label(price_kobo)} / {unit}"
    _pill(draw, (80, card_top + 120), price_text, _font(64), ACCENT, (255, 255, 255))
    cta = f"Order on WhatsApp: {whatsapp}" if whatsapp else "Order on WhatsApp"
    draw.text((80, h - 100), cta, font=_fit_text(draw, cta, w - 160, 40), fill=INK)
    return canvas.convert("RGB")


def to_jpeg(img, quality=90):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()
