"""Exact requests the providers send, against mock HTTP transports (no network, no keys)."""
import json

import anthropic
import httpx2 as httpx
import pytest
from pydantic import BaseModel

from app import config
from app.llm import ClaudeProvider, DeepSeekProvider


class Out(BaseModel):
    latex: str


def sse(events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


def claude_stream(text_chunks, stop_reason="end_turn"):
    return sse([
        {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant", "model": "m",
                                              "content": [], "stop_reason": None, "stop_sequence": None,
                                              "usage": {"input_tokens": 12, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        *[{"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": t}} for t in text_chunks],
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
         "usage": {"output_tokens": 7}},
        {"type": "message_stop"},
    ])


def claude(sent, stop_reason="end_turn"):
    def handler(request):
        sent.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=claude_stream(['{"latex": ', '"2q^2"}'], stop_reason))
    return ClaudeProvider(anthropic.Anthropic(api_key="test", http_client=anthropic.DefaultHttpxClient(
        transport=httpx.MockTransport(handler))))


def run(provider, model="claude-opus-5-5", tokens=None):
    return provider.complete(model=model, system="sys", schema=Out, effort="low", max_tokens=100,
                             messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
                             on_token=(lambda k, t: tokens.append(t)) if tokens is not None else None)


def test_claude_streams_tokens_and_parses_the_final_message():
    sent, tokens = [], []
    out = run(claude(sent), tokens=tokens)
    body = json.loads(sent[0].content)
    assert tokens == ['{"latex": ', '"2q^2"}']
    assert out.parsed == Out(latex="2q^2") and out.input_tokens == 12 and out.output_tokens == 7
    assert body["stream"] is True
    assert body["output_config"]["effort"] == "low" and body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in sent[0].headers["anthropic-beta"]


def test_claude_haiku_has_no_server_side_fallback():
    sent = []
    run(claude(sent), model="claude-haiku-5-5")
    assert "fallbacks" not in json.loads(sent[0].content)


def test_claude_refusal_is_reported_not_parsed():
    out = run(claude([], stop_reason="refusal"))
    assert out.refused and out.parsed is None


def deepseek(sent, chunks, monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "ds-key")

    def handler(request):
        sent.append(request)
        lines = [f"data: {json.dumps({'choices': [{'delta': {'content': c}, 'finish_reason': None}]})}" for c in chunks]
        lines.append("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}],
                                            "usage": {"prompt_tokens": 30, "completion_tokens": 9}}))
        lines.append("data: [DONE]")
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="\n\n".join(lines) + "\n\n")
    return DeepSeekProvider(transport=httpx.MockTransport(handler))


def test_deepseek_native_api_streams_json_mode(monkeypatch):
    sent, tokens = [], []
    out = run(deepseek(sent, ['{"latex"', ': "x"}'], monkeypatch), model="deepseek-v4-pro", tokens=tokens)
    req = sent[0]
    body = json.loads(req.content)
    assert str(req.url) == "https://api.deepseek.com/chat/completions"
    assert req.headers["authorization"] == "Bearer ds-key"
    assert body["model"] == "deepseek-v4-pro" and body["stream"] is True
    assert body["response_format"] == {"type": "json_object"}
    assert "json" in body["messages"][0]["content"] and '"latex"' in body["messages"][0]["content"]  # schema in prompt
    assert tokens == ['{"latex"', ': "x"}'] and out.parsed == Out(latex="x")
    assert (out.input_tokens, out.output_tokens) == (30, 9)


def test_deepseek_invalid_json_is_reported_as_invalid(monkeypatch):
    out = run(deepseek([], ['{"wrong": 1}'], monkeypatch), model="deepseek-v4-pro")
    assert out.parsed is None and out.text == '{"wrong": 1}'


IMAGE_MSG = [{"role": "user", "content": [
    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
    {"type": "text", "text": "read this"}]}]


def test_deepseek_pro_rejects_images(monkeypatch):
    p = deepseek([], [], monkeypatch)
    with pytest.raises(ValueError, match="text only"):
        p.complete(model="deepseek-v4-pro", system="s", schema=Out, effort="low", max_tokens=10, messages=IMAGE_MSG)


def test_deepseek_flash_sends_images_with_thinking_off_for_low_effort(monkeypatch):
    sent = []
    p = deepseek(sent, ['{"latex": "x"}'], monkeypatch)
    p.complete(model="deepseek-flash", system="s", schema=Out, effort="low", max_tokens=10, messages=IMAGE_MSG)
    body = json.loads(sent[0].content)
    assert body["messages"][1]["content"] == [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        {"type": "text", "text": "read this"},
    ]
    assert body["thinking"] == {"type": "disabled"} and "reasoning_effort" not in body


def test_deepseek_reasoning_turns_thinking_on(monkeypatch):
    sent = []
    run(deepseek(sent, ['{"latex": "x"}'], monkeypatch), model="deepseek-v4-pro")  # effort="low" in run()
    sent2 = []
    deepseek(sent2, ['{"latex": "x"}'], monkeypatch).complete(
        model="deepseek-v4-pro", system="s", schema=Out, effort="high", max_tokens=10,
        messages=[{"role": "user", "content": "hi"}])
    body = json.loads(sent2[0].content)
    assert body["thinking"] == {"type": "enabled"} and body["reasoning_effort"] == "high"


def test_deepseek_ignores_reasoning_content_in_the_stream(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "k")

    def handler(request):
        chunks = [{"choices": [{"delta": {"reasoning_content": "let me think"}}]},
                  {"choices": [{"delta": {"content": '{"latex": "y"}'}, "finish_reason": "stop"}]}]
        return httpx.Response(200, content="".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n")
    out = run(DeepSeekProvider(transport=httpx.MockTransport(handler)), model="deepseek-v4-pro")
    assert out.text == '{"latex": "y"}' and out.parsed == Out(latex="y")
