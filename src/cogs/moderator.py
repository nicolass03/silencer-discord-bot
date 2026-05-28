"""Trigger-word moderation: server-mute a target user when any of a configured
set of trigger words is detected in a transcript.

Configured via env:

- `MUTE_TRIGGER_WORDS`: comma-separated list of trigger words (case- and
  accent-insensitive). Default: `lechita,sexo,toma`.
- `MUTE_TARGET_USERNAME`: the Discord username (the unique @handle, not the
  display name) of the user to mute. Default: `bonijeyjey`.
- `MUTE_DURATION_SECONDS`: how long to keep the user muted. Default: `5`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import unicodedata

import discord
from discord.ext import commands

log = logging.getLogger("silencer.moderator")


def _strip_accents_lower(s: str) -> str:
    """Lowercase + remove combining marks so 'Tomá' == 'toma'."""
    nfd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn").lower()


class Moderator(commands.Cog):
    """Listens for transcripts and server-mutes a target user on trigger words."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

        raw_triggers = os.getenv("MUTE_TRIGGER_WORDS", "lechita,sexo,toma")
        self.trigger_words = [w.strip() for w in raw_triggers.split(",") if w.strip()]

        self.target_username = os.getenv("MUTE_TARGET_USERNAME", "bonijeyjey").strip()

        try:
            self.mute_duration = float(os.getenv("MUTE_DURATION_SECONDS", "5"))
        except ValueError:
            self.mute_duration = 5.0

        self._pattern: re.Pattern[str] | None = self._build_pattern()
        self._active_unmute: dict[int, asyncio.Task] = {}
        # Streaming transcription emits overlapping partial+final transcripts;
        # debounce per (guild, word) so a single utterance can't fire twice.
        self._last_trigger_at: dict[tuple[int, str], float] = {}
        self._trigger_debounce = 1.0

        log.info(
            "Moderator armed: triggers=%s target='%s' duration=%.1fs",
            self.trigger_words,
            self.target_username,
            self.mute_duration,
        )

    def _build_pattern(self) -> re.Pattern[str] | None:
        normalised = [_strip_accents_lower(w) for w in self.trigger_words]
        normalised = [w for w in normalised if w]
        if not normalised:
            return None
        escaped = [re.escape(w) for w in normalised]
        return re.compile(r"\b(" + "|".join(escaped) + r")\b")

    def _find_target(self, guild: discord.Guild) -> discord.Member | None:
        if not self.target_username:
            return None

        member = guild.get_member_named(self.target_username)
        if member is not None:
            return member

        for vc in guild.voice_channels:
            for m in vc.members:
                if m.name == self.target_username:
                    return m
        return None

    async def check_and_mute(self, guild: discord.Guild, text: str) -> None:
        if self._pattern is None or not text:
            return

        match = self._pattern.search(_strip_accents_lower(text))
        if match is None:
            return

        word = match.group(1)

        key = (guild.id, word)
        now = time.monotonic()
        if now - self._last_trigger_at.get(key, 0.0) < self._trigger_debounce:
            return
        self._last_trigger_at[key] = now

        target = self._find_target(guild)

        if target is None:
            log.info(
                "Trigger '%s' fired but target '%s' not found in guild '%s'",
                word,
                self.target_username,
                guild.name,
            )
            return

        if self.bot.user is not None and target.id == self.bot.user.id:
            return

        if target.voice is None or target.voice.channel is None:
            log.info(
                "Trigger '%s' fired but '%s' is not in a voice channel",
                word,
                target.name,
            )
            return

        existing = self._active_unmute.pop(guild.id, None)
        if existing is not None and not existing.done():
            existing.cancel()

        log.info(
            "Trigger '%s' detected — muting %s for %.1fs", word, target.name, self.mute_duration
        )

        try:
            await target.edit(mute=True, reason=f"silencer: trigger word '{word}'")
        except discord.Forbidden:
            log.warning(
                "Missing 'Mute Members' permission — cannot mute %s in guild '%s'",
                target.name,
                guild.name,
            )
            return
        except discord.HTTPException as exc:
            log.warning("Failed to mute %s: %s", target.name, exc)
            return

        self._active_unmute[guild.id] = asyncio.create_task(
            self._unmute_after(target, self.mute_duration)
        )

    async def _unmute_after(self, member: discord.Member, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

        current = member.guild.get_member(member.id) or member
        if current.voice is None:
            return

        try:
            await current.edit(mute=False, reason="silencer mute expired")
            log.info("Unmuted %s", current.name)
        except discord.Forbidden:
            log.warning("Missing permission to unmute %s", current.name)
        except discord.HTTPException as exc:
            log.warning("Failed to unmute %s: %s", current.name, exc)

    async def cog_unload(self) -> None:
        for task in list(self._active_unmute.values()):
            if not task.done():
                task.cancel()
        self._active_unmute.clear()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderator(bot))
