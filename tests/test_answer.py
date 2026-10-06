import base64
import types

import pytest

from recall import answer as answer_mod
from recall import llm
from recall.answer import answer_events, build_prompt, image_catalogue, resolve_image_url, retrieve, vision_blocks
from recall.config import Settings
from recall.index import Filters


def collect(events):
    out = {"sources": None, "text": "", "done": None, "error": None}
    for ev, data in events:
        if ev == "delta":
            out["text"] += data
        else:
            out[ev] = data
    return out


def test_retrieve_numbers_sources(index):
    hits = retrieve(index, "VLAN trunk", None, 5)
    assert [h["n"] for h in hits] == list(range(1, len(hits) + 1))


def test_image_catalogue_dedupes_and_ranks(index):
    hits = retrieve(index, "VLAN diagram DNS", None, 10)
    cat = image_catalogue(hits)
    urls = [c["url"] for c in cat]
    assert len(urls) == len(set(urls))
    assert "/api/file?path=images/vlan-diagram.png" in urls
    assert cat[0]["id"] == "img1"


def test_resolve_image_url_safe(index):
    assert resolve_image_url(index, "/api/file?path=images/vlan-diagram.png").is_file()
    assert resolve_image_url(index, "/api/file?path=../../etc/passwd") is None
    assert resolve_image_url(index, "/api/cache-image/../../x.png") is None
    assert resolve_image_url(index, "https://example.com/a.png") is None
    pdf_img = retrieve(index, "kubelet drain", None, 1)[0]["images"][0]["url"]
    assert resolve_image_url(index, pdf_img).is_file()


def test_prompt_contains_sources_catalogue_mode_and_question(index):
    hits = retrieve(index, "kubernetes upgrade", None, 3)
    cat = image_catalogue(hits)
    p = build_prompt("How do I upgrade?", "report", hits, cat)
    assert '<source id="1"' in p and "k8s-upgrade.pdf" in p
    assert cat[0]["url"] in p
    assert "detailed report" in p
    assert p.rstrip().endswith("<question>How do I upgrade?</question>")


def test_vision_blocks_are_base64_images(index):
    hits = retrieve(index, "VLAN trunk kubelet", None, 10)
    blocks = vision_blocks(index, image_catalogue(hits), limit=2)
    imgs = [b for b in blocks if b["type"] == "image"]
    assert 1 <= len(imgs) <= 2
    assert base64.b64decode(imgs[0]["data"])[:4] == b"\x89PNG"


def test_local_answer_without_ai(index):
    out = collect(answer_events(index, Settings(provider="none"), "kubelet drain upgrade", "summary"))
    assert out["sources"][0]["path"] == "k8s-upgrade.pdf"
    assert out["done"] == {"ai": False}
    assert "No AI provider" in out["text"]
    assert "/api/cache-image/" in out["text"]  # the PDF figure comes along


def test_local_report_longer_than_summary(index):
    s = collect(answer_events(index, Settings(provider="none"), "VLAN network router DNS", "summary"))
    r = collect(answer_events(index, Settings(provider="none"), "VLAN network router DNS", "report"))
    assert len(r["text"]) > len(s["text"])


def test_no_hits(index):
    out = collect(answer_events(index, Settings(provider="anthropic", api_key="k"), "xylophone quasar", "summary"))
    assert out["sources"] == [] and "No matching notes" in out["text"]


def test_filters_reach_retrieval(index):
    out = collect(answer_events(index, Settings(provider="none"), "VLAN upgrade", "summary", Filters(types=["pdf"])))
    assert {s["path"] for s in out["sources"]} == {"k8s-upgrade.pdf"}


def test_ai_answer_streams_with_fake_provider(index, monkeypatch):
    seen = {}

    def fake(settings, system, blocks, max_tokens):
        seen.update(system=system, blocks=blocks, max_tokens=max_tokens)
        yield "Upgrade by draining nodes [1].\n\n"
        yield "![fig](/api/cache-image/x.png)"

    monkeypatch.setattr(answer_mod, "stream_answer", fake)
    s = Settings(provider="anthropic", api_key="sk-test", send_images=True, max_images=3)
    out = collect(answer_events(index, s, "how to upgrade kubernetes", "report"))
    assert out["text"].startswith("Upgrade by draining")
    assert out["done"]["ai"] is True
    assert seen["max_tokens"] == 32000
    assert any(b["type"] == "image" for b in seen["blocks"])
    assert seen["blocks"][-1]["type"] == "text" and "<question>" in seen["blocks"][-1]["text"]


def test_send_images_off(index, monkeypatch):
    seen = {}

    def fake(settings, system, blocks, max_tokens):
        seen["blocks"] = blocks
        yield "ok"

    monkeypatch.setattr(answer_mod, "stream_answer", fake)
    collect(answer_events(index, Settings(api_key="k", send_images=False), "kubelet", "summary"))
    assert all(b["type"] == "text" for b in seen["blocks"])


def test_provider_error_becomes_error_event(index, monkeypatch):
    def boom(*a, **k):
        raise llm.LLMError("bad key")
        yield

    monkeypatch.setattr(answer_mod, "stream_answer", boom)
    out = collect(answer_events(index, Settings(api_key="k"), "kubelet", "summary"))
    assert out["error"] == {"message": "bad key"} and out["done"] is None


# ------------------------------------------------------------------ Anthropic provider wiring


class FakeStream:
    def __init__(self, stop_reason="end_turn"):
        self.text_stream = iter(["Hello ", "notes"])
        self._stop = stop_reason

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return types.SimpleNamespace(stop_reason=self._stop)


@pytest.fixture()
def fake_anthropic(monkeypatch):
    import anthropic

    calls = {}

    class FakeClient:
        def __init__(self, api_key=None):
            calls["api_key"] = api_key
            stream = lambda **kw: (calls.update(kw=kw, beta=False), FakeStream(calls.get("stop", "end_turn")))[1]
            beta_stream = lambda **kw: (calls.update(kw=kw, beta=True), FakeStream(calls.get("stop", "end_turn")))[1]
            self.messages = types.SimpleNamespace(stream=stream)
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(stream=beta_stream))

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    return calls


def test_anthropic_request_shape(fake_anthropic):
    s = Settings(provider="anthropic", api_key="sk-abc", model="claude-opus-5-5", effort="high")
    blocks = [{"type": "image", "media_type": "image/png", "data": "AAA"}, {"type": "text", "text": "Q"}]
    text = "".join(llm.stream_answer(s, "SYS", blocks, 8000))
    kw = fake_anthropic["kw"]
    assert text == "Hello notes"
    assert fake_anthropic["api_key"] == "sk-abc" and fake_anthropic["beta"] is True
    assert kw["model"] == "claude-opus-5-5" and kw["system"] == "SYS" and kw["max_tokens"] == 8000
    assert kw["output_config"] == {"effort": "high"}
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in kw  # adaptive thinking is the default on Opus 5.5
    img = kw["messages"][0]["content"][0]
    assert img == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA"}}


def test_anthropic_other_models_use_plain_stream(fake_anthropic):
    s = Settings(provider="anthropic", api_key="k", model="claude-haiku-4-5")
    "".join(llm.stream_answer(s, "S", [{"type": "text", "text": "q"}], 100))
    assert fake_anthropic["beta"] is False and "output_config" not in fake_anthropic["kw"]


def test_anthropic_refusal_and_truncation_notes(fake_anthropic):
    s = Settings(provider="anthropic", api_key="k")
    fake_anthropic["stop"] = "refusal"
    assert "declined" in "".join(llm.stream_answer(s, "S", [{"type": "text", "text": "q"}], 100))
    fake_anthropic["stop"] = "max_tokens"
    assert "truncated" in "".join(llm.stream_answer(s, "S", [{"type": "text", "text": "q"}], 100))


def test_openai_compatible_request_shape(monkeypatch):
    import openai

    calls = {}

    class FakeOpenAI:
        def __init__(self, api_key=None, base_url=None):
            calls.update(api_key=api_key, base_url=base_url)

            def create(**kw):
                calls["kw"] = kw
                chunk = lambda t: types.SimpleNamespace(choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=t))])
                return iter([chunk("Hi"), chunk(None), chunk(" there")])

            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=create))

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    s = Settings(provider="openai", model="llama3.2", base_url="http://localhost:11434/v1")
    blocks = [{"type": "image", "media_type": "image/png", "data": "AAA"}, {"type": "text", "text": "Q"}]
    assert "".join(llm.stream_answer(s, "SYS", blocks, 50)) == "Hi there"
    assert calls["base_url"] == "http://localhost:11434/v1" and calls["api_key"] == "not-needed"
    msgs = calls["kw"]["messages"]
    assert msgs[0] == {"role": "system", "content": "SYS"}
    assert msgs[1]["content"][0]["image_url"]["url"] == "data:image/png;base64,AAA"


def test_real_anthropic_sdk_request_and_stream(monkeypatch):
    """Drive the real SDK over a mocked HTTP transport: checks the wire request and SSE parsing."""
    import json as _json

    import anthropic
    import httpx2 as httpx

    captured = {}
    events = [
        ("message_start", {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant",
            "model": "claude-opus-5-5", "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 0}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Drain nodes "}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "first [1]."}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 5}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    body = "".join(f"event: {e}\ndata: {_json.dumps(d)}\n\n" for e, d in events)

    def handler(request: httpx.Request):
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["json"] = _json.loads(request.content)
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    real = anthropic.Anthropic
    monkeypatch.setattr(anthropic, "Anthropic",
                        lambda **kw: real(**kw, http_client=httpx.Client(transport=httpx.MockTransport(handler))))
    s = Settings(provider="anthropic", api_key="sk-ant-test", model="claude-opus-5-5", effort="high")
    blocks = [{"type": "image", "media_type": "image/png", "data": "iVBORw0KGgo="}, {"type": "text", "text": "Q?"}]
    assert "".join(llm.stream_answer(s, "SYS", blocks, 8000)) == "Drain nodes first [1]."

    req = captured["json"]
    assert captured["url"].startswith("https://api.anthropic.com/v1/messages")
    assert captured["headers"]["x-api-key"] == "sk-ant-test"
    assert "server-side-fallback-2026-07-01" in captured["headers"]["anthropic-beta"]
    assert req["model"] == "claude-opus-5-5" and req["stream"] is True and req["max_tokens"] == 8000
    assert req["output_config"] == {"effort": "high"} and req["fallbacks"] == "default"
    assert req["system"] == "SYS"
    assert req["messages"][0]["content"][0]["source"]["media_type"] == "image/png"


def test_real_anthropic_sdk_auth_error(monkeypatch):
    import anthropic
    import httpx2 as httpx

    def handler(request):
        return httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})

    real = anthropic.Anthropic
    monkeypatch.setattr(anthropic, "Anthropic",
                        lambda **kw: real(**kw, max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler))))
    with pytest.raises(llm.LLMError, match="rejected the API key"):
        "".join(llm.stream_answer(Settings(api_key="bad"), "S", [{"type": "text", "text": "q"}], 10))


def test_ai_switch_off_makes_no_ai_calls(index, monkeypatch):
    def fail(*a, **k):
        raise AssertionError("the AI must not be called while AI features are off")

    monkeypatch.setattr(answer_mod, "stream_answer", fail)
    s = Settings(provider="anthropic", api_key="sk-test", ai_features=False)
    out = collect(answer_events(index, s, "how to upgrade kubernetes", "summary"))
    assert out["done"]["ai"] is False
    assert out["text"].startswith("_AI features are turned off in Settings")
