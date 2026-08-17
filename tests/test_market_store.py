import tempfile
import unittest
import sqlite3
from pathlib import Path

from market_store import MarketStore


def board(value: float) -> dict:
    return {
        "code": "BK0001",
        "name": "测试板块",
        "category": "行业",
        "mainFlow": value,
        "price": 100,
        "changePct": 1,
    }


class MarketStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = MarketStore(Path(self.temp_dir.name) / "market.sqlite3")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_intraday_observation_is_insert_only(self) -> None:
        self.store.save_snapshot(
            "2026-08-11",
            "10:00",
            "eastmoney",
            "东方财富",
            "2026-08-11T10:00:02",
            [board(1_200_000_000)],
        )
        self.store.save_snapshot(
            "2026-08-11",
            "10:00",
            "eastmoney",
            "东方财富",
            "2026-08-11T14:00:02",
            [board(9_900_000_000)],
        )

        source, series = self.store.load_intraday_series("2026-08-11", "eastmoney")

        self.assertEqual(source, "eastmoney")
        self.assertEqual(series[0]["points"]["10:00"], 1_200_000_000)

    def test_sources_are_never_blended(self) -> None:
        for slot, value in (("09:35", 1), ("09:40", 2), ("09:45", 3)):
            self.store.save_snapshot(
                "2026-08-11", slot, "eastmoney", "东方财富", f"2026-08-11T{slot}:00", [board(value)]
            )
        self.store.save_snapshot(
            "2026-08-11",
            "09:35",
            "ths",
            "同花顺",
            "2026-08-11T09:35:00",
            [board(999)],
        )

        source, series = self.store.load_intraday_series("2026-08-11", "eastmoney")

        self.assertEqual(source, "eastmoney")
        self.assertEqual(series[0]["points"], {"09:35": 1.0, "09:40": 2.0, "09:45": 3.0})

    def test_stock_page_payload_restores_latest_verified_state(self) -> None:
        early = {"date": "2026-08-11", "verifiedThrough": "10:30", "events": [1]}
        later = {"date": "2026-08-11", "verifiedThrough": "11:20", "events": [1, 2]}
        self.store.save_payload(
            "stock-market", "", "2026-08-11", "10:30", "公开行情", "2026-08-11T10:31:00", early
        )
        self.store.save_payload(
            "stock-market", "", "2026-08-11", "11:20", "公开行情", "2026-08-11T11:21:00", later
        )

        self.assertEqual(self.store.load_latest_payload("stock-market", ""), later)

    def test_strategy_schema_adds_reference_index_fields_to_existing_database(self) -> None:
        database = Path(self.temp_dir.name) / "old.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute(
                """
                CREATE TABLE strategy_trades (
                    trading_date TEXT NOT NULL, stock_code TEXT NOT NULL,
                    allocation REAL NOT NULL DEFAULT 0.05,
                    execution_time TEXT NOT NULL DEFAULT '', execution_price REAL NOT NULL DEFAULT 0,
                    execution_volume REAL NOT NULL DEFAULT 0, status TEXT NOT NULL,
                    rejection_reason TEXT NOT NULL DEFAULT '', current_time TEXT NOT NULL DEFAULT '',
                    current_price REAL NOT NULL DEFAULT 0, close_price REAL NOT NULL DEFAULT 0,
                    next_open_price REAL NOT NULL DEFAULT 0, next_0931_price REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL, PRIMARY KEY (trading_date, stock_code)
                )
                """
            )
        MarketStore(database)
        with sqlite3.connect(database) as connection:
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(strategy_trades)")}
        self.assertIn("index_entry_price", columns)
        self.assertIn("index_next_0931_price", columns)

    def test_strategy_replay_progress_survives_restart(self) -> None:
        self.store.save_strategy_replay_progress(
            "2026-08-13", "11:20", "11:20", "complete", 9, 0, "2026-08-13T11:21:00"
        )
        reopened = MarketStore(self.store.database_path)
        progress = reopened.load_strategy_replay_progress("2026-08-13")
        self.assertIsNotNone(progress)
        self.assertEqual(progress["processed_through"], "11:20")
        self.assertEqual(progress["source_event_count"], 9)

    def test_strategy_lab_account_positions_and_versions_survive_restart(self) -> None:
        config = {"name": "测试策略", "oneMinuteRise": 0.8, "maxPositions": 20}
        account, changed = self.store.start_or_update_strategy_lab(
            config, "测试规则", 100_000, "2026-08-13", "10:00", "2026-08-13T10:00:01"
        )
        self.assertTrue(changed)
        version_id = int(account["active_version_id"])
        position_id = self.store.buy_strategy_lab_position(
            {
                "stock_code": "000001", "market": 0, "stock_name": "平安银行",
                "quantity": 100, "entry_date": "2026-08-13", "entry_time": "10:01",
                "entry_price": 10, "entry_cost": 0.30, "strategy_version_id": version_id,
                "exit_mode": "next_open", "take_profit_pct": 0, "stop_loss_pct": 0,
                "updated_at": "2026-08-13T10:01:01",
            },
            1_000.30,
        )
        self.assertIsNotNone(position_id)
        self.store.mark_strategy_lab_position(int(position_id), 10.5, "15:00", "2026-08-13T15:00:01")

        reopened = MarketStore(self.store.database_path)
        state = reopened.load_strategy_lab_state()
        self.assertAlmostEqual(float(state["account"]["cash"]), 98_999.70)
        self.assertEqual(state["positions"][0]["last_price"], 10.5)
        self.assertEqual(state["versions"][0]["config"], config)

        sold = reopened.sell_strategy_lab_position(
            int(position_id), "2026-08-14", "09:30", 10.4, 0.40, "2026-08-14T09:30:01"
        )
        self.assertIsNotNone(sold)
        final = reopened.load_strategy_lab_state()
        self.assertEqual(final["positions"][0]["status"], "closed")
        self.assertAlmostEqual(float(final["account"]["cash"]), 100_039.30)

        unchanged, changed_again = reopened.start_or_update_strategy_lab(
            config, "测试规则", 999_999, "2026-08-14", "10:00", "2026-08-14T10:00:01"
        )
        self.assertFalse(changed_again)
        self.assertEqual(int(unchanged["active_version_id"]), version_id)
        self.assertEqual(len(reopened.load_strategy_lab_state()["versions"]), 1)


if __name__ == "__main__":
    unittest.main()
