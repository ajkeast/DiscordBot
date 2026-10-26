-- Minecraft daily activity (created at runtime by utils/minecraft_job.py).
-- Additive only: do not alter or drop existing tables.
--
-- Weekly playtime leaderboard:
--   SELECT player_name, SUM(duration_seconds) AS seconds
--   FROM minecraft_sessions
--   WHERE activity_date >= CURRENT_DATE - 7
--   GROUP BY player_name
--   ORDER BY seconds DESC;

CREATE TABLE IF NOT EXISTS minecraft_sessions (
    id BIGSERIAL PRIMARY KEY,
    activity_date DATE NOT NULL,
    player_name TEXT NOT NULL,
    player_uuid TEXT,
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL,
    slice_started_at TIMESTAMPTZ NOT NULL,
    slice_ended_at TIMESTAMPTZ NOT NULL,
    duration_seconds INTEGER NOT NULL CHECK (duration_seconds > 0),
    end_reason TEXT NOT NULL CHECK (
        end_reason IN ('leave', 'server_stop', 'rejoin', 'still_online')
    ),
    likely_afk BOOLEAN NOT NULL,
    UNIQUE (player_name, started_at, activity_date)
);

CREATE INDEX IF NOT EXISTS minecraft_sessions_date_player_idx
    ON minecraft_sessions (activity_date, player_name);

CREATE TABLE IF NOT EXISTS minecraft_deaths (
    id BIGSERIAL PRIMARY KEY,
    activity_date DATE NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    player_name TEXT NOT NULL,
    player_uuid TEXT,
    message TEXT NOT NULL,
    UNIQUE (player_name, occurred_at, message)
);

CREATE INDEX IF NOT EXISTS minecraft_deaths_date_idx
    ON minecraft_deaths (activity_date);

CREATE TABLE IF NOT EXISTS minecraft_advancements (
    id BIGSERIAL PRIMARY KEY,
    activity_date DATE NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    player_name TEXT NOT NULL,
    player_uuid TEXT,
    kind TEXT NOT NULL CHECK (kind IN ('advancement', 'challenge', 'goal')),
    title TEXT NOT NULL,
    UNIQUE (player_name, occurred_at, kind, title)
);

CREATE INDEX IF NOT EXISTS minecraft_advancements_date_idx
    ON minecraft_advancements (activity_date);

CREATE TABLE IF NOT EXISTS minecraft_daily_posts (
    activity_date DATE PRIMARY KEY,
    channel_id BIGINT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'posted')),
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    posted_at TIMESTAMPTZ,
    message_id BIGINT,
    nobody_played BOOLEAN NOT NULL DEFAULT FALSE
);
