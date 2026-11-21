-- Delta Exchange BTC Options Decision Support Journal Schema
-- SQLite 3 (WAL mode recommended)
-- Stored at data/journal.db or journal.db

CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp_utc TEXT NOT NULL,
    symbol TEXT NOT NULL,
    spot_index_price REAL NOT NULL,
    verdict TEXT NOT NULL,
    validation_status TEXT NOT NULL,
    net_rr REAL,
    breakeven_win_probability REAL,
    config_hash TEXT NOT NULL,
    request_json TEXT NOT NULL,
    market_snapshot_json TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_evaluations_timestamp ON evaluations(timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_evaluations_symbol ON evaluations(symbol);
CREATE INDEX IF NOT EXISTS idx_evaluations_verdict ON evaluations(verdict);

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id INTEGER REFERENCES evaluations(id),
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    lots REAL NOT NULL,
    entry_price REAL NOT NULL,
    sl_price REAL NOT NULL,
    tp_price REAL NOT NULL,
    actual_fill_price REAL,
    actual_exit_price REAL,
    exit_reason TEXT,                 -- 'TP_HIT' | 'SL_HIT' | 'TIMEOUT' | 'MANUAL'
    pnl_usd REAL,
    entered_at TEXT NOT NULL,
    exited_at TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_paper_trades_entered ON paper_trades(entered_at);
CREATE INDEX IF NOT EXISTS idx_paper_trades_eval_id ON paper_trades(evaluation_id);
