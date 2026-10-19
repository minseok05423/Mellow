"""The student's model choice: offered only when configured, honoured per request, rejected otherwise."""
import pytest
from fastapi.testclient import TestClient

from app import config, llm, recognize
from app.main import app
from app.recognize import FallbackReading

client = TestClient(app)
H = {"X-Mellow-User": "test-user-1"}
LINE = {"strokes": [{"t0": 0, "pts": [[0, 0, 0.5, 0], [5, 5, 0.5, 16]]}], "image": "data:image/png;base64,AAAA"}


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "ds")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(config, "MYSCRIPT_APP_KEY", "a")
    monkeypatch.setattr(config, "MYSCRIPT_HMAC_KEY", "h")
    monkeypatch.setattr(config, "MATHPIX_APP_ID", "")
    monkeypatch.setattr(config, "MATHPIX_APP_KEY", "")


def test_catalog_marks_what_is_configured(keys):
    c = client.get("/models").json()
    avail = lambda group: {m["id"] for m in c[group] if m["available"]}
    assert avail("recognizers") == {"myscript", "none"}
    assert avail("readers") == {"deepseek-flash"}           # vision models with a key
    assert avail("reasoners") == {"deepseek-flash", "deepseek-v4-pro"}
    assert "deepseek-v4-pro" not in {m["id"] for m in c["readers"]}  # no image input
    assert all(m["price"] for m in c["reasoners"])


def test_unavailable_or_text_only_choices_are_rejected(keys):
    post = lambda **kw: client.post("/recognize", headers=H, json={**LINE, **kw})
    assert post(recognizer="mathpix").status_code == 400
    assert post(reader_model="claude-haiku-5-5").status_code == 400   # no Anthropic key
    assert post(reader_model="deepseek-v4-pro").status_code == 400    # can't read images
    assert post(reader_model="gpt-whatever").status_code == 400


def test_chosen_reader_model_is_used(keys, monkeypatch):
    used = []

    class Fake:
        name = "fake"

        def complete(self, *, model, on_token=None, **kw):
            used.append(model)
            out = FallbackReading(latex="x", confidence=0.9, uncertain=[], has_prose=False)
            return llm.Completion(text=out.model_dump_json(), parsed=out)
    llm.set_provider(Fake())
    r = client.post("/recognize", headers=H, json={**LINE, "recognizer": "none", "reader_model": "deepseek-flash"})
    assert r.status_code == 200 and r.json()["source"] == "llm" and used == ["deepseek-flash"]


def test_chosen_recognizer_is_used(keys, monkeypatch):
    calls = []
    monkeypatch.setitem(recognize.RECOGNIZERS, "myscript",
                        lambda strokes: calls.append(1) or recognize.Reading(latex="1+2", confidence=None, source="myscript"))
    r = client.post("/recognize", headers=H, json={**LINE, "recognizer": "myscript"})
    assert r.json()["latex"] == "1+2" and calls == [1]
