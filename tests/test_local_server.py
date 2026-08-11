import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


_IMPORT_TEMP = tempfile.TemporaryDirectory()
os.environ["FUND_FLOW_DB_PATH"] = str(Path(_IMPORT_TEMP.name) / "import.sqlite3")

import local_server as server  # noqa: E402
from market_store import MarketStore  # noqa: E402


def make_board(index: int, positive: bool) -> dict:
    sign = 1 if positive else -1
    return {
        "code": f"BK{index:04d}",
        "name": f"板块{index}",
        "category": "行业" if index % 2 else "概念",
        "mainFlow": sign * (index + 1) * 100_000_000,
        "price": 1000 + index,
        "changePct": sign * 0.5,
    }


def make_overview() -> dict:
    top_in = [make_board(index, True) for index in range(4)]
    top_out = [make_board(index + 4, False) for index in range(4)]
    return {
        "date": "2026-08-11",
        "updatedAt": "2026-08-11T09:45:00",
        "source": server.SOURCE_NAME,
        "isDemo": False,
        "warning": "",
        "indexes": [],
        "boards": top_in + top_out,
        "topIn": top_in,
        "topOut": top_out,
    }


class LocalServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.root = root
        self.original_store = server.STORE
        self.original_cache = server.CACHE_DIR
        server.STORE = MarketStore(root / "market.sqlite3")
        server.CACHE_DIR = root / "cache"
        server.CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        server.STORE = self.original_store
        server.CACHE_DIR = self.original_cache
        self.temp_dir.cleanup()

    def test_market_slot_uses_completed_real_interval(self) -> None:
        self.assertEqual(server.market_slot("2026-08-11T09:34:59", "2026-08-11"), "")
        self.assertEqual(server.market_slot("2026-08-11T10:02:00", "2026-08-11"), "10:00")
        self.assertEqual(server.market_slot("2026-08-11T12:00:00", "2026-08-11"), "11:30")
        self.assertEqual(server.market_slot("2026-08-11T17:00:00", "2026-08-11"), "15:00")

    def test_replay_contains_only_persisted_real_values(self) -> None:
        overview = make_overview()

        def real_series(board: dict) -> dict:
            multiplier = 1 if board["mainFlow"] > 0 else -1
            return {
                "board": board,
                "date": overview["date"],
                "points": {
                    "09:35": multiplier * 100_000_000,
                    "09:40": multiplier * 200_000_000,
                    "09:45": multiplier * 300_000_000,
                },
            }

        with mock.patch.object(server, "fetch_intraday_for_board", side_effect=real_series):
            replay = server.build_replay(force=True, overview=overview)

        self.assertFalse(replay["isDemo"])
        self.assertEqual(replay["schemaVersion"], 4)
        self.assertEqual([frame["time"] for frame in replay["frames"]], ["09:35", "09:40", "09:45"])
        self.assertEqual(replay["verifiedThrough"], "09:45")
        self.assertNotIn("演示", str(replay))
        self.assertNotIn("随机", str(replay))

        original = next(
            item["mainFlow"]
            for item in replay["frames"][0]["boards"]
            if item["code"] == "BK0000"
        )

        def revised_series(board: dict) -> dict:
            result = real_series(board)
            result["points"]["09:35"] = 99_000_000_000
            return result

        with mock.patch.object(server, "fetch_intraday_for_board", side_effect=revised_series):
            refreshed = server.build_replay(force=True, overview=overview)

        frozen = next(
            item["mainFlow"]
            for item in refreshed["frames"][0]["boards"]
            if item["code"] == "BK0000"
        )
        self.assertEqual(frozen, original)

        server.CACHE_DIR = self.root / "fresh-cache"
        server.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(
            server,
            "fetch_intraday_for_board",
            side_effect=AssertionError("saved replay should restore before crawling"),
        ):
            restored = server.build_replay(force=False, overview=overview)
        self.assertEqual(restored["frames"], refreshed["frames"])

    def test_failed_sources_never_create_a_simulated_replay(self) -> None:
        overview = make_overview()
        with mock.patch.object(server, "fetch_intraday_for_board", return_value=None):
            replay = server.build_replay(force=True, overview=overview)

        self.assertFalse(replay["isDemo"])
        self.assertEqual(replay["frames"], [])
        self.assertNotIn("演示", str(replay))
        self.assertNotIn("随机", str(replay))


if __name__ == "__main__":
    unittest.main()
