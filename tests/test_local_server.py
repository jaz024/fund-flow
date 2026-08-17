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
        self.assertEqual(server.minute_number("99:99"), -1)

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

    def test_stock_filter_excludes_st_but_keeps_all_mainland_markets(self) -> None:
        self.assertFalse(server.is_allowed_stock("600001", "*ST测试"))
        self.assertFalse(server.is_allowed_stock("000001", "ST 测试"))
        self.assertTrue(server.is_allowed_stock("688001", "科创测试"))
        self.assertTrue(server.is_allowed_stock("920001", "北交测试"))
        self.assertFalse(server.is_allowed_stock("900904", "神奇B股"))
        self.assertFalse(server.is_allowed_stock("200001", "深B测试"))
        self.assertEqual(server.stock_secid("688001"), "1.688001")
        self.assertEqual(server.stock_secid("920001"), "0.920001")

    def test_one_minute_speed_uses_adjacent_verified_prices(self) -> None:
        candidate = {
            "code": "000001", "market": 0, "name": "平安银行", "price": 10,
            "changePct": 0, "turnover": 1, "preClose": 10, "sourceTimestamp": 0,
        }
        trend = {
            "points": [
                {"time": "11:18", "price": 10.00},
                {"time": "11:19", "price": 10.10},
                {"time": "11:20", "price": 10.20},
                {"time": "11:21", "price": 99.00},
            ]
        }
        with mock.patch.object(server, "fetch_stock_trends", return_value=trend):
            item = server.exact_one_minute_item(candidate, "11:20")

        self.assertIsNotNone(item)
        self.assertAlmostEqual(item["speed1m"], (10.20 / 10.10 - 1) * 100)
        self.assertEqual(item["asOf"], "11:20")

    def test_strategy_event_feed_is_not_limited_by_chart_density(self) -> None:
        rows = [
            {"c": f"600{index:03d}", "n": f"股票{index}", "t": 8201, "tm": 100000 + index, "i": "10,0.01", "m": 1}
            for index in range(12)
        ]
        response = {"data": {"tc": len(rows), "allstock": rows}}
        with mock.patch.object(server, "fetch_json", return_value=response):
            chart_events = server.fetch_stock_events("2026-08-13", "15:00")
            strategy_events = server.fetch_stock_events("2026-08-13", "15:00", chart_density_limit=False)
        self.assertEqual(len(chart_events), 4)
        self.assertEqual(len(strategy_events), 12)

    def test_quote_cross_check_keeps_real_order_book(self) -> None:
        eastmoney = {
            "code": "000001", "market": 0, "name": "平安银行", "price": 11.25,
            "changePct": -0.09, "high": 11.29, "low": 11.20, "open": 11.26,
            "preClose": 11.26, "volume": 1, "amount": 2, "turnover": 1.2,
            "pe": 5, "pb": 0.5, "marketCap": 3, "floatMarketCap": 3,
            "bidPrice": 0, "bidVolume": 0, "askPrice": 0, "askVolume": 0,
            "bidLevels": [], "askLevels": [], "sourceTime": "", "source": server.STOCK_SOURCE_NAME,
        }
        sina = {
            **eastmoney, "source": server.SINA_STOCK_SOURCE, "sourceTime": "2026-08-12 15:00:00",
            "bidPrice": 11.25, "bidVolume": 1000, "askPrice": 11.26, "askVolume": 900,
            "bidLevels": [{"level": 1, "price": 11.25, "volume": 1000}],
            "askLevels": [{"level": 1, "price": 11.26, "volume": 900}],
        }
        with (
            mock.patch.object(server, "fetch_eastmoney_stock_quote", return_value=eastmoney),
            mock.patch.object(server, "fetch_sina_stock_quote", return_value=sina),
        ):
            quote = server.fetch_stock_quote("000001", 0)
        self.assertEqual(quote["bidPrice"], 11.25)
        self.assertEqual(quote["askPrice"], 11.26)
        self.assertEqual(len(quote["verifiedBy"]), 2)

    def test_ffmpeg_finder_accepts_explicit_real_file(self) -> None:
        binary = self.root / "ffmpeg-test"
        binary.write_bytes(b"not executed")
        with mock.patch.dict(os.environ, {"FUND_FLOW_FFMPEG": str(binary)}):
            self.assertEqual(server.find_ffmpeg(), str(binary))

    def test_video_capability_fails_before_recording_without_ffmpeg(self) -> None:
        with mock.patch.object(server, "find_ffmpeg", return_value=None):
            result = server.video_capability()
        self.assertFalse(result["ok"])
        self.assertFalse(result["ffmpeg"])

    def test_adaptive_threshold_uses_only_prior_dates(self) -> None:
        server.STORE.save_strategy_signal({
            "tradingDate": "2026-08-11", "code": "000001", "market": 0,
            "name": "历史股票", "signalTime": "10:00", "eventType": 8201,
            "oneMinuteReturn": 1.2, "signalPrice": 10, "industryCode": "BK1",
            "industryName": "银行", "sectorSlot": "10:00", "sectorChangePct": 1,
            "sectorMainFlow": 100_000_000, "liquidityAmount": 100_000_000,
            "score": 88, "thresholdScore": 55, "eligible": True,
            "decisionReason": "通过", "capturedAt": "2026-08-11T10:00:00",
        })
        server.STORE.save_strategy_signal({
            "tradingDate": "2026-08-12", "code": "000002", "market": 0,
            "name": "今日股票", "signalTime": "10:00", "eventType": 8201,
            "oneMinuteReturn": 1.2, "signalPrice": 10, "industryCode": "BK1",
            "industryName": "银行", "sectorSlot": "10:00", "sectorChangePct": 1,
            "sectorMainFlow": 100_000_000, "liquidityAmount": 100_000_000,
            "score": 99, "thresholdScore": 55, "eligible": True,
            "decisionReason": "通过", "capturedAt": "2026-08-12T10:00:00",
        })
        scores = server.STORE.load_recent_strategy_scores("2026-08-12")
        self.assertEqual(scores, [88.0])

    def test_strategy_signal_is_insert_only(self) -> None:
        signal = {
            "tradingDate": "2026-08-12", "code": "600001", "market": 1,
            "name": "测试股票", "signalTime": "10:05", "eventType": 8201,
            "oneMinuteReturn": 1.0, "signalPrice": 10, "industryCode": "BK1",
            "industryName": "测试行业", "sectorSlot": "10:05", "sectorChangePct": 1,
            "sectorMainFlow": 100, "liquidityAmount": 200, "score": 60,
            "thresholdScore": 55, "eligible": True, "decisionReason": "首次",
            "capturedAt": "2026-08-12T10:05:00",
        }
        self.assertTrue(server.STORE.save_strategy_signal(signal))
        self.assertFalse(server.STORE.save_strategy_signal({**signal, "score": 99, "decisionReason": "未来改写"}))
        row = server.STORE.load_strategy_rows("2026-08-12")[0]
        self.assertEqual(row["strategy_score"], 60)
        self.assertEqual(row["decision_reason"], "首次")

    def test_trade_return_separates_entry_and_realized_costs(self) -> None:
        entry_only = server.trade_return(10, 11, after_cost=True, realized=False)
        realized = server.trade_return(10, 11, after_cost=True, realized=True)
        self.assertIsNotNone(entry_only)
        self.assertIsNotNone(realized)
        self.assertGreater(entry_only, realized)

    def test_sector_snapshot_never_reads_after_signal_time(self) -> None:
        server.STORE.save_snapshot("2026-08-12", "10:00", "source", "source", "now", [make_board(1, True)])
        server.STORE.save_snapshot("2026-08-12", "10:05", "source", "source", "later", [make_board(2, True)])
        rows = server.STORE.load_sector_snapshot_at("2026-08-12", "10:02")
        self.assertEqual({row["slot_time"] for row in rows}, {"10:00"})

    def test_strategy_does_not_backfill_a_previous_trading_day(self) -> None:
        market = {
            "date": "2026-08-12", "verifiedThrough": "15:00",
            "events": [{"code": "000001", "market": 0, "name": "平安银行", "time": "10:00", "direction": 1, "eventType": 8201}],
        }
        with mock.patch.object(server, "fetch_stock_trends", side_effect=AssertionError("historical signal must not be crawled")):
            server.capture_strategy_signals(market)
        self.assertEqual(server.STORE.load_strategy_rows("2026-08-12"), [])

    def test_same_day_strategy_replay_resumes_after_saved_cursor(self) -> None:
        current_date = server.now_cn().date().isoformat()
        server.STORE.save_strategy_replay_progress(
            current_date, "10:00", "10:00", "complete", 1, 0,
            f"{current_date}T10:01:00",
        )
        market = {
            "date": current_date, "verifiedThrough": "10:05", "index": {"points": []},
            "events": [
                {"code": "000001", "market": 0, "name": "过期", "time": "09:55", "direction": 1, "eventType": 8201, "severity": 9},
                {"code": "000002", "market": 0, "name": "新增", "time": "10:05", "direction": 1, "eventType": 8201, "severity": 10},
            ],
        }
        trend = {
            "preClose": 10,
            "points": [
                {"date": current_date, "time": "10:04", "price": 10, "amount": 40_000_000, "volume": 100},
                {"date": current_date, "time": "10:05", "price": 10.1, "amount": 20_000_000, "volume": 100},
            ],
        }
        sector = {
            "board_code": "BK1", "board_name": "银行", "category": "行业", "slot_time": "10:05",
            "change_pct": 1, "main_flow": 100_000_000, "price": 100, "source_key": "saved", "captured_at": "now",
        }
        with (
            mock.patch.object(server, "update_existing_strategy_trades"),
            mock.patch.object(server, "fetch_stock_trends", return_value=trend) as trends,
            mock.patch.object(server, "stock_primary_industry", return_value={"name": "银行", "source": "real"}),
            mock.patch.object(server.STORE, "load_sector_snapshot_at", return_value=[sector]),
        ):
            result = server.capture_strategy_signals(market)
        self.assertEqual(trends.call_count, 1)
        self.assertEqual(result["processedThrough"], "10:05")
        rows = server.STORE.load_strategy_rows(current_date)
        self.assertEqual([row["stock_code"] for row in rows], ["000002"])

    def test_market_page_falls_back_only_to_persisted_real_payload(self) -> None:
        payload = {
            "date": "2026-08-11", "verifiedThrough": "11:20", "isDemo": False,
            "isStale": False, "warning": "", "events": [],
        }
        server.STORE.save_payload(
            "stock-market", "", "2026-08-11", "11:20", server.STOCK_SOURCE_NAME,
            "2026-08-11T11:21:00", payload,
        )
        with mock.patch.object(server, "fetch_broad_market_trend", side_effect=server.DataSourceError("offline")):
            restored = server.build_stock_market(force=True)

        self.assertTrue(restored["isStale"])
        self.assertFalse(restored["isDemo"])
        self.assertEqual(restored["verifiedThrough"], "11:20")
        self.assertNotIn("随机", str(restored))

    def test_strategy_lab_preview_uses_only_points_through_verified_time(self) -> None:
        trading_date = "2026-08-13"
        server.STORE.save_strategy_signal({
            "tradingDate": trading_date, "code": "000001", "market": 0,
            "name": "平安银行", "signalTime": "10:00", "eventType": 8201,
            "oneMinuteReturn": 1.2, "signalPrice": 10, "industryCode": "BK1",
            "industryName": "银行", "sectorSlot": "10:00", "sectorChangePct": 1,
            "sectorMainFlow": 100_000_000, "liquidityAmount": 100_000_000,
            "score": 80, "thresholdScore": 55, "eligible": True,
            "decisionReason": "通过", "capturedAt": f"{trading_date}T10:00:01",
        })
        trend = {
            "preClose": 9.9,
            "points": [
                {"date": trading_date, "time": "10:00", "open": 10, "price": 10, "average": 10, "volume": 100},
                {"date": trading_date, "time": "10:01", "open": 10.05, "price": 10.1, "average": 10.08, "volume": 100},
                {"date": trading_date, "time": "10:02", "open": 10.1, "price": 10.2, "average": 10.14, "volume": 100},
                {"date": trading_date, "time": "10:03", "open": 99, "price": 99, "average": 99, "volume": 100},
            ],
        }
        market = {
            "date": trading_date, "verifiedThrough": "10:02",
            "index": {"points": [
                {"date": trading_date, "time": "09:30", "value": 4000},
                {"date": trading_date, "time": "10:00", "value": 4004},
                {"date": trading_date, "time": "10:02", "value": 4008},
            ]},
        }
        config = server.validate_strategy_lab_config({})
        with mock.patch.object(server, "fetch_verified_stock_trends", return_value=trend):
            preview = server.build_strategy_lab_preview(config, market)

        self.assertEqual(preview["tradesFilled"], 1)
        self.assertEqual(preview["trades"][0]["currentPrice"], 10.2)
        self.assertEqual(preview["trades"][0]["executionTime"], "10:01")
        self.assertEqual(preview["trades"][0]["quantity"] % 100, 0)
        self.assertIsNone(server.STORE.load_strategy_lab_state()["account"])
        self.assertLess(max(point["portfolioValue"] for point in preview["equity"]), 101_000)

    def test_strategy_lab_exit_enforces_t_plus_one(self) -> None:
        position = {
            "entry_date": "2026-08-13", "entry_price": 10, "exit_mode": "next_open",
            "take_profit_pct": 3, "stop_loss_pct": 1.5,
        }
        same_day = [{"date": "2026-08-13", "time": "15:00", "price": 11}]
        next_day = [{"date": "2026-08-14", "time": "09:30", "price": 10.5}]
        self.assertEqual(server.strategy_lab_exit_point(position, same_day, "15:00"), (None, ""))
        point, reason = server.strategy_lab_exit_point(position, next_day, "09:30")
        self.assertEqual(point, next_day[0])
        self.assertEqual(reason, "次一交易日开盘")
        self.assertEqual(
            server.strategy_lab_exit_point(position, next_day, "09:30", is_next_trading_day=False),
            (None, ""),
        )

    def test_strategy_lab_quantity_obeys_each_exchange_order_unit(self) -> None:
        self.assertEqual(server.strategy_lab_order_quantity("000001", 1_059, 1_059, 10), 100)
        self.assertEqual(server.strategy_lab_order_quantity("688001", 2_059, 2_059, 10), 205)
        self.assertEqual(server.strategy_lab_order_quantity("920001", 1_059, 1_059, 10), 105)
        self.assertEqual(server.strategy_lab_order_quantity("688001", 1_999, 1_999, 10), 0)

    def test_continuous_strategy_retries_signal_until_execution_minute_exists(self) -> None:
        trading_date = "2026-08-13"
        config = server.validate_strategy_lab_config({})
        account, _ = server.STORE.start_or_update_strategy_lab(
            config, server.strategy_lab_summary(config), config["initialCapital"],
            trading_date, "09:29", f"{trading_date}T09:29:01",
        )
        server.STORE.save_strategy_signal({
            "tradingDate": trading_date, "code": "000001", "market": 0,
            "name": "平安银行", "signalTime": "10:00", "eventType": 8201,
            "oneMinuteReturn": 1.2, "signalPrice": 10, "industryCode": "BK1",
            "industryName": "银行", "sectorSlot": "10:00", "sectorChangePct": 1,
            "sectorMainFlow": 100_000_000, "liquidityAmount": 100_000_000,
            "score": 80, "thresholdScore": 55, "eligible": True,
            "decisionReason": "通过", "capturedAt": f"{trading_date}T10:00:01",
        })
        base_point = {"date": trading_date, "time": "10:00", "open": 10, "price": 10, "average": 10, "volume": 100}
        market = {
            "date": trading_date, "verifiedThrough": "10:00",
            "index": {"points": [{"date": trading_date, "time": "10:00", "value": 4000}]},
        }
        with mock.patch.object(server, "strategy_lab_prefetch_trends", return_value={"000001": {"preClose": 9.9, "points": [base_point]}}):
            server.process_continuous_strategy_lab(market)
        waiting = server.STORE.load_strategy_lab_state()
        self.assertEqual(waiting["account"]["last_processed_time"], "09:59")
        self.assertEqual(waiting["positions"], [])

        next_point = {"date": trading_date, "time": "10:01", "open": 10.05, "price": 10.1, "average": 10.08, "volume": 100}
        market["verifiedThrough"] = "10:01"
        market["index"]["points"].append({"date": trading_date, "time": "10:01", "value": 4001})
        with mock.patch.object(server, "strategy_lab_prefetch_trends", return_value={"000001": {"preClose": 9.9, "points": [base_point, next_point]}}):
            server.process_continuous_strategy_lab(market)
        completed = server.STORE.load_strategy_lab_state()
        self.assertEqual(completed["account"]["last_processed_time"], "10:01")
        self.assertEqual(completed["positions"][0]["entry_time"], "10:01")
        self.assertEqual(completed["positions"][0]["quantity"] % 100, 0)
        self.assertEqual(completed["positions"][0]["strategy_version_id"], int(account["active_version_id"]))


if __name__ == "__main__":
    unittest.main()
