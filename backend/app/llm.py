"""Every LLM call goes through `structured_call` (SPEC §7, non-negotiables):

cache -> quota -> call -> Pydantic validation + semantic checks -> one retry with the
error appended -> clean error. Every attempt is logged; invalid output is never returned.

The provider sits behind a small interface so the model tests (DECISIONS #12) can swap Claude
for DeepSeek or another host without touching endpoint code.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Callable, Protocol, TypeVar

import httpx2 as httpx

from pydantic import BaseModel, ValidationError

from . import config, store

M = TypeVar("M", bound=BaseModel)


class LLMError(Exception):
    """Clean failure after the retry. The message is safe to show the student."""


class QuotaExceeded(Exception):
    pass


@dataclass
class Completion:
    text: str  # raw model output (JSON text), kept for the retry message and the log
    parsed: BaseModel | None
    input_tokens: int = 0
    output_tokens: int = 0
    refused: bool = False


# Streaming: providers always stream (timeout protection, and tokens can be shown as they arrive).
# `on_token(kind, text)` receives ("token", text) per chunk and ("reset", "") before a retry, so a UI
# showing partial text can clear the attempt that failed validation. Only validated output is returned.
OnToken = Callable[[str, str], None]


class Provider(Protocol):
    name: str

    def complete(self, *, model: str, system: str, messages: list[dict], schema: type[BaseModel],
                 effort: str, max_tokens: int, on_token: OnToken | None = None) -> Completion: ...


class ClaudeProvider:
    name = "anthropic"

    def __init__(self, client=None):
        import anthropic

        self.client = client or anthropic.Anthropic(timeout=config.LLM_TIMEOUT_S, max_retries=1)

    def complete(self, *, model, system, messages, schema, effort, max_tokens, on_token=None):
        kwargs = {}
        if model in ("claude-opus-5-5", "claude-sonnet-5-5"):
            # Server-side refusal fallback: a declined request is re-run on another model inside
            # the same call instead of failing (Anthropic's recommended default for these models).
            kwargs = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        try:
            with self.client.beta.messages.stream(
                model=model, max_tokens=max_tokens, system=system, messages=messages,
                output_format=schema, output_config={"effort": effort}, **kwargs,
            ) as stream:
                for text in stream.text_stream:
                    if on_token:
                        on_token("token", text)
                r = stream.get_final_message()
        except ValidationError as e:  # the SDK validates against `schema`; surface as invalid output
            return Completion(text=str(e), parsed=None)
        text = next((b.text for b in r.content if b.type == "text"), "")
        return Completion(
            text=text,
            parsed=r.parsed_output if r.stop_reason != "refusal" else None,
            input_tokens=r.usage.input_tokens,
            output_tokens=r.usage.output_tokens,
            refused=r.stop_reason == "refusal",
        )


class DeepSeekProvider:
    """DeepSeek's native API (OpenAI chat format). JSON mode only, no strict schema, so the schema goes
    in the prompt and our Pydantic validation + retry does the enforcing.
    Images: only `deepseek-flash` takes them (DeepSeek labels its vision experimental)."""

    name = "deepseek"
    VISION_MODELS = {"deepseek-flash"}
    # Our effort levels -> DeepSeek thinking mode. "low" turns thinking off (fast live reading).
    THINKING = {
        "low": {"thinking": {"type": "disabled"}},
        "medium": {"thinking": {"type": "enabled"}, "reasoning_effort": "low"},
        "high": {"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
        "xhigh": {"thinking": {"type": "enabled"}, "reasoning_effort": "max"},
        "max": {"thinking": {"type": "enabled"}, "reasoning_effort": "max"},
    }

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.http = httpx.Client(base_url=config.DEEPSEEK_BASE_URL, timeout=config.LLM_TIMEOUT_S, transport=transport)

    def complete(self, *, model, system, messages, schema, effort, max_tokens, on_token=None):
        def convert(content):
            """Anthropic-style content blocks -> OpenAI-style parts."""
            if isinstance(content, str):
                return content
            parts = []
            for b in content:
                if b["type"] == "text":
                    parts.append({"type": "text", "text": b["text"]})
                elif b["type"] == "image" and model in self.VISION_MODELS:
                    src = b["source"]
                    parts.append({"type": "image_url",
                                  "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"}})
                else:
                    raise ValueError(f"{model} takes text only")
            return parts

        body = {
            "model": model, "max_tokens": max_tokens, "stream": True, **self.THINKING.get(effort, {}),
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system + "\n\nReply with json only, matching this JSON schema:\n"
                          + json.dumps(schema.model_json_schema())}]
            + [{"role": m["role"], "content": convert(m["content"])} for m in messages],
        }
        text, usage, finish = "", {}, None
        with self.http.stream("POST", "/chat/completions", json=body,
                              headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}"}) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line.startswith("data:") or line.strip() == "data: [DONE]":
                    continue
                chunk = json.loads(line[5:])
                usage = chunk.get("usage") or usage
                for choice in chunk.get("choices", []):
                    # Thinking arrives in delta.reasoning_content; only the answer (delta.content) is kept.
                    piece = (choice.get("delta") or {}).get("content") or ""
                    if piece:
                        text += piece
                        if on_token:
                            on_token("token", piece)
                    finish = choice.get("finish_reason") or finish
        try:
            parsed = schema.model_validate_json(text) if text.strip() else None
        except ValidationError:
            parsed = None
        return Completion(text=text, parsed=parsed, refused=finish == "content_filter",
                          input_tokens=usage.get("prompt_tokens", 0), output_tokens=usage.get("completion_tokens", 0))


_override: Provider | None = None
_providers: dict[str, Provider] = {}


def get_provider(model: str) -> Provider:
    """Route by model name: deepseek-* -> DeepSeek's API, everything else -> Claude."""
    if _override is not None:
        return _override
    kind = "deepseek" if model.startswith("deepseek-") else "anthropic"
    if kind not in _providers:
        _providers[kind] = DeepSeekProvider() if kind == "deepseek" else ClaudeProvider()
    return _providers[kind]


def set_provider(p: Provider | None) -> None:  # tests inject fakes here
    global _override
    _override = p


def cache_key(endpoint: str, cache_input, model: str) -> str:
    blob = json.dumps([endpoint, cache_input, model, config.SCHEMA_VERSION], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def structured_call(
    *, endpoint: str, user: str, cost_class: str, model: str, system: str, content: list[dict],
    schema: type[M], cache_input, semantic_check: Callable[[M], list[str]] | None = None,
    effort: str = "medium", max_tokens: int = 4096, on_token: OnToken | None = None,
) -> M:
    key = cache_key(endpoint, cache_input, model)
    if (hit := store.cache_get(key)) is not None:
        store.log(user=user, endpoint=endpoint, provider="cache", model=model, cache_key=key,
                  request=cache_input, response=hit, valid=1, retries=0, cached=1, latency_ms=0)
        return schema.model_validate(hit)

    if not store.take_quota(user, cost_class):
        raise QuotaExceeded(f"Daily limit reached for {cost_class}")

    provider = get_provider(model)
    messages = [{"role": "user", "content": content}]
    last_error = ""
    for attempt in range(2):
        t = time.time()
        if attempt and on_token:
            on_token("reset", "")
        try:
            out = provider.complete(model=model, system=system, messages=messages, schema=schema,
                                    effort=effort, max_tokens=max_tokens, on_token=on_token)
            errors = (["refused"] if out.refused else
                      ["output did not match the schema: " + out.text[:500]] if out.parsed is None else
                      (semantic_check(out.parsed) if semantic_check else []))
        except Exception as e:  # timeouts, network, API errors
            out, errors = None, [f"{type(e).__name__}: {e}"]
        latency = int((time.time() - t) * 1000)
        store.log(user=user, endpoint=endpoint, provider=provider.name, model=model, cache_key=key,
                  request=cache_input, response=out.text if out else None, valid=int(not errors),
                  error="; ".join(errors) or None, retries=attempt, cached=0, latency_ms=latency,
                  input_tokens=out.input_tokens if out else 0, output_tokens=out.output_tokens if out else 0)
        if not errors:
            value = out.parsed.model_dump()
            store.cache_put(key, value)
            return out.parsed
        last_error = "; ".join(errors)
        if out is None or out.refused:
            break  # retrying the same request won't fix a network error or a refusal
        # Retry once, showing the model its output and what was wrong with it.
        messages = messages[:1] + [
            {"role": "assistant", "content": out.text or "(empty)"},
            {"role": "user", "content": f"That output was invalid: {last_error}. Return corrected output only."},
        ]
    raise LLMError(f"The assistant couldn't produce a valid answer ({endpoint}). {last_error[:200]}")
