# DiscordBot

Peter Dinklage — the Discord bot for [dinkscord.com](https://dinkscord.com), backed by PostgreSQL, xAI Grok, and Lavalink.

## Features

### AI Chat, Images & Voice (xAI Grok)

- **`/ask`** — Shared server conversation powered by xAI's stateful Responses API with real-time web/X search, image inputs, and client-side self-knowledge tools (`services/self_knowledge.py`).
- **`/imagine`** — Image generation and single-image editing via Grok Imagine, with persistent **Edit** and **Retry** buttons and per-user rate limiting.
- **`/voice`** — Answers a prompt and synthesizes the reply as an MP3 attachment via xAI TTS.
- **`/clear`** — Resets the shared conversation immediately (also auto-clears daily at 3:00 AM Eastern).

#### Self-Knowledge Tools

Every `/ask` and `/voice` turn exposes client-side tools so Grok answers questions about the bot from live data instead of guessing:

| Tool | What it returns |
|------|-----------------|
| `get_bot_documentation` | Member-facing docs (`docs/self_knowledge/`) covering overview, `/1st`, DinkCoin, AI, music, and server stats |
| `list_bot_commands` | Live registered command list from all loaded cogs |
| `get_first_game_stats` | Live `/1st` win leaderboard, most recent winner, and current streak |
| `get_juice_stats` | Live juice leaderboard and single-day high score |
| `get_dink_ledger` | Live top DINK holders and total circulation |

### Daily `/1st` Game & DinkCoin (DINK)

Members compete in **#general** to claim the first win of each day (America/New_York). Winning `/1st` atomically records the win and mints **1 DINK** (`DINK_MINT_AMOUNT`).

| Command | Description |
|---------|-------------|
| `/1st` | Claim first for today in `#general` |
| `/score` | Top 5 firsts leaderboard and current streak |
| `/stats [member]` | Score, total juice, and longest streak |
| `/juice` | Total juice leaderboard and 1-day high score |
| `/graph` / `/juicegraph` | Cumulative firsts and daily juice PNG charts |
| `/balance` / `/ledger` | Check your DINK balance or top holders |
| `/pay` / `/request` | Send DINK or request DINK with persistent Accept / Decline buttons |

### Music (SoundCloud Voice)

Requires **Lavalink** with the [Soundy](https://github.com/Lochis/soundy) plugin (`deploy/lavalink/application.yml`) for SoundCloud AAC HLS playback via Wavelink.

| Command | Description |
|---------|-------------|
| `/play <url or search>` | Play a SoundCloud URL or top full-stream search result |
| `/skip` / `/stop` / `/pause` / `/resume` | Playback controls |
| `/queue` / `/np` / `/leave` | Inspect queue, now playing, or disconnect |

The bot auto-disconnects after 5 minutes idle (`MUSIC_INACTIVE_TIMEOUT`) or 90 seconds alone in voice.

### Daily Minecraft Recap

Every morning at **8:00 AM America/New_York**, the bot posts a concise Grok-written recap of the previous play day (**5:00 AM – 5:00 AM Eastern**) to **#minecraft**:

- Reads playday sessions and deaths from the Dinkboard collector schema (`minecraft.sessions`, `minecraft.deaths`), subtracts `plugins/Camera/sessions.jsonl` time when present, verifies first-time players against collector history, and attaches the newest playday screenshot from `MC_RECAP_SCREENSHOT_DIR`.
- Passes clean factual JSON (playtimes, sessions, peak stretch, chronological death log with PVP flags) to Grok (`GROK_MINECRAFT_MODEL`, default `grok-4.7`). Falls back to a compact template if Grok is unavailable.
- Writes AFK-annotated session slices to `public.minecraft_sessions` (for the dashboard) and idempotent claims to `minecraft_daily_posts`.
- Admins can preview read-only or post a finished day early:

```text
/minecraft_summary
/minecraft_summary day:2026-10-25
/minecraft_summary post:True
/minecraft_summary day:2026-10-25 post:True
```

### Nightly Sentiment & Server Stats

- **Nightly Sentiment (4:15 AM Eastern):** Scores unscored original messages via xAI structured outputs using a single SQL `LATERAL` query to fetch local channel context. Admins can run `/score_sentiment [limit]` manually.
- **Monthly Activity Report (9:00 AM Eastern on the 1st):** Idempotently posts top monthly messengers to `#general`. Admins can sync server metadata with `/members`, `/emojis`, and `/channels`.

## Project Structure

- `bot.py`: `DinkBot` entrypoint, pooled DB + shared `aiohttp` session lifecycle, message/edit logging
- `config.py`: Single `load_dotenv()`, typed env settings, `zoneinfo` Eastern timezone
- `cogs/`: Thin Discord command, task, and view adapters (`ai.py`, `ai_imagine_ui.py`, `dinkcoin.py`, `first.py`, `minecraft.py`, `music.py`, `sentiment.py`, `server.py`, `utility.py`)
- `db/`: `psycopg_pool` connection pool (`pool.py`), canonical `schema.sql`, and domain repositories
- `minecraft/`: Collector-backed playday analytics, Grok narrative, screenshots, and recap orchestrator
- `services/`: Unified xAI client (`xai.py`), streak/juice analytics (`first_game.py`), thread-safe matplotlib (`charts.py`), self-knowledge (`self_knowledge.py`), sentiment (`sentiment.py`), and SoundCloud helpers (`soundcloud.py`)
- `utils/`: Shared admin permissions and Discord interaction helpers
- `docs/self_knowledge/`: Member-facing markdown docs exposed to Grok
- `deploy/`: VPS deploy script and Lavalink config
- `tests/`: Unit, DB, and live smoke test suites

## Local Development

1. Copy `.env.example` to `.env` and fill in credentials.
2. Optionally set `DISCORD_GUILD_ID` for instant slash-command sync.
3. Stop any running production bot instance sharing the same token, then run:

```bash
./scripts/dev.sh
```

## Testing & CI

Run unit tests (no live external calls):

```bash
./scripts/test.sh -m "not live"
```

Run live xAI smoke tests only:

```bash
source .venv/bin/activate
pytest tests/live/test_grok_live.py -v --tb=short
```

CI (`.github/workflows/test.yml`) runs `ruff`, `mypy`, initializes a `pgvector/pgvector:pg16` Postgres service with `db/schema.sql`, and runs the full `pytest` suite on pull requests.
