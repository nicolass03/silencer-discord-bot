"""Entrypoint for the Silencer Discord bot."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import discord
from discord.ext import commands
from dotenv import load_dotenv

log = logging.getLogger("silencer.bot")


class SilencerBot(commands.Bot):
    """Slash-command-only bot. Loads cogs and syncs the command tree on startup."""

    def __init__(self) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.voice_states = True
        intents.messages = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
        )

    async def setup_hook(self) -> None:
        await self.load_extension("src.cogs.moderator")
        await self.load_extension("src.cogs.transcribe")
        await self.load_extension("src.cogs.voice")
        await self.load_extension("src.cogs.chat")
        synced = await self.tree.sync()
        log.info("Synced %d slash command(s) globally", len(synced))

    async def on_ready(self) -> None:
        assert self.user is not None
        log.info(
            "Logged in as %s (id=%s) in %d guild(s)",
            self.user,
            self.user.id,
            len(self.guilds),
        )


def _read_token() -> str:
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token or token == "your-bot-token-here":
        sys.stderr.write(
            "ERROR: DISCORD_TOKEN is not set. Copy .env.example to .env "
            "and fill in your bot token.\n"
        )
        sys.exit(1)
    return token


async def _main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    token = _read_token()
    bot = SilencerBot()

    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        log.info("Shutting down on keyboard interrupt")
