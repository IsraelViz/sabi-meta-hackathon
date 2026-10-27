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
    database_url: str = _env("DATABASE_URL")
    auto_post_limit_kobo: int = int(_env("AUTO_POST_LIMIT_NAIRA", "50000")) * 100


settings = Settings()
