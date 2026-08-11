import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
