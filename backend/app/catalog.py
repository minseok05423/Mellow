"""What the student can pick from (SPEC §7). Only models whose provider key is configured are offered;
requests naming anything else are rejected, so keys and costs stay under server control."""
from pydantic import BaseModel

from . import config, recognize


class ModelInfo(BaseModel):
    id: str
    label: str
    provider: str
    vision: bool
    price: str  # human-readable, per million tokens (input / output)
    note: str = ""


MODELS = [
    ModelInfo(id="deepseek-flash", label="DeepSeek Flash", provider="deepseek", vision=True,
              price="$0.15 / $0.60 (off-peak; peak ×2)", note="image input is experimental"),
    ModelInfo(id="deepseek-v4-pro", label="DeepSeek V4 Pro", provider="deepseek", vision=False,
              price="$0.66 / $1.98 (off-peak; peak ×2)"),
    ModelInfo(id="claude-haiku-5-5", label="Claude Haiku 5.5", provider="anthropic", vision=True, price="$0.10 / $0.50"),
    ModelInfo(id="claude-sonnet-5-5", label="Claude Sonnet 5.5", provider="anthropic", vision=True, price="$2 / $10"),
    ModelInfo(id="claude-opus-5-5", label="Claude Opus 5.5", provider="anthropic", vision=True, price="$4 / $20"),
]
RECOGNIZER_LABELS = {"mathpix": "Mathpix", "myscript": "MyScript", "none": "None (type readings yourself)"}


def model_available(model_id: str, *, vision: bool = False) -> bool:
    m = next((m for m in MODELS if m.id == model_id), None)
    return bool(m and (m.vision or not vision) and config.llm_configured(model_id))


def catalog() -> dict:
    return {
        "recognizers": [{"id": r, "label": label, "available": r == "none" or recognize.recognizer_configured(r)}
                        for r, label in RECOGNIZER_LABELS.items()],
        "readers": [{**m.model_dump(), "available": model_available(m.id, vision=True)} for m in MODELS if m.vision],
        "reasoners": [{**m.model_dump(), "available": model_available(m.id)} for m in MODELS],
        "defaults": {"recognizer": config.RECOGNIZER, "reader": config.FALLBACK_READER_MODEL,
                     "reasoner": config.REASONING_MODEL},
    }
