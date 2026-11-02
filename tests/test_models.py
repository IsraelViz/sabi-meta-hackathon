"""Model backends: which client and transcriber each setting picks, and the Baseten and Fireworks voice requests."""
import asyncio
import dataclasses

import httpx

import app.media.transcribe as transcribe_mod
import app.models.llm as llm_mod
from app.config import settings


def with_settings(monkeypatch, module, **kw):
    s = dataclasses.replace(settings, **kw)
    monkeypatch.setattr(module, "settings", s)
    return s


def test_fireworks_backend_runs_muse_spark_open(monkeypatch):
    with_settings(monkeypatch, llm_mod, llm_backend="fireworks", fireworks_api_key="fw-test")
    c = llm_mod.LLMClient()
    assert c.model == "accounts/fireworks/models/muse-spark-open"
    assert str(c._client.base_url).rstrip("/") == "https://us.api.fireworks.ai/inference/v1"


def test_has_llm_follows_the_chosen_backend():
    assert dataclasses.replace(settings, llm_backend="fireworks", fireworks_api_key="k").has_llm()
    assert not dataclasses.replace(settings, llm_backend="fireworks", fireworks_api_key="", model_api_key="k").has_llm()
    assert dataclasses.replace(settings, llm_backend="meta", model_api_key="k").has_llm()


def test_voice_notes_need_a_transcriber_key():
    # Fireworks serverless has no speech model today, so voice notes stay on Meta unless set otherwise.
    fw_only = dataclasses.replace(settings, llm_backend="fireworks", fireworks_api_key="k", model_api_key="",
                                  transcribe_backend="")
    assert fw_only.transcriber() == "meta" and not fw_only.can_transcribe()
    assert dataclasses.replace(fw_only, transcribe_backend="fireworks").can_transcribe()
    assert dataclasses.replace(fw_only, model_api_key="m").can_transcribe()


def test_baseten_hears_voice_notes_with_the_same_key():
    bt = dataclasses.replace(settings, llm_backend="baseten", baseten_api_key="k", model_api_key="", transcribe_backend="")
    assert bt.transcriber() == "baseten" and bt.can_transcribe()
    assert not dataclasses.replace(bt, baseten_api_key="").can_transcribe()


def test_voice_note_goes_to_muse_spark_open_as_audio(monkeypatch):
    with_settings(monkeypatch, transcribe_mod, llm_backend="baseten", baseten_api_key="bt-test", transcribe_backend="")
    seen = {}

    class FakeCompletions:
        def create(self, **kw):
            seen.update(kw)
            msg = type("M", (), {"content": " I sell 2 carton Indomie give Chioma "})
            return type("R", (), {"choices": [type("C", (), {"message": msg})]})

    class FakeOpenAI:
        def __init__(self, base_url, api_key, timeout):
            seen.update(base_url=base_url, api_key=api_key)
            self.chat = type("Chat", (), {"completions": FakeCompletions()})

    import openai
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    ogg = b"OggS" + b"\0" * 100
    assert asyncio.run(transcribe_mod.transcribe(ogg)) == "I sell 2 carton Indomie give Chioma"
    assert seen["model"] == "meta-models/muse-spark-open-1026" and seen["api_key"] == "bt-test"
    text, audio = seen["messages"][0]["content"]
    assert "word for word" in text["text"]
    assert audio["type"] == "audio_url" and audio["audio_url"]["url"].startswith("data:audio/ogg;base64,T2dnUw")


def test_voice_note_goes_to_fireworks_whisper_as_it_is(monkeypatch):
    with_settings(monkeypatch, transcribe_mod, llm_backend="fireworks", fireworks_api_key="fw-test", transcribe_backend="fireworks")
    seen = {}

    async def fake_post(self, url, data=None, files=None, headers=None):
        seen.update(url=url, data=data, files=files, headers=headers)
        return httpx.Response(200, json={"text": " I sell 2 carton Indomie give Chioma "})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    ogg = b"OggS" + b"\0" * 100
    assert asyncio.run(transcribe_mod.transcribe(ogg)) == "I sell 2 carton Indomie give Chioma"
    assert seen["url"].endswith("/v1/audio/transcriptions") and seen["data"]["model"] == "whisper-v3-turbo"
    assert seen["files"]["file"] == ("voice.ogg", ogg) and seen["headers"]["Authorization"] == "Bearer fw-test"


def test_demo_hides_voice_without_a_transcriber(monkeypatch):
    import app.demo as demo_mod
    from tests.test_demo import client_for, registry

    with_settings(monkeypatch, demo_mod, model_api_key="", transcribe_backend="meta")
    c = client_for(registry())
    s = c.post("/demo/api/session").json()
    assert s["voice"] is False and "voice note" not in s["intro"][0]["body"]
    r = c.post(f"/demo/api/{s['id']}/message", json={"who": "owner", "audio": "data:audio/webm;base64,AAAA"})
    assert r.status_code == 503 and "type it" in r.json()["detail"]


def test_transcription_errors_are_raised(monkeypatch):
    with_settings(monkeypatch, transcribe_mod, transcribe_backend="fireworks", fireworks_api_key="bad")

    async def fake_post(self, url, **kw):
        return httpx.Response(401, text="unauthorized")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    try:
        asyncio.run(transcribe_mod.transcribe(b"\x1aE\xdf\xa3webm"))
    except RuntimeError as e:
        assert "401" in str(e)
    else:
        raise AssertionError("expected an error")
