"""Shared voice-channel connect helpers for voice and music cogs."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import voice_recv

if TYPE_CHECKING:
    from discord.ext import commands

log = logging.getLogger("silencer.voice_connect")


class NotInVoiceChannelError(Exception):
    """Raised when the member is not in a voice channel."""


async def ensure_voice_client(
    guild: discord.Guild,
    member: discord.Member,
    *,
    bot: commands.Bot,
) -> voice_recv.VoiceRecvClient:
    """Connect or move the bot to the member's voice channel and start transcription."""
    if member.voice is None or member.voice.channel is None:
        raise NotInVoiceChannelError()

    target = member.voice.channel
    voice_client = guild.voice_client

    if voice_client is not None and voice_client.is_connected():
        if voice_client.channel != target:
            await voice_client.move_to(target)
            log.info("Moved to voice channel '%s' in guild '%s'", target, guild)
        if not isinstance(voice_client, voice_recv.VoiceRecvClient):
            raise discord.ClientException(
                "Existing voice client is not a VoiceRecvClient"
            )
    else:
        voice_client = await target.connect(
            cls=voice_recv.VoiceRecvClient, reconnect=True
        )
        log.info("Connected to voice channel '%s' in guild '%s'", target, guild)

    assert isinstance(voice_client, voice_recv.VoiceRecvClient)

    transcribe_cog = bot.get_cog("TranscribeCog")
    if transcribe_cog is not None:
        try:
            await transcribe_cog.start(voice_client)
        except Exception:
            log.exception("Failed to start transcription")

    return voice_client
