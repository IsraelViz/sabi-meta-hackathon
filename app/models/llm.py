"""One client for every chat model: Muse Spark on the Meta Model API, or Muse
Spark Open on Baseten. Both speak the OpenAI chat-completions format, so
switching is a config change (LLM_BACKEND)."""
from dataclasses import dataclass

from app.config import settings


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class Reply:
    content: str | None
    tool_calls: list[ToolCall]
    tokens: int | None
    model: str
    raw_message: dict


class LLMClient:
    def __init__(self, backend=None):
        from openai import OpenAI

        self.backend = backend or settings.llm_backend
        if self.backend == "baseten":
            self.model = settings.baseten_model
            self._client = OpenAI(base_url=settings.baseten_base_url, api_key=settings.baseten_api_key)
        else:
            self.model = settings.chat_model
            # The OpenAI SDK does not read MODEL_API_KEY on its own.
            self._client = OpenAI(base_url=settings.meta_base_url, api_key=settings.model_api_key)

    def chat(self, messages, tools=None, **kwargs) -> Reply:
        resp = self._client.chat.completions.create(
            model=self.model, messages=messages, tools=tools or None, **kwargs)
        msg = resp.choices[0].message
        calls = [ToolCall(c.id, c.function.name, c.function.arguments or "{}") for c in (msg.tool_calls or [])]
        return Reply(
            content=msg.content,
            tool_calls=calls,
            tokens=getattr(resp.usage, "total_tokens", None),
            model=self.model,
            raw_message=msg.model_dump(exclude_none=True),
        )
