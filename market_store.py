"""Durable local storage for verified market observations.

The dashboard is a local application, so Python's built-in SQLite support keeps
captured data available across browser and app restarts without another service.
Every intraday observation is insert-only: later crawls may add missing times,
but they cannot silently rewrite values that the user already saw.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class MarketStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS captures (
                    trading_date TEXT NOT NULL,
                    slot_time TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    source_label TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    board_count INTEGER NOT NULL,
                    payload_hash TEXT NOT NULL,
                    is_final INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (trading_date, slot_time, source_key)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS board_snapshots (
                    trading_date TEXT NOT NULL,
                    slot_time TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    board_code TEXT NOT NULL,
                    board_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    main_flow REAL NOT NULL,
                    price REAL NOT NULL DEFAULT 0,
                    change_pct REAL NOT NULL DEFAULT 0,
                    super_flow REAL NOT NULL DEFAULT 0,
                    large_flow REAL NOT NULL DEFAULT 0,
                    medium_flow REAL NOT NULL DEFAULT 0,
                    small_flow REAL NOT NULL DEFAULT 0,
                    captured_at TEXT NOT NULL,
                    PRIMARY KEY (trading_date, slot_time, source_key, board_code)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_board_flow (
                    trading_date TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    source_label TEXT NOT NULL,
                    board_code TEXT NOT NULL,
                    board_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    main_flow REAL NOT NULL,
                    captured_at TEXT NOT NULL,
                    PRIMARY KEY (trading_date, source_key, board_code)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_snapshots_date_source_time
                ON board_snapshots (trading_date, source_key, slot_time)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_daily_code_date
                ON daily_board_flow (board_code, trading_date)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS saved_payloads (
                    payload_kind TEXT NOT NULL,
                    identity_key TEXT NOT NULL,
                    trading_date TEXT NOT NULL,
                    verified_through TEXT NOT NULL,
                    source_label TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (payload_kind, identity_key, trading_date)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_saved_payloads_lookup
                ON saved_payloads (payload_kind, identity_key, trading_date DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_signals (
                    trading_date TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    market INTEGER NOT NULL,
                    stock_name TEXT NOT NULL,
                    signal_time TEXT NOT NULL,
                    event_type INTEGER NOT NULL,
                    event_label TEXT NOT NULL,
                    one_minute_return REAL NOT NULL,
                    signal_price REAL NOT NULL,
                    industry_code TEXT NOT NULL,
                    industry_name TEXT NOT NULL,
                    sector_slot TEXT NOT NULL,
                    sector_change_pct REAL NOT NULL,
                    sector_main_flow REAL NOT NULL,
                    liquidity_amount REAL NOT NULL,
                    strategy_score REAL NOT NULL,
                    threshold_score REAL NOT NULL,
                    eligible INTEGER NOT NULL,
                    decision_reason TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    PRIMARY KEY (trading_date, stock_code)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_strategy_signals_date_score
                ON strategy_signals (trading_date, eligible, signal_time, strategy_score DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_trades (
                    trading_date TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    allocation REAL NOT NULL DEFAULT 0.05,
                    execution_time TEXT NOT NULL DEFAULT '',
                    execution_price REAL NOT NULL DEFAULT 0,
                    execution_volume REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    rejection_reason TEXT NOT NULL DEFAULT '',
                    current_time TEXT NOT NULL DEFAULT '',
                    current_price REAL NOT NULL DEFAULT 0,
                    close_price REAL NOT NULL DEFAULT 0,
                    next_open_price REAL NOT NULL DEFAULT 0,
                    next_0931_price REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (trading_date, stock_code),
                    FOREIGN KEY (trading_date, stock_code)
                    REFERENCES strategy_signals (trading_date, stock_code)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_strategy_trades_date_status
                ON strategy_trades (trading_date, status)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_replay_progress (
                    trading_date TEXT PRIMARY KEY,
                    processed_through TEXT NOT NULL DEFAULT '',
                    verified_through TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    source_event_count INTEGER NOT NULL DEFAULT 0,
                    unresolved_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_lab_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    effective_date TEXT NOT NULL,
                    effective_time TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    summary TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_lab_account (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    status TEXT NOT NULL,
                    initial_cash REAL NOT NULL,
                    cash REAL NOT NULL,
                    started_at TEXT NOT NULL,
                    active_version_id INTEGER NOT NULL,
                    last_processed_date TEXT NOT NULL DEFAULT '',
                    last_processed_time TEXT NOT NULL DEFAULT '',
                    benchmark_start REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (active_version_id) REFERENCES strategy_lab_versions (id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_lab_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stock_code TEXT NOT NULL,
                    market INTEGER NOT NULL,
                    stock_name TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    entry_date TEXT NOT NULL,
                    entry_time TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    entry_cost REAL NOT NULL,
                    strategy_version_id INTEGER NOT NULL,
                    exit_mode TEXT NOT NULL,
                    take_profit_pct REAL NOT NULL DEFAULT 0,
                    stop_loss_pct REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    last_price REAL NOT NULL DEFAULT 0,
                    last_price_time TEXT NOT NULL DEFAULT '',
                    exit_date TEXT NOT NULL DEFAULT '',
                    exit_time TEXT NOT NULL DEFAULT '',
                    exit_price REAL NOT NULL DEFAULT 0,
                    exit_cost REAL NOT NULL DEFAULT 0,
                    realized_pnl REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (strategy_version_id) REFERENCES strategy_lab_versions (id)
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_lab_positions_open_code
                ON strategy_lab_positions (stock_code)
                WHERE status = 'open'
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_strategy_lab_positions_status_entry
                ON strategy_lab_positions (status, entry_date, entry_time)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_lab_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    trading_date TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    stock_code TEXT NOT NULL DEFAULT '',
                    stock_name TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    price REAL NOT NULL DEFAULT 0,
                    quantity INTEGER NOT NULL DEFAULT 0,
                    amount REAL NOT NULL DEFAULT 0,
                    strategy_version_id INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_strategy_lab_events_date_time
                ON strategy_lab_events (trading_date DESC, event_time DESC, id DESC)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_lab_equity (
                    trading_date TEXT NOT NULL,
                    point_time TEXT NOT NULL,
                    portfolio_value REAL NOT NULL,
                    cash REAL NOT NULL,
                    market_value REAL NOT NULL,
                    benchmark_value REAL NOT NULL DEFAULT 0,
                    return_pct REAL NOT NULL,
                    benchmark_return_pct REAL NOT NULL DEFAULT 0,
                    source TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (trading_date, point_time)
                )
                """
            )
            # Additive migration for users opening an existing database from a
            # previous app version. These reference-index fields are nullable
            # by using zero as "not observed", so no historical value is
            # invented during migration.
            trade_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(strategy_trades)")}
            for column in (
                "index_entry_price", "index_current_price", "index_close_price",
                "index_next_open_price", "index_next_0931_price",
            ):
                if column not in trade_columns:
                    connection.execute(
                        f"ALTER TABLE strategy_trades ADD COLUMN {column} REAL NOT NULL DEFAULT 0"
                    )
            connection.execute("PRAGMA optimize")

    @staticmethod
    def _payload_hash(boards: list[dict[str, Any]]) -> str:
        compact = [
            [str(item.get("code") or ""), float(item.get("mainFlow") or 0)]
            for item in sorted(boards, key=lambda row: str(row.get("code") or ""))
        ]
        raw = json.dumps(compact, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def save_snapshot(
        self,
        trading_date: str,
        slot_time: str,
        source_key: str,
        source_label: str,
        captured_at: str,
        boards: list[dict[str, Any]],
        *,
        is_final: bool = False,
    ) -> int:
        """Insert one verified snapshot without changing earlier observations."""
        valid = [item for item in boards if item.get("code") and item.get("name")]
        if not trading_date or not slot_time or not valid:
            return 0
        payload_hash = self._payload_hash(valid)
        rows = [
            (
                trading_date,
                slot_time,
                source_key,
                str(item.get("code") or ""),
                str(item.get("name") or ""),
                str(item.get("category") or "板块"),
                float(item.get("mainFlow") or 0),
                float(item.get("price") or 0),
                float(item.get("changePct") or 0),
                float(item.get("superFlow") or 0),
                float(item.get("largeFlow") or 0),
                float(item.get("mediumFlow") or 0),
                float(item.get("smallFlow") or 0),
                captured_at,
            )
            for item in valid
        ]
        with self._lock, self._connect() as connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT OR IGNORE INTO board_snapshots (
                    trading_date, slot_time, source_key, board_code, board_name,
                    category, main_flow, price, change_pct, super_flow,
                    large_flow, medium_flow, small_flow, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            inserted = connection.total_changes - before
            connection.execute(
                """
                INSERT INTO captures (
                    trading_date, slot_time, source_key, source_label,
                    captured_at, board_count, payload_hash, is_final
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (trading_date, slot_time, source_key) DO UPDATE SET
                    source_label = excluded.source_label,
                    captured_at = CASE
                        WHEN excluded.board_count > captures.board_count
                        THEN excluded.captured_at ELSE captures.captured_at END,
                    board_count = MAX(captures.board_count, excluded.board_count),
                    payload_hash = CASE
                        WHEN excluded.board_count > captures.board_count
                        THEN excluded.payload_hash ELSE captures.payload_hash END,
                    is_final = MAX(captures.is_final, excluded.is_final)
                """,
                (
                    trading_date,
                    slot_time,
                    source_key,
                    source_label,
                    captured_at,
                    len(valid),
                    payload_hash,
                    1 if is_final else 0,
                ),
            )
            if is_final:
                connection.executemany(
                    """
                    INSERT INTO daily_board_flow (
                        trading_date, source_key, source_label, board_code,
                        board_name, category, main_flow, captured_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (trading_date, source_key, board_code) DO UPDATE SET
                        source_label = excluded.source_label,
                        board_name = excluded.board_name,
                        category = excluded.category,
                        main_flow = excluded.main_flow,
                        captured_at = excluded.captured_at
                    WHERE excluded.captured_at > daily_board_flow.captured_at
                    """,
                    [
                        (
                            trading_date,
                            source_key,
                            source_label,
                            str(item.get("code") or ""),
                            str(item.get("name") or ""),
                            str(item.get("category") or "板块"),
                            float(item.get("mainFlow") or 0),
                            captured_at,
                        )
                        for item in valid
                    ],
                )
        return inserted

    def save_daily_points(
        self,
        source_key: str,
        source_label: str,
        board: dict[str, Any],
        points: list[tuple[str, float]],
        captured_at: str,
    ) -> None:
        if not points:
            return
        with self._lock, self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO daily_board_flow (
                    trading_date, source_key, source_label, board_code,
                    board_name, category, main_flow, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (trading_date, source_key, board_code) DO UPDATE SET
                    source_label = excluded.source_label,
                    board_name = excluded.board_name,
                    category = excluded.category,
                    main_flow = excluded.main_flow,
                    captured_at = excluded.captured_at
                WHERE excluded.captured_at > daily_board_flow.captured_at
                """,
                [
                    (
                        date_label,
                        source_key,
                        source_label,
                        str(board.get("code") or ""),
                        str(board.get("name") or ""),
                        str(board.get("category") or "板块"),
                        float(value),
                        captured_at,
                    )
                    for date_label, value in points
                ],
            )

    def source_summaries(self, trading_date: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT
                    source_key,
                    COUNT(DISTINCT slot_time) AS slot_count,
                    COUNT(DISTINCT board_code) AS board_count,
                    MAX(slot_time) AS verified_through
                FROM board_snapshots
                WHERE trading_date = ?
                GROUP BY source_key
                ORDER BY slot_count DESC, board_count DESC
                """,
                (trading_date,),
            ).fetchall()
        return [dict(row) for row in rows]

    def load_sector_snapshot_at(self, trading_date: str, slot_time: str) -> list[dict[str, Any]]:
        """Load one already-captured snapshot at or before the signal time."""
        with self._lock, self._connect() as connection:
            source = connection.execute(
                """
                SELECT source_key, MAX(slot_time) AS slot_time, COUNT(*) AS board_count
                FROM board_snapshots
                WHERE trading_date = ? AND slot_time <= ?
                GROUP BY source_key
                ORDER BY slot_time DESC, board_count DESC
                LIMIT 1
                """,
                (trading_date, slot_time),
            ).fetchone()
            if source is None:
                return []
            rows = connection.execute(
                """
                SELECT board_code, board_name, category, main_flow, price,
                       change_pct, slot_time, source_key, captured_at
                FROM board_snapshots
                WHERE trading_date = ? AND source_key = ? AND slot_time = ?
                ORDER BY category, board_name
                """,
                (trading_date, source["source_key"], source["slot_time"]),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_strategy_sector_observation(
        self,
        trading_date: str,
        slot_time: str,
        board: dict[str, Any],
        captured_at: str,
    ) -> None:
        """Persist one reconstructed same-day industry observation insert-only."""
        if not trading_date or not slot_time or not board.get("code"):
            return
        self.save_snapshot(
            trading_date,
            slot_time,
            "eastmoney-strategy-replay",
            "东方财富同日行业价格与资金分时",
            captured_at,
            [board],
        )

    def load_intraday_series(
        self,
        trading_date: str,
        preferred_source: str = "",
    ) -> tuple[str, list[dict[str, Any]]]:
        summaries = self.source_summaries(trading_date)
        if not summaries:
            return "", []
        best = summaries[0]
        preferred = next(
            (
                item
                for item in summaries
                if item["source_key"] == preferred_source
            ),
            None,
        )
        selected = (
            preferred
            if preferred
            and int(preferred["slot_count"]) >= 2
            and int(preferred["slot_count"]) >= int(best["slot_count"]) * 0.8
            else best
        )
        source_key = str(selected["source_key"])
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM board_snapshots
                WHERE trading_date = ? AND source_key = ?
                ORDER BY slot_time, board_code
                """,
                (trading_date, source_key),
            ).fetchall()
        by_code: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = by_code.setdefault(
                str(row["board_code"]),
                {
                    "board": {
                        "code": str(row["board_code"]),
                        "name": str(row["board_name"]),
                        "category": str(row["category"]),
                        "price": float(row["price"]),
                        "changePct": float(row["change_pct"]),
                        "mainFlow": float(row["main_flow"]),
                        "superFlow": float(row["super_flow"]),
                        "largeFlow": float(row["large_flow"]),
                        "mediumFlow": float(row["medium_flow"]),
                        "smallFlow": float(row["small_flow"]),
                    },
                    "points": {},
                },
            )
            item["points"][str(row["slot_time"])] = float(row["main_flow"])
            item["board"]["mainFlow"] = float(row["main_flow"])
        return source_key, list(by_code.values())

    def load_daily_points(
        self,
        board_code: str,
        earliest_date: str,
        preferred_source: str = "eastmoney",
    ) -> tuple[str, list[tuple[str, float]]]:
        with self._lock, self._connect() as connection:
            sources = connection.execute(
                """
                SELECT source_key, COUNT(*) AS point_count
                FROM daily_board_flow
                WHERE board_code = ? AND trading_date >= ?
                GROUP BY source_key
                ORDER BY point_count DESC
                """,
                (board_code, earliest_date),
            ).fetchall()
            if not sources:
                return "", []
            source_key = next(
                (str(row["source_key"]) for row in sources if row["source_key"] == preferred_source),
                str(sources[0]["source_key"]),
            )
            rows = connection.execute(
                """
                SELECT trading_date, main_flow
                FROM daily_board_flow
                WHERE board_code = ? AND trading_date >= ? AND source_key = ?
                ORDER BY trading_date
                """,
                (board_code, earliest_date, source_key),
            ).fetchall()
        return source_key, [(str(row["trading_date"]), float(row["main_flow"])) for row in rows]

    def has_capture(self, trading_date: str, slot_time: str) -> bool:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM captures
                WHERE trading_date = ? AND slot_time = ?
                LIMIT 1
                """,
                (trading_date, slot_time),
            ).fetchone()
        return row is not None

    def save_payload(
        self,
        payload_kind: str,
        identity_key: str,
        trading_date: str,
        verified_through: str,
        source_label: str,
        captured_at: str,
        payload: dict[str, Any],
    ) -> None:
        """Keep the newest verified page payload for each trading date.

        Stock pages combine several public endpoints. Saving the completed,
        verified payload as one unit means a later app restart never mixes data
        captured at different times, while still avoiding simulated backfill.
        """
        if not payload_kind or not trading_date or not payload:
            return
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO saved_payloads (
                    payload_kind, identity_key, trading_date, verified_through,
                    source_label, captured_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (payload_kind, identity_key, trading_date) DO UPDATE SET
                    verified_through = excluded.verified_through,
                    source_label = excluded.source_label,
                    captured_at = excluded.captured_at,
                    payload_json = excluded.payload_json
                WHERE excluded.verified_through >= saved_payloads.verified_through
                """,
                (
                    payload_kind,
                    identity_key,
                    trading_date,
                    verified_through,
                    source_label,
                    captured_at,
                    encoded,
                ),
            )

    def load_latest_payload(
        self,
        payload_kind: str,
        identity_key: str = "",
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM saved_payloads
                WHERE payload_kind = ? AND identity_key = ?
                ORDER BY trading_date DESC, verified_through DESC
                LIMIT 1
                """,
                (payload_kind, identity_key),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(str(row["payload_json"]))
        except (TypeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def save_strategy_signal(self, signal: dict[str, Any]) -> bool:
        """Insert the first decision for a stock/day; later refreshes cannot rewrite it."""
        required = ("tradingDate", "code", "name", "signalTime")
        if any(not signal.get(key) for key in required):
            return False
        with self._lock, self._connect() as connection:
            before = connection.total_changes
            connection.execute(
                """
                INSERT OR IGNORE INTO strategy_signals (
                    trading_date, stock_code, market, stock_name, signal_time,
                    event_type, event_label, one_minute_return, signal_price,
                    industry_code, industry_name, sector_slot, sector_change_pct,
                    sector_main_flow, liquidity_amount, strategy_score,
                    threshold_score, eligible, decision_reason, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(signal["tradingDate"]), str(signal["code"]), int(signal.get("market") or 0),
                    str(signal["name"]), str(signal["signalTime"]), int(signal.get("eventType") or 0),
                    str(signal.get("event") or "一分钟快速上涨"), float(signal.get("oneMinuteReturn") or 0),
                    float(signal.get("signalPrice") or 0), str(signal.get("industryCode") or ""),
                    str(signal.get("industryName") or ""), str(signal.get("sectorSlot") or ""),
                    float(signal.get("sectorChangePct") or 0), float(signal.get("sectorMainFlow") or 0),
                    float(signal.get("liquidityAmount") or 0), float(signal.get("score") or 0),
                    float(signal.get("thresholdScore") or 0), 1 if signal.get("eligible") else 0,
                    str(signal.get("decisionReason") or ""), str(signal.get("capturedAt") or ""),
                ),
            )
            return connection.total_changes > before

    def create_strategy_trade(self, trading_date: str, stock_code: str, updated_at: str) -> bool:
        with self._lock, self._connect() as connection:
            before = connection.total_changes
            connection.execute(
                """
                INSERT OR IGNORE INTO strategy_trades (
                    trading_date, stock_code, allocation, status, updated_at
                ) VALUES (?, ?, 0.05, 'pending_execution', ?)
                """,
                (trading_date, stock_code, updated_at),
            )
            inserted = connection.total_changes > before
            if inserted:
                position = connection.execute(
                    "SELECT COUNT(*) AS position FROM strategy_trades WHERE trading_date = ?",
                    (trading_date,),
                ).fetchone()
                connection.execute(
                    "UPDATE strategy_signals SET decision_reason = ? WHERE trading_date = ? AND stock_code = ?",
                    (f"通过实时过滤 · 第{int(position['position'] if position else 0)}个仓位", trading_date, stock_code),
                )
            return inserted

    def update_strategy_signal_reason(self, trading_date: str, stock_code: str, reason: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE strategy_signals SET decision_reason = ? WHERE trading_date = ? AND stock_code = ?",
                (reason, trading_date, stock_code),
            )

    def update_strategy_trade(self, trading_date: str, stock_code: str, **values: Any) -> None:
        allowed = {
            "execution_time": "execution_time", "execution_price": "execution_price",
            "execution_volume": "execution_volume", "status": "status",
            "rejection_reason": "rejection_reason", "current_time": "current_time",
            "current_price": "current_price", "close_price": "close_price",
            "next_open_price": "next_open_price", "next_0931_price": "next_0931_price",
            "index_entry_price": "index_entry_price", "index_current_price": "index_current_price",
            "index_close_price": "index_close_price", "index_next_open_price": "index_next_open_price",
            "index_next_0931_price": "index_next_0931_price",
            "updated_at": "updated_at",
        }
        updates = [(allowed[key], value) for key, value in values.items() if key in allowed]
        if not updates:
            return
        assignments = ", ".join(f"{column} = ?" for column, _ in updates)
        parameters = [value for _, value in updates] + [trading_date, stock_code]
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE strategy_trades SET {assignments} WHERE trading_date = ? AND stock_code = ?",
                parameters,
            )

    def count_strategy_trades(self, trading_date: str) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM strategy_trades WHERE trading_date = ? AND status != 'unfilled'",
                (trading_date,),
            ).fetchone()
        return int(row["count"] if row else 0)

    def load_strategy_rows(self, earliest_date: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT s.*, t.allocation, t.execution_time, t.execution_price,
                       t.execution_volume, t.status, t.rejection_reason,
                       t.current_time, t.current_price, t.close_price,
                       t.next_open_price, t.next_0931_price, t.updated_at,
                       t.index_entry_price, t.index_current_price,
                       t.index_close_price, t.index_next_open_price,
                       t.index_next_0931_price
                FROM strategy_signals s
                LEFT JOIN strategy_trades t
                  ON t.trading_date = s.trading_date AND t.stock_code = s.stock_code
                WHERE s.trading_date >= ?
                ORDER BY s.trading_date DESC, s.signal_time, s.strategy_score DESC
                """,
                (earliest_date,),
            ).fetchall()
        return [dict(row) for row in rows]

    def load_strategy_signals_for_date(self, trading_date: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM strategy_signals
                WHERE trading_date = ?
                ORDER BY signal_time, strategy_score DESC, stock_code
                """,
                (trading_date,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _decode_config(raw: Any) -> dict[str, Any]:
        try:
            value = json.loads(str(raw or "{}"))
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def load_strategy_lab_state(self) -> dict[str, Any]:
        with self._lock, self._connect() as connection:
            account = connection.execute(
                "SELECT * FROM strategy_lab_account WHERE id = 1"
            ).fetchone()
            versions = connection.execute(
                "SELECT * FROM strategy_lab_versions ORDER BY id DESC LIMIT 30"
            ).fetchall()
            positions = connection.execute(
                """
                SELECT * FROM strategy_lab_positions
                ORDER BY CASE WHEN status = 'open' THEN 0 ELSE 1 END,
                         entry_date DESC, entry_time DESC, id DESC
                LIMIT 200
                """
            ).fetchall()
            events = connection.execute(
                """
                SELECT * FROM strategy_lab_events
                ORDER BY trading_date DESC, event_time DESC, id DESC
                LIMIT 300
                """
            ).fetchall()
            equity = connection.execute(
                """
                SELECT * FROM strategy_lab_equity
                ORDER BY trading_date, point_time
                """
            ).fetchall()
        version_rows = [dict(row) for row in versions]
        for version in version_rows:
            version["config"] = self._decode_config(version.pop("config_json", "{}"))
        return {
            "account": dict(account) if account else None,
            "versions": version_rows,
            "positions": [dict(row) for row in positions],
            "events": [dict(row) for row in events],
            "equity": [dict(row) for row in equity],
        }

    def start_or_update_strategy_lab(
        self,
        config: dict[str, Any],
        summary: str,
        initial_cash: float,
        effective_date: str,
        effective_time: str,
        created_at: str,
    ) -> tuple[dict[str, Any], bool]:
        encoded = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._lock, self._connect() as connection:
            account = connection.execute(
                "SELECT * FROM strategy_lab_account WHERE id = 1"
            ).fetchone()
            active_config = ""
            if account:
                active = connection.execute(
                    "SELECT config_json FROM strategy_lab_versions WHERE id = ?",
                    (account["active_version_id"],),
                ).fetchone()
                active_config = str(active["config_json"] if active else "")
            changed = not account or active_config != encoded
            if changed:
                cursor = connection.execute(
                    """
                    INSERT INTO strategy_lab_versions (
                        created_at, effective_date, effective_time, config_json, summary
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (created_at, effective_date, effective_time, encoded, summary),
                )
                version_id = int(cursor.lastrowid)
            else:
                version_id = int(account["active_version_id"])
            if account is None:
                connection.execute(
                    """
                    INSERT INTO strategy_lab_account (
                        id, status, initial_cash, cash, started_at,
                        active_version_id, last_processed_date,
                        last_processed_time, benchmark_start, updated_at
                    ) VALUES (1, 'running', ?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        initial_cash, initial_cash, created_at, version_id,
                        effective_date, effective_time, created_at,
                    ),
                )
                event_type = "strategy_started"
                title = "持续模拟已开始"
            else:
                connection.execute(
                    """
                    UPDATE strategy_lab_account
                    SET status = 'running', active_version_id = ?,
                        last_processed_date = ?, last_processed_time = ?, updated_at = ?
                    WHERE id = 1
                    """,
                    (version_id, effective_date, effective_time, created_at),
                )
                event_type = "strategy_changed" if changed else "strategy_resumed"
                title = "策略规则已更新" if changed else "持续模拟已恢复"
            if changed or (account and str(account["status"]) != "running"):
                connection.execute(
                    """
                    INSERT INTO strategy_lab_events (
                        occurred_at, trading_date, event_time, event_type,
                        title, detail, strategy_version_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (created_at, effective_date, effective_time, event_type, title, summary, version_id),
                )
            updated = connection.execute(
                "SELECT * FROM strategy_lab_account WHERE id = 1"
            ).fetchone()
        return dict(updated), changed

    def set_strategy_lab_status(
        self,
        status: str,
        trading_date: str,
        event_time: str,
        updated_at: str,
    ) -> None:
        if status not in {"running", "paused"}:
            raise ValueError("策略模拟状态不正确")
        with self._lock, self._connect() as connection:
            account = connection.execute(
                "SELECT active_version_id FROM strategy_lab_account WHERE id = 1"
            ).fetchone()
            if account is None:
                return
            connection.execute(
                "UPDATE strategy_lab_account SET status = ?, updated_at = ? WHERE id = 1",
                (status, updated_at),
            )
            connection.execute(
                """
                INSERT INTO strategy_lab_events (
                    occurred_at, trading_date, event_time, event_type,
                    title, detail, strategy_version_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    updated_at, trading_date, event_time,
                    "strategy_paused" if status == "paused" else "strategy_resumed",
                    "持续模拟已暂停" if status == "paused" else "持续模拟已恢复",
                    "暂停期间不产生新买入，已有持仓仍保留。",
                    int(account["active_version_id"]),
                ),
            )

    def update_strategy_lab_progress(
        self,
        trading_date: str,
        processed_time: str,
        updated_at: str,
        benchmark_start: float = 0,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE strategy_lab_account
                SET last_processed_date = ?, last_processed_time = ?,
                    benchmark_start = CASE WHEN benchmark_start <= 0 AND ? > 0 THEN ? ELSE benchmark_start END,
                    updated_at = ?
                WHERE id = 1
                """,
                (trading_date, processed_time, benchmark_start, benchmark_start, updated_at),
            )

    def append_strategy_lab_event(self, event: dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO strategy_lab_events (
                    occurred_at, trading_date, event_time, event_type,
                    stock_code, stock_name, title, detail, price,
                    quantity, amount, strategy_version_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.get("occurred_at") or ""), str(event.get("trading_date") or ""),
                    str(event.get("event_time") or ""), str(event.get("event_type") or "note"),
                    str(event.get("stock_code") or ""), str(event.get("stock_name") or ""),
                    str(event.get("title") or "记录"), str(event.get("detail") or ""),
                    float(event.get("price") or 0), int(event.get("quantity") or 0),
                    float(event.get("amount") or 0), int(event.get("strategy_version_id") or 0),
                ),
            )

    def buy_strategy_lab_position(self, position: dict[str, Any], total_debit: float) -> int | None:
        with self._lock, self._connect() as connection:
            account = connection.execute(
                "SELECT cash, status FROM strategy_lab_account WHERE id = 1"
            ).fetchone()
            if account is None or str(account["status"]) != "running" or float(account["cash"]) + 1e-6 < total_debit:
                return None
            existing = connection.execute(
                "SELECT id FROM strategy_lab_positions WHERE stock_code = ? AND status = 'open'",
                (str(position["stock_code"]),),
            ).fetchone()
            if existing:
                return None
            cursor = connection.execute(
                """
                INSERT INTO strategy_lab_positions (
                    stock_code, market, stock_name, quantity, entry_date,
                    entry_time, entry_price, entry_cost, strategy_version_id,
                    exit_mode, take_profit_pct, stop_loss_pct, status,
                    last_price, last_price_time, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)
                """,
                (
                    str(position["stock_code"]), int(position.get("market") or 0),
                    str(position["stock_name"]), int(position["quantity"]),
                    str(position["entry_date"]), str(position["entry_time"]),
                    float(position["entry_price"]), float(position.get("entry_cost") or 0),
                    int(position["strategy_version_id"]), str(position["exit_mode"]),
                    float(position.get("take_profit_pct") or 0),
                    float(position.get("stop_loss_pct") or 0),
                    float(position["entry_price"]), str(position["entry_time"]),
                    str(position["updated_at"]),
                ),
            )
            connection.execute(
                "UPDATE strategy_lab_account SET cash = cash - ?, updated_at = ? WHERE id = 1",
                (total_debit, str(position["updated_at"])),
            )
            return int(cursor.lastrowid)

    def mark_strategy_lab_position(
        self,
        position_id: int,
        price: float,
        price_time: str,
        updated_at: str,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE strategy_lab_positions
                SET last_price = ?, last_price_time = ?, updated_at = ?
                WHERE id = ? AND status = 'open'
                """,
                (price, price_time, updated_at, position_id),
            )

    def sell_strategy_lab_position(
        self,
        position_id: int,
        exit_date: str,
        exit_time: str,
        exit_price: float,
        exit_cost: float,
        updated_at: str,
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM strategy_lab_positions WHERE id = ? AND status = 'open'",
                (position_id,),
            ).fetchone()
            if row is None:
                return None
            gross = int(row["quantity"]) * exit_price
            proceeds = gross - exit_cost
            realized = proceeds - (int(row["quantity"]) * float(row["entry_price"]) + float(row["entry_cost"]))
            connection.execute(
                """
                UPDATE strategy_lab_positions
                SET status = 'closed', exit_date = ?, exit_time = ?,
                    exit_price = ?, exit_cost = ?, realized_pnl = ?,
                    last_price = ?, last_price_time = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    exit_date, exit_time, exit_price, exit_cost, realized,
                    exit_price, exit_time, updated_at, position_id,
                ),
            )
            connection.execute(
                "UPDATE strategy_lab_account SET cash = cash + ?, updated_at = ? WHERE id = 1",
                (proceeds, updated_at),
            )
            result = dict(row)
            result.update({"exit_price": exit_price, "exit_cost": exit_cost, "realized_pnl": realized, "proceeds": proceeds})
            return result

    def save_strategy_lab_equity(self, point: dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO strategy_lab_equity (
                    trading_date, point_time, portfolio_value, cash,
                    market_value, benchmark_value, return_pct,
                    benchmark_return_pct, source, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (trading_date, point_time) DO UPDATE SET
                    portfolio_value = excluded.portfolio_value,
                    cash = excluded.cash,
                    market_value = excluded.market_value,
                    benchmark_value = excluded.benchmark_value,
                    return_pct = excluded.return_pct,
                    benchmark_return_pct = excluded.benchmark_return_pct,
                    source = excluded.source,
                    recorded_at = excluded.recorded_at
                """,
                (
                    str(point["trading_date"]), str(point["point_time"]),
                    float(point["portfolio_value"]), float(point["cash"]),
                    float(point["market_value"]), float(point.get("benchmark_value") or 0),
                    float(point["return_pct"]), float(point.get("benchmark_return_pct") or 0),
                    str(point.get("source") or "真实行情估值"), str(point["recorded_at"]),
                ),
            )

    def load_recent_strategy_scores(self, before_date: str, days: int = 30) -> list[float]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT strategy_score
                FROM strategy_signals
                WHERE trading_date < ?
                  AND one_minute_return >= 0.8
                  AND sector_change_pct > 0
                  AND sector_main_flow > 0
                  AND liquidity_amount >= 50000000
                ORDER BY trading_date DESC, signal_time DESC
                LIMIT ?
                """,
                (before_date, max(20, days * 30)),
            ).fetchall()
        return [float(row["strategy_score"]) for row in rows]

    def load_strategy_replay_progress(self, trading_date: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM strategy_replay_progress WHERE trading_date = ?",
                (trading_date,),
            ).fetchone()
        return dict(row) if row else None

    def save_strategy_replay_progress(
        self,
        trading_date: str,
        processed_through: str,
        verified_through: str,
        status: str,
        source_event_count: int,
        unresolved_count: int,
        updated_at: str,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO strategy_replay_progress (
                    trading_date, processed_through, verified_through, status,
                    source_event_count, unresolved_count, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (trading_date) DO UPDATE SET
                    processed_through = excluded.processed_through,
                    verified_through = excluded.verified_through,
                    status = excluded.status,
                    source_event_count = excluded.source_event_count,
                    unresolved_count = excluded.unresolved_count,
                    updated_at = excluded.updated_at
                WHERE excluded.verified_through >= strategy_replay_progress.verified_through
                   OR excluded.processed_through >= strategy_replay_progress.processed_through
                """,
                (
                    trading_date, processed_through, verified_through, status,
                    max(0, source_event_count), max(0, unresolved_count), updated_at,
                ),
            )

    def prune_strategy_history(self, keep_trading_days: int = 30) -> None:
        """Retain the newest N distinct trading days, not N calendar days."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT trading_date FROM strategy_signals ORDER BY trading_date DESC"
            ).fetchall()
            dates = [str(row["trading_date"]) for row in rows]
            if len(dates) <= keep_trading_days:
                return
            cutoff = dates[keep_trading_days - 1]
            connection.execute("DELETE FROM strategy_trades WHERE trading_date < ?", (cutoff,))
            connection.execute("DELETE FROM strategy_signals WHERE trading_date < ?", (cutoff,))
            connection.execute("DELETE FROM strategy_replay_progress WHERE trading_date < ?", (cutoff,))
