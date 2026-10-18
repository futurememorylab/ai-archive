"""chat() asks Vertex for a quick answer: low thinking, search only when asked."""

from typing import Any

from google.genai import types

from backend.app.services.gemini import ChatTurn, GeminiService, thought_headline


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
    result = await svc.chat(model="gemini-3.8-flash", system_instruction="s", turns=turns)
    config = models.last_config
    assert config.thinking_config.thinking_level == types.ThinkingLevel.LOW
    assert result["text"] == "Dobrý den."


async def test_chat_without_search_sends_no_tools():
    svc, models = _svc()
    turns: list[ChatTurn] = [{"role": "user", "text": "q"}]
    await svc.chat(model="m", system_instruction="s", turns=turns, google_search=False)
    assert models.last_config.tools is None


def _chunk(*parts: types.Part, grounding: types.GroundingMetadata | None = None):
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=list(parts)),
                grounding_metadata=grounding,
            )
        ]
    )


class _FakeStreamModels(_FakeAioModels):
    def __init__(self, chunks):
        super().__init__()
        self.chunks = chunks

    async def generate_content_stream(self, *, model, contents, config):
        self.last_config = config

        async def gen():
            for c in self.chunks:
                yield c

        return gen()


async def test_chat_stream_reports_thinking_text_then_searches_and_sources():
    grounding = types.GroundingMetadata(
        web_search_queries=["kdy otevřeli Barrandov"],
        grounding_chunks=[
            types.GroundingChunk(web=types.GroundingChunkWeb(uri="https://a.test/x", title="a")),
            types.GroundingChunk(web=types.GroundingChunkWeb(uri="javascript:alert(1)")),
        ],
    )
    models = _FakeStreamModels(
        [
            _chunk(types.Part(text="**Recalling Film Studio Opening**\n\nThe user…", thought=True)),
            _chunk(types.Part(text="Barrandov byl ")),
            _chunk(types.Part(text="otevřen 1933."), grounding=grounding),
        ]
    )
    svc = GeminiService.__new__(GeminiService)
    client: Any = _FakeClient()
    client.aio.models = models
    svc._client = client
    turns: list[ChatTurn] = [{"role": "user", "text": "q"}]
    events = [e async for e in svc.chat_stream(model="m", system_instruction="s", turns=turns)]
    assert events[0] == {"type": "thinking", "text": "Recalling Film Studio Opening"}
    assert [e["text"] for e in events[1:3]] == ["Barrandov byl ", "otevřen 1933."]
    done = events[-1]
    assert done["text"] == "Barrandov byl otevřen 1933."
    assert done["searches"] == ["kdy otevřeli Barrandov"]
    assert done["sources"] == [{"title": "a", "uri": "https://a.test/x"}]  # no javascript:
    assert models.last_config.thinking_config.include_thoughts is True


def test_thought_headline_falls_back_to_the_first_line():
    assert thought_headline("Checking dates\nmore") == "Checking dates"
    assert thought_headline("") == ""


async def test_older_models_get_no_thinking_level():
    """gemini-2.5-pro rejects thinking_level with a 400 (seen on End chat)."""
    svc, models = _svc()
    turns: list[ChatTurn] = [{"role": "user", "text": "q"}]
    await svc.chat(model="gemini-2.5-pro", system_instruction="s", turns=turns)
    assert models.last_config.thinking_config.thinking_level is None
