"""Hosting flyers at a public URL, and publishing to Instagram and a Facebook Page.

Instagram's API fetches the image from a public URL, so flyers are saved under
MEDIA_DIR and served by the app at /media/<name> (PUBLIC_BASE_URL is the
tunnel or deployment address).
"""
import secrets
from pathlib import Path

import httpx

from app.config import settings

GRAPH = "https://graph.facebook.com"


class LocalMedia:
    def __init__(self, directory=None, public_base_url=None):
        self.dir = Path(directory or settings.media_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.base = (public_base_url if public_base_url is not None else settings.public_base_url).rstrip("/")

    def save(self, data: bytes, ext="jpg"):
        name = f"{secrets.token_hex(8)}.{ext}"   # unguessable, so drafts aren't browsable
        (self.dir / name).write_bytes(data)
        return {"name": name, "path": str(self.dir / name), "url": f"{self.base}/media/{name}" if self.base else None}


class MetaPublisher:
    """Instagram (content publishing API) and, if configured, the linked Facebook Page."""

    def __init__(self, ig_user_id=None, page_id=None, token=None, version=None, client=None):
        self.ig_user_id = ig_user_id or settings.ig_user_id
        self.page_id = page_id or settings.fb_page_id
        self.token = token or settings.page_access_token
        self.version = version or settings.graph_version
        self.client = client or httpx.Client(timeout=60)

    @property
    def platforms(self):
        return [p for p, ok in (("instagram", self.ig_user_id), ("facebook", self.page_id)) if ok and self.token]

    def _post(self, path, data):
        r = self.client.post(f"{GRAPH}/{self.version}/{path}", data={**data, "access_token": self.token})
        if r.status_code >= 400:
            raise RuntimeError(f"Graph API {path}: {r.status_code} {r.text[:300]}")
        return r.json()

    def publish(self, image_url, caption):
        """Returns {platform: post_id}. Raises if a configured platform fails."""
        if not image_url:
            raise RuntimeError("flyer has no public URL; set PUBLIC_BASE_URL")
        out = {}
        if "instagram" in self.platforms:
            container = self._post(f"{self.ig_user_id}/media", {"image_url": image_url, "caption": caption})
            out["instagram"] = self._post(f"{self.ig_user_id}/media_publish",
                                          {"creation_id": container["id"]})["id"]
        if "facebook" in self.platforms:
            out["facebook"] = self._post(f"{self.page_id}/photos", {"url": image_url, "caption": caption})["id"]
        if not out:
            raise RuntimeError("no Instagram or Facebook account configured (IG_USER_ID / FB_PAGE_ID / PAGE_ACCESS_TOKEN)")
        return out
