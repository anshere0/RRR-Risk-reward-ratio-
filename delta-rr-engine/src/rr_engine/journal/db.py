"""SQLite Journal database client for deterministic replay and paper trading.

Requirements (docs/01 §4, docs/05 §4, docs/06 §9):
  - Every evaluation is journaled (inputs, config hash, market snapshot, response).
  - Bit-for-bit replay via scripts/replay.py.
  - Forward-testing & calibration metrics via scripts/calibrate.py.
  - Session discipline gate support (daily loss limit, consecutive loss cooldown).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class JournalDB:
    """Thread-safe SQLite database manager for trade evaluations and paper trading."""

    def __init__(self, db_path: str | Path = "journal.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        schema_file = Path(__file__).parent / "schema.sql"
        if schema_file.exists():
            schema_sql = schema_file.read_text(encoding="utf-8")
        else:
            # Fallback schema if file not found
            schema_sql = """
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
                exit_reason TEXT,
                pnl_usd REAL,
                entered_at TEXT NOT NULL,
                exited_at TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        with self._get_connection() as conn:
            conn.executescript(schema_sql)

    def record_evaluation(
        self,
        timestamp_utc: str,
        symbol: str,
        spot_index_price: float,
        verdict: str,
        validation_status: str,
        config_hash: str,
        request_data: dict[str, Any] | str,
        market_snapshot: dict[str, Any] | str,
        response_data: dict[str, Any] | str,
        net_rr: float | None = None,
        breakeven_win_probability: float | None = None,
    ) -> int:
        """Insert an evaluation record into the journal and return its ID."""
        req_str = request_data if isinstance(request_data, str) else json.dumps(request_data)
        snap_str = market_snapshot if isinstance(market_snapshot, str) else json.dumps(market_snapshot)
        resp_str = response_data if isinstance(response_data, str) else json.dumps(response_data)

        sql = """
        INSERT INTO evaluations (
            timestamp_utc, symbol, spot_index_price, verdict, validation_status,
            net_rr, breakeven_win_probability, config_hash,
            request_json, market_snapshot_json, response_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._get_connection() as conn:
            cur = conn.execute(
                sql,
                (
                    timestamp_utc,
                    symbol,
                    spot_index_price,
                    verdict,
                    validation_status,
                    net_rr,
                    breakeven_win_probability,
                    config_hash,
                    req_str,
                    snap_str,
                    resp_str,
                ),
            )
            return cur.lastrowid or 0

    def get_evaluation(self, eval_id: int) -> dict[str, Any] | None:
        """Fetch an evaluation record by ID."""
        sql = "SELECT * FROM evaluations WHERE id = ?"
        with self._get_connection() as conn:
            row = conn.execute(sql, (eval_id,)).fetchone()
            if not row:
                return None
            res = dict(row)
            res["request"] = json.loads(res["request_json"])
            res["market_snapshot"] = json.loads(res["market_snapshot_json"])
            res["response"] = json.loads(res["response_json"])
            return res

    def get_latest_evaluation(self) -> dict[str, Any] | None:
        """Fetch the most recent evaluation record."""
        sql = "SELECT * FROM evaluations ORDER BY id DESC LIMIT 1"
        with self._get_connection() as conn:
            row = conn.execute(sql).fetchone()
            if not row:
                return None
            res = dict(row)
            res["request"] = json.loads(res["request_json"])
            res["market_snapshot"] = json.loads(res["market_snapshot_json"])
            res["response"] = json.loads(res["response_json"])
            return res

    def list_evaluations(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """List summary of evaluations for reporting and HUD."""
        sql = """
        SELECT id, timestamp_utc, symbol, spot_index_price, verdict,
               validation_status, net_rr, breakeven_win_probability, config_hash
        FROM evaluations
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """
        with self._get_connection() as conn:
            rows = conn.execute(sql, (limit, offset)).fetchall()
            return [dict(r) for r in rows]

    def count_evaluations(self) -> int:
        """Count total evaluations logged."""
        sql = "SELECT COUNT(*) FROM evaluations"
        with self._get_connection() as conn:
            return int(conn.execute(sql).fetchone()[0])

    def record_paper_trade(
        self,
        symbol: str,
        direction: str,
        lots: float,
        entry_price: float,
        sl_price: float,
        tp_price: float,
        entered_at: str,
        evaluation_id: int | None = None,
        actual_fill_price: float | None = None,
    ) -> int:
        """Log a new paper trade."""
        sql = """
        INSERT INTO paper_trades (
            evaluation_id, symbol, direction, lots, entry_price, sl_price, tp_price,
            actual_fill_price, entered_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self._get_connection() as conn:
            cur = conn.execute(
                sql,
                (
                    evaluation_id,
                    symbol,
                    direction,
                    lots,
                    entry_price,
                    sl_price,
                    tp_price,
                    actual_fill_price or entry_price,
                    entered_at,
                ),
            )
            return cur.lastrowid or 0

    def update_paper_trade_exit(
        self,
        trade_id: int,
        actual_exit_price: float,
        exit_reason: str,
        exited_at: str,
        pnl_usd: float,
    ) -> None:
        """Record trade exit outcome (TP, SL, or TIMEOUT)."""
        sql = """
        UPDATE paper_trades
        SET actual_exit_price = ?, exit_reason = ?, exited_at = ?, pnl_usd = ?
        WHERE id = ?
        """
        with self._get_connection() as conn:
            conn.execute(sql, (actual_exit_price, exit_reason, exited_at, pnl_usd, trade_id))

    def get_all_paper_trades(self) -> list[dict[str, Any]]:
        """Retrieve all paper trades for calibration."""
        sql = "SELECT * FROM paper_trades ORDER BY id ASC"
        with self._get_connection() as conn:
            rows = conn.execute(sql).fetchall()
            return [dict(r) for r in rows]

    def count_paper_trades(self) -> int:
        """Count total paper trades logged."""
        sql = "SELECT COUNT(*) FROM paper_trades"
        with self._get_connection() as conn:
            return int(conn.execute(sql).fetchone()[0])

    def get_daily_loss(self, date_prefix: str) -> float:
        """Calculate net P&L loss for the IST calendar day (F10).

        If net P&L is negative, returns the positive loss amount.
        If net P&L is positive, returns 0.0.
        """
        # SQLite datetime with +5 hours 30 minutes offset for Indian Standard Time (IST)
        sql = """
        SELECT SUM(pnl_usd) FROM paper_trades
        WHERE strftime('%Y-%m-%d', datetime(entered_at, '+5 hours', '+30 minutes')) = ?
          AND pnl_usd IS NOT NULL
        """
        try:
            with self._get_connection() as conn:
                val = conn.execute(sql, (date_prefix,)).fetchone()[0]
                if val is not None and val < 0:
                    return float(abs(val))
                if val is not None and val >= 0:
                    return 0.0
        except Exception:
            pass

        # Fallback to standard entered_at prefix
        sql_fb = "SELECT SUM(pnl_usd) FROM paper_trades WHERE entered_at LIKE ? AND pnl_usd IS NOT NULL"
        with self._get_connection() as conn:
            val = conn.execute(sql_fb, (f"{date_prefix}%",)).fetchone()[0]
            if val is not None and val < 0:
                return float(abs(val))
            return 0.0

    def get_consecutive_losses(self) -> tuple[int, str | None]:
        """Return (consecutive_loss_count, last_loss_time_iso)."""
        sql = """
        SELECT pnl_usd, exited_at FROM paper_trades
        WHERE exited_at IS NOT NULL
        ORDER BY id DESC
        LIMIT 20
        """
        with self._get_connection() as conn:
            rows = conn.execute(sql).fetchall()
            count = 0
            last_time = None
            for r in rows:
                pnl = r["pnl_usd"] or 0.0
                if pnl < 0:
                    count += 1
                    if last_time is None:
                        last_time = r["exited_at"]
                else:
                    break
            return count, last_time
