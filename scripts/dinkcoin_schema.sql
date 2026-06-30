-- DinkCoin MySQL ledger tables (run once against the bot database)

CREATE TABLE IF NOT EXISTS dinkcoin_balances (
    user_id VARCHAR(32) PRIMARY KEY,
    balance DECIMAL(18, 8) NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS dinkcoin_transactions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    from_user_id VARCHAR(32) NULL,
    to_user_id VARCHAR(32) NOT NULL,
    amount DECIMAL(18, 8) NOT NULL,
    tx_type ENUM('mint', 'transfer') NOT NULL,
    tx_hash VARCHAR(66) NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
