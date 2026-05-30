# syntax=docker/dockerfile:1.7

ARG BOT_PROFILE=full

FROM python:3.12-slim AS base

ARG BOT_PROFILE

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BOT_PROFILE=${BOT_PROFILE}

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libopus0 libgomp1 git ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-base.txt requirements-${BOT_PROFILE}.txt ./

# llama-cpp-python ships no PyPI wheel; pull a prebuilt CPU wheel from the
# project index so we don't need a C/C++ toolchain in the image (full only).
RUN if [ "$BOT_PROFILE" = "full" ]; then \
        export HF_HOME=/cache/huggingface XDG_CACHE_HOME=/cache; \
        pip install --no-cache-dir --prefer-binary \
            --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu \
            -r requirements-full.txt; \
    else \
        pip install --no-cache-dir --prefer-binary \
            -r requirements-slim.txt; \
    fi

COPY src ./src
COPY prompts ./prompts

RUN useradd --create-home --uid 1000 silencer \
    && mkdir -p /cache/huggingface \
    && chown -R silencer:silencer /app /cache

USER silencer

ENV HF_HOME=/cache/huggingface \
    XDG_CACHE_HOME=/cache

CMD ["python", "-m", "src.bot"]
