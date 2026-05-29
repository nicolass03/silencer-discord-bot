"""Voice channel slash commands."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands, voice_recv

from src.voice_connect import NotInVoiceChannelError, ensure_voice_client

log = logging.getLogger("silencer.voice")


class Voice(commands.Cog):
    """Slash commands for joining and leaving voice channels."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _transcribe_cog(self):
        return self.bot.get_cog("TranscribeCog")

    @app_commands.command(name="ping", description="Check that the bot is alive.")
    async def ping(self, interaction: discord.Interaction) -> None:
        latency_ms = round(self.bot.latency * 1000)
        await interaction.response.send_message(
            f"Pong! Gateway latency: {latency_ms} ms", ephemeral=True
        )

    @app_commands.command(
        name="join",
        description="Join the voice channel you are currently in.",
    )
    @app_commands.guild_only()
    async def join(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        member = interaction.user
        if not isinstance(member, discord.Member) or member.voice is None or member.voice.channel is None:
            await interaction.response.send_message(
                "You need to be in a voice channel first.", ephemeral=True
            )
            return

        target = member.voice.channel
        voice_client = interaction.guild.voice_client
        if (
            voice_client is not None
            and voice_client.is_connected()
            and voice_client.channel == target
        ):
            await interaction.response.send_message(
                f"Already in {target.mention}.", ephemeral=True
            )
            return

        try:
            await ensure_voice_client(interaction.guild, member, bot=self.bot)
        except NotInVoiceChannelError:
            await interaction.response.send_message(
                "You need to be in a voice channel first.", ephemeral=True
            )
            return
        except discord.ClientException as exc:
            log.exception("Failed to join voice channel")
            await interaction.response.send_message(
                f"Could not join voice channel: {exc}", ephemeral=True
            )
            return

        await interaction.response.send_message(f"Joined {target.mention}.")

    @app_commands.command(
        name="leave",
        description="Leave the current voice channel.",
    )
    @app_commands.guild_only()
    async def leave(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        voice_client = interaction.guild.voice_client
        if voice_client is None or not voice_client.is_connected():
            await interaction.response.send_message(
                "I'm not in a voice channel.", ephemeral=True
            )
            return

        channel = voice_client.channel

        transcribe_cog = self._transcribe_cog()
        if transcribe_cog is not None:
            try:
                await transcribe_cog.stop(interaction.guild)
            except Exception:
                log.exception("Failed to stop transcription")

        await voice_client.disconnect(force=False)
        log.info("Disconnected from voice channel '%s' in guild '%s'", channel, interaction.guild)
        await interaction.response.send_message(f"Left {channel.mention if channel else 'voice channel'}.")

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Log voice-channel activity for the channel the bot is currently in.

        Note: this logs *voice state* events (join/leave/mute/deafen/stream/video).
        Detecting whether someone is *currently speaking* requires receiving the
        voice stream, which discord.py does not support out of the box.
        """
        if self.bot.user is not None and member.id == self.bot.user.id:
            return

        guild = member.guild
        voice_client = guild.voice_client
        if voice_client is None or not voice_client.is_connected():
            return

        bot_channel = voice_client.channel
        in_before = before.channel is not None and before.channel == bot_channel
        in_after = after.channel is not None and after.channel == bot_channel
        if not in_before and not in_after:
            return

        who = f"{member.name} (id={member.id})"
        channel_name = bot_channel.name if bot_channel else "?"

        if not in_before and in_after:
            log.info("[voice/%s] %s joined", channel_name, who)
            return

        if in_before and not in_after:
            if after.channel is None:
                log.info("[voice/%s] %s disconnected from voice", channel_name, who)
            else:
                log.info(
                    "[voice/%s] %s moved to '%s'", channel_name, who, after.channel.name
                )
            return

        changes: list[str] = []
        if before.self_mute != after.self_mute:
            changes.append(f"self_mute={after.self_mute}")
        if before.self_deaf != after.self_deaf:
            changes.append(f"self_deaf={after.self_deaf}")
        if before.mute != after.mute:
            changes.append(f"server_mute={after.mute}")
        if before.deaf != after.deaf:
            changes.append(f"server_deaf={after.deaf}")
        if before.self_stream != after.self_stream:
            changes.append(f"streaming={after.self_stream}")
        if before.self_video != after.self_video:
            changes.append(f"video={after.self_video}")
        if before.suppress != after.suppress:
            changes.append(f"suppress={after.suppress}")

        if changes:
            log.info("[voice/%s] %s state change: %s", channel_name, who, ", ".join(changes))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Voice(bot))
