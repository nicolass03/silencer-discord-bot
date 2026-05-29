# Bot personality prompt

Edit [`personality.txt`](personality.txt) to define how the bot behaves when someone @mentions it in chat.

The file is loaded as the **system** message for every @mention chat provider (see [`src/llm.py`](../src/llm.py)). Restart the bot after saving changes.

## Override order

1. **`CHAT_SYSTEM_PROMPT`** in `.env` — single-line override; wins over the file (useful for quick tests).
2. **`CHAT_PERSONALITY_PATH`** in `.env` — path to a different text file (absolute or relative to the repo root).
3. **`prompts/personality.txt`** — default file (this folder).

## Docker

Both [`Dockerfile`](../Dockerfile) and [`Dockerfile.gpu`](../Dockerfile.gpu) copy `prompts/` into the image at `/app/prompts/personality.txt`.

[`docker-compose.gpu.yml`](../docker-compose.gpu.yml) bind-mounts `./prompts` read-only so you can edit `personality.txt` without rebuilding. Use:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
```

The CPU compose file does not mount `prompts/` by default; add the same volume there if you want live edits without rebuild on CPU.
