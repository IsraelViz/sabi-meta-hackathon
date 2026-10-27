"""Settings, read once from the environment (and .env in development)."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env(name, default=""):
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    model_api_key: str = _env("MODEL_API_KEY")
    meta_base_url: str = _env("META_BASE_URL", "https://api.meta.ai/v1")
    chat_model: str = _env("CHAT_MODEL", "muse-spark-1.3")
    transcribe_model: str = _env("TRANSCRIBE_MODEL", "muse-voice-transcribe-1.0")
    sam_model: str = _env("SAM_MODEL", "sam-3.1")
    sam_endpoint: str = _env("SAM_ENDPOINT")
    stocktake_backend: str = _env("STOCKTAKE_BACKEND")   # "sam" | "vision"; default: sam if SAM_ENDPOINT is set
    image_model: str = _env("IMAGE_MODEL", "muse-image-1.0")

    # Promotion: public URL for flyers (tunnel or deployment), Instagram and Facebook Page
    public_base_url: str = _env("PUBLIC_BASE_URL").rstrip("/")
    media_dir: str = _env("MEDIA_DIR", "media_cache")
    ig_user_id: str = _env("IG_USER_ID")
    fb_page_id: str = _env("FB_PAGE_ID")
    page_access_token: str = _env("PAGE_ACCESS_TOKEN")
    shop_whatsapp_display: str = _env("SHOP_WHATSAPP_DISPLAY")   # e.g. "0801 234 5678" printed on flyers

    # Owner app: the token her phone sends; voice notes and photos kept as evidence
    owner_app_token: str = _env("OWNER_APP_TOKEN")
    private_media_dir: str = _env("PRIVATE_MEDIA_DIR", "private_media")

    llm_backend: str = _env("LLM_BACKEND", "meta")
    baseten_api_key: str = _env("BASETEN_API_KEY")
    baseten_base_url: str = _env("BASETEN_BASE_URL", "https://inference.baseten.co/v1")
    baseten_model: str = _env("BASETEN_MODEL", "meta-models/muse-spark-open-1026")

    wa_phone_number_id: str = _env("WA_PHONE_NUMBER_ID")
    wa_access_token: str = _env("WA_ACCESS_TOKEN")
    wa_verify_token: str = _env("WA_VERIFY_TOKEN")
    meta_app_secret: str = _env("META_APP_SECRET")
    graph_version: str = _env("GRAPH_VERSION", "v21.0")

    owner_phone: str = _env("OWNER_PHONE")
    store: str = _env("STORE", "memory")
    demo_history: bool = _env("DEMO_HISTORY", "0") == "1"   # memory store only: add a week of demo trading
    database_url: str = _env("DATABASE_URL")
    auto_post_limit_kobo: int = int(_env("AUTO_POST_LIMIT_NAIRA", "50000")) * 100


settings = Settings()
