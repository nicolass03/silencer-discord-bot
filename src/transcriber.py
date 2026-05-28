"""Speech-to-text via faster-whisper.

Optimised for low-latency, CPU-only trigger-word detection:

- The model loads on a background thread the first time it is needed.
- Raw 48 kHz signed-16-bit stereo PCM from Discord is converted in-process to
  Whisper's native input format (mono float32 at 16 kHz) using a single shared
  PyAV `AudioResampler`. No WAV wrapping, no extra decode pass.
- Inference runs in a dedicated single-worker `ThreadPoolExecutor`. One call
  at a time lets CTranslate2 saturate every CPU core; two parallel calls on
  the same CPU each run ~1.8x slower than one, so sequential is faster
  end-to-end.
"""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import av
import numpy as np

log = logging.getLogger("silencer.transcriber")


@dataclass(frozen=True)
class TranscriberConfig:
    model_name: str = "base"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str | None = None
    initial_prompt: str | None = None

    @classmethod
    def from_env(cls) -> "TranscriberConfig":
        lang = os.getenv("WHISPER_LANGUAGE", "").strip() or None
        prompt = os.getenv("WHISPER_INITIAL_PROMPT", "").strip() or None
        return cls(
            model_name=os.getenv("WHISPER_MODEL", "base").strip() or "base",
            device=os.getenv("WHISPER_DEVICE", "cpu").strip() or "cpu",
            compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "int8").strip() or "int8",
            language=lang,
            initial_prompt=prompt,
        )


class FasterWhisperTranscriber:
    """Async-friendly wrapper around `faster_whisper.WhisperModel`."""

    def __init__(self, config: TranscriberConfig | None = None) -> None:
        self.config = config or TranscriberConfig.from_env()
        self._model = None
        self._load_lock = asyncio.Lock()
        self._load_task: asyncio.Task | None = None
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="whisper-infer"
        )
        # PyAV resamplers are stateful (they hold internal buffers). Each
        # transcription chunk is independent so we build a fresh resampler per
        # call rather than reuse one — keeps things stateless and correct
        # across silence boundaries.

    def warmup(self) -> asyncio.Task:
        """Kick off model loading in the background. Safe to call repeatedly."""
        if self._load_task is None:
            self._load_task = asyncio.create_task(self._ensure_loaded())
        return self._load_task

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    async def _ensure_loaded(self) -> None:
        async with self._load_lock:
            if self._model is not None:
                return
            cfg = self.config
            cpu_threads = os.cpu_count() or 4
            log.info(
                "Loading faster-whisper model '%s' (device=%s, compute_type=%s, cpu_threads=%d)...",
                cfg.model_name,
                cfg.device,
                cfg.compute_type,
                cpu_threads,
            )

            def _load():
                from faster_whisper import WhisperModel

                return WhisperModel(
                    cfg.model_name,
                    device=cfg.device,
                    compute_type=cfg.compute_type,
                    cpu_threads=cpu_threads,
                    num_workers=1,
                )

            self._model = await asyncio.to_thread(_load)
            log.info("faster-whisper model ready")

    async def transcribe_pcm(
        self,
        pcm: bytes,
        *,
        sample_rate: int = 48000,
        channels: int = 2,
        sample_width: int = 2,
    ) -> str | None:
        """Transcribe raw PCM audio. Returns the trimmed text, or None if empty."""
        if not pcm:
            return None

        await self._ensure_loaded()
        assert self._model is not None

        duration = len(pcm) / (sample_rate * channels * sample_width)
        if duration < 0.2:
            return None

        try:
            audio = _pcm_to_mono16k_float32(
                pcm,
                sample_rate=sample_rate,
                channels=channels,
            )
        except Exception:
            log.exception("PCM preprocessing failed")
            return None

        if audio.size == 0:
            return None

        def _run() -> str:
            assert self._model is not None
            segments, _info = self._model.transcribe(
                audio,
                language=self.config.language,
                initial_prompt=self.config.initial_prompt,
                vad_filter=False,
                beam_size=1,
                best_of=1,
                temperature=0.0,
                without_timestamps=True,
                condition_on_previous_text=False,
            )
            return " ".join(seg.text.strip() for seg in segments).strip()

        loop = asyncio.get_running_loop()
        try:
            text = await loop.run_in_executor(self._executor, _run)
        except Exception:
            log.exception("Transcription failed")
            return None

        return text or None


def _pcm_to_mono16k_float32(
    pcm: bytes,
    *,
    sample_rate: int,
    channels: int,
) -> np.ndarray:
    """Convert raw interleaved s16 PCM into mono float32 at 16 kHz.

    Uses a per-call PyAV `AudioResampler` so each chunk is independent and
    state from a previous flush can't leak into the next one.
    """
    samples = np.frombuffer(pcm, dtype=np.int16)
    if samples.size == 0:
        return np.empty(0, dtype=np.float32)

    if channels == 1:
        layout = "mono"
        ndarray = samples.reshape(1, -1)
    elif channels == 2:
        layout = "stereo"
        ndarray = samples.reshape(1, -1)
    else:
        raise ValueError(f"Unsupported channel count: {channels}")

    frame = av.AudioFrame.from_ndarray(ndarray, format="s16", layout=layout)
    frame.sample_rate = sample_rate

    resampler = av.AudioResampler(format="flt", layout="mono", rate=16000)
    out_frames = resampler.resample(frame)
    flush = resampler.resample(None)
    if flush:
        out_frames = (out_frames or []) + list(flush)

    if not out_frames:
        return np.empty(0, dtype=np.float32)

    parts = [f.to_ndarray().reshape(-1) for f in out_frames]
    return np.concatenate(parts).astype(np.float32, copy=False)
