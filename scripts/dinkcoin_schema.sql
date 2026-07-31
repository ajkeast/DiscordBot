-- DinkCoin Postgres ledger tables (included in deploy/postgres/init.sql on VPS)

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
