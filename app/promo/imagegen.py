"""Flyer backgrounds from Muse Image. The prompt asks for an empty scene: no
products, no packaging, no text. The real product and all words are added by
code afterwards."""
import base64
import io

import httpx
from PIL import Image, ImageDraw

from app.config import settings

SCENES = {
    "default": "a bright, clean Nigerian market stall counter in warm morning light, colourful fabric in the "
               "background softly out of focus",
    "food": "a warm Nigerian kitchen countertop with a wooden board, soft daylight, shallow depth of field",
    "drinks": "a sunny Lagos street-side kiosk counter, cheerful colours, soft bokeh",
}
RULES = ("Empty space in the centre for a product to be placed later. Photographic, inviting, uncluttered. "
         "No products, no packaging, no logos, no brand names, no people, no text, no letters, no numbers.")


class MuseImage:
    name = "muse-image-1.0"

    def generate(self, scene="default", size=(1080, 1350)) -> Image.Image:
        prompt = f"{SCENES.get(scene, scene)}. {RULES}"
        r = httpx.post(f"{settings.meta_base_url}/images/generations",
                       headers={"Authorization": f"Bearer {settings.model_api_key}"},
                       json={"model": settings.image_model, "prompt": prompt, "n": 1}, timeout=180)
        r.raise_for_status()
        item = r.json()["data"][0]
        if item.get("b64_json"):
            raw = base64.b64decode(item["b64_json"])
        else:
            raw = httpx.get(item["url"], timeout=60).raise_for_status().content
        return Image.open(io.BytesIO(raw)).convert("RGB")


class GradientBackground:
    """Offline stand-in: a warm two-colour gradient. Used in tests and when no key is set."""
    name = "gradient"

    def generate(self, scene="default", size=(1080, 1350)) -> Image.Image:
        w, h = size
        img = Image.new("RGB", size)
        draw = ImageDraw.Draw(img)
        top, bottom = (255, 214, 102), (232, 93, 4)
        for y in range(h):
            t = y / (h - 1)
            draw.line([(0, y), (w, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
        return img
