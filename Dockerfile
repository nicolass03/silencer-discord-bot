# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/cache/huggingface \
    XDG_CACHE_HOME=/cache

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libopus0 libgomp1 git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
# llama-cpp-python ships no PyPI wheel; pull a prebuilt CPU wheel from the
# project index so we don't need a C/C++ toolchain in the image.
RUN pip install --no-cache-dir --prefer-binary \
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu \
    -r requirements.txt

COPY src ./src
COPY prompts ./prompts

RUN useradd --create-home --uid 1000 silencer \
    && mkdir -p /cache/huggingface \
    && chown -R silencer:silencer /app /cache
USER silencer

CMD ["python", "-m", "src.bot"]
