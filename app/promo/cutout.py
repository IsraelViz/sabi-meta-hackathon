"""Cut the real product out of the owner's shelf photo with SAM 3.1.

The flyer always shows her actual product, never an AI redraw of branded
packaging. `mask_from_detection` takes SAM 3.1's own mask (decoded with Meta's
`meta-sam-parser`), and also a base64 PNG mask, a polygon, or at least a
bounding box (a rectangular crop).
"""
import base64
import io

from PIL import Image, ImageDraw

from app.media.counting import SamCounter


def decode_data_url(data_url):
    header, b64 = data_url.split(",", 1)
    return base64.b64decode(b64)


def _box(det):
    """(x1, y1, x2, y2). 'box'/'box_xyxy' are corners; 'bbox' is COCO-style [x, y, w, h]."""
    for key in ("box", "box_xyxy"):
        b = det.get(key)
        if b and len(b) == 4:
            return tuple(float(v) for v in b)
    b = det.get("bbox")
    if b and len(b) == 4:
        x, y, w, h = (float(v) for v in b)
        return x, y, x + w, y + h
    return None


def _sam_mask(det, size):
    """SAM 3.1's mask: a raster for the object's box, placed back on a photo-sized mask."""
    from meta_sam_parser import SegmentationMask, decode_mask_to_raster
    x, y, mh, mw, encoding, payload = det["mask"]
    raster = decode_mask_to_raster(SegmentationMask(encoding=encoding, payload=payload, width=mw, height=mh))
    piece = Image.frombytes("L", (mw, mh), bytes(255 if v else 0 for v in raster))
    sw, sh = det.get("size") or size     # SAM's pixels; the photo may have been resized since
    mask = Image.new("L", (sw, sh), 0)
    mask.paste(piece, (int(det["box"][0]) + x, int(det["box"][1]) + y))
    return mask if (sw, sh) == tuple(size) else mask.resize(size)


def mask_from_detection(det, size):
    """Return an 'L' mask the size of the photo, or None if the detection has no shape we understand."""
    w, h = size
    if isinstance(det.get("mask"), tuple):
        try:
            return _sam_mask(det, size)
        except Exception:      # an undecodable mask still leaves the box
            pass
    for key in ("mask_png", "mask"):
        val = det.get(key)
        if isinstance(val, str):
            raw = base64.b64decode(val.split(",", 1)[-1])
            return Image.open(io.BytesIO(raw)).convert("L").resize(size)
    for key in ("polygon", "points", "contour"):
        pts = det.get(key)
        if pts and len(pts) >= 3:
            mask = Image.new("L", size, 0)
            ImageDraw.Draw(mask).polygon([(float(x), float(y)) for x, y in pts], fill=255)
            return mask
    box = _box(det)
    if box:
        mask = Image.new("L", size, 0)
        x1, y1, x2, y2 = box
        if max(x2, y2) <= 1.0:   # normalised coordinates
            x1, x2, y1, y2 = x1 * w, x2 * w, y1 * h, y2 * h
        ImageDraw.Draw(mask).rectangle([x1, y1, x2, y2], fill=255)
        return mask
    return None


def apply_mask(photo: Image.Image, mask: Image.Image) -> Image.Image:
    """RGBA cutout cropped to the mask."""
    rgba = photo.convert("RGBA")
    rgba.putalpha(mask)
    bbox = mask.getbbox()
    return rgba.crop(bbox) if bbox else rgba


class SamCutter:
    name = "sam-3.1"

    def __init__(self, sam=None):
        self.sam = sam or SamCounter()

    def cutout(self, image_data_url, prompt) -> Image.Image:
        photo = Image.open(io.BytesIO(decode_data_url(image_data_url)))
        raw = self.sam.segment(image_data_url, prompt)
        from app.media.counting import _detections
        dets = [d for d in _detections(raw) if isinstance(d, dict)]
        if not dets:
            raise ValueError(f"SAM found no {prompt!r} in the photo")
        best = max(dets, key=lambda d: d.get("score", 0))
        mask = mask_from_detection(best, photo.size)
        if mask is None:
            raise ValueError("SAM detection had no mask, polygon or box")
        return apply_mask(photo, mask)


class CenterCutter:
    """No SAM configured: use the middle of the photo. Still her real product, just not cut out."""
    name = "center-crop"

    def cutout(self, image_data_url, prompt) -> Image.Image:
        photo = Image.open(io.BytesIO(decode_data_url(image_data_url))).convert("RGBA")
        w, h = photo.size
        return photo.crop((w // 8, h // 8, w - w // 8, h - h // 8))
