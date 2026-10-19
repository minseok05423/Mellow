import pytest

from app import config, llm


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Every test gets its own SQLite file and no real provider or key."""
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")  # tests never reach a real API with the .env keys
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    llm.set_provider(None)
    yield
    llm.set_provider(None)
