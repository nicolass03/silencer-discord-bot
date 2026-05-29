# silencer-discord-bot

A small self-hosted Python Discord bot. The initial scaffolding can connect to
Discord and join / leave the voice channel the invoking user is in via slash
commands.

## Requirements

- Python 3.11 or newer
- A Discord application + bot token

## Setup

### 1. Clone and create a virtual environment

```bash
cd silencer-discord-bot
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

`PyNaCl` is bundled in `requirements.txt` and is required for voice connections.
No `ffmpeg` install is needed yet because the bot does not play or record audio
in this initial scaffold.

### 3. Create a Discord application and bot

1. Go to <https://discord.com/developers/applications> and create a new
   application.
2. Open the **Bot** tab and click **Add Bot**.
3. Under the bot's **Token** section, click **Reset Token** and copy the new
   token.
4. No privileged intents need to be enabled. The bot uses `guilds`,
   `voice_states`, and `messages` (non-privileged). Mention-only chat does
   **not** require the Message Content privileged intent.

### 4. Configure your token

```bash
cp .env.example .env
```

Edit `.env` and paste your token:

```
DISCORD_TOKEN=your-bot-token-here
```

`.env` is already covered by `.gitignore` and will not be committed.

### 5. Invite the bot to your server

In the Developer Portal go to **OAuth2 -> URL Generator** and select:

- **Scopes**: `bot`, `applications.commands`
- **Bot Permissions**: `View Channels`, `Send Messages`, `Read Message History`,
  `Connect`, `Speak`, `Use Voice Activity`

Open the generated URL in a browser and pick a server you have **Manage Server**
permission on.

### 6. Run the bot

From the repo root, with the venv active:

```bash
python -m src.bot
```

You should see log output similar to:

```
[INFO] silencer.bot: Synced 3 slash command(s) globally
[INFO] silencer.bot: Logged in as YourBot#1234 (id=...) in 1 guild(s)
```

Stop the bot with `Ctrl+C`.

## Commands

All commands are slash commands.

| Command | Description |
| --- | --- |
| `/ping` | Replies with the bot's gateway latency. |
| `/join` | Connects the bot to the voice channel you are currently in. |
| `/leave` | Disconnects the bot from its current voice channel. |

## Voice activity logging

While the bot is connected to a voice channel, it logs voice-state events for
that channel to stdout, e.g.:

```
[INFO] silencer.voice: [voice/General] Alice (id=123...) joined
[INFO] silencer.voice: [voice/General] Alice (id=123...) state change: self_mute=True
[INFO] silencer.voice: [voice/General] Alice (id=123...) state change: streaming=True
[INFO] silencer.voice: [voice/General] Alice (id=123...) disconnected from voice
```

Tracked transitions: join, leave, move, self-mute, self-deafen, server-mute,
server-deafen, stream (Go Live), camera, and stage-channel suppress.

## Speech-to-text transcription

When the bot is in a voice channel, it also transcribes what each user is
saying using [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
running fully locally. Transcripts are written to stdout, one line per
utterance, tagged with the speaker:

```
[INFO] silencer.transcribe: [transcript/General] Alice (id=123...): hey can you hear me
[INFO] silencer.transcribe: [transcript/General] Bob (id=456...): yeah loud and clear
```

### How it works

- Voice receive is provided by
  [discord-ext-voice-recv](https://github.com/imayhaveborkedit/discord-ext-voice-recv).
- The bot maintains per-user PCM buffers and ticks every 50 ms. A buffer is
  flushed to Whisper as soon as either condition hits:
  - the speaker has been silent for ~0.25 s (full flush), or
  - the buffer has reached ~1.5 s of speech (streaming flush — a 0.3 s tail
    is retained as overlap so trigger words that straddle the chunk
    boundary still appear in the next chunk).
- PCM is converted in-process (stereo 48 kHz s16 → mono 16 kHz float32 via
  PyAV's `AudioResampler`) and handed directly to faster-whisper as a numpy
  array — no WAV wrapping, no extra decode pass.
- Inference runs in a dedicated single-worker `ThreadPoolExecutor` so each
  call can saturate every CPU core. Two simultaneous speakers therefore
  transcribe sequentially (faster than parallel on CPU) but each chunk is
  short enough that end-to-end latency stays low.
- Decoding is greedy (`beam_size=1`, `best_of=1`, `temperature=0`) and
  Whisper's internal Silero VAD is disabled — our buffer-level chunking is
  already silence-aware, and Silero would otherwise drop short utterances.

### Configuration

All settings are optional and live in `.env`:

| Variable | Default | Notes |
| --- | --- | --- |
| `WHISPER_MODEL` | `base` | One of `tiny`, `base`, `small`, `medium`, `large-v3`. Larger = more accurate but slower and more RAM. |
| `WHISPER_DEVICE` | `cpu` | `cpu` or `cuda`. Use `cuda` only if you've set up an NVIDIA GPU + CUDA. |
| `WHISPER_COMPUTE_TYPE` | `int8` | `int8` for CPU, `int8_float16` or `float16` for GPU, `float32` for max precision. |
| `WHISPER_LANGUAGE` | _(empty, auto)_ | ISO code like `en`, `fr`, `de`. Setting this is faster and more accurate than auto-detect. |

### First-run notes

- The first time the bot starts, faster-whisper downloads the model from
  Hugging Face into the cache directory. Sizes are roughly: `tiny` ~75 MB,
  `base` ~150 MB, `small` ~500 MB, `medium` ~1.5 GB, `large-v3` ~3 GB.
- In Docker, the model is cached in the `whisper-cache` named volume defined
  by [docker-compose.yml](docker-compose.yml), so subsequent restarts reuse
  the download.
- On CPU, expect roughly:
  - `tiny` / `base`: transcripts of a 1.5 s chunk land in ~150–300 ms, so
    trigger words inside long speech fire within ~1.5–1.8 s and short
    utterances fire ~300–500 ms after the speaker pauses.
  - `small`: ~2x slower than `base`; still usable but the streaming
    pipeline can fall behind if multiple people talk at once.
  - `medium` / `large-v3`: slower than real time on most CPUs — use a GPU
    or stick to `tiny` / `base`.
- Detecting whether a user is currently *speaking in real time* is implicit
  in the transcript stream: partial transcripts appear every ~1.5 s while
  someone is talking, plus a final one once they pause.

## @mention chat (local LLM)

When a user @mentions the bot in a text channel, the bot can reply using
[Dolphin 2.6 Mistral 7B](https://huggingface.co/TheBloke/dolphin-2.6-mistral-7B-GGUF)
(GGUF via [llama-cpp-python](https://github.com/abetlen/llama-cpp-python))
running fully on your machine. The prompt sent to the model is
`{username}: {message text after the mention}` (Discord @handle, not display name).

This feature is **off by default** (`CHAT_ENABLED=false`). When disabled, an
@mention still gets a short reply (`Chat disabled.` by default) so users know
chat is not running. When enabled, the model loads lazily on the first
@mention and subsequent replies reuse it.

### Configuration

| Variable | Default | Notes |
| --- | --- | --- |
| `CHAT_ENABLED` | `false` | Set to `true` / `1` / `yes` / `on` to load the model and reply. |
| `CHAT_MODEL_PATH` | _(empty)_ | Optional path to a local `.gguf` file; skips Hugging Face download if set. |
| `CHAT_MODEL_REPO` | `TheBloke/dolphin-2.6-mistral-7B-GGUF` | Hugging Face repo for auto-download. |
| `CHAT_MODEL_FILE` | `...Q4_K_M.gguf` | Quantized file name (~4 GB). Q4_K_M balances speed and RAM. |
| `CHAT_N_CTX` | `2048` | Context window; lower uses less RAM. |
| `CHAT_N_GPU_LAYERS` | `0` | `0` for CPU; `-1` or `35` to offload layers when using a CUDA llama-cpp build. |
| `CHAT_N_THREADS` | _(all cores)_ | CPU threads for inference. |
| `CHAT_MAX_TOKENS` | `256` | Max reply length from the model. |
| `CHAT_TEMPERATURE` | `0.7` | Sampling temperature. |
| `CHAT_TOP_P` | `0.9` | Nucleus sampling. |
| `CHAT_INFERENCE_TIMEOUT` | `120` | Seconds before the bot gives up on a slow reply. |
| `CHAT_PERSONALITY_PATH` | `prompts/personality.txt` | Text file with role, personality, and rules (see below). |
| `CHAT_SYSTEM_PROMPT` | _(empty)_ | Optional one-line override; wins over the personality file. |
| `CHAT_DISABLED_MESSAGE` | `Chat disabled.` | Reply when tagged but `CHAT_ENABLED=false`. |

### Personality / role prompt

Edit [`prompts/personality.txt`](prompts/personality.txt) to define who the bot is, how it speaks, and server rules. The file is sent as the ChatML **system** message on every @mention reply. See [`prompts/README.md`](prompts/README.md) for override order and Docker bind-mount tips.

Restart the bot after editing the file. Placeholder sections in the default file are meant to be replaced with your own text.

### First-run notes

- The first @mention with chat enabled downloads the GGUF from Hugging Face
  (same cache as Whisper when `HF_HOME` is set, e.g. in Docker under `/cache`).
- Expect ~4 GB RAM for the Q4_K_M 7B model **in addition to** the Whisper
  model. On a CPU-only host, use `WHISPER_MODEL=tiny` or `base`, or keep chat
  disabled.
- On a GPU host you can set `CHAT_N_GPU_LAYERS=-1` in `.env`. The stock
  `pip install llama-cpp-python` wheel may still be CPU-only unless you install
  a CUDA-enabled build yourself; the GPU Docker image does not change that.

## Run with Docker

You can also run the bot in a container, which is the easiest way to "press
play" from Docker Desktop and have it auto-restart.

### Option A: Docker Compose (recommended)

This is the one-click path for Docker Desktop. The Compose project will appear
in the **Containers** tab and you can start/stop it from there.

1. Make sure your `.env` file exists at the repo root with `DISCORD_TOKEN=...`.
2. Build and start:

   ```bash
   docker compose up -d --build
   ```

3. View logs:

   ```bash
   docker compose logs -f
   ```

4. Stop:

   ```bash
   docker compose down
   ```

After the first `docker compose up`, Docker Desktop will show a
`silencer-discord-bot` stack under **Containers**, and you can use the play /
stop buttons there directly.

### Option B: Plain Docker

```bash
docker build -t silencer-discord-bot .
docker run -d --name silencer-discord-bot \
  --restart unless-stopped \
  --env-file .env \
  silencer-discord-bot
```

To launch from the Docker Desktop UI instead of the CLI: after `docker build`,
open Docker Desktop -> **Images** -> click the `silencer-discord-bot` image ->
**Run**, expand **Optional settings**, and under **Environment variables** add
`DISCORD_TOKEN` with your token value, then click **Run**.

## Notes

- Global slash commands can take up to about an hour to appear in Discord the
  first time they are synced. For instant updates during development, you can
  change `await self.tree.sync()` in [src/bot.py](src/bot.py) to a guild-scoped
  sync, e.g.:

  ```python
  guild = discord.Object(id=YOUR_GUILD_ID)
  self.tree.copy_global_to(guild=guild)
  await self.tree.sync(guild=guild)
  ```

- The bot uses slash commands for voice control; the `command_prefix` is set
  to `when_mentioned` purely because `commands.Bot` requires a prefix value.
  Text chat is handled via an `on_message` listener when the bot is @mentioned.

## Project layout

```
silencer-discord-bot/
├── .dockerignore
├── .env.example
├── Dockerfile
├── README.md
├── docker-compose.yml
├── prompts/
│   ├── README.md
│   └── personality.txt
├── requirements.txt
└── src/
    ├── __init__.py
    ├── bot.py
    ├── transcriber.py
    ├── llm.py
    ├── personality.py
    └── cogs/
        ├── __init__.py
        ├── chat.py
        ├── moderator.py
        ├── transcribe.py
        └── voice.py
```
