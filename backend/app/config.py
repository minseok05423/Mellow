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

# Models. Every call goes to Anthropic (DECISIONS #39); a deepseek-* id still works if set (#15).
FALLBACK_READER_MODEL = env("FALLBACK_READER_MODEL", "claude-haiku-5-5")  # must accept images
# Lean (DECISIONS #25, #27): Claude, which proved 25/25 problems in L0 where DeepSeek V4 Pro proved 1 of 4.
LEAN_MODEL = env("LEAN_MODEL", "claude-sonnet-5-5")  # statements, translations, single-claim proofs
LEAN_WORDS_MODEL = env("LEAN_WORDS_MODEL", "claude-haiku-5-5")  # reads a statement back in words, independently
# The graph check (DECISIONS #32, #35): a cheap model splits the proof into steps, a strong one links them; the steps'
# SymPy/Lean forms and the three judges use CHECK_MODEL.
CHUNK_MODEL = env("CHUNK_MODEL", "claude-haiku-5-5")
GRAPH_MODEL = env("GRAPH_MODEL", "claude-opus-5-5")
CHECK_MODEL = env("CHECK_MODEL", "claude-sonnet-5-5")
# When set, every model call is answered by Claude Code agents through this folder (leaneval/agentq.py), not an API.
AGENT_QUEUE = env("MELLOW_AGENT_QUEUE")
DEEPSEEK_API_KEY = env("DEEPSEEK_API_KEY")  # native API (DECISIONS #13)
DEEPSEEK_BASE_URL = env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
# Longest wait between streamed chunks: Claude at high effort can think silently for over 20 s.
LLM_TIMEOUT_S = float(env("LLM_TIMEOUT_S", "120"))
VENDOR_TIMEOUT_S = float(env("VENDOR_TIMEOUT_S", "10"))

# When set, every route but /health needs it in X-Mellow-Key, so a leaked server URL can't spend the LLM budget.
ACCESS_CODE = env("ACCESS_CODE")

# Daily caps per user, by cost class (SPEC §7).
CAPS = {
    "stroke": int(env("CAP_STROKE", "3000")),
    "read_llm": int(env("CAP_READ_LLM", "200")),
    "reason": int(env("CAP_REASON", "50")),  # the leaneval experiments' class
    "lean": int(env("CAP_LEAN", "300")),  # model calls for Lean: a statement is 2, a check 1-10
    "check": int(env("CAP_CHECK", "600")),  # model calls for the graph check: about 2 + 3 per judged step
}


def llm_configured(model: str) -> bool:
    """Is there a key for the provider serving `model`?"""
    if AGENT_QUEUE:
        return True
    return bool(DEEPSEEK_API_KEY) if model.startswith("deepseek-") else bool(env("ANTHROPIC_API_KEY"))
