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
    vapid_subject: str = _env("VAPID_SUBJECT", "mailto:sabi-pilot@example.com")   # contact for phone push services
    media_dir: str = _env("MEDIA_DIR", "media_cache")
    ig_user_id: str = _env("IG_USER_ID")
    fb_page_id: str = _env("FB_PAGE_ID")
    page_access_token: str = _env("PAGE_ACCESS_TOKEN")
    shop_whatsapp_display: str = _env("SHOP_WHATSAPP_DISPLAY")   # e.g. "0801 234 5678" printed on flyers

    # Payment reminders outside WhatsApp's 24-hour window must use an approved template
    waba_id: str = _env("WABA_ID")   # WhatsApp Business Account id, only for creating the template
    reminder_template: str = _env("WA_REMINDER_TEMPLATE", "payment_reminder")
    reminder_template_lang: str = _env("WA_REMINDER_TEMPLATE_LANG", "en")

    # Public "Try Sabi" demo at /demo/: per-visitor shops, with limits so it can't run up the model bill
    demo_enabled: bool = _env("DEMO_ENABLED", "1") == "1"
    demo_max_messages: int = int(_env("DEMO_MAX_MESSAGES", "40"))      # per visitor session
    demo_daily_messages: int = int(_env("DEMO_DAILY_MESSAGES", "2000"))  # across all visitors
    demo_max_sessions: int = int(_env("DEMO_MAX_SESSIONS", "200"))

    # Owner app: the token her phone sends; voice notes and photos kept as evidence
    owner_app_token: str = _env("OWNER_APP_TOKEN")
    statement_secret: str = _env("STATEMENT_SECRET")   # signs business-statement links; default: OWNER_APP_TOKEN
    private_media_dir: str = _env("PRIVATE_MEDIA_DIR", "private_media")

    llm_backend: str = _env("LLM_BACKEND", "meta")
    baseten_api_key: str = _env("BASETEN_API_KEY")
    baseten_base_url: str = _env("BASETEN_BASE_URL", "https://inference.baseten.co/v1")
    baseten_model: str = _env("BASETEN_MODEL", "meta-models/muse-spark-open-1026")
    # Muse Spark Open on Fireworks serverless (US-only capacity), plus Whisper for voice notes
    fireworks_api_key: str = _env("FIREWORKS_API_KEY")
    fireworks_base_url: str = _env("FIREWORKS_BASE_URL", "https://us.api.fireworks.ai/inference/v1")
    fireworks_model: str = _env("FIREWORKS_MODEL", "accounts/fireworks/models/muse-spark-open")
    fireworks_transcribe_url: str = _env("FIREWORKS_TRANSCRIBE_URL",
                                         "https://audio-turbo.api.fireworks.ai/v1/audio/transcriptions")
    fireworks_transcribe_model: str = _env("FIREWORKS_TRANSCRIBE_MODEL", "whisper-v3-turbo")
    # "meta" (Muse Voice Transcribe) | "baseten" (Muse Spark Open hears the audio itself)
    # | "fireworks" (Whisper, if Fireworks offers it on your account). Empty: baseten when the chat
    # model runs there, else meta. On WhatsApp-compressed test clips Muse Spark Open got every amount
    # right while Voice Transcribe sometimes repeated a word ("five five hundred"), so baseten stays first.
    transcribe_backend: str = _env("TRANSCRIBE_BACKEND")

    def has_llm(self) -> bool:
        return bool({"meta": self.model_api_key, "baseten": self.baseten_api_key,
                     "fireworks": self.fireworks_api_key}.get(self.llm_backend))

    def transcriber(self) -> str:
        return self.transcribe_backend or ("baseten" if self.llm_backend == "baseten" else "meta")

    def can_transcribe(self) -> bool:
        return bool({"meta": self.model_api_key, "baseten": self.baseten_api_key,
                     "fireworks": self.fireworks_api_key}.get(self.transcriber()))

    wa_phone_number_id: str = _env("WA_PHONE_NUMBER_ID")
    wa_access_token: str = _env("WA_ACCESS_TOKEN")
    wa_verify_token: str = _env("WA_VERIFY_TOKEN")
    meta_app_secret: str = _env("META_APP_SECRET")
    graph_version: str = _env("GRAPH_VERSION", "v21.0")

    owner_phone: str = _env("OWNER_PHONE")
    store: str = _env("STORE", "memory")
    shop_file: str = _env("SHOP_FILE")   # your shop (business info, products, contacts, opening stock); empty = demo shop
    demo_history: bool = _env("DEMO_HISTORY", "0") == "1"   # a week of demo trading for the demo shop (Postgres: first run only)
    database_url: str = _env("DATABASE_URL")

    # Pilot shops: real testers, each with her own books, chat in the app and a customer chat link.
    # A Postgres URL, or "pgserver:<folder>" for an embedded Postgres kept in that folder. Empty = off.
    shops_db: str = _env("SHOPS_DB")
    admin_token: str = _env("ADMIN_TOKEN")             # /admin/ (create shops, read chats); default: OWNER_APP_TOKEN
    shop_daily_messages: int = int(_env("SHOP_DAILY_MESSAGES", "400"))   # per shop, owner and customers together
    auto_post_limit_kobo: int = int(_env("AUTO_POST_LIMIT_NAIRA", "50000")) * 100


settings = Settings()
