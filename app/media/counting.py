"""Count products in a shelf photo.

`SamCounter` uses SAM 3.1 with one text prompt per product. Its endpoint and
request shape are not in Meta's public docs yet, so both live in settings and
`_detections`; check them against dev.meta.ai on the first run.
`VisionCounter` asks Muse Spark to count instead: the fallback and the baseline.
"""
import base64

import httpx

from app.config import settings
from app.normalize.json_reply import parse_json


class SamCounter:
    name = "sam-3.1"

    def __init__(self, endpoint=None, min_score=0.5):
        self.endpoint = endpoint or settings.sam_endpoint
        self.min_score = min_score
        if not self.endpoint:
            raise RuntimeError("SAM_ENDPOINT is not set")

    def segment(self, image_data_url, prompt):
        r = httpx.post(self.endpoint, headers={"Authorization": f"Bearer {settings.model_api_key}"},
                       json={"model": settings.sam_model, "image": image_data_url, "prompt": prompt}, timeout=120)
        r.raise_for_status()
        return r.json()

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


def make_counter(llm):
    backend = settings.stocktake_backend or ("sam" if settings.sam_endpoint else "vision")
    return SamCounter() if backend == "sam" else VisionCounter(llm)


def data_url(image_bytes, mime="image/jpeg"):
    return f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
