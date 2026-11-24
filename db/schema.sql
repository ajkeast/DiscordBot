-- Canonical idempotent PostgreSQL schema for DiscordBot.
-- Used to initialize fresh local/CI databases.
-- Additive only: never ALTER or DROP existing production tables.

CREATE TABLE IF NOT EXISTS members (
    id BIGINT PRIMARY KEY,
    user_name TEXT,
    display_name TEXT,
    avatar TEXT,
    created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS emojis (
    id BIGINT PRIMARY KEY,
    emoji_name TEXT,
    guild_id BIGINT,
    url TEXT,
    created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS channels (
    id BIGINT PRIMARY KEY,
    channel_name TEXT,
    created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (
    id BIGINT PRIMARY KEY,
    member_id BIGINT,
    channel_id BIGINT,
    content VARCHAR(4000),
    created_at TIMESTAMP,
    edited_content VARCHAR(4000),
    edited_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS firstlist_id (
    user_id VARCHAR(255) NOT NULL,
    timesent TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chatgpt_logs (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255),
    model VARCHAR(64),
    request_messages JSONB,
    response_content TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    message_id BIGINT REFERENCES messages(id),
    function_calls JSONB,
    image_urls JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dalle_3_prompts (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(255),
    prompt TEXT,
    message_id BIGINT REFERENCES messages(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Bot-managed runtime tables below (also ensured at startup via db/schema.py)

CREATE TABLE IF NOT EXISTS dinkcoin_balances (
    user_id VARCHAR(32) PRIMARY KEY,
    balance NUMERIC(18, 8) NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS dinkcoin_transactions (
    id SERIAL PRIMARY KEY,
    from_user_id VARCHAR(32) NULL,
    to_user_id VARCHAR(32) NOT NULL,
    amount NUMERIC(18, 8) NOT NULL,
    tx_type TEXT NOT NULL CHECK (tx_type IN ('mint', 'transfer')),
    tx_hash VARCHAR(66) NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dinkcoin_requests (
    id BIGSERIAL PRIMARY KEY,
    requester_id BIGINT NOT NULL,
    payer_id BIGINT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount > 0),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'accepted', 'declined', 'expired')),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS monthly_report_posts (
    report_month VARCHAR(7) PRIMARY KEY,
    channel_id BIGINT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'posted')),
    claimed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    posted_at TIMESTAMPTZ,
    message_id BIGINT
);

CREATE TABLE IF NOT EXISTS message_sentiment (
    message_id BIGINT PRIMARY KEY REFERENCES messages(id),
    polarity TEXT NOT NULL CHECK (polarity IN ('positive', 'negative', 'neutral', 'mixed')),
    polarity_score REAL NOT NULL,
    emotions VARCHAR(128) NOT NULL,
    sarcasm BOOLEAN NOT NULL,
    toxicity TEXT NOT NULL CHECK (toxicity IN ('none', 'mild', 'moderate', 'severe')),
    directed_at TEXT NOT NULL CHECK (directed_at IN ('general', 'person', 'group', 'self', 'topic')),
    confidence REAL NOT NULL,
    rationale VARCHAR(255) NOT NULL,
    model VARCHAR(64) NOT NULL,
    scored_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

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
