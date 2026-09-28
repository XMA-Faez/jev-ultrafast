"""Every environment-driven setting, with its default defined once."""

import os
import shutil
from pathlib import Path

TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
OPENROUTER_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_TEXT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TEXT_MODEL = "inception/mercury-2.5"
DEFAULT_MCP_IDLE_MINUTES = 10
DEFAULT_MCP_MAX_SESSIONS = 3
CHROME_CANDIDATES = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "brave",
    "brave-browser",
)


def decision_route():
    """TypeSafe directly when a TypeSafe key is set, otherwise Jev through OpenRouter's Decisions API."""
    if key := os.environ.get("TYPESAFE_API_KEY"):
        return TYPESAFE_URL, key, os.environ.get("TYPESAFE_MODEL", "jev-latest")
    if key := os.environ.get("OPENROUTER_API_KEY"):
        return OPENROUTER_DECISIONS_URL, key, os.environ.get("OPENROUTER_JEV_MODEL", "typesafe/jev-1.13")
    raise ValueError("Set TYPESAFE_API_KEY or OPENROUTER_API_KEY; no action executed.")


def text_model_name():
    return os.environ.get("TEXT_MODEL", DEFAULT_TEXT_MODEL)


def text_model():
    """Endpoint, model, key and reasoning payload for the OpenAI-compatible text helper."""
    key = os.environ.get("TEXT_MODEL_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise ValueError(
            "TYPE_TEXT needs TEXT_MODEL_API_KEY or OPENROUTER_API_KEY; no text is hardcoded or guessed by the executor."
        )
    base_url = os.environ.get("TEXT_MODEL_BASE_URL", DEFAULT_TEXT_BASE_URL).rstrip("/")
    reasoning_setting = os.environ.get("TEXT_MODEL_REASONING", "none")
    if reasoning_setting == "none":
        reasoning = {"reasoning": {"enabled": False}}
    elif "api.deepseek.com/" in base_url + "/":
        reasoning = {"thinking": {"type": "disabled"}}
    else:
        reasoning = {"reasoning": {"effort": reasoning_setting}}
    return {"base_url": base_url, "model": text_model_name(), "key": key, "reasoning": reasoning}


def trace_dir():
    configured = os.environ.get("JEV_TRACE_DIR")
    return Path(configured) if configured else None


def headless():
    return os.environ.get("JEV_HEADLESS", "").lower() in {"1", "true", "yes"}


def mcp_idle_seconds():
    """How long an MCP session may sit unused before its tab is closed."""
    return float(os.environ.get("JEV_MCP_IDLE_MINUTES", DEFAULT_MCP_IDLE_MINUTES)) * 60


def mcp_max_sessions():
    """How many MCP tabs stay open; opening one more closes the least recently used idle tab."""
    return max(1, int(os.environ.get("JEV_MCP_MAX_SESSIONS", DEFAULT_MCP_MAX_SESSIONS)))


def profile_dir():
    configured = os.environ.get("JEV_PROFILE_DIR")
    return Path(configured).expanduser() if configured else None


def chrome_path():
    """An explicit JEV_CHROME_PATH, else the first Chromium-family browser on PATH."""
    if configured := os.environ.get("JEV_CHROME_PATH"):
        return shutil.which(configured) or configured
    for candidate in CHROME_CANDIDATES:
        if found := shutil.which(candidate):
            return found
    return None


def load_dotenv(path=None):
    """Fill unset variables from a .env file; existing environment values win."""
    path = Path(path) if path else Path.cwd() / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
