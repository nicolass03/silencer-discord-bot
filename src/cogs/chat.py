"""@mention chat: reply in text channels using a configurable LLM backend."""

from __future__ import annotations

import logging
import os

import discord
from discord.ext import commands

from src.llm import ChatBackend, ChatConfig, create_chat_backend

log = logging.getLogger("silencer.chat")

_DISCORD_MAX_MESSAGE = 2000
_DEFAULT_DISABLED_MESSAGE = "Chat disabled."


def _chat_enabled() -> bool:
    raw = os.getenv("CHAT_ENABLED", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _disabled_message() -> str:
    return os.getenv("CHAT_DISABLED_MESSAGE", _DEFAULT_DISABLED_MESSAGE).strip() or _DEFAULT_DISABLED_MESSAGE


def _resolve_mentions(message: discord.Message, bot_user: discord.ClientUser) -> str:
    """Strip the bot's own mention and replace other user mentions with names."""
    text = message.content
    for mention in message.mentions:
        if mention.id == bot_user.id:
            text = text.replace(f"<@{mention.id}>", "")
            text = text.replace(f"<@!{mention.id}>", "")
        else:
            name = getattr(mention, "display_name", mention.name)
            text = text.replace(f"<@{mention.id}>", f"@{name}")
            text = text.replace(f"<@!{mention.id}>", f"@{name}")
    return " ".join(text.split()).strip()


class Chat(commands.Cog):
    """Handles @mention replies with an LLM when enabled."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._config = ChatConfig.from_env()
        self._llm: ChatBackend | None = None
        if self._config.enabled:
            self._llm = create_chat_backend(self._config)
            log.info(
                "Chat replies enabled (provider=%s)",
                self._config.provider,
            )

    def cog_unload(self) -> None:
        if self._llm is not None:
            self._llm.shutdown()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if self.bot.user is None or self.bot.user not in message.mentions:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return

        if not _chat_enabled():
            try:
                await message.reply(_disabled_message(), mention_author=False)
            except discord.HTTPException:
                log.exception("Failed to send chat-disabled reply")
            return

        body = _resolve_mentions(message, self.bot.user)
        if not body:
            try:
                await message.reply(
                    "Say something after mentioning me.",
                    mention_author=False,
                )
            except discord.HTTPException:
                log.exception("Failed to send empty-body reply")
            return

        user_line = f"{message.author.name}: {body}"
        if self._llm is None:
            self._llm = create_chat_backend(ChatConfig.from_env())

        try:
            async with message.channel.typing():
                response = await self._llm.complete(user_line)
        except TimeoutError:
            log.warning("Chat inference timed out for message %s", message.id)
            try:
                await message.reply(
                    "That took too long — try a shorter message.",
                    mention_author=False,
                )
            except discord.HTTPException:
                log.exception("Failed to send timeout reply")
            return
        except Exception:
            log.exception("Chat inference failed for message %s", message.id)
            try:
                await message.reply(
                    "Chat model is not available right now.",
                    mention_author=False,
                )
            except discord.HTTPException:
                log.exception("Failed to send error reply")
            return

        if not response:
            response = "(no response)"

        if len(response) > _DISCORD_MAX_MESSAGE:
            response = response[: _DISCORD_MAX_MESSAGE - 3] + "..."

        try:
            await message.reply(response, mention_author=False)
        except discord.HTTPException:
            log.exception("Failed to send chat reply")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Chat(bot))
