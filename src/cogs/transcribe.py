"""Per-user speech-to-text for the voice channel the bot is sitting in.

Pipeline:

1. /join (in cogs/voice.py) connects with `VoiceRecvClient` and calls
   `TranscribeCog.start()`. We register a custom `AudioSink` that pushes
   decoded PCM into per-user ring buffers.
2. A background task ticks every `TICK_INTERVAL_SECONDS` and inspects each
   buffer. A buffer is flushed to the transcriber when either:
     - The buffer has reached `MAX_SEGMENT_SECONDS` (eager / streaming flush;
       we keep an `OVERLAP_SECONDS` tail so trigger words straddling a
       chunk boundary still appear in the next chunk), or
     - The user has been silent for `SILENCE_FLUSH_SECONDS` (full flush,
       no overlap retained — no more audio is coming).
3. Each flush queues a non-blocking transcription on the shared transcriber's
   dedicated worker and the result is logged to stdout + forwarded to the
   moderator.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import discord
from discord.ext import commands, voice_recv

from src.transcriber import FasterWhisperTranscriber

OnTranscript = Callable[[discord.Guild, discord.Member, str], Awaitable[None]]

log = logging.getLogger("silencer.transcribe")


SAMPLE_RATE = 48000
CHANNELS = 2
SAMPLE_WIDTH = 2
BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH

SILENCE_FLUSH_SECONDS = 0.25
MAX_SEGMENT_SECONDS = 1.5
TICK_INTERVAL_SECONDS = 0.05
MIN_FLUSH_SECONDS = 0.20
OVERLAP_SECONDS = 0.30
WATCHDOG_INTERVAL_SECONDS = 60.0
PACKET_STALL_WARN_SECONDS = 30.0
# Seconds of no decoded packets (while connected, listening, and humans are in
# the channel) before forcing a full voice reconnect. <= 0 disables the
# packet-idle reconnect (the is_connected / is_listening recoveries still run).
# Tune via VOICE_STALL_RECONNECT_SECONDS.
PACKET_STALL_RECONNECT_SECONDS = float(
    os.getenv("VOICE_STALL_RECONNECT_SECONDS", "90") or "90"
)
# Minimum gap between reconnect attempts so a persistent stall can't thrash the
# voice connection every watchdog tick.
RECONNECT_COOLDOWN_SECONDS = 45.0

OVERLAP_BYTES = int(OVERLAP_SECONDS * BYTES_PER_SECOND)
# PCM frames from voice-recv are 20 ms stereo s16 = 3840 bytes; align overlap
# tail to that frame size so we never split a sample mid-frame.
_FRAME_BYTES = SAMPLE_RATE // 50 * CHANNELS * SAMPLE_WIDTH
if OVERLAP_BYTES % _FRAME_BYTES:
    OVERLAP_BYTES = (OVERLAP_BYTES // _FRAME_BYTES) * _FRAME_BYTES


@dataclass
class _UserBuffer:
    member: discord.Member
    chunks: list[bytes] = field(default_factory=list)
    size: int = 0
    last_write: float = field(default_factory=time.monotonic)

    def append(self, pcm: bytes) -> None:
        self.chunks.append(pcm)
        self.size += len(pcm)
        self.last_write = time.monotonic()

    def take(self, keep_tail_bytes: int = 0) -> bytes:
        """Return all buffered PCM, optionally retaining a trailing window.

        When `keep_tail_bytes` > 0 the last `keep_tail_bytes` bytes are kept
        in the buffer so the next chunk overlaps with this one — important
        for trigger words that straddle a streaming flush boundary.
        """
        data = b"".join(self.chunks)
        if keep_tail_bytes and len(data) > keep_tail_bytes:
            head, tail = data[:-keep_tail_bytes], data[-keep_tail_bytes:]
            self.chunks = [tail]
            self.size = len(tail)
            return head
        self.chunks.clear()
        self.size = 0
        return data

    def duration(self) -> float:
        return self.size / BYTES_PER_SECOND

    def silence_for(self, now: float) -> float:
        return now - self.last_write


class _LiveSink(voice_recv.AudioSink):
    """AudioSink that pushes PCM into a `_GuildSession` buffer.

    `write()` is called from the voice-recv thread, NOT the asyncio loop, so
    we only do thread-safe work here (buffer append under a lock).
    """

    def __init__(self, session: "_GuildSession") -> None:
        super().__init__()
        self._session = session

    def wants_opus(self) -> bool:
        return False

    def write(self, user, data: voice_recv.VoiceData) -> None:
        if user is None or data.pcm is None:
            return
        if not isinstance(user, discord.Member):
            return
        if not self._session.accepts_user(user):
            return
        self._session.push_pcm(user, data.pcm)

    def cleanup(self) -> None:
        return


class _GuildSession:
    """Holds per-user buffers + background flush task for one guild."""

    def __init__(
        self,
        guild: discord.Guild,
        loop: asyncio.AbstractEventLoop,
        transcriber: FasterWhisperTranscriber,
        on_transcript: OnTranscript | None = None,
        allowed_usernames: frozenset[str] | None = None,
    ) -> None:
        self.guild = guild
        self.loop = loop
        self.transcriber = transcriber
        self.on_transcript = on_transcript
        self.allowed_usernames = allowed_usernames or frozenset()
        self.voice_client: voice_recv.VoiceRecvClient | None = None
        self.sink: voice_recv.AudioSink | None = None
        self._buffers: dict[int, _UserBuffer] = {}
        self._lock = threading.Lock()
        self._task: asyncio.Task | None = None
        self._stopped = False
        self._packets_received = 0
        self._last_packet_at = time.monotonic()
        self._last_watchdog_at = time.monotonic()
        self._recovering = False
        self._last_reconnect_at = 0.0

    def attach(
        self,
        voice_client: voice_recv.VoiceRecvClient,
        sink: voice_recv.AudioSink,
    ) -> None:
        self.voice_client = voice_client
        self.sink = sink

    def accepts_user(self, member: discord.Member) -> bool:
        if not self.allowed_usernames:
            return True
        return member.name in self.allowed_usernames

    def start(self) -> None:
        if self._task is None:
            self._task = self.loop.create_task(self._run())

    async def stop(self) -> None:
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        await self._flush_all()

    def push_pcm(self, member: discord.Member, pcm: bytes) -> None:
        with self._lock:
            buf = self._buffers.get(member.id)
            if buf is None:
                buf = _UserBuffer(member=member)
                self._buffers[member.id] = buf
            buf.append(pcm)
            self._packets_received += 1
            self._last_packet_at = time.monotonic()

    async def _run(self) -> None:
        while not self._stopped:
            try:
                await asyncio.sleep(TICK_INTERVAL_SECONDS)
                self._collect_and_dispatch()
                now = time.monotonic()
                if now - self._last_watchdog_at >= WATCHDOG_INTERVAL_SECONDS:
                    self._last_watchdog_at = now
                    self._watchdog(now)
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception(
                    "transcribe loop iteration failed in guild '%s'; continuing",
                    self.guild,
                )

    def _watchdog(self, now: float) -> None:
        idle = now - self._last_packet_at
        log.info(
            "[watchdog/%s] %d packets received total; %.1fs since last packet",
            self.guild.name,
            self._packets_received,
            idle,
        )
        if idle > PACKET_STALL_WARN_SECONDS:
            log.warning(
                "[watchdog/%s] No voice packets received for %.0fs - audio pipeline may be stalled",
                self.guild.name,
                idle,
            )

        vc = self.voice_client
        sink = self.sink
        if vc is None or sink is None or self._recovering:
            return

        try:
            connected = vc.is_connected()
        except Exception:
            connected = False
        if not connected:
            # Unambiguous failure: we expect to be connected but aren't.
            log.warning(
                "[watchdog/%s] Voice client is no longer connected; reconnecting",
                self.guild.name,
            )
            self.loop.create_task(self._recover("not connected"))
            return

        listening: bool | None
        try:
            listening = vc.is_listening()
        except Exception:
            listening = None

        if listening is False:
            log.warning(
                "[watchdog/%s] Voice client stopped listening; re-attaching sink",
                self.guild.name,
            )
            try:
                vc.listen(sink)
            except Exception:
                log.exception(
                    "[watchdog/%s] Failed to re-attach sink", self.guild.name
                )
            return

        # Connected and listening, but no decoded packets for a long time while
        # humans are in the channel -> likely a silent decode/receive stall.
        # Reconnect to reset the pipeline (throttled by the cooldown). Genuine
        # silence with nobody talking is expected and must not trigger this.
        if (
            PACKET_STALL_RECONNECT_SECONDS > 0
            and idle >= PACKET_STALL_RECONNECT_SECONDS
            and now - self._last_reconnect_at >= RECONNECT_COOLDOWN_SECONDS
            and self._human_listeners_present()
        ):
            log.warning(
                "[watchdog/%s] No decoded packets for %.0fs with humans present; "
                "reconnecting to recover the audio pipeline",
                self.guild.name,
                idle,
            )
            self.loop.create_task(self._recover("packet stall"))

    def _human_listeners_present(self) -> bool:
        vc = self.voice_client
        channel = vc.channel if vc is not None else None
        if channel is None:
            return False
        return any(not member.bot for member in channel.members)

    async def _recover(self, reason: str) -> None:
        """Force a voice reconnect and re-attach the sink (watchdog recovery)."""
        if self._recovering or self._stopped:
            return
        self._recovering = True
        self._last_reconnect_at = time.monotonic()
        vc = self.voice_client
        sink = self.sink
        try:
            if vc is None or sink is None:
                return
            channel = vc.channel
            if channel is None:
                log.warning(
                    "[watchdog/%s] Cannot recover: voice channel unknown",
                    self.guild.name,
                )
                return
            log.warning(
                "[watchdog/%s] Recovering voice connection (reason: %s)...",
                self.guild.name,
                reason,
            )
            try:
                vc.stop_listening()
            except Exception:
                log.debug(
                    "[watchdog/%s] stop_listening during recovery raised",
                    self.guild.name,
                    exc_info=True,
                )
            try:
                await vc.disconnect(force=True)
            except Exception:
                log.debug(
                    "[watchdog/%s] disconnect during recovery raised",
                    self.guild.name,
                    exc_info=True,
                )
            await asyncio.sleep(1.0)
            if self._stopped:
                return
            try:
                new_vc = await channel.connect(
                    cls=voice_recv.VoiceRecvClient, reconnect=True
                )
            except Exception:
                log.exception(
                    "[watchdog/%s] Reconnect failed", self.guild.name
                )
                return
            self.voice_client = new_vc
            # Fresh sink: the previous one may still be considered "in use" by
            # voice-recv after the forced disconnect.
            new_sink = _LiveSink(self)
            self.sink = new_sink
            try:
                new_vc.listen(new_sink)
            except Exception:
                log.exception(
                    "[watchdog/%s] Failed to re-attach sink after reconnect",
                    self.guild.name,
                )
                return
            now = time.monotonic()
            self._last_packet_at = now
            self._last_watchdog_at = now
            log.info(
                "[watchdog/%s] Voice reconnected and listening re-attached",
                self.guild.name,
            )
        finally:
            self._recovering = False

    def _collect_and_dispatch(self) -> None:
        now = time.monotonic()
        to_flush: list[tuple[discord.Member, bytes]] = []
        with self._lock:
            for buf in self._buffers.values():
                if buf.size == 0:
                    continue
                idle = buf.silence_for(now)
                duration = buf.duration()
                silence_flush = idle >= SILENCE_FLUSH_SECONDS
                max_flush = duration >= MAX_SEGMENT_SECONDS
                if not (silence_flush or max_flush):
                    continue
                if duration < MIN_FLUSH_SECONDS and not silence_flush:
                    continue
                # Streaming flush keeps an overlap tail so the next chunk
                # contains the boundary; a true silence flush clears fully.
                keep_tail = 0 if silence_flush else OVERLAP_BYTES
                to_flush.append((buf.member, buf.take(keep_tail_bytes=keep_tail)))

        for member, pcm in to_flush:
            self.loop.create_task(self._transcribe_and_log(member, pcm))

    async def _flush_all(self) -> None:
        with self._lock:
            pending: list[tuple[discord.Member, bytes]] = [
                (buf.member, buf.take())
                for buf in self._buffers.values()
                if buf.size > 0
            ]
            self._buffers.clear()
        for member, pcm in pending:
            await self._transcribe_and_log(member, pcm)

    async def _transcribe_and_log(
        self, member: discord.Member, pcm: bytes
    ) -> None:
        text = await self.transcriber.transcribe_pcm(
            pcm,
            sample_rate=SAMPLE_RATE,
            channels=CHANNELS,
            sample_width=SAMPLE_WIDTH,
        )
        if not text:
            return
        channel = self.guild.voice_client.channel if self.guild.voice_client else None
        channel_name = channel.name if channel else "?"
        log.info(
            "[transcript/%s] %s (id=%s): %s",
            channel_name,
            member.name,
            member.id,
            text,
        )

        if self.on_transcript is not None:
            try:
                await self.on_transcript(self.guild, member, text)
            except Exception:
                log.exception("on_transcript callback raised")


class TranscribeCog(commands.Cog):
    """Public API: `start(voice_client)` and `stop(guild)`.

    Called by the voice cog on /join and /leave.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.transcriber = FasterWhisperTranscriber()
        self._sessions: dict[int, _GuildSession] = {}
        self._allowed_usernames = self._read_allowed_usernames()
        if self._allowed_usernames:
            log.info(
                "Transcription scoped to user(s): %s",
                ", ".join(sorted(self._allowed_usernames)),
            )

    @staticmethod
    def _read_allowed_usernames() -> frozenset[str]:
        raw = os.getenv("MUTE_TARGET_USERNAME", "").strip()
        if not raw:
            return frozenset()
        return frozenset(part.strip() for part in raw.split(",") if part.strip())

    async def cog_load(self) -> None:
        self.transcriber.warmup()

    async def cog_unload(self) -> None:
        for session in list(self._sessions.values()):
            await session.stop()
        self._sessions.clear()
        self.transcriber.shutdown()

    async def start(self, voice_client: voice_recv.VoiceRecvClient) -> None:
        guild = voice_client.guild
        if guild.id in self._sessions:
            log.debug("Transcription already running for guild '%s'", guild)
            return

        session = _GuildSession(
            guild=guild,
            loop=asyncio.get_running_loop(),
            transcriber=self.transcriber,
            on_transcript=self._dispatch_transcript,
            allowed_usernames=self._allowed_usernames,
        )
        sink = _LiveSink(session)
        session.attach(voice_client, sink)
        session.start()
        self._sessions[guild.id] = session

        voice_client.listen(sink)
        log.info("Started transcription in guild '%s'", guild)

    async def stop(self, guild: discord.Guild) -> None:
        session = self._sessions.pop(guild.id, None)
        if session is None:
            return
        voice_client = guild.voice_client
        if isinstance(voice_client, voice_recv.VoiceRecvClient):
            try:
                voice_client.stop_listening()
            except Exception:
                log.debug("stop_listening() raised", exc_info=True)
        await session.stop()
        log.info("Stopped transcription in guild '%s'", guild)

    async def _dispatch_transcript(
        self, guild: discord.Guild, member: discord.Member, text: str
    ) -> None:
        moderator = self.bot.get_cog("Moderator")
        if moderator is None:
            return
        try:
            await moderator.check_and_mute(guild, text)
        except Exception:
            log.exception("Moderator.check_and_mute raised")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TranscribeCog(bot))
