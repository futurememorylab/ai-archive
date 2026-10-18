"""chat() asks Vertex for a quick answer: low thinking, search only when asked."""

from typing import Any

from google.genai import types

from backend.app.services.gemini import ChatTurn, GeminiService


class _FakeAioModels:
    def __init__(self):
        self.last_config: Any = None

    async def generate_content(self, *, model, contents, config):
        self.last_config = config

        class _R:
            text = " Dobrý den. "

            def model_dump(self):
                return {}

        return _R()


class _FakeClient:
    def __init__(self):
        self.aio = type("Aio", (), {"models": _FakeAioModels()})()


def _svc() -> tuple[GeminiService, _FakeAioModels]:
    svc = GeminiService.__new__(GeminiService)  # bypass genai.Client construction
    client: Any = _FakeClient()
    svc._client = client
    return svc, client.aio.models


async def test_chat_thinks_briefly_so_answers_come_fast():
    svc, models = _svc()
    turns: list[ChatTurn] = [{"role": "user", "text": "q"}]
    result = await svc.chat(model="m", system_instruction="s", turns=turns)
    config = models.last_config
    assert config.thinking_config.thinking_level == types.ThinkingLevel.LOW
    assert result["text"] == "Dobrý den."


async def test_chat_without_search_sends_no_tools():
    svc, models = _svc()
    turns: list[ChatTurn] = [{"role": "user", "text": "q"}]
    await svc.chat(model="m", system_instruction="s", turns=turns, google_search=False)
    assert models.last_config.tools is None
