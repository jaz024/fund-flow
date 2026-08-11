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
from pathlib import Path
from typing import Any


class MarketStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

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
