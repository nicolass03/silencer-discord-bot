"""YouTube music playback via yt-dlp and FFmpeg."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import deque
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands, voice_recv

from src.voice_connect import NotInVoiceChannelError, ensure_voice_client

log = logging.getLogger("silencer.music")

FFMPEG_BEFORE = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_OPTIONS = "-vn"

_YOUTUBE_URL_RE = re.compile(
    r"^https?://(?:www\.)?(?:youtube\.com/watch|youtu\.be/|music\.youtube\.com/)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Track:
    title: str
    stream_url: str
    webpage_url: str
    requester_id: int


class _GuildPlayer:
    def __init__(self, guild_id: int, max_queue: int) -> None:
        self.guild_id = guild_id
        self.max_queue = max_queue
        self.lock = asyncio.Lock()
        self.queue: deque[Track] = deque()
        self.current: Track | None = None

    def queue_size(self) -> int:
        return len(self.queue)

def _read_max_queue() -> int:
    try:
        return max(1, int(os.getenv("MUSIC_MAX_QUEUE", "10") or "10"))
    except ValueError:
        return 10


def _cookies_path() -> str | None:
    path = os.getenv("YTDLP_COOKIES_PATH", "").strip()
    return path if path and os.path.isfile(path) else None


def _resolve_search_url(query: str) -> str:
    query = query.strip()
    if _YOUTUBE_URL_RE.match(query):
        return query
    return f"ytsearch1:{query}"


def _extract_track_sync(search_url: str) -> Track | None:
    import yt_dlp

    opts: dict = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "default_search": "ytsearch",
    }
    cookies = _cookies_path()
    if cookies:
        opts["cookiefile"] = cookies

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(search_url, download=False)

    if info is None:
        return None

    if info.get("_type") == "playlist" or "entries" in info:
        entries = info.get("entries") or []
        info = next((e for e in entries if e), None)
        if info is None:
            return None

    stream_url = info.get("url")
    if not stream_url:
        return None

    title = info.get("title") or "Unknown"
    webpage_url = info.get("webpage_url") or info.get("original_url") or search_url
    return Track(
        title=title,
        stream_url=stream_url,
        webpage_url=webpage_url,
        requester_id=0,
    )


class Music(commands.Cog):
    """Slash commands for YouTube playback in voice channels."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._players: dict[int, _GuildPlayer] = {}
        self._max_queue = _read_max_queue()

    def _player(self, guild_id: int) -> _GuildPlayer:
        player = self._players.get(guild_id)
        if player is None:
            player = _GuildPlayer(guild_id, self._max_queue)
            self._players[guild_id] = player
        return player

    async def _resolve_track(self, query: str, requester_id: int) -> Track:
        search_url = _resolve_search_url(query)
        base = await asyncio.to_thread(_extract_track_sync, search_url)
        if base is None:
            raise RuntimeError("Could not find a playable track for that query.")
        return Track(
            title=base.title,
            stream_url=base.stream_url,
            webpage_url=base.webpage_url,
            requester_id=requester_id,
        )

    def _schedule_play_next(self, guild: discord.Guild, error: Exception | None) -> None:
        if error:
            log.error("Playback error in guild '%s': %s", guild, error)

        player = self._players.get(guild.id)
        if player is not None:
            player.current = None

        asyncio.run_coroutine_threadsafe(self._play_next(guild), self.bot.loop)

    async def _play_track(
        self, guild: discord.Guild, voice_client: voice_recv.VoiceRecvClient, track: Track
    ) -> None:
        player = self._player(guild.id)
        player.current = track

        source = discord.FFmpegOpusAudio(
            track.stream_url,
            before_options=FFMPEG_BEFORE,
            options=FFMPEG_OPTIONS,
        )

        def after(exc: Exception | None) -> None:
            self._schedule_play_next(guild, exc)

        voice_client.play(source, after=after)
        log.info(
            "Playing '%s' in guild '%s' (requested by %s)",
            track.title,
            guild.name,
            track.requester_id,
        )

    async def _play_next(self, guild: discord.Guild) -> None:
        player = self._players.get(guild.id)
        if player is None:
            return

        async with player.lock:
            voice_client = guild.voice_client
            if voice_client is None or not voice_client.is_connected():
                return
            if voice_client.is_playing():
                return

            if not player.queue:
                return
            track = player.queue.popleft()

            if not isinstance(voice_client, voice_recv.VoiceRecvClient):
                return

            await self._play_track(guild, voice_client, track)

    async def _enqueue_or_play(
        self,
        guild: discord.Guild,
        member: discord.Member,
        track: Track,
    ) -> str:
        voice_client = await ensure_voice_client(guild, member, bot=self.bot)
        player = self._player(guild.id)

        async with player.lock:
            if voice_client.is_playing() or player.current is not None:
                if player.queue_size() >= player.max_queue:
                    raise RuntimeError(
                        f"Queue is full ({player.max_queue} tracks). Use /skip or /stop."
                    )
                player.queue.append(track)
                position = player.queue_size()
                return f"Queued **{track.title}** (position {position})."

            await self._play_track(guild, voice_client, track)
            return f"Now playing **{track.title}**."

    @app_commands.command(
        name="play",
        description="Play a song from YouTube (search query or URL).",
    )
    @app_commands.describe(query="Song name or YouTube URL")
    @app_commands.guild_only()
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message(
                "Could not resolve server member.", ephemeral=True
            )
            return

        if member.voice is None or member.voice.channel is None:
            await interaction.response.send_message(
                "You need to be in a voice channel first.", ephemeral=True
            )
            return

        await interaction.response.defer()

        try:
            track = await self._resolve_track(query, member.id)
        except Exception as exc:
            log.exception("Failed to resolve track")
            await interaction.followup.send(
                f"Could not find that track: {exc}", ephemeral=True
            )
            return

        try:
            message = await self._enqueue_or_play(interaction.guild, member, track)
        except NotInVoiceChannelError:
            await interaction.followup.send(
                "You need to be in a voice channel first.", ephemeral=True
            )
            return
        except discord.ClientException as exc:
            log.exception("Failed to join voice for playback")
            await interaction.followup.send(
                f"Could not join voice channel: {exc}", ephemeral=True
            )
            return
        except RuntimeError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except Exception:
            log.exception("Playback failed")
            await interaction.followup.send(
                "Something went wrong while starting playback.", ephemeral=True
            )
            return

        await interaction.followup.send(message)

    @app_commands.command(
        name="stop",
        description="Stop playback and clear the music queue.",
    )
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        guild = interaction.guild
        voice_client = guild.voice_client
        player = self._players.get(guild.id)

        if player is None:
            player = _GuildPlayer(guild.id, self._max_queue)

        if voice_client is None or (
            not voice_client.is_playing() and player.current is None and not player.queue
        ):
            await interaction.response.send_message(
                "Nothing is playing.", ephemeral=True
            )
            return

        async with player.lock:
            player.queue.clear()
            player.current = None
            if voice_client is not None and voice_client.is_playing():
                voice_client.stop()

        await interaction.response.send_message("Stopped playback and cleared the queue.")

    @app_commands.command(
        name="skip",
        description="Skip the current track and play the next in queue.",
    )
    @app_commands.guild_only()
    async def skip(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        guild = interaction.guild
        voice_client = guild.voice_client
        player = self._players.get(guild.id)

        if voice_client is None or not voice_client.is_playing():
            await interaction.response.send_message(
                "Nothing is playing.", ephemeral=True
            )
            return

        skipped = player.current.title if player and player.current else "current track"
        voice_client.stop()
        await interaction.response.send_message(f"Skipped **{skipped}**.")

    @app_commands.command(
        name="queue",
        description="Show the current track and queued tracks.",
    )
    @app_commands.guild_only()
    async def queue(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        player = self._players.get(interaction.guild.id)
        if player is None or (not player.current and player.queue_size() == 0):
            await interaction.response.send_message(
                "The queue is empty.", ephemeral=True
            )
            return

        lines: list[str] = []
        if player.current:
            lines.append(f"**Now playing:** {player.current.title}")
        for i, track in enumerate(player.queue, start=1):
            lines.append(f"{i}. {track.title}")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
