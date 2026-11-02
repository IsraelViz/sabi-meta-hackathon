"""Count products in a shelf photo.

`SamCounter` uses SAM 3.1 on the Meta Model API with one text prompt per product: a POST to
/v1/responses with the photo and a short noun phrase ("Indomie carton"). SAM answers in text, one
record per object found: `<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..;c=..|><|mask;x=..;y=..;data=H,W,..|>`.
`parse_records` turns that into detections; the mask is decoded with Meta's `meta-sam-parser`.
`VisionCounter` asks Muse Spark to count instead: the fallback and the baseline.
"""
import base64
import re

import httpx

from app.config import settings
from app.normalize.json_reply import parse_json

_RECORD = re.compile(r"(\d+)<\|box;(.*?)\|>(?:<\|mask;(.*?)\|>)?")


def _fields(token):
    return dict(kv.split("=", 1) for kv in token.split(";") if "=" in kv)


def parse_records(text):
    """SAM's output text for one image -> [{"id", "box": (x1, y1, x2, y2), "size": (w, h), "score", "mask"}].
    "mask" is (x, y, height, width, encoding, payload) relative to the box, or None."""
    line = next((ln for ln in (text or "").splitlines() if ln.startswith("<0f>")), "")
    out = []
    for m in _RECORD.finditer(line):
        box = _fields(m.group(2))
        det = {"id": int(m.group(1)),
               "box": tuple(float(box[k]) for k in ("x1", "y1", "x2", "y2")),
               "size": (int(box["w"]), int(box["h"])),
               "score": float(box.get("c", 1.0)), "mask": None}
        if m.group(3):
            mask = _fields(m.group(3))
            h, w, payload = mask["data"].split(",", 2)
            det["mask"] = (int(mask.get("x", 0)), int(mask.get("y", 0)), int(h), int(w),
                           "lossless" if payload.startswith("~") else "one_bit", payload)
        out.append(det)
    return out


class SamCounter:
    name = "sam-3.1"

    def __init__(self, endpoint=None, min_score=0.5):
        self.endpoint = endpoint or settings.sam_endpoint or f"{settings.meta_base_url}/responses"
        self.min_score = min_score
        if not settings.model_api_key and not endpoint:
            raise RuntimeError("MODEL_API_KEY is not set")

    def segment(self, image_data_url, prompt):
        """{"detections": [...]} for every object matching the prompt (an empty list when none)."""
        r = httpx.post(self.endpoint, headers={"Authorization": f"Bearer {settings.model_api_key}"}, timeout=120,
                       json={"model": settings.sam_model, "metadata": {"include_confidence": "true"},
                             "input": [{"type": "message", "role": "user", "content": [
                                 {"type": "input_text", "text": prompt},
                                 {"type": "input_image", "image_url": image_data_url}]}]})
        r.raise_for_status()
        text = "".join(part.get("text", "") for item in r.json().get("output") or []
                       for part in item.get("content") or [] if part.get("type") == "output_text")
        return {"detections": parse_records(text)}

    def count(self, image_data_url, prompts):
        """prompts: {key: text prompt}. Returns {key: {"count": int, "detections": [...]}}."""
        out = {}
        for key, prompt in prompts.items():
            dets = [d for d in _detections(self.segment(image_data_url, prompt))
                    if not isinstance(d, dict) or d.get("score", 1.0) >= self.min_score]
            out[key] = {"count": len(dets), "detections": dets}
        return out


def _detections(raw):
    for key in ("detections", "objects", "masks", "instances", "results", "data"):
        val = raw.get(key) if isinstance(raw, dict) else None
        if isinstance(val, list):
            return val
    if isinstance(raw, dict) and isinstance(raw.get("scores"), list):
        return [{"score": s} for s in raw["scores"]]
    raise ValueError(f"no detections found in SAM response (keys: {list(raw) if isinstance(raw, dict) else type(raw)})")


class VisionCounter:
    name = "muse-spark-vision"

    def __init__(self, llm):
        self.llm = llm

    def count(self, image_data_url, prompts):
        listing = "\n".join(f'- "{k}": {p}' for k, p in prompts.items())
        reply = self.llm.chat([{"role": "user", "content": [
            {"type": "text", "text":
                "Count each of these products in the shelf photo. Count only whole units you can clearly see; "
                "do not estimate hidden ones. Reply with JSON only: {\"<key>\": <count>, ...}\n" + listing},
            {"type": "image_url", "image_url": {"url": image_data_url}}]}])
        data = parse_json(reply.content or "{}")
        return {k: {"count": int(data.get(k) or 0), "detections": []} for k in prompts}


def sam_available():
    return settings.stocktake_backend != "vision" and bool(settings.model_api_key or settings.sam_endpoint)


def make_counter(llm):
    return SamCounter() if settings.stocktake_backend == "sam" or sam_available() else VisionCounter(llm)


def data_url(image_bytes, mime="image/jpeg"):
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
