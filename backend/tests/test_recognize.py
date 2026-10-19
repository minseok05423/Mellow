"""Hybrid reading: the stroke recognizer first, the LLM fallback for prose / low confidence / no recognizer."""
import hashlib
import hmac
import json

import pytest

from app import config, llm, recognize
from app.recognize import FallbackReading, RecognizeRequest

STROKES = [{"t0": 1000, "pts": [[0, 0, 0.5, 0], [10, 5, 0.6, 16], [20, 0, 0.5, 33]]}]
IMAGE = "data:image/png;base64,iVBORw0KGgo="


class Resp:
    def __init__(self, data=None, text=""):
        self._data, self.text = data, text

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class FakeLLM:
    name = "fake"

    def __init__(self):
        self.calls = []

    def complete(self, *, model, system, messages, schema, effort, max_tokens, on_token=None):
        self.calls.append(messages)
        out = FallbackReading(latex=r"\text{hence } p \text{ is even}", confidence=0.9, uncertain=[], has_prose=True)
        return llm.Completion(text=out.model_dump_json(), parsed=out)


@pytest.fixture
def mathpix(monkeypatch):
    monkeypatch.setattr(config, "RECOGNIZER", "mathpix")
    monkeypatch.setattr(config, "MATHPIX_APP_ID", "id")
    monkeypatch.setattr(config, "MATHPIX_APP_KEY", "key")
    sent = []

    def respond_with(data):
        def post(url, **kw):
            sent.append((url, kw))
            return Resp(data)
        monkeypatch.setattr(recognize.httpx, "post", post)
        return sent
    return respond_with


@pytest.fixture
def fake_llm(monkeypatch):
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "test")
    f = FakeLLM()
    llm.set_provider(f)
    return f


def req(**kw):
    return RecognizeRequest(strokes=STROKES, **kw)


def test_confident_math_comes_from_mathpix_and_skips_llm(mathpix, fake_llm):
    sent = mathpix({"latex_styled": "2 q^{2}", "confidence": 0.97, "text": r"\( 2 q^{2} \)"})
    r = recognize.recognize(req(image=IMAGE), "user-1")
    assert (r.latex, r.source) == ("2 q^{2}", "mathpix") and not fake_llm.calls
    url, kw = sent[0]
    assert url.endswith("/v3/strokes") and kw["json"]["strokes"]["strokes"]["x"] == [[0, 10, 20]]
    assert kw["headers"] == {"app_id": "id", "app_key": "key"}


def test_same_strokes_are_cached(mathpix, fake_llm):
    sent = mathpix({"latex_styled": "x", "confidence": 0.99})
    recognize.recognize(req(), "user-1")
    recognize.recognize(req(), "user-1")
    assert len(sent) == 1


def test_low_confidence_goes_to_llm_with_the_guess_and_context(mathpix, fake_llm):
    mathpix({"latex_styled": "2 9^{2}", "confidence": 0.4})
    r = recognize.recognize(req(image=IMAGE, context=["2q^2=p^2"]), "user-1")
    assert r.source == "llm"
    text = fake_llm.calls[0][0]["content"][1]["text"]
    assert "2 9^{2}" in text and "2q^2=p^2" in text


def test_a_pasted_image_skips_the_stroke_recognizer_and_reads_everything(mathpix, fake_llm):
    sent = mathpix({"latex_styled": "x", "confidence": 1.0})
    r = recognize.recognize(RecognizeRequest(strokes=[], image=IMAGE), "user-1")
    assert r.source == "llm" and sent == []  # no strokes to send to Mathpix
    assert "transcribe all of its math and text" in fake_llm.calls[0][0]["content"][1]["text"]


def test_prose_goes_to_llm(mathpix, fake_llm):
    mathpix({"text": r"hence \( p \) is even", "confidence": 0.95})
    assert recognize.recognize(req(image=IMAGE), "user-1").source == "llm"


def test_words_read_as_letters_go_to_llm(monkeypatch, fake_llm):
    # MyScript's math mode reads "let f ... be" as single letters.
    monkeypatch.setattr(config, "RECOGNIZER", "myscript")
    monkeypatch.setattr(config, "MYSCRIPT_APP_KEY", "a")
    monkeypatch.setattr(config, "MYSCRIPT_HMAC_KEY", "h")
    monkeypatch.setattr(recognize.httpx, "post", lambda url, **kw: Resp(text=r"l e t f : \left[ 0 , 1 \right] b e"))
    assert recognize.recognize(req(image=IMAGE), "user-1").source == "llm"
    assert not recognize.looks_like_words(r"\sin x + 2 q^{2}")


def test_mathpix_mixed_text_to_latex():
    assert recognize._mathpix_text_to_latex(r"hence \( p \) is even") == r"\text{hence}\ p\ \text{is even}"


def test_no_recognizer_no_llm_returns_empty_reading(monkeypatch):
    monkeypatch.setattr(config, "RECOGNIZER", "none")
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    assert recognize.recognize(req(image=IMAGE), "user-1").source == "none"


def test_myscript_disables_solver_and_signs_body(monkeypatch):
    monkeypatch.setattr(config, "MYSCRIPT_APP_KEY", "app")
    monkeypatch.setattr(config, "MYSCRIPT_HMAC_KEY", "hk")
    sent = []
    monkeypatch.setattr(recognize.httpx, "post", lambda url, **kw: sent.append((url, kw)) or Resp(text="2q^{2}\n"))
    r = recognize.read_myscript(req().strokes)
    url, kw = sent[0]
    body = json.loads(kw["content"])
    assert url == "https://cloud.myscript.com/api/v4.0/iink/recognize"  # V2 REST endpoint
    assert kw["headers"]["Accept"] == "application/x-latex" and kw["headers"]["applicationKey"] == "app"
    assert r.latex == "2q^{2}"
    assert body["contentType"] == "Math" and body["configuration"]["math"]["solver"]["enable"] is False
    assert body["strokes"][0]["t"] == [1000, 1016, 1033] and body["strokes"][0]["p"] == [0.5, 0.6, 0.5]
    expected = hmac.new(b"apphk", kw["content"].encode(), hashlib.sha512).hexdigest()
    assert kw["headers"]["hmac"] == expected
