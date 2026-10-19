"""The non-negotiables around every LLM call: cache, quota, validation + one retry, logging."""
import sqlite3

import pytest
from pydantic import BaseModel

from app import config, llm


class Out(BaseModel):
    latex: str


class FakeProvider:
    name = "fake"

    def __init__(self, outputs):
        self.outputs = list(outputs)  # each: Out | None (schema failure) | "refuse" | Exception
        self.calls = []

    def complete(self, *, model, system, messages, schema, effort, max_tokens, on_token=None):
        self.calls.append(messages)
        o = self.outputs.pop(0)
        if isinstance(o, Exception):
            raise o
        if o == "refuse":
            return llm.Completion(text="", parsed=None, refused=True)
        return llm.Completion(text=o.model_dump_json() if o else "{bad", parsed=o, input_tokens=10, output_tokens=5)


def call(semantic=None, user="user-1", key="k"):
    return llm.structured_call(endpoint="test", user=user, cost_class="read_llm", model="m", system="s",
                               content=[{"type": "text", "text": "hi"}], schema=Out, cache_input=key,
                               semantic_check=semantic)


def log_rows():
    with sqlite3.connect(config.DB_PATH) as c:
        return c.execute("SELECT provider, valid, retries, cached, error FROM request_log ORDER BY id").fetchall()


def test_valid_output_is_cached_and_logged():
    p = FakeProvider([Out(latex="x")])
    llm.set_provider(p)
    assert call().latex == "x"
    assert call().latex == "x"  # second call served from cache
    assert len(p.calls) == 1
    assert log_rows() == [("fake", 1, 0, 0, None), ("cache", 1, 0, 1, None)]


def test_invalid_then_valid_retries_once_with_error_appended():
    p = FakeProvider([None, Out(latex="y")])
    llm.set_provider(p)
    assert call().latex == "y"
    retry_msgs = p.calls[1]
    assert retry_msgs[1]["role"] == "assistant" and "invalid" in retry_msgs[2]["content"]
    assert [r[1:3] for r in log_rows()] == [(0, 0), (1, 1)]


def test_semantic_failure_triggers_retry():
    p = FakeProvider([Out(latex="{"), Out(latex="{}")])
    llm.set_provider(p)
    check = lambda o: [] if o.latex.count("{") == o.latex.count("}") else ["unbalanced braces"]
    assert call(semantic=check).latex == "{}"
    assert "unbalanced braces" in p.calls[1][2]["content"]


def test_two_failures_give_clean_error_and_nothing_cached():
    llm.set_provider(FakeProvider([None, None]))
    with pytest.raises(llm.LLMError):
        call()
    llm.set_provider(FakeProvider([Out(latex="z")]))
    assert call().latex == "z"  # the failure wasn't cached


def test_refusal_and_network_errors_are_not_retried():
    p = FakeProvider(["refuse", Out(latex="never")])
    llm.set_provider(p)
    with pytest.raises(llm.LLMError):
        call()
    assert len(p.calls) == 1
    p = FakeProvider([TimeoutError("slow"), Out(latex="never")])
    llm.set_provider(p)
    with pytest.raises(llm.LLMError, match="TimeoutError"):
        call(key="k2")
    assert len(p.calls) == 1


def test_daily_cap(monkeypatch):
    monkeypatch.setitem(config.CAPS, "read_llm", 2)
    llm.set_provider(FakeProvider([Out(latex="a"), Out(latex="b"), Out(latex="c")]))
    call(key=1)
    call(key=2)
    with pytest.raises(llm.QuotaExceeded):
        call(key=3)
    call(key=1)  # cache hits don't count against the cap
    llm.set_provider(FakeProvider([Out(latex="d")]))
    call(user="someone-else", key=3)  # caps are per user


def test_streaming_callback_gets_tokens_and_a_reset_before_the_retry():
    class Streaming(FakeProvider):
        def complete(self, *, on_token=None, **kw):
            out = super().complete(**kw)
            if on_token:
                on_token("token", out.text)
            return out

    llm.set_provider(Streaming([None, Out(latex="ok")]))
    events = []
    llm.structured_call(endpoint="t", user="user-1", cost_class="reason", model="m", system="s",
                        content=[{"type": "text", "text": "hi"}], schema=Out, cache_input="stream",
                        on_token=lambda k, t: events.append(k))
    assert events == ["token", "reset", "token"]


def test_models_route_to_their_provider(monkeypatch):
    llm.set_provider(None)
    monkeypatch.setattr(llm, "_providers", {"anthropic": "claude-p", "deepseek": "deepseek-p"})
    assert llm.get_provider("deepseek-v4-pro") == "deepseek-p"
    assert llm.get_provider("claude-opus-5-5") == "claude-p"
