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
    """'₦12,500' when the font can draw the money sign, else 'N12,500'. Never a broken box."""
    text = naira(kobo)
    font = _font(64)
    return text if all(has_glyph(font, c) for c in set(text) if not c.isascii()) else (
        text.replace("₦", "N").replace("₵", "C").replace("\u202f", " ").replace("\u00a0", " "))


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
    pad_x, pad_y = 26, 12
    tw = draw.textlength(text, font=font)
    box = (x, y, x + tw + 2 * pad_x, y + font.size + 2 * pad_y)
    draw.rounded_rectangle(box, radius=(box[3] - box[1]) // 2, fill=fill)
    draw.text((x + pad_x, (box[1] + box[3]) // 2), text, font=font, fill=ink, anchor="lm")
    return box


def compose(background, cutout, *, shop_name, product_name, price_kobo, unit, whatsapp=None, scale=1.0,
            show_price=True):
    """`scale` makes the product bigger or smaller than usual (0.5 to 1.3); `show_price` False leaves the price off."""
    canvas = _cover(background.convert("RGB"), SIZE).convert("RGBA")
    w, h = SIZE
    band, card_top, margin = 96, h - (290 if show_price else 210), 56

    # Product: centred in the space between the name band and the card, with room around it.
    scale = max(0.5, min(1.3, scale or 1))
    space_top, space_h = band + 40, card_top - band - 80
    area_w, area_h = int(w * 0.50 * scale), int(space_h * 0.68 * scale)
    fit = min(area_w / cutout.width, area_h / cutout.height)
    product = cutout.convert("RGBA").resize((max(1, round(cutout.width * fit)), max(1, round(cutout.height * fit))))
    px, py = (w - product.width) // 2, space_top + (space_h - product.height) // 2
    # Blur on a padded canvas so the shadow fades out instead of stopping at a hard edge.
    pad = 50
    alpha = Image.new("L", (product.width + 2 * pad, product.height + 2 * pad), 0)
    alpha.paste(product.getchannel("A").point(lambda a: a * 0.4), (pad, pad))
    shadow = Image.new("RGBA", alpha.size, (0, 0, 0, 0))
    shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(16)))
    canvas.alpha_composite(shadow, (px + 10 - pad, py + 16 - pad))
    canvas.alpha_composite(product, (px, py))

    draw = ImageDraw.Draw(canvas)
    # Top band: shop name
    draw.rectangle((0, 0, w, band), fill=(*CREAM, 235))
    draw.text((w // 2, band // 2), shop_name, font=_fit_text(draw, shop_name, w - 2 * margin, 44, 24), fill=INK,
              anchor="mm")

    # Bottom card: product, price, how to order
    draw.rounded_rectangle((margin, card_top, w - margin, h - margin), radius=28, fill=(*CREAM, 245))
    x = margin + 36
    draw.text((x, card_top + 30), product_name, font=_fit_text(draw, product_name, w - 2 * x, 44, 24), fill=INK)
    if show_price:
        _pill(draw, (x, card_top + 96), f"{price_label(price_kobo)} / {unit}", _font(44), ACCENT, (255, 255, 255))
    cta = f"Order on WhatsApp: {whatsapp}" if whatsapp else "Order on WhatsApp"
    draw.text((x, h - margin - 58), cta, font=_fit_text(draw, cta, w - 2 * x, 30, 20), fill=INK)
    return canvas.convert("RGB")


def to_jpeg(img, quality=90):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()
