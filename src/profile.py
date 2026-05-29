"""Bot deployment profile: full (STT + local LLM) vs slim (Ollama Cloud + music)."""

from __future__ import annotations

import os
import sys

_VALID_PROFILES = frozenset({"full", "slim"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def profile_name() -> str:
    raw = os.getenv("BOT_PROFILE", "full").strip().lower()
    return raw or "full"


def is_full() -> bool:
    return profile_name() == "full"


def is_slim() -> bool:
    return profile_name() == "slim"


def _env_bool(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in _TRUTHY


def validate_profile_config() -> None:
    """Fail fast on invalid profile or incompatible env for slim."""
    profile = profile_name()
    if profile not in _VALID_PROFILES:
        sys.stderr.write(
            f"ERROR: BOT_PROFILE={profile!r} is invalid; "
            f"expected one of: {', '.join(sorted(_VALID_PROFILES))}\n"
        )
        sys.exit(1)

    if not is_slim():
        return

    if not _env_bool("CHAT_ENABLED", default=False):
        return

    provider = os.getenv("CHAT_PROVIDER", "llama").strip().lower() or "llama"
    if provider != "ollama_cloud":
        sys.stderr.write(
            "ERROR: BOT_PROFILE=slim requires CHAT_PROVIDER=ollama_cloud when "
            "CHAT_ENABLED is true. Local llama and LM Studio are not available "
            "in the slim profile.\n"
        )
        sys.exit(1)

    api_key = os.getenv("OLLAMA_API_KEY", "").strip()
    if not api_key:
        sys.stderr.write(
            "ERROR: BOT_PROFILE=slim with CHAT_ENABLED=true requires OLLAMA_API_KEY.\n"
        )
        sys.exit(1)
