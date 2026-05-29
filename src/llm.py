"""Chat completions for @mention replies.

Backends (CHAT_PROVIDER):
- llama: local GGUF via llama-cpp-python
- ollama_cloud: Ollama Cloud API (OLLAMA_API_KEY)
- lmstudio: LM Studio OpenAI-compatible local server
"""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import aiohttp

from src.personality import load_personality_prompt

log = logging.getLogger("silencer.llm")

_IM_END = "<|" + "im_end" + "|>"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_VALID_PROVIDERS = frozenset({"llama", "ollama_cloud", "lmstudio"})
_OLLAMA_CLOUD_URL = "https://ollama.com/api/chat"
_DEFAULT_LMSTUDIO_BASE_URL = "http://localhost:1234/v1"


def _env_bool(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in _TRUTHY


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return float(raw)


@dataclass(frozen=True)
class ChatConfig:
    enabled: bool = False
    provider: str = "llama"
    model_path: str | None = None
    model_repo: str = "TheBloke/dolphin-2.6-mistral-7B-GGUF"
    model_file: str = "dolphin-2.6-mistral-7b.Q4_K_M.gguf"
    n_ctx: int = 2048
    n_gpu_layers: int = 0
    n_threads: int | None = None
    max_tokens: int = 256
    temperature: float = 0.7
    top_p: float = 0.9
    system_prompt: str = ""
    inference_timeout: float = 120.0
    ollama_model: str = "gpt-oss:120b"
    ollama_api_key: str | None = None
    lmstudio_base_url: str = _DEFAULT_LMSTUDIO_BASE_URL
    lmstudio_model: str = ""

    @classmethod
    def from_env(cls) -> "ChatConfig":
        threads_raw = os.getenv("CHAT_N_THREADS", "").strip()
        n_threads = int(threads_raw) if threads_raw else None
        model_path = os.getenv("CHAT_MODEL_PATH", "").strip() or None
        provider = os.getenv("CHAT_PROVIDER", "llama").strip().lower() or "llama"
        ollama_key = os.getenv("OLLAMA_API_KEY", "").strip() or None
        lmstudio_base = (
            os.getenv("LMSTUDIO_BASE_URL", _DEFAULT_LMSTUDIO_BASE_URL).strip()
            or _DEFAULT_LMSTUDIO_BASE_URL
        )
        return cls(
            enabled=_env_bool("CHAT_ENABLED", default=False),
            provider=provider,
            model_path=model_path,
            model_repo=os.getenv(
                "CHAT_MODEL_REPO", "TheBloke/dolphin-2.6-mistral-7B-GGUF"
            ).strip()
            or "TheBloke/dolphin-2.6-mistral-7B-GGUF",
            model_file=os.getenv(
                "CHAT_MODEL_FILE", "dolphin-2.6-mistral-7b.Q4_K_M.gguf"
            ).strip()
            or "dolphin-2.6-mistral-7b.Q4_K_M.gguf",
            n_ctx=_env_int("CHAT_N_CTX", 2048),
            n_gpu_layers=_env_int("CHAT_N_GPU_LAYERS", 0),
            n_threads=n_threads,
            max_tokens=_env_int("CHAT_MAX_TOKENS", 256),
            temperature=_env_float("CHAT_TEMPERATURE", 0.7),
            top_p=_env_float("CHAT_TOP_P", 0.9),
            system_prompt=load_personality_prompt(),
            inference_timeout=_env_float("CHAT_INFERENCE_TIMEOUT", 120.0),
            ollama_model=os.getenv("CHAT_OLLAMA_MODEL", "gpt-oss:120b").strip()
            or "gpt-oss:120b",
            ollama_api_key=ollama_key,
            lmstudio_base_url=lmstudio_base.rstrip("/"),
            lmstudio_model=os.getenv("CHAT_LMSTUDIO_MODEL", "").strip(),
        )


@runtime_checkable
class ChatBackend(Protocol):
    async def complete(self, user_line: str) -> str: ...

    def shutdown(self) -> None: ...


def _chat_messages(config: ChatConfig, user_line: str) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    if config.system_prompt:
        messages.append({"role": "system", "content": config.system_prompt})
    messages.append({"role": "user", "content": user_line})
    return messages


def format_chatml_prompt(*, system: str, user_line: str) -> str:
    """Build a ChatML prompt for Nous-Hermes-2-Pro."""
    return (
        f"<|im_start|>system\n{system}\n"
        f"<|im_start|>user\n{user_line}\n"
        f"<|im_start|>assistant\n"
    )


def parse_chatml_response(text: str) -> str:
    """Extract the assistant turn from model output."""
    marker = "<|im_start|>assistant"
    if marker in text:
        text = text.split(marker)[-1]
    text = text.replace(_IM_END, "").strip()
    return text


def resolve_model_path(config: ChatConfig) -> str:
    """Return a local path to the GGUF file, downloading from HF if needed."""
    if config.model_path:
        path = Path(config.model_path).expanduser()
        if path.is_file():
            return str(path.resolve())
        raise FileNotFoundError(f"CHAT_MODEL_PATH does not exist: {path}")

    cache_dir = os.getenv("HF_HOME", "").strip() or None

    from huggingface_hub import hf_hub_download, try_to_load_from_cache

    cached = try_to_load_from_cache(
        repo_id=config.model_repo,
        filename=config.model_file,
        cache_dir=cache_dir,
    )
    if cached is not None:
        return cached

    log.info(
        "Downloading chat model %s/%s (cache_dir=%s)...",
        config.model_repo,
        config.model_file,
        cache_dir or "default",
    )

    downloaded = hf_hub_download(
        repo_id=config.model_repo,
        filename=config.model_file,
        cache_dir=cache_dir,
    )
    log.info("Chat model downloaded to: %s", downloaded)
    log.info(
        "Set CHAT_MODEL_PATH=%s in .env to use this file directly on the next run",
        downloaded,
    )
    return downloaded


class LlamaChat:
    """Async-friendly wrapper around `llama_cpp.Llama`."""

    def __init__(self, config: ChatConfig | None = None) -> None:
        self.config = config or ChatConfig.from_env()
        self._model = None
        self._load_lock = asyncio.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="llama-infer"
        )

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def _ensure_loaded(self) -> None:
        async with self._load_lock:
            if self._model is not None:
                return
            cfg = self.config
            n_threads = cfg.n_threads if cfg.n_threads is not None else (os.cpu_count() or 4)

            def _load():
                from llama_cpp import Llama

                model_path = resolve_model_path(cfg)
                log.info(
                    "Loading llama.cpp model from %s (n_ctx=%d, n_gpu_layers=%d, n_threads=%d)...",
                    model_path,
                    cfg.n_ctx,
                    cfg.n_gpu_layers,
                    n_threads,
                )
                return Llama(
                    model_path=model_path,
                    n_ctx=cfg.n_ctx,
                    n_batch=512,
                    n_threads=n_threads,
                    n_gpu_layers=cfg.n_gpu_layers,
                    verbose=False,
                )

            self._model = await asyncio.to_thread(_load)
            log.info("llama.cpp chat model ready")

    async def complete(self, user_line: str) -> str:
        """Run one chat completion. `user_line` is e.g. 'Alice: hello'."""
        cfg = self.config
        prompt = format_chatml_prompt(system=cfg.system_prompt, user_line=user_line)

        await self._ensure_loaded()
        assert self._model is not None

        def _run() -> str:
            assert self._model is not None
            result = self._model(
                prompt,
                max_tokens=cfg.max_tokens,
                temperature=cfg.temperature,
                top_p=cfg.top_p,
                stop=[_IM_END, "<|im_start|>"],
                echo=False,
            )
            choices = result.get("choices") or []
            if not choices:
                return ""
            text = choices[0].get("text", "")
            return parse_chatml_response(text)

        loop = asyncio.get_running_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(self._executor, _run),
            timeout=cfg.inference_timeout,
        )


class OllamaCloudChat:
    """Chat completions via Ollama Cloud (https://ollama.com)."""

    def __init__(self, config: ChatConfig) -> None:
        if not config.ollama_api_key:
            raise ValueError(
                "OLLAMA_API_KEY is required when CHAT_PROVIDER=ollama_cloud"
            )
        self.config = config

    def shutdown(self) -> None:
        return

    async def complete(self, user_line: str) -> str:
        cfg = self.config
        payload = {
            "model": cfg.ollama_model,
            "messages": _chat_messages(cfg, user_line),
            "stream": False,
            "options": {
                "temperature": cfg.temperature,
                "top_p": cfg.top_p,
                "num_predict": cfg.max_tokens,
            },
        }
        headers = {
            "Authorization": f"Bearer {cfg.ollama_api_key}",
            "Content-Type": "application/json",
        }

        async def _request() -> str:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    _OLLAMA_CLOUD_URL,
                    json=payload,
                    headers=headers,
                ) as resp:
                    body = await resp.json(content_type=None)
                    if resp.status >= 400:
                        raise RuntimeError(
                            f"Ollama Cloud API error {resp.status}: {body}"
                        )
                    message = body.get("message") or {}
                    content = message.get("content", "")
                    return str(content).strip()

        return await asyncio.wait_for(_request(), timeout=cfg.inference_timeout)


class LmStudioChat:
    """Chat completions via LM Studio's OpenAI-compatible API."""

    def __init__(self, config: ChatConfig) -> None:
        if not config.lmstudio_model:
            raise ValueError(
                "CHAT_LMSTUDIO_MODEL is required when CHAT_PROVIDER=lmstudio"
            )
        self.config = config

    def shutdown(self) -> None:
        return

    async def complete(self, user_line: str) -> str:
        cfg = self.config
        url = f"{cfg.lmstudio_base_url}/chat/completions"
        payload = {
            "model": cfg.lmstudio_model,
            "messages": _chat_messages(cfg, user_line),
            "max_tokens": cfg.max_tokens,
            "temperature": cfg.temperature,
            "top_p": cfg.top_p,
            "stream": False,
        }

        async def _request() -> str:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    body = await resp.json(content_type=None)
                    if resp.status >= 400:
                        raise RuntimeError(
                            f"LM Studio API error {resp.status}: {body}"
                        )
                    choices = body.get("choices") or []
                    if not choices:
                        return ""
                    message = choices[0].get("message") or {}
                    content = message.get("content", "")
                    return str(content).strip()

        return await asyncio.wait_for(_request(), timeout=cfg.inference_timeout)


def create_chat_backend(config: ChatConfig | None = None) -> ChatBackend:
    """Instantiate the chat backend selected by CHAT_PROVIDER."""
    cfg = config or ChatConfig.from_env()
    provider = cfg.provider

    if provider not in _VALID_PROVIDERS:
        raise ValueError(
            f"Unknown CHAT_PROVIDER={provider!r}; "
            f"expected one of: {', '.join(sorted(_VALID_PROVIDERS))}"
        )

    if provider == "llama":
        return LlamaChat(cfg)
    if provider == "ollama_cloud":
        return OllamaCloudChat(cfg)
    return LmStudioChat(cfg)
