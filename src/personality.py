"""Load the chat bot's role/personality system prompt from file or env."""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("silencer.personality")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PERSONALITY_FILE = _REPO_ROOT / "prompts" / "personality.txt"
_FALLBACK_SYSTEM_PROMPT = (
    "You are a helpful assistant in a Discord server. Reply concisely."
)


def resolve_personality_path() -> Path:
    """Path to the personality file (env override or default)."""
    raw = os.getenv("CHAT_PERSONALITY_PATH", "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = _REPO_ROOT / path
        return path
    return _DEFAULT_PERSONALITY_FILE


def load_personality_prompt() -> str:
    """Resolve the system prompt for @mention chat.

    Precedence:
    1. CHAT_SYSTEM_PROMPT (non-empty) in the environment
    2. Contents of the personality file (CHAT_PERSONALITY_PATH or prompts/personality.txt)
    3. Built-in fallback if the file is missing or empty
    """
    inline = os.getenv("CHAT_SYSTEM_PROMPT", "").strip()
    if inline:
        log.info("Using chat system prompt from CHAT_SYSTEM_PROMPT")
        return inline

    path = resolve_personality_path()
    if not path.is_file():
        log.warning(
            "Personality file not found at %s — using built-in default. "
            "Create the file or set CHAT_PERSONALITY_PATH.",
            path,
        )
        return _FALLBACK_SYSTEM_PROMPT

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        log.warning(
            "Personality file %s is empty — using built-in default.",
            path,
        )
        return _FALLBACK_SYSTEM_PROMPT

    log.info("Loaded chat personality from %s (%d chars)", path, len(text))
    return text
