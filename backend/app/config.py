"""Settings from environment variables (or backend/.env). Keys never leave the server."""
import os
from pathlib import Path

_env = Path(__file__).resolve().parent.parent / ".env"
if _env.exists():
    for line in _env.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


DB_PATH = env("MELLOW_DB", str(Path(__file__).resolve().parent.parent / "mellow.sqlite3"))
SCHEMA_VERSION = 1  # part of every cache key; bump when prompts or schemas change meaning

# Reading (SPEC §4/§7). RECOGNIZER: mathpix | myscript | none
RECOGNIZER = env("RECOGNIZER", "mathpix")
MATHPIX_APP_ID, MATHPIX_APP_KEY = env("MATHPIX_APP_ID"), env("MATHPIX_APP_KEY")
MYSCRIPT_APP_KEY, MYSCRIPT_HMAC_KEY = env("MYSCRIPT_APP_KEY"), env("MYSCRIPT_HMAC_KEY")
CONFIDENCE_MIN = float(env("CONFIDENCE_MIN", "0.8"))  # below this, the LLM fallback re-reads the line

# Models (DECISIONS #15): DeepSeek for every LLM call for now. Any claude-* id works too.
FALLBACK_READER_MODEL = env("FALLBACK_READER_MODEL", "deepseek-flash")  # must accept images
REASONING_MODEL = env("REASONING_MODEL", "deepseek-v4-pro")
DEEPSEEK_API_KEY = env("DEEPSEEK_API_KEY")  # native API (DECISIONS #13)
DEEPSEEK_BASE_URL = env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
LLM_TIMEOUT_S = float(env("LLM_TIMEOUT_S", "20"))
VENDOR_TIMEOUT_S = float(env("VENDOR_TIMEOUT_S", "10"))

# Daily caps per user, by cost class (SPEC §7).
CAPS = {
    "stroke": int(env("CAP_STROKE", "3000")),
    "read_llm": int(env("CAP_READ_LLM", "200")),
    "reason": int(env("CAP_REASON", "50")),
    "structure": int(env("CAP_STRUCTURE", "500")),  # page labelling (DECISIONS #21), cheap model
}


def llm_configured(model: str) -> bool:
    """Is there a key for the provider serving `model`?"""
    return bool(DEEPSEEK_API_KEY) if model.startswith("deepseek-") else bool(env("ANTHROPIC_API_KEY"))
