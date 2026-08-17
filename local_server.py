#!/usr/bin/env python3
"""Local data and video service for the A-share fund-flow dashboard.

The service uses public market pages instead of a paid data key. Market values
are never generated: independent providers are cross-checked where possible,
and every response is labelled with the source that actually supplied it.
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import html
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from market_store import MarketStore


ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "data" / "cache"
OUTPUT_DIR = ROOT / "output"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

HOST = "127.0.0.1"
PORT = int(os.environ.get("FUND_FLOW_API_PORT", "8765"))
SOURCE_NAME = "东方财富公开行情"
EASTMONEY_MINUTE_SOURCE = "东方财富真实分钟资金（延时）"
STOCK_SOURCE_NAME = "东方财富公开个股行情（延时）"
SINA_STOCK_SOURCE = "新浪财经公开个股行情"
TENCENT_STOCK_SOURCE = "腾讯证券公开个股行情"
API_VERSION = 5
THS_SOURCE_NAME = "同花顺公开网页"
EASTMONEY_UT = "b2884a393a59ad64002292a3e90d46a5"
EASTMONEY_CHANGE_UT = "7eea3edcaed734bea9cbfc24409ed989"
EASTMONEY_LIVE_HOST = "https://push2.eastmoney.com"
EASTMONEY_DELAY_HOST = "https://push2delay.eastmoney.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": "https://data.eastmoney.com/bkzj/",
    "Accept": "application/json,text/plain,*/*",
}

_CACHE_LOCK = threading.Lock()
_COLLECTION_LOCK = threading.RLock()
_REPLAY_LOCK = threading.Lock()
_STOCK_MARKET_LOCK = threading.Lock()
_STOCK_DETAIL_LOCK = threading.Lock()
_STRATEGY_LOCK = threading.RLock()
_STRATEGY_LAB_LOCK = threading.RLock()
STORE = MarketStore(Path(os.environ.get("FUND_FLOW_DB_PATH", str(ROOT / "data" / "fund-flow.sqlite3"))))


class DataSourceError(RuntimeError):
    pass


def now_cn() -> dt.datetime:
    return dt.datetime.now()


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def fetch_json(
    base_url: str,
    params: dict[str, Any],
    attempts: int = 3,
    referer: str | None = None,
) -> dict[str, Any]:
    query = urllib.parse.urlencode(params, safe=":,+!")
    url = f"{base_url}?{query}"
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            curl = "/usr/bin/curl" if Path("/usr/bin/curl").exists() else shutil.which("curl")
            if curl:
                completed = subprocess.run(
                    [
                        curl,
                        "--http1.1",
                        "--max-time",
                        "22",
                        "-sS",
                        "-H",
                        f"User-Agent: {USER_AGENT}",
                        "-H",
                        f"Referer: {referer or HEADERS['Referer']}",
                        "-H",
                        f"Accept: {HEADERS['Accept']}",
                        url,
                    ],
                    capture_output=True,
                    timeout=28,
                )
                if completed.returncode != 0:
                    raise DataSourceError(completed.stderr.decode("utf-8", errors="replace").strip())
                raw = completed.stdout.decode("utf-8", errors="replace")
            else:
                request_headers = {**HEADERS, "Referer": referer or HEADERS["Referer"]}
                request = urllib.request.Request(url, headers=request_headers)
                with urllib.request.urlopen(request, timeout=22) as response:
                    raw = response.read().decode("utf-8", errors="replace")
            payload = json.loads(raw)
            if payload.get("rc") not in (None, 0):
                raise DataSourceError(f"数据源返回错误代码 {payload.get('rc')}")
            return payload
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, DataSourceError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.45 * (attempt + 1))
    raise DataSourceError(str(last_error or "数据源暂时不可用"))


def fetch_page(url: str, encoding: str = "utf-8", attempts: int = 3) -> str:
    """Fetch a public HTML page through curl, with retry and explicit decoding."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            curl = "/usr/bin/curl" if Path("/usr/bin/curl").exists() else shutil.which("curl")
            if not curl:
                raise DataSourceError("未找到 curl")
            completed = subprocess.run(
                [
                    curl,
                    "--http1.1",
                    "--max-time",
                    "28",
                    "-sS",
                    "-A",
                    USER_AGENT,
                    "-e",
                    "https://data.10jqka.com.cn/",
                    url,
                ],
                capture_output=True,
                timeout=34,
            )
            if completed.returncode != 0:
                raise DataSourceError(completed.stderr.decode("utf-8", errors="replace").strip())
            if not completed.stdout:
                raise DataSourceError("网页返回空内容")
            return completed.stdout.decode(encoding, errors="ignore")
        except (subprocess.SubprocessError, DataSourceError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.7 * (attempt + 1))
    raise DataSourceError(str(last_error or "网页源暂时不可用"))


def fetch_text_url(
    url: str,
    *,
    encoding: str = "utf-8",
    referer: str = "https://quote.eastmoney.com/",
    attempts: int = 3,
) -> str:
    """Fetch a public text/JSON endpoint without assuming an Eastmoney shape."""
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            curl = "/usr/bin/curl" if Path("/usr/bin/curl").exists() else shutil.which("curl")
            if not curl:
                raise DataSourceError("未找到 curl")
            completed = subprocess.run(
                [
                    curl,
                    "--http1.1",
                    "--max-time",
                    "24",
                    "-sS",
                    "-A",
                    USER_AGENT,
                    "-e",
                    referer,
                    url,
                ],
                capture_output=True,
                timeout=30,
            )
            if completed.returncode != 0:
                raise DataSourceError(completed.stderr.decode("utf-8", errors="replace").strip())
            if not completed.stdout:
                raise DataSourceError("行情源返回空内容")
            return completed.stdout.decode(encoding, errors="ignore")
        except (subprocess.SubprocessError, DataSourceError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.45 * (attempt + 1))
    raise DataSourceError(str(last_error or "行情源暂时不可用"))


def cache_path(name: str) -> Path:
    safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_")
    return CACHE_DIR / f"{safe}.json"


def read_cache(name: str, max_age_seconds: int | None = None) -> Any | None:
    path = cache_path(name)
    if not path.exists():
        return None
    if max_age_seconds is not None and time.time() - path.stat().st_mtime > max_age_seconds:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_cache(name: str, payload: Any) -> None:
    path = cache_path(name)
    temp_path = path.with_suffix(".tmp")
    with _CACHE_LOCK:
        temp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp_path.replace(path)


def to_float(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
        if math.isfinite(number):
            return number
    except (TypeError, ValueError):
        pass
    return fallback


def fetch_eastmoney_json(
    path: str,
    params: dict[str, Any],
    *,
    prefer_delayed: bool = False,
    attempts: int = 2,
) -> dict[str, Any]:
    """Try both official public quote hosts without changing data semantics."""
    hosts = (
        (EASTMONEY_DELAY_HOST, EASTMONEY_LIVE_HOST)
        if prefer_delayed
        else (EASTMONEY_LIVE_HOST, EASTMONEY_DELAY_HOST)
    )
    errors: list[str] = []
    for host in hosts:
        try:
            return fetch_json(
                f"{host}{path}",
                params,
                attempts=attempts,
                referer="https://data.eastmoney.com/bkzj/",
            )
        except Exception as exc:
            errors.append(f"{host}: {exc}")
    raise DataSourceError("；".join(errors) or "东方财富公开行情暂不可用")


def validate_rankings(rows: list[dict[str, Any]], source_label: str) -> list[dict[str, Any]]:
    unique_codes = {str(item.get("code") or "") for item in rows}
    if len(unique_codes) < 60:
        raise DataSourceError(
            f"{source_label}返回的板块列表不完整"
            f"（仅取得 {len(unique_codes)} 个板块）"
        )
    return rows


def board_rank(category: str, order_desc: bool, limit: int = 120) -> list[dict[str, Any]]:
    board_type = "2" if category == "industry" else "3"
    payload = fetch_eastmoney_json(
        "/api/qt/clist/get",
        {
            "pn": 1,
            "pz": limit,
            "po": 1 if order_desc else 0,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f62",
            "fs": f"m:90+t:{board_type}",
            "fields": "f12,f14,f2,f3,f62,f66,f72,f78,f84,f124",
            "ut": EASTMONEY_UT,
        },
    )
    rows = payload.get("data", {}).get("diff") or []
    result: list[dict[str, Any]] = []
    for row in rows:
        code = str(row.get("f12") or "")
        name = str(row.get("f14") or "")
        if not code.startswith("BK") or not name or not is_sector_name(name):
            continue
        result.append(
            {
                "code": code,
                "name": name,
                "category": "行业" if category == "industry" else "概念",
                "price": to_float(row.get("f2")),
                "changePct": to_float(row.get("f3")),
                "mainFlow": to_float(row.get("f62")),
                "superFlow": to_float(row.get("f66")),
                "largeFlow": to_float(row.get("f72")),
                "mediumFlow": to_float(row.get("f78")),
                "smallFlow": to_float(row.get("f84")),
                "sourceTimestamp": int(to_float(row.get("f124"))),
                "dataSource": SOURCE_NAME,
            }
        )
    return result


def clean_cell(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()


def normalize_board_name(value: str) -> str:
    value = html.unescape(value).upper()
    value = re.sub(r"[\s（）()·—_\-/]", "", value)
    value = value.replace("概念", "").replace("行业", "")
    value = value.replace("Ⅱ", "").replace("II", "")
    return value


NON_SECTOR_NAMES = {
    "融资融券", "沪股通", "深股通", "转融券标的", "机构重仓", "基金重仓",
    "社保重仓", "QFII重仓", "证金持股", "MSCI中国", "富时罗素", "标准普尔",
    "AB股", "AH股", "ST股", "百元股", "低价股", "破净股", "破发股",
    "上证50", "上证180", "中证500", "深证100R", "HS300", "创业板综",
}


def is_sector_name(name: str) -> bool:
    normalized = normalize_board_name(name)
    if normalized in {normalize_board_name(item) for item in NON_SECTOR_NAMES}:
        return False
    return not normalized.startswith(("昨日", "2025", "2026", "近期新高", "历史新高"))


def resolve_board_code(name: str, code_map: dict[str, str]) -> str | None:
    normalized = normalize_board_name(name)
    if normalized in code_map:
        return code_map[normalized]
    # Providers sometimes use a Chinese expansion plus an acronym (for example
    # 共封装光学(CPO) versus CPO概念). Prefer the longest unambiguous containment.
    matches = [
        (len(key), code)
        for key, code in code_map.items()
        if len(key) >= 3 and (key in normalized or normalized in key)
    ]
    if not matches:
        return None
    matches.sort(reverse=True)
    return matches[0][1]


def eastmoney_board_directory() -> dict[str, str]:
    cached = read_cache("board-name-map", max_age_seconds=7 * 24 * 3600)
    if cached:
        return {str(key): str(value) for key, value in cached.items()}
    page = fetch_page("https://data.eastmoney.com/zjlx/")
    matches = re.findall(
        r"href=[\"'](?:https?:)?//?[^\"']*?/bkzj/(BK\d+)\.html[^\"']*[\"'][^>]*>(.*?)</a>",
        page,
        flags=re.I | re.S,
    )
    if not matches:
        # Relative links are the normal production form.
        matches = re.findall(
            r"href=[\"'][^\"']*?/bkzj/(BK\d+)\.html[^\"']*[\"'][^>]*>(.*?)</a>",
            page,
            flags=re.I | re.S,
        )
    result: dict[str, str] = {}
    for code, raw_name in matches:
        name = clean_cell(raw_name)
        normalized = normalize_board_name(name)
        if normalized and normalized not in result:
            result[normalized] = code
    if not result:
        raise DataSourceError("未解析到板块代码目录")
    write_cache("board-name-map", result)
    return result


def ths_board_rank(category: str, order_desc: bool) -> list[dict[str, Any]]:
    path = "hyzjl" if category == "industry" else "gnzjl"
    order = "desc" if order_desc else "asc"
    page = fetch_page(f"https://data.10jqka.com.cn/funds/{path}/field/je/order/{order}/page/1/", "gbk")
    code_map: dict[str, str] = {}
    try:
        code_map = eastmoney_board_directory()
    except Exception:
        pass
    result: list[dict[str, Any]] = []
    for raw_row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, flags=re.I | re.S):
        raw_cells = re.findall(r"<td[^>]*>(.*?)</td>", raw_row, flags=re.I | re.S)
        if len(raw_cells) < 7:
            continue
        cells = [clean_cell(cell) for cell in raw_cells]
        name = cells[1]
        if not name or not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", cells[6].replace(",", "")):
            continue
        ths_match = re.search(r"/code/(\d+)/", raw_cells[1])
        if not is_sector_name(name):
            continue
        mapped_code = resolve_board_code(name, code_map)
        code = mapped_code or (f"THS{ths_match.group(1)}" if ths_match else f"THS-{normalize_board_name(name)}")
        net_yi = to_float(cells[6].replace(",", ""))
        inflow_yi = to_float(cells[4].replace(",", ""))
        outflow_yi = to_float(cells[5].replace(",", ""))
        result.append(
            {
                "code": code,
                "name": name,
                "category": "行业" if category == "industry" else "概念",
                "price": to_float(cells[2].replace(",", "")),
                "changePct": to_float(cells[3].replace("%", "")),
                "mainFlow": net_yi * 100_000_000,
                "superFlow": 0.0,
                "largeFlow": inflow_yi * 100_000_000,
                "mediumFlow": -outflow_yi * 100_000_000,
                "smallFlow": 0.0,
                "sourceTimestamp": 0,
                "dataSource": THS_SOURCE_NAME,
            }
        )
    if not result:
        raise DataSourceError("同花顺网页未解析到板块资金数据")
    return result


def fetch_ths_rankings() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    errors: list[Exception] = []
    for category in ("industry", "concept"):
        for order_desc in (True, False):
            try:
                rows.extend(ths_board_rank(category, order_desc))
            except Exception as exc:
                errors.append(exc)
            time.sleep(0.12)
    if errors:
        raise DataSourceError(f"同花顺板块列表抓取不完整：{errors[0]}")
    if not rows:
        raise DataSourceError(str(errors[0]) if errors else "未获取到同花顺板块资金数据")
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["category"], row["name"])
        deduped[key] = row
    result = sorted(deduped.values(), key=lambda item: item["mainFlow"], reverse=True)
    return validate_rankings(result, THS_SOURCE_NAME)


def fetch_all_rankings() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    errors: list[Exception] = []
    # The public quote host occasionally closes simultaneous list requests.
    # Four small sequential requests are more reliable and still finish quickly.
    for category in ("industry", "concept"):
        for order_desc in (True, False):
            try:
                rows.extend(board_rank(category, order_desc))
            except Exception as exc:  # best-effort across the two public lists
                errors.append(exc)
            time.sleep(0.18)
    if errors:
        try:
            return fetch_ths_rankings()
        except Exception as ths_error:
            raise DataSourceError(f"东方财富列表不完整：{errors[0]}；同花顺备用源失败：{ths_error}") from ths_error
    if not rows:
        return fetch_ths_rankings()
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        existing = deduped.get(row["code"])
        if existing is None or abs(row["mainFlow"]) > abs(existing["mainFlow"]):
            deduped[row["code"]] = row
    result = sorted(deduped.values(), key=lambda item: item["mainFlow"], reverse=True)
    return validate_rankings(result, SOURCE_NAME)


def fetch_indexes() -> list[dict[str, Any]]:
    definitions = [("000001", "上证指数"), ("399001", "深证成指"), ("899050", "北证50")]
    try:
        payload = fetch_eastmoney_json(
            "/api/qt/ulist.np/get",
            {
                "fltt": 2,
                "invt": 2,
                "fields": "f12,f14,f2,f3",
                "secids": "1.000001,0.399001,0.899050",
                "ut": EASTMONEY_UT,
            },
        )
        rows = payload.get("data", {}).get("diff") or []
        by_code = {str(row.get("f12")): row for row in rows}
        return [
            {
                "code": code,
                "name": name,
                "price": to_float(by_code.get(code, {}).get("f2")),
                "changePct": to_float(by_code.get(code, {}).get("f3")),
            }
            for code, name in definitions
        ]
    except Exception:
        page = fetch_page(
            "https://hq.sinajs.cn/list=s_sh000001,s_sz399001,s_bj899050",
            "gbk",
        )
        parsed: dict[str, tuple[float, float]] = {}
        for market, code, body in re.findall(r"hq_str_s_(sh|sz|bj)(\d+)=\"([^\"]*)\"", page):
            fields = body.split(",")
            if len(fields) >= 4:
                parsed[code] = (to_float(fields[1]), to_float(fields[3]))
        if len(parsed) < 2:
            raise DataSourceError("未获取到沪深京指数")
        return [
            {
                "code": code,
                "name": name,
                "price": parsed.get(code, (0.0, 0.0))[0],
                "changePct": parsed.get(code, (0.0, 0.0))[1],
            }
            for code, name in definitions
        ]


def source_key(source_label: str) -> str:
    if "同花顺" in source_label:
        return "ths"
    if "东方财富" in source_label:
        return "eastmoney"
    if "新浪" in source_label:
        return "sina"
    return normalize_board_name(source_label).lower() or "unknown"


def market_slot(updated_at: str, trading_date: str) -> str:
    """Map a real source timestamp to the latest completed five-minute slot."""
    try:
        observed = dt.datetime.fromisoformat(updated_at)
    except (TypeError, ValueError):
        observed = now_cn()
    try:
        data_date = dt.date.fromisoformat(trading_date)
    except (TypeError, ValueError):
        data_date = observed.date()
    if data_date < observed.date():
        return "15:00"
    minute_of_day = observed.hour * 60 + observed.minute
    if minute_of_day < 9 * 60 + 35:
        return ""
    if minute_of_day <= 11 * 60 + 30:
        floored = minute_of_day - minute_of_day % 5
    elif minute_of_day < 13 * 60 + 5:
        return "11:30"
    elif minute_of_day <= 15 * 60:
        floored = minute_of_day - minute_of_day % 5
    else:
        return "15:00"
    return f"{floored // 60:02d}:{floored % 60:02d}"


def persist_overview(result: dict[str, Any]) -> None:
    if result.get("isDemo") or not result.get("boards"):
        return
    slot = market_slot(str(result.get("updatedAt") or ""), str(result.get("date") or ""))
    if not slot:
        return
    label = str(result.get("source") or "公开行情")
    STORE.save_snapshot(
        str(result["date"]),
        slot,
        source_key(label),
        label,
        str(result["updatedAt"]),
        list(result["boards"]),
        is_final=slot == "15:00",
    )


def build_overview(force: bool = False) -> dict[str, Any]:
    cache_key = "overview-latest"
    if not force:
        cached = read_cache(cache_key, max_age_seconds=180)
        if cached and not cached.get("isDemo"):
            persist_overview(cached)
            return cached
    warning = ""
    try:
        boards = fetch_all_rankings()
    except Exception as exc:
        cached = read_cache(cache_key)
        if cached and not cached.get("isDemo"):
            cached["warning"] = f"实时源暂不可用，显示最近缓存：{exc}"
            persist_overview(cached)
            return cached
        raise DataSourceError(f"未取得经过验证的板块资金数据：{exc}") from exc
    ranking_source = str(boards[0].get("dataSource") or SOURCE_NAME) if boards else SOURCE_NAME
    if ranking_source == THS_SOURCE_NAME:
        warning = "东方财富实时列表当前限流，已切换到同花顺公开网页；当日数据保持同一来源口径。"
    try:
        indexes = fetch_indexes()
    except Exception as exc:
        recent = read_cache(cache_key) or {}
        indexes = recent.get("indexes") or [
            {"code": "000001", "name": "上证指数", "price": 0, "changePct": 0},
            {"code": "399001", "name": "深证成指", "price": 0, "changePct": 0},
            {"code": "899050", "name": "北证50", "price": 0, "changePct": 0},
        ]
        warning = f"{warning} 指数暂用最近已验证缓存：{exc}".strip()
    timestamps = [int(item.get("sourceTimestamp") or 0) for item in boards]
    source_timestamp = max(timestamps, default=0)
    source_time = dt.datetime.fromtimestamp(source_timestamp) if source_timestamp > 0 else now_cn()
    data_date = source_time.strftime("%Y-%m-%d")
    result = {
        "date": data_date,
        "updatedAt": source_time.isoformat(timespec="seconds"),
        "source": ranking_source,
        "isDemo": False,
        "warning": warning,
        "indexes": indexes,
        "boards": boards,
        "topIn": [item for item in boards if item["mainFlow"] > 0][:15],
        "topOut": sorted(
            [item for item in boards if item["mainFlow"] < 0],
            key=lambda item: item["mainFlow"],
        )[:15],
    }
    write_cache(cache_key, result)
    write_cache(f"overview-{data_date}", result)
    persist_overview(result)
    return result


def fetch_intraday_for_board(board: dict[str, Any]) -> dict[str, Any] | None:
    code = board["code"]
    if not code.startswith("BK"):
        return None
    try:
        payload = fetch_eastmoney_json(
            "/api/qt/stock/fflow/kline/get",
            {
                "lmt": 0,
                "klt": 1,
                "secid": f"90.{code}",
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                "ut": EASTMONEY_UT,
            },
            prefer_delayed=True,
            attempts=2,
        )
    except Exception:
        return None
    data = payload.get("data") or {}
    klines = data.get("klines") or []
    points: dict[str, float] = {}
    date = ""
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 2:
            continue
        timestamp = parts[0]
        date = timestamp[:10]
        points[timestamp[11:16]] = to_float(parts[1])
    if not points:
        return None
    return {"board": board, "date": date, "points": points}


def five_minute_times() -> list[str]:
    result: list[str] = []
    for hour, start, end in ((9, 35, 55), (10, 0, 55), (11, 0, 30), (13, 5, 55), (14, 0, 55), (15, 0, 0)):
        minute = start
        while minute <= end:
            result.append(f"{hour:02d}:{minute:02d}")
            minute += 5
    return result


def elapsed_five_minute_times(updated_at: str = "", date_label: str = "") -> list[str]:
    """Return only market frames that could already exist at the data timestamp."""
    times = five_minute_times()
    try:
        updated = dt.datetime.fromisoformat(updated_at)
    except (TypeError, ValueError):
        updated = now_cn()
    try:
        data_date = dt.date.fromisoformat(date_label)
    except (TypeError, ValueError):
        data_date = updated.date()
    if data_date < updated.date():
        return times
    cutoff = updated.strftime("%H:%M")
    return [label for label in times if label <= cutoff]


def previous_point(points: dict[str, float], target: str) -> float | None:
    eligible = [key for key in points if key <= target]
    if not eligible:
        return None
    return points[max(eligible)]


def persist_intraday_series(series: list[dict[str, Any]]) -> None:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in series:
        date_label = str(item.get("date") or "")
        board = item.get("board") or {}
        points = item.get("points") or {}
        for slot in five_minute_times():
            if slot not in points:
                continue
            grouped.setdefault((date_label, slot), []).append(
                {**board, "mainFlow": to_float(points[slot])}
            )
    captured_at = now_cn().isoformat(timespec="seconds")
    for (date_label, slot), boards in grouped.items():
        STORE.save_snapshot(
            date_label,
            slot,
            "eastmoney",
            EASTMONEY_MINUTE_SOURCE,
            captured_at,
            boards,
            is_final=slot == "15:00",
        )


def replay_source_label(source: str) -> str:
    if source == "eastmoney":
        return EASTMONEY_MINUTE_SOURCE
    if source == "ths":
        return f"{THS_SOURCE_NAME} · 本机五分钟快照"
    if source == "sina":
        return "新浪公开行情 · 本机五分钟快照"
    return "本机保存的真实五分钟快照"


def empty_replay(overview: dict[str, Any], warning: str) -> dict[str, Any]:
    return {
        "date": overview["date"],
        "updatedAt": now_cn().isoformat(timespec="seconds"),
        "source": "真实分时数据核验中",
        "isDemo": False,
        "warning": warning,
        "indexes": overview["indexes"],
        "schemaVersion": 4,
        "verifiedThrough": "",
        "capturedSlots": 0,
        "coveragePercent": 0,
        "frames": [],
    }


def collect_overview(force: bool = False) -> dict[str, Any]:
    with _COLLECTION_LOCK:
        return build_overview(force=force)


def build_replay(force: bool = False, overview: dict[str, Any] | None = None) -> dict[str, Any]:
    # The frontend requests the overview immediately before the replay. Reusing
    # that fresh cache prevents a manual refresh from crawling the rankings twice.
    overview = overview or collect_overview(force=False)
    trading_date = str(overview["date"])
    cache_key = f"replay-v4-{trading_date}"
    if not force:
        cached = read_cache(cache_key, max_age_seconds=600)
        if cached and not cached.get("isDemo") and cached.get("frames"):
            return cached
        pending = read_cache(cache_key, max_age_seconds=60)
        if pending and not pending.get("isDemo"):
            return pending
    preferred_source = source_key(str(overview.get("source") or ""))
    selected_source, stored_series = STORE.load_intraday_series(trading_date, preferred_source)
    stored_maximum = max((len(item.get("points") or {}) for item in stored_series), default=0)
    completed_slots = elapsed_five_minute_times(overview.get("updatedAt", ""), trading_date)
    if force or stored_maximum < max(2, math.ceil(len(completed_slots) * 0.8)):
        # Rebuild enough fixed board identities to recover a truthful full-day
        # sequence. Limiting this to today's closing ranks can omit sectors that
        # mattered earlier, so use the complete current board list and then keep
        # the 30 largest actual intraday movers after the series are fetched.
        candidates = [
            board for board in overview["boards"]
            if str(board.get("code") or "").startswith("BK")
        ]
        series: list[dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(fetch_intraday_for_board, board) for board in candidates]
            for future in concurrent.futures.as_completed(futures):
                item = future.result()
                if item:
                    series.append(item)
        if series:
            persist_intraday_series(series)
            selected_source, stored_series = STORE.load_intraday_series(trading_date, preferred_source)

    if not stored_series:
        result = empty_replay(
            overview,
            "正在通过多个公开行情源核验今日真实分钟数据；页面不会生成或预测缺失数值。",
        )
        write_cache(cache_key, result)
        return result

    maximum_points = max((len(item.get("points") or {}) for item in stored_series), default=0)
    minimum_points = max(2, math.ceil(maximum_points * 0.6))
    complete_series = [
        item for item in stored_series if len(item.get("points") or {}) >= minimum_points
    ]
    if not complete_series:
        result = empty_replay(
            overview,
            "真实行情正在持续核验，达到稳定回放所需覆盖度后会自动显示。",
        )
        write_cache(cache_key, result)
        return result

    # Select one fixed set by the largest absolute value reached during the day.
    # Those same identities are carried through every frame, including sign
    # changes, so the visual can animate a real bubble instead of swapping ranks.
    tracked_series = sorted(
        complete_series,
        key=lambda item: max((abs(value) for value in item["points"].values()), default=0),
        reverse=True,
    )[:30]
    latest_available = max(
        (max(item["points"]) for item in tracked_series if item.get("points")),
        default="",
    )
    frames: list[dict[str, Any]] = []
    for label in five_minute_times():
        if latest_available and label > latest_available:
            continue
        values: list[dict[str, Any]] = []
        for item in tracked_series:
            value = item["points"].get(label)
            if value is not None:
                values.append({**item["board"], "mainFlow": value})
        if len(values) < min(8, len(tracked_series)):
            continue
        positives = sorted((row for row in values if row["mainFlow"] >= 0), key=lambda row: row["mainFlow"], reverse=True)
        negatives = sorted((row for row in values if row["mainFlow"] < 0), key=lambda row: row["mainFlow"])
        frames.append({"time": label, "boards": values, "inflow": positives, "outflow": negatives})
    if not frames:
        result = empty_replay(
            overview,
            "真实分钟数据正在核验，当前不会以估算曲线代替。",
        )
        write_cache(cache_key, result)
        return result
    all_market_slots = elapsed_five_minute_times(
        overview.get("updatedAt", ""),
        trading_date,
    )
    coverage = round(len(frames) / max(len(all_market_slots), 1) * 100)
    result = {
        "date": trading_date,
        "updatedAt": now_cn().isoformat(timespec="seconds"),
        "source": replay_source_label(selected_source),
        "isDemo": False,
        "warning": "",
        "indexes": overview["indexes"],
        "schemaVersion": 4,
        "verifiedThrough": frames[-1]["time"],
        "capturedSlots": len(frames),
        "coveragePercent": min(100, coverage),
        "frames": frames,
    }
    write_cache(cache_key, result)
    return result


def rolling_mean(values: list[float], window: int) -> list[float | None]:
    result: list[float | None] = []
    for index in range(len(values)):
        if index + 1 < window:
            result.append(None)
        else:
            section = values[index + 1 - window : index + 1]
            result.append(sum(section) / window)
    return result


def fetch_history(code: str, name: str) -> dict[str, Any]:
    cache_key = f"history-{code}"
    cached = read_cache(cache_key, max_age_seconds=6 * 3600)
    if cached and not cached.get("isDemo"):
        return cached
    if not code.startswith("BK"):
        raise DataSourceError("该备用来源板块暂未匹配到可核验的历史代码")
    cutoff = now_cn().date() - dt.timedelta(days=95)
    cutoff_label = cutoff.isoformat()
    overview = read_cache("overview-latest") or {}
    current_board = next(
        (row for row in overview.get("boards", []) if row.get("code") == code),
        {"code": code, "name": name, "category": "板块"},
    )
    try:
        payload = fetch_json(
            "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
            {
                "lmt": 120,
                "klt": 101,
                "secid": f"90.{code}",
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                "ut": EASTMONEY_UT,
            },
            referer=f"https://data.eastmoney.com/bkzj/{code}.html",
        )
        data = payload.get("data") or {}
        rows = data.get("klines") or []
        parsed: list[tuple[str, float]] = []
        for line in rows:
            parts = str(line).split(",")
            if len(parts) < 2:
                continue
            try:
                date_value = dt.date.fromisoformat(parts[0])
            except ValueError:
                continue
            if date_value >= cutoff:
                parsed.append((parts[0], to_float(parts[1])))
        if len(parsed) < 5:
            raise DataSourceError("历史资金数据不足")
        STORE.save_daily_points(
            "eastmoney",
            SOURCE_NAME,
            current_board,
            parsed,
            now_cn().isoformat(timespec="seconds"),
        )
        values = [item[1] for item in parsed]
        ma5 = rolling_mean(values, 5)
        ma20 = rolling_mean(values, 20)
        result = {
            "code": code,
            "name": data.get("name") or name,
            "source": SOURCE_NAME,
            "isDemo": False,
            "points": [
                {"date": item[0], "mainFlow": item[1], "ma5": ma5[index], "ma20": ma20[index]}
                for index, item in enumerate(parsed)
            ],
        }
        write_cache(cache_key, result)
        return result
    except Exception as exc:
        stored_source, parsed = STORE.load_daily_points(code, cutoff_label)
        if len(parsed) < 5:
            raise DataSourceError(f"近三个月真实资金历史暂未通过核验：{exc}") from exc
        values = [item[1] for item in parsed]
        ma5 = rolling_mean(values, 5)
        ma20 = rolling_mean(values, 20)
        return {
            "code": code,
            "name": name,
            "source": replay_source_label(stored_source),
            "isDemo": False,
            "points": [
                {"date": item[0], "mainFlow": item[1], "ma5": ma5[index], "ma20": ma20[index]}
                for index, item in enumerate(parsed)
            ],
        }


STOCK_UNIVERSE = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
STOCK_CHANGE_TYPES = "8201,8202,8203,8204,4,8"
STOCK_CHANGE_LABELS = {
    8201: ("火箭发射", 1),
    8202: ("快速反弹", 1),
    8203: ("高台跳水", -1),
    8204: ("加速下跌", -1),
    4: ("封涨停板", 1),
    8: ("封跌停板", -1),
}

STRATEGY_MAX_TRADES = 20
STRATEGY_ALLOCATION = 0.05
STRATEGY_MIN_ONE_MINUTE_RETURN = 0.8
STRATEGY_MIN_DAILY_AMOUNT = 50_000_000
STRATEGY_DEFAULT_THRESHOLD = 55.0
STRATEGY_COMMISSION_RATE = 0.00025
STRATEGY_TRANSFER_AND_REGULATORY_RATE = 0.0000541
STRATEGY_STAMP_DUTY_RATE = 0.0005
STRATEGY_SLIPPAGE_RATE = 0.0005
STRATEGY_LAB_DEFAULT_CONFIG: dict[str, Any] = {
    "name": "板块确认追涨",
    "marketScope": "all",
    "oneMinuteRise": 0.8,
    "sectorFilter": "both",
    "minAmount": 50_000_000,
    "minScore": 55.0,
    "startTime": "09:45",
    "endTime": "14:50",
    "buyDelayMinutes": 1,
    "entryPriceMode": "minute_close",
    "allocationMode": "fixed_pct",
    "positionPct": 5.0,
    "maxPositions": 20,
    "exitMode": "next_0931",
    "takeProfitPct": 3.0,
    "stopLossPct": 1.5,
    "initialCapital": 100_000.0,
}


def is_allowed_stock(code: str, name: str) -> bool:
    """Keep mainland A shares while explicitly excluding ST and *ST names."""
    normalized = name.upper().replace(" ", "")
    a_share_prefixes = (
        "000", "001", "002", "003", "300", "301",
        "600", "601", "603", "605", "688", "689",
        "43", "83", "87", "88", "92",
    )
    return bool(
        len(code) == 6 and name and code.isdigit()
        and code.startswith(a_share_prefixes)
        and "ST" not in normalized
    )


def infer_stock_market(code: str, market: Any = None) -> int:
    if str(market) in {"0", "1"}:
        return int(str(market))
    return 1 if code.startswith(("5", "6")) and not code.startswith("920") else 0


def stock_secid(code: str, market: Any = None) -> str:
    return f"{infer_stock_market(code, market)}.{code}"


def parse_trend_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for raw in data.get("trends") or []:
        parts = str(raw).split(",")
        if len(parts) < 8 or " " not in parts[0]:
            continue
        date_label, time_label = parts[0].split(" ", 1)
        points.append(
            {
                "date": date_label,
                "time": time_label[:5],
                "open": to_float(parts[1]),
                "price": to_float(parts[2]),
                "high": to_float(parts[3]),
                "low": to_float(parts[4]),
                "volume": to_float(parts[5]),
                "amount": to_float(parts[6]),
                "average": to_float(parts[7]),
            }
        )
    return [point for point in points if point["price"] > 0]


def fetch_stock_trends(
    code: str,
    market: Any = None,
    *,
    cache_seconds: int = 42,
) -> dict[str, Any]:
    market_number = infer_stock_market(code, market)
    cache_key = f"stock-trends-{market_number}-{code}"
    cached = read_cache(cache_key, max_age_seconds=cache_seconds)
    if cached and cached.get("points"):
        return cached
    payload = fetch_eastmoney_json(
        "/api/qt/stock/trends2/get",
        {
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "ut": EASTMONEY_UT,
            "ndays": 1,
            "iscr": 0,
            "secid": stock_secid(code, market_number),
        },
        prefer_delayed=True,
        attempts=2,
    )
    data = payload.get("data") or {}
    points = parse_trend_rows(data)
    if not points:
        raise DataSourceError(f"{code} 未返回真实分时行情")
    result = {
        "code": code,
        "market": market_number,
        "name": str(data.get("name") or code),
        "preClose": to_float(data.get("preClose")),
        "points": points,
    }
    write_cache(cache_key, result)
    return result


def public_stock_symbol(code: str, market: Any = None) -> str:
    """Return the exchange-prefixed symbol used by Sina and Tencent."""
    if code.startswith("92"):
        return f"bj{code}"
    return f"sh{code}" if infer_stock_market(code, market) == 1 else f"sz{code}"


def fetch_tencent_stock_trends(code: str, market: Any = None) -> dict[str, Any]:
    """Real one-day minute prices from Tencent, used when EM minute data fails."""
    symbol = public_stock_symbol(code, market)
    raw = fetch_text_url(
        f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={symbol}",
        referer=f"https://gu.qq.com/{symbol}/gp",
        attempts=2,
    )
    payload = json.loads(raw)
    container = (payload.get("data") or {}).get(symbol) or {}
    minute_container = container.get("data") or container
    rows = minute_container.get("data") if isinstance(minute_container, dict) else minute_container
    date_digits = str(
        (minute_container.get("date") if isinstance(minute_container, dict) else "")
        or container.get("date")
        or now_cn().strftime("%Y%m%d")
    )
    if len(date_digits) == 8 and date_digits.isdigit():
        date_label = f"{date_digits[:4]}-{date_digits[4:6]}-{date_digits[6:]}"
    else:
        date_label = now_cn().date().isoformat()
    points: list[dict[str, Any]] = []
    prior_volume = 0.0
    prior_amount = 0.0
    for row in rows or []:
        parts = str(row).split()
        if len(parts) < 3:
            continue
        time_digits = parts[0]
        price = to_float(parts[1])
        cumulative_volume = to_float(parts[2])
        cumulative_amount = to_float(parts[3]) if len(parts) > 3 else 0.0
        if len(time_digits) != 4 or price <= 0:
            continue
        volume = max(0.0, cumulative_volume - prior_volume) * 100
        amount = max(0.0, cumulative_amount - prior_amount)
        average = cumulative_amount / (cumulative_volume * 100) if cumulative_amount > 0 and cumulative_volume > 0 else 0.0
        points.append(
            {
                "date": date_label,
                "time": f"{time_digits[:2]}:{time_digits[2:]}",
                "open": 0.0,
                "price": price,
                "high": 0.0,
                "low": 0.0,
                "volume": volume,
                "amount": amount,
                "average": average,
            }
        )
        prior_volume = cumulative_volume
        prior_amount = cumulative_amount
    if not points:
        raise DataSourceError(f"腾讯证券未返回 {code} 的真实分时行情")
    return {
        "code": code,
        "market": infer_stock_market(code, market),
        "name": code,
        "preClose": 0.0,
        "points": points,
        "source": TENCENT_STOCK_SOURCE,
    }


def fetch_verified_stock_trends(code: str, market: Any = None) -> dict[str, Any]:
    """Fetch minutes from two sources and keep a truthful fallback if one fails."""
    eastmoney: dict[str, Any] | None = None
    tencent: dict[str, Any] | None = None
    errors: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            "em": executor.submit(fetch_stock_trends, code, market, cache_seconds=30),
            "tencent": executor.submit(fetch_tencent_stock_trends, code, market),
        }
        for provider, future in futures.items():
            try:
                if provider == "em":
                    eastmoney = future.result()
                else:
                    tencent = future.result()
            except Exception as exc:
                errors.append(f"{provider}: {exc}")
    if eastmoney:
        result = dict(eastmoney)
        result["source"] = STOCK_SOURCE_NAME
        result["verifiedBy"] = [STOCK_SOURCE_NAME]
        if tencent:
            em_by_time = {point["time"]: point["price"] for point in eastmoney["points"]}
            common = [point for point in tencent["points"] if point["time"] in em_by_time]
            if common:
                comparison = common[-1]
                em_price = em_by_time[comparison["time"]]
                tolerance = max(0.02, em_price * 0.003)
                if abs(comparison["price"] - em_price) <= tolerance:
                    result["verifiedBy"].append(TENCENT_STOCK_SOURCE)
                    result["source"] = f"{STOCK_SOURCE_NAME} · {TENCENT_STOCK_SOURCE}交叉核验"
                else:
                    result["verificationNote"] = f"两源在 {comparison['time']} 存在时间差，保留东方财富分钟序列"
        return result
    if tencent:
        tencent["verifiedBy"] = [TENCENT_STOCK_SOURCE]
        tencent["verificationNote"] = "东方财富分钟源暂不可用，已改用腾讯证券真实分钟序列"
        return tencent
    raise DataSourceError("；".join(errors) or f"{code} 未取得真实分时行情")


def fetch_broad_market_trend() -> dict[str, Any]:
    result = fetch_verified_stock_trends("000985", 1)
    result["name"] = "中证全指"
    result["code"] = "000985"
    return result


def fetch_stock_candidates(field: str, descending: bool, limit: int = 36) -> list[dict[str, Any]]:
    payload = fetch_eastmoney_json(
        "/api/qt/clist/get",
        {
            "pn": 1,
            "pz": limit,
            "po": 1 if descending else 0,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": field,
            "fs": STOCK_UNIVERSE,
            "fields": "f12,f13,f14,f2,f3,f8,f18,f22,f124",
            "ut": EASTMONEY_UT,
        },
        prefer_delayed=True,
        attempts=2,
    )
    rows: list[dict[str, Any]] = []
    for raw in (payload.get("data") or {}).get("diff") or []:
        code = str(raw.get("f12") or "")
        name = str(raw.get("f14") or "")
        if not is_allowed_stock(code, name):
            continue
        timestamp = int(to_float(raw.get("f124")))
        rows.append(
            {
                "code": code,
                "market": infer_stock_market(code, raw.get("f13")),
                "name": name,
                "price": to_float(raw.get("f2")),
                "changePct": to_float(raw.get("f3")),
                "turnover": to_float(raw.get("f8")),
                "preClose": to_float(raw.get("f18")),
                "providerSpeed": to_float(raw.get("f22")),
                "sourceTimestamp": timestamp,
            }
        )
    return rows


def exact_one_minute_item(candidate: dict[str, Any], cutoff: str) -> dict[str, Any] | None:
    try:
        trend = fetch_stock_trends(candidate["code"], candidate["market"])
    except Exception:
        return None
    points = [point for point in trend["points"] if point["time"] <= cutoff]
    if len(points) < 2 or points[-2]["price"] <= 0:
        return None
    latest, previous = points[-1], points[-2]
    speed = (latest["price"] / previous["price"] - 1) * 100
    return {
        **{key: value for key, value in candidate.items() if key != "providerSpeed"},
        "price": latest["price"],
        "speed1m": speed,
        "asOf": latest["time"],
    }


def build_exact_speed_rankings(cutoff: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # f22 is Eastmoney's public short-period speed field (normally 3 minutes).
    # Use a broad two-sided candidate pool, then discard f22 and recompute every
    # retained stock from its two actual adjacent minute prices.
    rising = fetch_stock_candidates("f22", True, 96)[:80]
    falling = fetch_stock_candidates("f22", False, 96)[:80]
    candidates: dict[str, dict[str, Any]] = {
        item["code"]: item for item in rising + falling
    }
    exact: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = [
            executor.submit(exact_one_minute_item, candidate, cutoff)
            for candidate in candidates.values()
        ]
        for future in concurrent.futures.as_completed(futures):
            item = future.result()
            if item is not None:
                exact.append(item)
    fastest_rise = sorted(
        (item for item in exact if item["speed1m"] > 0),
        key=lambda item: item["speed1m"],
        reverse=True,
    )[:10]
    fastest_fall = sorted(
        (item for item in exact if item["speed1m"] < 0),
        key=lambda item: item["speed1m"],
    )[:10]
    if len(fastest_rise) < 10 or len(fastest_fall) < 10:
        raise DataSourceError("一分钟涨跌速排名的真实分时样本不足")
    return fastest_rise, fastest_fall


def fetch_turnover_ranking() -> list[dict[str, Any]]:
    items = fetch_stock_candidates("f8", True, 48)
    result = [
        {
            **{key: value for key, value in item.items() if key != "providerSpeed"},
            "asOf": dt.datetime.fromtimestamp(item["sourceTimestamp"]).strftime("%H:%M")
            if item["sourceTimestamp"] > 0
            else "",
        }
        for item in items
    ][:10]
    if len(result) < 10:
        raise DataSourceError("换手率排名返回不足十只非 ST 股票")
    return result


def event_severity(parts: list[float], event_type: int) -> float:
    if event_type in {4, 8}:
        return 100.0
    ratios = [abs(value) * 100 for value in parts if 0 < abs(value) <= 1]
    return max(ratios, default=max((abs(value) for value in parts), default=0.0))


def fetch_stock_events(trading_date: str, cutoff: str, *, chart_density_limit: bool = True) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page_size = 1000
    total = page_size
    page_index = 0
    while page_index * page_size < min(total, 5000):
        payload = fetch_json(
            "https://push2ex.eastmoney.com/getAllStockChanges",
            {
                "type": STOCK_CHANGE_TYPES,
                "ut": EASTMONEY_CHANGE_UT,
                "pageindex": page_index,
                "pagesize": page_size,
                "dpt": "wzchanges",
            },
            attempts=2,
            referer="https://quote.eastmoney.com/center/gridlist.html",
        )
        data = payload.get("data") or {}
        total = int(to_float(data.get("tc"), page_size))
        rows.extend(data.get("allstock") or [])
        page_index += 1
        if not data.get("allstock"):
            break

    deduped: dict[tuple[str, str, int], dict[str, Any]] = {}
    for raw in rows:
        code = str(raw.get("c") or "")
        name = str(raw.get("n") or "")
        event_type = int(to_float(raw.get("t")))
        definition = STOCK_CHANGE_LABELS.get(event_type)
        if definition is None or not is_allowed_stock(code, name):
            continue
        time_digits = str(int(to_float(raw.get("tm")))).zfill(6)
        time_label = f"{time_digits[:2]}:{time_digits[2:4]}"
        if time_label > cutoff:
            continue
        numeric_parts = [to_float(value) for value in str(raw.get("i") or "").split(",")]
        label, direction = definition
        item = {
            "code": code,
            "market": infer_stock_market(code, raw.get("m")),
            "name": name,
            "time": time_label,
            "eventType": event_type,
            "event": label,
            "direction": direction,
            "severity": event_severity(numeric_parts, event_type),
            "price": numeric_parts[0] if numeric_parts else 0,
        }
        key = (code, time_label, event_type)
        previous = deduped.get(key)
        if previous is None or item["severity"] > previous["severity"]:
            deduped[key] = item

    # The chart needs a readable density, while the strategy must inspect every
    # real candidate. Never let a presentation limit silently shrink the
    # signal universe used by the simulation.
    all_events = sorted(deduped.values(), key=lambda item: (item["time"], -item["severity"]))
    if not chart_density_limit:
        return all_events
    # Preserve events throughout the day instead of allowing one hectic minute
    # to crowd out every other label. The chart performs a second density pass
    # based on the current zoom level.
    buckets: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for item in deduped.values():
        hour, minute = (int(part) for part in item["time"].split(":"))
        bucket = (hour * 60 + minute) // 5
        buckets.setdefault((bucket, item["direction"]), []).append(item)
    selected: list[dict[str, Any]] = []
    for bucket_rows in buckets.values():
        selected.extend(sorted(bucket_rows, key=lambda item: item["severity"], reverse=True)[:4])
    return sorted(selected, key=lambda item: (item["time"], -item["severity"]))


def next_minute_point(points: list[dict[str, Any]], signal_time: str) -> dict[str, Any] | None:
    return next((point for point in points if str(point.get("time") or "") > signal_time), None)


def minute_number(time_label: str) -> int:
    try:
        hour, minute = (int(part) for part in time_label.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return -1
        return hour * 60 + minute
    except (TypeError, ValueError):
        return -1


def point_at_or_before(points: list[dict[str, Any]], time_label: str) -> dict[str, Any] | None:
    eligible = [point for point in points if str(point.get("time") or "") <= time_label]
    return eligible[-1] if eligible else None


def stock_primary_industry(code: str, market: int) -> dict[str, str]:
    cache_key = f"stock-industry-{market}-{code}"
    cached = read_cache(cache_key, max_age_seconds=30 * 24 * 3600)
    if cached and cached.get("name"):
        return {"name": str(cached["name"]), "source": str(cached.get("source") or STOCK_SOURCE_NAME)}
    payload = fetch_eastmoney_json(
        "/api/qt/stock/get",
        {
            "secid": stock_secid(code, market),
            "fields": "f57,f58,f127",
            "ut": EASTMONEY_UT,
        },
        prefer_delayed=True,
        attempts=2,
    )
    data = payload.get("data") or {}
    industry = str(data.get("f127") or "").strip()
    if not industry:
        raise DataSourceError(f"{code} 未返回真实主营行业")
    result = {"name": industry, "source": STOCK_SOURCE_NAME}
    write_cache(cache_key, result)
    return result


def industry_match_score(stock_industry: str, board_name: str) -> int:
    left, right = normalize_board_name(stock_industry), normalize_board_name(board_name)
    if not left or not right:
        return -1
    if left == right:
        return 100
    strip_levels = lambda value: re.sub(r"[ⅠⅡⅢIVX]+$", "", value)
    left_base, right_base = strip_levels(left), strip_levels(right)
    if left_base == right_base:
        return 95
    if min(len(left_base), len(right_base)) >= 2 and (left_base in right_base or right_base in left_base):
        return 70 + min(len(left_base), len(right_base))
    return -1


def resolve_signal_industry(stock_industry: str, snapshot: list[dict[str, Any]]) -> dict[str, Any] | None:
    industries = [item for item in snapshot if str(item.get("category")) == "行业"]
    ranked = sorted(
        ((industry_match_score(stock_industry, str(item.get("board_name") or "")), item) for item in industries),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not ranked or ranked[0][0] < 70:
        return None
    return dict(ranked[0][1])


def fetch_industry_candidates() -> list[dict[str, Any]]:
    cache_key = "strategy-industry-candidates"
    cached = read_cache(cache_key, max_age_seconds=6 * 3600)
    if cached and cached.get("boards"):
        return list(cached["boards"])
    boards = [
        row for row in fetch_all_rankings()
        if row.get("category") == "行业" and str(row.get("code") or "").startswith("BK")
    ]
    if not boards:
        raise DataSourceError("未取得可用于策略回放的真实行业列表")
    write_cache(cache_key, {"boards": boards, "capturedAt": now_cn().isoformat(timespec="seconds")})
    return boards


def match_primary_industry_board(industry_name: str, boards: list[dict[str, Any]]) -> dict[str, Any] | None:
    scored = sorted(
        ((industry_match_score(industry_name, str(board.get("name") or "")), board) for board in boards),
        key=lambda item: item[0],
        reverse=True,
    )
    return scored[0][1] if scored and scored[0][0] >= 70 else None


def fetch_industry_replay_series(board: dict[str, Any], trading_date: str) -> dict[str, Any]:
    code = str(board.get("code") or "")
    cache_key = f"strategy-industry-series-{trading_date}-{code}"
    cached = read_cache(cache_key, max_age_seconds=6 * 3600)
    if cached and cached.get("flow") and cached.get("price"):
        return cached
    flow_result = fetch_intraday_for_board(board)
    if not flow_result or str(flow_result.get("date") or "") != trading_date:
        raise DataSourceError(f"{board.get('name') or code} 未返回当日行业资金分时")
    payload = fetch_eastmoney_json(
        "/api/qt/stock/trends2/get",
        {
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "ut": EASTMONEY_UT, "ndays": 1, "iscr": 0, "secid": f"90.{code}",
        },
        prefer_delayed=True,
        attempts=2,
    )
    trend_data = payload.get("data") or {}
    trend_points = parse_trend_rows(trend_data)
    price_points = {
        str(point["time"]): to_float(point["price"])
        for point in trend_points
        if str(point.get("date") or "") == trading_date
    }
    if not price_points:
        raise DataSourceError(f"{board.get('name') or code} 未返回当日行业价格分时")
    result = {
        "date": trading_date, "code": code, "name": str(board.get("name") or code), "category": "行业",
        "preClose": to_float(trend_data.get("preClose")), "flow": dict(flow_result["points"]), "price": price_points,
    }
    write_cache(cache_key, result)
    return result


def reconstructed_industry_observation(
    series: dict[str, Any],
    signal_time: str,
) -> dict[str, Any] | None:
    common_times = sorted(key for key in set(series.get("flow") or {}) & set(series.get("price") or {}) if key <= signal_time)
    if not common_times:
        return None
    slot_time = common_times[-1]
    flow = to_float(series["flow"][slot_time])
    price = to_float(series["price"][slot_time])
    pre_close = to_float(series.get("preClose"))
    if price <= 0 or pre_close <= 0:
        return None
    return {
        "board_code": str(series["code"]), "board_name": str(series["name"]), "category": "行业",
        "main_flow": flow, "price": price, "change_pct": (price / pre_close - 1) * 100,
        "slot_time": slot_time, "source_key": "eastmoney-strategy-replay",
        "captured_at": now_cn().isoformat(timespec="seconds"),
    }


def adaptive_strategy_threshold(trading_date: str) -> float:
    history = sorted(STORE.load_recent_strategy_scores(trading_date, 30))
    if len(history) < 40:
        return STRATEGY_DEFAULT_THRESHOLD
    # Only prior-day scores participate. The 7/8 quantile targets roughly the
    # strongest one eighth of signals without knowing today's later signals.
    index = min(len(history) - 1, max(0, math.floor(len(history) * 0.875)))
    return max(45.0, min(90.0, history[index]))


def strategy_score(one_minute_return: float, sector_change_pct: float, sector_flow: float, amount: float) -> float:
    speed_component = min(100.0, max(0.0, one_minute_return / 2.5 * 100))
    sector_change_component = min(100.0, max(0.0, sector_change_pct / 3.0 * 100))
    sector_flow_component = min(100.0, max(0.0, math.log10(1 + max(0.0, sector_flow) / 10_000_000) / 2.7 * 100))
    liquidity_component = min(100.0, max(0.0, math.log10(1 + max(0.0, amount) / 10_000_000) / 2.3 * 100))
    return (
        speed_component * 0.45
        + sector_change_component * 0.25
        + sector_flow_component * 0.20
        + liquidity_component * 0.10
    )


def execution_cost_rate(*, realized: bool) -> float:
    # Commission is configurable as a rate because the simulation allocates
    # percentages rather than assuming an arbitrary account size/minimum fee.
    entry = STRATEGY_COMMISSION_RATE + STRATEGY_TRANSFER_AND_REGULATORY_RATE + STRATEGY_SLIPPAGE_RATE
    if not realized:
        return entry
    exit_cost = STRATEGY_COMMISSION_RATE + STRATEGY_TRANSFER_AND_REGULATORY_RATE + STRATEGY_SLIPPAGE_RATE + STRATEGY_STAMP_DUTY_RATE
    return entry + exit_cost


def trade_return(entry: float, exit_price: float, *, after_cost: bool = False, realized: bool = False) -> float | None:
    if entry <= 0 or exit_price <= 0:
        return None
    gross = exit_price / entry - 1
    return (gross - execution_cost_rate(realized=realized)) * 100 if after_cost else gross * 100


def is_probably_unbuyable_limit_up(
    code: str,
    pre_close: float,
    execution: dict[str, Any],
    previous: dict[str, Any] | None,
) -> bool:
    if pre_close <= 0:
        return False
    limit = 1.30 if code.startswith(("4", "8", "92")) else 1.20 if code.startswith(("3", "68")) else 1.10
    limit_price = round(pre_close * limit + 1e-8, 2)
    price = to_float(execution.get("price"))
    no_printed_volume = to_float(execution.get("volume")) <= 0
    pinned = previous is not None and to_float(previous.get("price")) >= limit_price - 0.011
    return price >= limit_price - 0.011 and (no_printed_volume or pinned)


def update_existing_strategy_trades(current_date: str, index_market: dict[str, Any] | None = None) -> None:
    earliest = (dt.date.fromisoformat(current_date) - dt.timedelta(days=45)).isoformat()
    rows = STORE.load_strategy_rows(earliest)
    by_identity = {(str(row["trading_date"]), str(row["stock_code"])): row for row in rows if row.get("status")}
    for (trading_date, code), row in by_identity.items():
        market = int(row.get("market") or 0)
        try:
            if trading_date == current_date:
                trend = fetch_stock_trends(code, market, cache_seconds=30)
                points = trend["points"]
                if row["status"] == "pending_execution":
                    execution = next_minute_point(points, str(row["signal_time"]))
                    if execution:
                        signal_point = point_at_or_before(points, str(row["signal_time"]))
                        if is_probably_unbuyable_limit_up(code, to_float(trend.get("preClose")), execution, signal_point):
                            STORE.update_strategy_trade(
                                trading_date, code, status="unfilled", rejection_reason="下一分钟仍封于涨停，按无法成交处理",
                                updated_at=now_cn().isoformat(timespec="seconds"),
                            )
                        elif to_float(execution.get("price")) > 0 and to_float(execution.get("volume")) > 0:
                            index_execution = point_at_or_before(
                                list((index_market or {}).get("points") or []), str(execution["time"])
                            )
                            STORE.update_strategy_trade(
                                trading_date, code, status="open", execution_time=str(execution["time"]),
                                execution_price=to_float(execution["price"]), execution_volume=to_float(execution["volume"]),
                                index_entry_price=to_float((index_execution or {}).get("value")),
                                updated_at=now_cn().isoformat(timespec="seconds"),
                            )
                    elif points and str(points[-1]["time"]) >= "15:00":
                        STORE.update_strategy_trade(
                            trading_date, code, status="unfilled", rejection_reason="收盘前没有下一分钟真实成交",
                            updated_at=now_cn().isoformat(timespec="seconds"),
                        )
                refreshed = STORE.load_strategy_rows(trading_date)
                current = next((item for item in refreshed if item["trading_date"] == trading_date and item["stock_code"] == code), row)
                if current.get("status") == "open" and points:
                    latest = points[-1]
                    changes: dict[str, Any] = {
                        "current_time": str(latest["time"]), "current_price": to_float(latest["price"]),
                        "updated_at": now_cn().isoformat(timespec="seconds"),
                    }
                    index_points = list((index_market or {}).get("points") or [])
                    index_latest = point_at_or_before(index_points, str(latest["time"]))
                    if index_latest:
                        changes["index_current_price"] = to_float(index_latest.get("value"))
                    close_point = next((point for point in reversed(points) if str(point["time"]) <= "15:00"), None)
                    if close_point and str(close_point["time"]) == "15:00":
                        changes["close_price"] = to_float(close_point["price"])
                        index_close = point_at_or_before(index_points, "15:00")
                        if index_close and str(index_close.get("time")) == "15:00":
                            changes["index_close_price"] = to_float(index_close.get("value"))
                    STORE.update_strategy_trade(trading_date, code, **changes)
            elif (
                str(row.get("status") or "") != "complete"
                or (
                    not to_float(row.get("next_0931_price"))
                    and (dt.date.fromisoformat(current_date) - dt.date.fromisoformat(trading_date)).days <= 4
                )
            ):
                daily = fetch_stock_daily(code, market)["points"]
                index_daily = fetch_stock_daily("000985", 1)["points"]
                trade_index = next((index for index, point in enumerate(daily) if point["date"] == trading_date), -1)
                index_trade_index = next((index for index, point in enumerate(index_daily) if point["date"] == trading_date), -1)
                changes: dict[str, Any] = {"updated_at": now_cn().isoformat(timespec="seconds")}
                next_date = ""
                if trade_index >= 0:
                    changes["close_price"] = to_float(daily[trade_index]["close"])
                    if trade_index + 1 < len(daily):
                        next_day = daily[trade_index + 1]
                        next_date = str(next_day["date"])
                        changes["next_open_price"] = to_float(next_day["open"])
                        changes["status"] = "complete"
                if index_trade_index >= 0:
                    changes["index_close_price"] = to_float(index_daily[index_trade_index]["close"])
                    if index_trade_index + 1 < len(index_daily):
                        changes["index_next_open_price"] = to_float(index_daily[index_trade_index + 1]["open"])
                trend = fetch_stock_trends(code, market, cache_seconds=300)
                points = trend["points"]
                if points and next_date and str(points[0].get("date")) == next_date:
                    first_0931 = next((point for point in points if str(point["time"]) >= "09:31"), None)
                    if first_0931:
                        changes["next_0931_price"] = to_float(first_0931["price"])
                index_points = list((index_market or {}).get("points") or [])
                if index_points and next_date and str(index_points[0].get("date")) == next_date:
                    index_0931 = next((point for point in index_points if str(point.get("time")) >= "09:31"), None)
                    if index_0931:
                        changes["index_next_0931_price"] = to_float(index_0931.get("value"))
                STORE.update_strategy_trade(trading_date, code, **changes)
        except Exception:
            continue


def capture_strategy_signals(stock_market: dict[str, Any]) -> dict[str, Any]:
    trading_date = str(stock_market["date"])
    verified = str(stock_market["verifiedThrough"])
    threshold = adaptive_strategy_threshold(trading_date)
    current = now_cn()
    is_same_day_replay = (
        trading_date == current.date().isoformat()
        and current.weekday() < 5
    )
    update_existing_strategy_trades(trading_date, stock_market.get("index"))
    if not is_same_day_replay:
        STORE.prune_strategy_history(30)
        return {"status": "unavailable", "processedThrough": "", "unresolved": 0, "sourceEvents": 0}
    progress = STORE.load_strategy_replay_progress(trading_date) or {}
    resume_after = str(progress.get("processed_through") or "09:29")
    existing_rows = STORE.load_strategy_rows(trading_date)
    existing_codes = {str(row["stock_code"]) for row in existing_rows if row["trading_date"] == trading_date}
    # Rebuild decisions strictly in event-time order. Each completed minute is
    # reduced only with information that existed inside that same minute; later
    # prices and later events never influence whether the signal is selected.
    raw_rising_events = [
        event for event in stock_market.get("events") or []
        if int(event.get("direction") or 0) > 0
        and int(event.get("eventType") or 0) in {8201, 8202}
        and "09:30" <= str(event.get("time") or "") <= verified
        and resume_after < str(event.get("time") or "")
    ]
    # At most one strongest fast-rise candidate is selected after each minute
    # completes. This is causal (the simulated order is the following minute),
    # bounds public-source load, and does not rank a 10:01 event using 10:04.
    strongest_by_minute: dict[str, dict[str, Any]] = {}
    for event in raw_rising_events:
        time_label = str(event["time"])
        previous_event = strongest_by_minute.get(time_label)
        if previous_event is None or to_float(event.get("severity")) > to_float(previous_event.get("severity")):
            strongest_by_minute[time_label] = event
    rising_events = sorted(strongest_by_minute.values(), key=lambda event: (event["time"], -to_float(event.get("severity"))))
    industry_boards: list[dict[str, Any]] | None = None
    industry_series: dict[str, dict[str, Any]] = {}
    unresolved = 0
    unresolved_times: list[str] = []
    trends_by_code: dict[str, dict[str, Any] | Exception] = {}
    event_by_code = {str(event["code"]): event for event in rising_events if str(event["code"]) not in existing_codes}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(
                fetch_stock_trends,
                code,
                infer_stock_market(code, event.get("market")),
                cache_seconds=300,
            ): code
            for code, event in event_by_code.items()
        }
        for future in concurrent.futures.as_completed(futures):
            code = futures[future]
            try:
                trends_by_code[code] = future.result()
            except Exception as exc:
                trends_by_code[code] = exc
    for event in rising_events:
        code = str(event["code"])
        if code in existing_codes:
            continue
        market = infer_stock_market(code, event.get("market"))
        try:
            trend = trends_by_code.get(code)
            if isinstance(trend, Exception) or trend is None:
                raise trend if isinstance(trend, Exception) else DataSourceError(f"{code} 分时行情尚未取得")
            points = [point for point in trend["points"] if str(point["time"]) <= verified]
            signal_index = next((index for index, point in enumerate(points) if str(point["time"]) >= str(event["time"])), -1)
            if signal_index <= 0:
                continue
            signal_point, previous = points[signal_index], points[signal_index - 1]
            one_minute_return = (to_float(signal_point["price"]) / to_float(previous["price"]) - 1) * 100
            industry = stock_primary_industry(code, market)
            snapshot = STORE.load_sector_snapshot_at(trading_date, str(event["time"]))
            sector = resolve_signal_industry(industry["name"], snapshot)
            if sector is None:
                if industry_boards is None:
                    industry_boards = fetch_industry_candidates()
                board = match_primary_industry_board(industry["name"], industry_boards)
                if board:
                    board_code = str(board["code"])
                    if board_code not in industry_series:
                        industry_series[board_code] = fetch_industry_replay_series(board, trading_date)
                    sector = reconstructed_industry_observation(industry_series[board_code], str(event["time"]))
                    if sector:
                        STORE.save_strategy_sector_observation(
                            trading_date,
                            str(sector["slot_time"]),
                            {
                                "code": sector["board_code"], "name": sector["board_name"], "category": "行业",
                                "mainFlow": sector["main_flow"], "price": sector["price"],
                                "changePct": sector["change_pct"],
                            },
                            str(sector["captured_at"]),
                        )
            sector_change = to_float(sector.get("change_pct")) if sector else 0.0
            sector_flow = to_float(sector.get("main_flow")) if sector else 0.0
            # Use only turnover accumulated through the signal minute. The
            # current quote would leak trades that happened after the signal.
            amount = sum(to_float(point.get("amount")) for point in points[: signal_index + 1])
            score = strategy_score(one_minute_return, sector_change, sector_flow, amount)
            reasons: list[str] = []
            if one_minute_return < STRATEGY_MIN_ONE_MINUTE_RETURN:
                reasons.append(f"一分钟涨幅低于 {STRATEGY_MIN_ONE_MINUTE_RETURN:.1f}%")
            if sector is None:
                reasons.append("未在信号前的已保存快照中匹配到主营行业")
            elif sector_change <= 0 or sector_flow <= 0:
                reasons.append("主营行业涨幅或资金净流入未同时为正")
            if amount < STRATEGY_MIN_DAILY_AMOUNT:
                reasons.append("当日成交额低于流动性门槛")
            if score < threshold:
                reasons.append("实时综合分未达到历史阈值")
            eligible = not reasons
            signal = {
                "tradingDate": trading_date, "code": code, "market": market, "name": str(event["name"]),
                "signalTime": str(event["time"]), "eventType": int(event.get("eventType") or 0),
                "event": str(event.get("event") or "一分钟快速上涨"), "oneMinuteReturn": one_minute_return,
                "signalPrice": to_float(signal_point["price"]), "industryCode": str(sector.get("board_code") if sector else ""),
                "industryName": str(sector.get("board_name") if sector else industry["name"]),
                "sectorSlot": str(sector.get("slot_time") if sector else ""), "sectorChangePct": sector_change,
                "sectorMainFlow": sector_flow, "liquidityAmount": amount, "score": score,
                "thresholdScore": threshold, "eligible": eligible,
                "decisionReason": "通过实时过滤" if eligible else "；".join(reasons),
                "capturedAt": now_cn().isoformat(timespec="seconds"),
            }
            inserted = STORE.save_strategy_signal(signal)
            existing_codes.add(code)
            if inserted and eligible:
                if STORE.count_strategy_trades(trading_date) < STRATEGY_MAX_TRADES:
                    STORE.create_strategy_trade(trading_date, code, now_cn().isoformat(timespec="seconds"))
                else:
                    # The signal remains auditable but is not a trade: all 20
                    # chronological slots were already occupied at this time.
                    STORE.update_strategy_signal_reason(trading_date, code, "符合过滤，但当日20个仓位已满")
        except Exception:
            unresolved += 1
            unresolved_times.append(str(event.get("time") or ""))
            continue
    status = "complete" if unresolved == 0 else "partial"
    processed_through = verified if not unresolved_times else resume_after
    STORE.save_strategy_replay_progress(
        trading_date, processed_through, verified, status, len(rising_events), unresolved,
        now_cn().isoformat(timespec="seconds"),
    )
    update_existing_strategy_trades(trading_date, stock_market.get("index"))
    STORE.prune_strategy_history(30)
    return {"status": status, "processedThrough": processed_through, "unresolved": unresolved, "sourceEvents": len(rising_events)}


def median_or_none(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def fetch_strategy_market() -> dict[str, Any]:
    """Fetch only the real observations needed by the strategy recorder.

    The full stock page also rebuilds three rankings. Repeating that expensive
    crawl every minute in the background would add no information to a trading
    decision, so the recorder reads the broad-market clock and the public event
    feed, then fetches minute prices only for newly triggered stocks.
    """
    broad = fetch_broad_market_trend()
    points = broad["points"]
    trading_date = str(points[-1]["date"])
    verified = str(points[-1]["time"])
    pre_close = to_float(broad.get("preClose"))
    return {
        "date": trading_date,
        "verifiedThrough": verified,
        "index": {
            "code": "000985",
            "name": "中证全指",
            "preClose": pre_close,
            "points": [
                {
                    "date": point["date"], "time": point["time"], "value": point["price"],
                    "changePct": (point["price"] / pre_close - 1) * 100 if pre_close else 0,
                }
                for point in points if str(point["time"]) <= verified
            ],
        },
        "events": fetch_stock_events(trading_date, verified, chart_density_limit=False),
    }


def build_strategy_payload(current_market: dict[str, Any] | None = None, force: bool = False) -> dict[str, Any]:
    with _STRATEGY_LOCK:
        market = current_market or fetch_strategy_market()
        replay_result = capture_strategy_signals(market)
        current_date = str(market["date"])
        now = now_cn()
        current_minutes = now.hour * 60 + now.minute
        market_live = (
            current_date == now.date().isoformat()
            and now.weekday() < 5
            and ((570 <= current_minutes <= 690) or (780 <= current_minutes <= 900))
            and abs(current_minutes - minute_number(str(market["verifiedThrough"]))) <= 8
        )
        earliest = (dt.date.fromisoformat(current_date) - dt.timedelta(days=45)).isoformat()
        raw_rows = STORE.load_strategy_rows(earliest)
        trades: list[dict[str, Any]] = []
        signals_today = sum(1 for row in raw_rows if row["trading_date"] == current_date)
        for row in raw_rows:
            if not row.get("status"):
                continue
            entry = to_float(row.get("execution_price"))
            current_return = trade_return(entry, to_float(row.get("current_price")))
            close_return = trade_return(entry, to_float(row.get("close_price")))
            next_open_return = trade_return(entry, to_float(row.get("next_open_price")))
            next_0931_return = trade_return(entry, to_float(row.get("next_0931_price")))
            index_entry = to_float(row.get("index_entry_price"))
            index_current_return = trade_return(index_entry, to_float(row.get("index_current_price")))
            index_close_return = trade_return(index_entry, to_float(row.get("index_close_price")))
            index_next_open_return = trade_return(index_entry, to_float(row.get("index_next_open_price")))
            index_next_0931_return = trade_return(index_entry, to_float(row.get("index_next_0931_price")))
            trades.append({
                "date": str(row["trading_date"]), "code": str(row["stock_code"]), "market": int(row["market"]),
                "name": str(row["stock_name"]), "signalTime": str(row["signal_time"]),
                "event": str(row["event_label"]), "oneMinuteReturn": float(row["one_minute_return"]),
                "industryName": str(row["industry_name"]), "sectorChangePct": float(row["sector_change_pct"]),
                "sectorMainFlow": float(row["sector_main_flow"]), "score": float(row["strategy_score"]),
                "allocation": float(row.get("allocation") or STRATEGY_ALLOCATION), "status": str(row["status"]),
                "reason": str(row.get("rejection_reason") or ""), "executionTime": str(row.get("execution_time") or ""),
                "executionPrice": entry, "currentTime": str(row.get("current_time") or ""),
                "currentPrice": to_float(row.get("current_price")), "closePrice": to_float(row.get("close_price")),
                "nextOpenPrice": to_float(row.get("next_open_price")), "next0931Price": to_float(row.get("next_0931_price")),
                "currentReturn": current_return, "currentReturnAfterCost": trade_return(entry, to_float(row.get("current_price")), after_cost=True, realized=False),
                "closeReturn": close_return, "closeReturnAfterCost": trade_return(entry, to_float(row.get("close_price")), after_cost=True, realized=False),
                "nextOpenReturn": next_open_return, "nextOpenReturnAfterCost": trade_return(entry, to_float(row.get("next_open_price")), after_cost=True, realized=True),
                "next0931Return": next_0931_return, "next0931ReturnAfterCost": trade_return(entry, to_float(row.get("next_0931_price")), after_cost=True, realized=True),
                "indexCurrentReturn": index_current_return, "indexCloseReturn": index_close_return,
                "indexNextOpenReturn": index_next_open_return, "indexNext0931Return": index_next_0931_return,
            })
        today_trades = [trade for trade in trades if trade["date"] == current_date]
        valid_today = [trade for trade in today_trades if trade["executionPrice"] > 0 and trade["status"] != "unfilled"]
        live_portfolio = sum((trade["currentReturnAfterCost"] or 0) * trade["allocation"] for trade in valid_today)
        live_benchmark = sum((trade["indexCurrentReturn"] or 0) * trade["allocation"] for trade in valid_today)
        by_date: dict[str, list[dict[str, Any]]] = {}
        for trade in trades:
            by_date.setdefault(trade["date"], []).append(trade)
        close_values = [
            sum((trade["closeReturnAfterCost"] or 0) * trade["allocation"] for trade in day_trades)
            for day_trades in by_date.values()
            if any(trade["closeReturnAfterCost"] is not None for trade in day_trades)
        ]
        next_values = [
            sum((trade["next0931ReturnAfterCost"] or 0) * trade["allocation"] for trade in day_trades)
            for day_trades in by_date.values()
            if any(trade["next0931ReturnAfterCost"] is not None for trade in day_trades)
        ]
        next_open_values = [
            sum((trade["nextOpenReturnAfterCost"] or 0) * trade["allocation"] for trade in day_trades)
            for day_trades in by_date.values()
            if any(trade["nextOpenReturnAfterCost"] is not None for trade in day_trades)
        ]
        close_pairs = [
            (
                sum((trade["closeReturnAfterCost"] or 0) * trade["allocation"] for trade in day_trades),
                sum((trade["indexCloseReturn"] or 0) * trade["allocation"] for trade in day_trades),
            )
            for day_trades in by_date.values()
            if any(trade["closeReturnAfterCost"] is not None and trade["indexCloseReturn"] is not None for trade in day_trades)
        ]
        next_open_pairs = [
            (
                sum((trade["nextOpenReturnAfterCost"] or 0) * trade["allocation"] for trade in day_trades),
                sum((trade["indexNextOpenReturn"] or 0) * trade["allocation"] for trade in day_trades),
            )
            for day_trades in by_date.values()
            if any(trade["nextOpenReturnAfterCost"] is not None and trade["indexNextOpenReturn"] is not None for trade in day_trades)
        ]
        next_pairs = [
            (
                sum((trade["next0931ReturnAfterCost"] or 0) * trade["allocation"] for trade in day_trades),
                sum((trade["indexNext0931Return"] or 0) * trade["allocation"] for trade in day_trades),
            )
            for day_trades in by_date.values()
            if any(trade["next0931ReturnAfterCost"] is not None and trade["indexNext0931Return"] is not None for trade in day_trades)
        ]
        return {
            "date": current_date, "verifiedThrough": str(market["verifiedThrough"]), "updatedAt": now_cn().isoformat(timespec="seconds"),
            "mode": "观察性模拟", "isDemo": False, "captureStatus": "live" if market_live else "closed",
            "captureMessage": (
                f"已按时间顺序恢复至 {replay_result.get('processedThrough') or market['verifiedThrough']}；后续仅处理新增分钟"
                if replay_result.get("status") == "complete"
                else f"同日回放仍有 {int(replay_result.get('unresolved') or 0)} 个分钟候选待公开源核验；已保存的判断不会重写"
                if replay_result.get("status") == "partial"
                else "只恢复公开源仍能证明的同日分钟；不会推算更早交易日"
            ),
            "replayStatus": str(replay_result.get("status") or "unavailable"),
            "replayProcessedThrough": str(replay_result.get("processedThrough") or ""),
            "replaySourceEvents": int(replay_result.get("sourceEvents") or 0),
            "replayUnresolved": int(replay_result.get("unresolved") or 0),
            "signalsToday": signals_today, "tradesToday": len(today_trades),
            "filledToday": len(valid_today), "unfilledToday": sum(1 for trade in today_trades if trade["status"] == "unfilled"),
            "remainingSlots": max(0, STRATEGY_MAX_TRADES - sum(trade["status"] != "unfilled" for trade in today_trades)), "cashWeight": max(0.0, 1 - len(valid_today) * STRATEGY_ALLOCATION),
            "livePortfolioReturn": live_portfolio, "liveBenchmarkReturn": live_benchmark,
            "liveAlpha": live_portfolio - live_benchmark, "threshold": adaptive_strategy_threshold(current_date),
            "summary": {
                "closeMean": statistics.fmean(close_values) if close_values else None, "closeMedian": median_or_none(close_values),
                "closeWinRate": sum(value > 0 for value in close_values) / len(close_values) * 100 if close_values else None,
                "next0931Mean": statistics.fmean(next_values) if next_values else None, "next0931Median": median_or_none(next_values),
                "next0931WinRate": sum(value > 0 for value in next_values) / len(next_values) * 100 if next_values else None,
                "nextOpenMean": statistics.fmean(next_open_values) if next_open_values else None,
                "nextOpenMedian": median_or_none(next_open_values),
                "nextOpenWinRate": sum(value > 0 for value in next_open_values) / len(next_open_values) * 100 if next_open_values else None,
                "completedClose": len(close_values), "completedNext": len(next_values),
                "completedNextOpen": len(next_open_values),
                "closeBenchmarkMean": statistics.fmean(pair[1] for pair in close_pairs) if close_pairs else None,
                "closeAlphaMean": statistics.fmean(pair[0] - pair[1] for pair in close_pairs) if close_pairs else None,
                "nextOpenBenchmarkMean": statistics.fmean(pair[1] for pair in next_open_pairs) if next_open_pairs else None,
                "nextOpenAlphaMean": statistics.fmean(pair[0] - pair[1] for pair in next_open_pairs) if next_open_pairs else None,
                "next0931BenchmarkMean": statistics.fmean(pair[1] for pair in next_pairs) if next_pairs else None,
                "next0931AlphaMean": statistics.fmean(pair[0] - pair[1] for pair in next_pairs) if next_pairs else None,
            },
            "costs": {
                "commissionRate": STRATEGY_COMMISSION_RATE * 100, "regulatoryAndTransferRate": STRATEGY_TRANSFER_AND_REGULATORY_RATE * 100,
                "stampDutyRate": STRATEGY_STAMP_DUTY_RATE * 100, "slippagePerSide": STRATEGY_SLIPPAGE_RATE * 100,
                "entryEstimate": execution_cost_rate(realized=False) * 100,
                "roundTripEstimate": execution_cost_rate(realized=True) * 100,
            },
            "trades": trades,
            "method": "按信号时间顺序；一分钟涨幅≥0.8%，主营行业涨幅和资金净流入同时为正，成交额达门槛，综合分超过仅由此前交易日形成的阈值；下一真实分钟成交，单股每日一次，最多20笔，每笔5%。",
        }


def collect_strategy(force: bool = False) -> dict[str, Any]:
    # ``force`` is accepted for parity with the other endpoints. Strategy
    # decisions themselves are insert-only, so refresh can never rewrite one.
    return build_strategy_payload(force=force)


def validate_strategy_lab_config(raw: Any) -> dict[str, Any]:
    incoming = raw if isinstance(raw, dict) else {}
    config = {**STRATEGY_LAB_DEFAULT_CONFIG, **incoming}

    def bounded(name: str, minimum: float, maximum: float) -> float:
        return min(max(to_float(config.get(name), to_float(STRATEGY_LAB_DEFAULT_CONFIG[name])), minimum), maximum)

    def choice(name: str, allowed: set[str]) -> str:
        value = str(config.get(name) or STRATEGY_LAB_DEFAULT_CONFIG[name])
        return value if value in allowed else str(STRATEGY_LAB_DEFAULT_CONFIG[name])

    def time_choice(name: str) -> str:
        value = str(config.get(name) or STRATEGY_LAB_DEFAULT_CONFIG[name])
        if not re.fullmatch(r"\d{2}:\d{2}", value) or minute_number(value) < 0:
            return str(STRATEGY_LAB_DEFAULT_CONFIG[name])
        return value

    result = {
        "name": str(config.get("name") or "我的策略").strip()[:36] or "我的策略",
        "marketScope": choice("marketScope", {"all", "sh", "sz", "bj"}),
        "oneMinuteRise": round(bounded("oneMinuteRise", 0.1, 10.0), 2),
        "sectorFilter": choice("sectorFilter", {"both", "flow", "rise", "none"}),
        "minAmount": round(bounded("minAmount", 0, 5_000_000_000), 2),
        "minScore": round(bounded("minScore", 0, 100), 1),
        "startTime": time_choice("startTime"),
        "endTime": time_choice("endTime"),
        "buyDelayMinutes": int(choice("buyDelayMinutes", {"1", "2", "5"})),
        "entryPriceMode": choice("entryPriceMode", {"minute_open", "minute_close", "minute_average"}),
        "allocationMode": choice("allocationMode", {"fixed_pct", "equal_slots"}),
        "positionPct": round(bounded("positionPct", 1, 50), 1),
        "maxPositions": int(round(bounded("maxPositions", 1, 50))),
        "exitMode": choice("exitMode", {"next_open", "next_0931", "risk_close", "hold"}),
        "takeProfitPct": round(bounded("takeProfitPct", 0, 30), 2),
        "stopLossPct": round(bounded("stopLossPct", 0, 20), 2),
        "initialCapital": round(bounded("initialCapital", 10_000, 100_000_000), 2),
    }
    if result["startTime"] >= result["endTime"]:
        result["startTime"], result["endTime"] = "09:45", "14:50"
    return result


def strategy_lab_summary(config: dict[str, Any]) -> str:
    scope = {"all": "沪深京A股", "sh": "沪市A股", "sz": "深市A股", "bj": "北交所"}[config["marketScope"]]
    sector = {
        "both": "所属行业上涨且资金净流入",
        "flow": "所属行业资金净流入",
        "rise": "所属行业上涨",
        "none": "不使用行业过滤",
    }[config["sectorFilter"]]
    price = {"minute_open": "该分钟开盘价", "minute_close": "该分钟最新价", "minute_average": "该分钟均价"}[config["entryPriceMode"]]
    allocation = (
        f"每笔使用初始资金的 {config['positionPct']:.1f}%"
        if config["allocationMode"] == "fixed_pct"
        else "按剩余仓位平均分配现金"
    )
    exit_text = {
        "next_open": "次一交易日开盘卖出",
        "next_0931": "次一交易日09:31卖出",
        "risk_close": f"次一交易日起止盈{config['takeProfitPct']:.1f}%、止损{config['stopLossPct']:.1f}%，否则收盘卖出",
        "hold": "持续持有，不自动卖出",
    }[config["exitMode"]]
    return (
        f"{scope}中，一分钟涨幅达到 {config['oneMinuteRise']:.2f}%、{sector}、"
        f"累计成交额不少于 {config['minAmount'] / 100_000_000:.2f}亿元且评分达到 {config['minScore']:.1f} 时，"
        f"延迟 {config['buyDelayMinutes']} 分钟按{price}模拟买入；{allocation}，最多 {config['maxPositions']} 只；{exit_text}。"
    )


def strategy_lab_market_matches(code: str, market: int, scope: str) -> bool:
    if scope == "all":
        return True
    if scope == "sh":
        return market == 1 and code.startswith("6")
    if scope == "bj":
        return code.startswith(("4", "8", "92"))
    return market == 0 and not code.startswith(("4", "8", "92"))


def strategy_lab_signal_matches(row: dict[str, Any], config: dict[str, Any], verified: str) -> bool:
    signal_time = str(row.get("signal_time") or "")
    if not (config["startTime"] <= signal_time <= min(config["endTime"], verified)):
        return False
    code = str(row.get("stock_code") or "")
    market = int(row.get("market") or infer_stock_market(code))
    if not strategy_lab_market_matches(code, market, config["marketScope"]):
        return False
    if to_float(row.get("one_minute_return")) < config["oneMinuteRise"]:
        return False
    if to_float(row.get("liquidity_amount")) < config["minAmount"]:
        return False
    if to_float(row.get("strategy_score")) < config["minScore"]:
        return False
    sector_rise = to_float(row.get("sector_change_pct")) > 0
    sector_flow = to_float(row.get("sector_main_flow")) > 0
    return {
        "both": sector_rise and sector_flow,
        "flow": sector_flow,
        "rise": sector_rise,
        "none": True,
    }[config["sectorFilter"]]


def strategy_lab_execution_point(
    points: list[dict[str, Any]],
    signal_time: str,
    delay_minutes: int,
) -> dict[str, Any] | None:
    target = minute_number(signal_time) + delay_minutes
    return next((point for point in points if minute_number(str(point.get("time") or "")) >= target), None)


def strategy_lab_entry_price(point: dict[str, Any], mode: str) -> float:
    field = {"minute_open": "open", "minute_close": "price", "minute_average": "average"}[mode]
    return to_float(point.get(field)) or to_float(point.get("price"))


def strategy_lab_order_quantity(code: str, budget: float, available_cash: float, per_share_debit: float) -> int:
    if per_share_debit <= 0:
        return 0
    affordable = int(min(budget, available_cash) / per_share_debit)
    if code.startswith(("688", "689")):
        return affordable if affordable >= 200 else 0
    if code.startswith(("4", "8", "92")):
        return affordable if affordable >= 100 else 0
    return affordable // 100 * 100


def strategy_lab_prefetch_trends(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any] | Exception]:
    results: dict[str, dict[str, Any] | Exception] = {}
    unique = {str(row["stock_code"]): row for row in rows}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(fetch_verified_stock_trends, code, int(row.get("market") or infer_stock_market(code))): code
            for code, row in unique.items()
        }
        for future in concurrent.futures.as_completed(futures):
            code = futures[future]
            try:
                results[code] = future.result()
            except Exception as exc:
                results[code] = exc
    return results


def build_strategy_lab_preview(
    config: dict[str, Any],
    market: dict[str, Any],
) -> dict[str, Any]:
    trading_date = str(market["date"])
    verified = str(market["verifiedThrough"])
    rows = [
        row for row in STORE.load_strategy_signals_for_date(trading_date)
        if strategy_lab_signal_matches(row, config, verified)
    ]
    trends = strategy_lab_prefetch_trends(rows)
    initial_cash = float(config["initialCapital"])
    cash = initial_cash
    trades: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    failed = 0
    fees = 0.0
    for row in rows:
        if len(trades) >= int(config["maxPositions"]):
            break
        code = str(row["stock_code"])
        trend = trends.get(code)
        if not isinstance(trend, dict):
            failed += 1
            continue
        points = [
            point for point in trend.get("points") or []
            if str(point.get("date") or trading_date) == trading_date and str(point.get("time") or "") <= verified
        ]
        execution = strategy_lab_execution_point(points, str(row["signal_time"]), int(config["buyDelayMinutes"]))
        if not execution:
            failed += 1
            continue
        raw_price = strategy_lab_entry_price(execution, str(config["entryPriceMode"]))
        if raw_price <= 0 or is_probably_unbuyable_limit_up(code, to_float(trend.get("preClose")), execution, point_at_or_before(points, str(row["signal_time"]))):
            failed += 1
            continue
        price = raw_price * (1 + STRATEGY_SLIPPAGE_RATE)
        remaining_slots = max(1, int(config["maxPositions"]) - len(trades))
        budget = (
            cash / remaining_slots
            if config["allocationMode"] == "equal_slots"
            else initial_cash * to_float(config["positionPct"]) / 100
        )
        per_share_debit = price * (1 + STRATEGY_COMMISSION_RATE + STRATEGY_TRANSFER_AND_REGULATORY_RATE)
        quantity = strategy_lab_order_quantity(code, budget, cash, per_share_debit)
        if quantity <= 0:
            failed += 1
            continue
        gross = quantity * price
        entry_cost = gross * (STRATEGY_COMMISSION_RATE + STRATEGY_TRANSFER_AND_REGULATORY_RATE)
        debit = gross + entry_cost
        cash -= debit
        fees += entry_cost + quantity * raw_price * STRATEGY_SLIPPAGE_RATE
        latest = points[-1]
        latest_price = to_float(latest.get("price"))
        trade = {
            "code": code, "market": int(row.get("market") or 0), "name": str(row["stock_name"]),
            "signalTime": str(row["signal_time"]), "executionTime": str(execution["time"]),
            "executionPrice": price, "rawExecutionPrice": raw_price, "quantity": quantity,
            "entryCost": entry_cost, "debit": debit, "currentPrice": latest_price,
            "currentValue": quantity * latest_price,
            "unrealizedPnl": quantity * latest_price - debit,
            "unrealizedReturn": (latest_price / price - 1) * 100 if price else 0,
            "industryName": str(row.get("industry_name") or ""),
            "sectorChangePct": to_float(row.get("sector_change_pct")),
            "sectorMainFlow": to_float(row.get("sector_main_flow")),
            "oneMinuteReturn": to_float(row.get("one_minute_return")),
            "score": to_float(row.get("strategy_score")), "status": "open",
        }
        trades.append({**trade, "_points": points})
        events.append({
            "date": trading_date, "time": str(execution["time"]), "type": "buy",
            "code": code, "name": str(row["stock_name"]), "title": f"买入 {row['stock_name']}",
            "detail": f"{row['event_label']} · 一分钟 {to_float(row['one_minute_return']):+.2f}% · {quantity}股",
            "price": price, "quantity": quantity,
        })

    index_points = list((market.get("index") or {}).get("points") or [])
    chart_times = [
        str(point["time"]) for point in index_points
        if str(point.get("time") or "") <= verified
        and str(point.get("time") or "") >= "09:30"
        and (minute_number(str(point["time"])) % 5 == 0 or str(point["time"]) == verified)
    ]
    if verified and verified not in chart_times:
        chart_times.append(verified)
    benchmark_base = to_float(index_points[0].get("value")) if index_points else 0
    equity: list[dict[str, Any]] = []
    for label in sorted(set(chart_times)):
        point_cash = initial_cash
        market_value = 0.0
        for trade in trades:
            if str(trade["executionTime"]) > label:
                continue
            point_cash -= to_float(trade["debit"])
            price_point = point_at_or_before(list(trade["_points"]), label)
            market_value += int(trade["quantity"]) * to_float((price_point or {}).get("price"), to_float(trade["executionPrice"]))
        portfolio = point_cash + market_value
        index_point = point_at_or_before(index_points, label)
        benchmark_value = to_float((index_point or {}).get("value"))
        equity.append({
            "date": trading_date, "time": label, "portfolioValue": portfolio,
            "cash": point_cash, "marketValue": market_value,
            "returnPct": (portfolio / initial_cash - 1) * 100,
            "benchmarkReturnPct": (benchmark_value / benchmark_base - 1) * 100 if benchmark_base else 0,
        })
    clean_trades = [{key: value for key, value in trade.items() if key != "_points"} for trade in trades]
    latest_portfolio = equity[-1]["portfolioValue"] if equity else initial_cash
    market_value = sum(to_float(trade["currentValue"]) for trade in clean_trades)
    return {
        "date": trading_date, "verifiedThrough": verified, "initialCapital": initial_cash,
        "portfolioValue": latest_portfolio, "cash": cash, "marketValue": market_value,
        "returnPct": (latest_portfolio / initial_cash - 1) * 100,
        "benchmarkReturnPct": equity[-1]["benchmarkReturnPct"] if equity else 0,
        "fees": fees, "signalsMatched": len(rows), "tradesFilled": len(clean_trades),
        "failedOrders": failed, "openPositions": len(clean_trades),
        "winningPositions": sum(to_float(trade["unrealizedPnl"]) > 0 for trade in clean_trades),
        "trades": clean_trades, "events": events, "equity": equity,
        "notice": "今日回放独立使用真实分钟数据，不会写入持续模拟账户；当日买入受T+1约束，仅显示浮动盈亏。",
    }


def strategy_lab_active_config(state: dict[str, Any]) -> dict[str, Any]:
    account = state.get("account") or {}
    version_id = int(account.get("active_version_id") or 0)
    version = next((item for item in state.get("versions") or [] if int(item.get("id") or 0) == version_id), None)
    return validate_strategy_lab_config((version or {}).get("config") or STRATEGY_LAB_DEFAULT_CONFIG)


def recent_strategy_trading_dates() -> list[str]:
    cache_key = "strategy-lab-trading-calendar"
    cached = read_cache(cache_key, max_age_seconds=6 * 3600)
    if cached and isinstance(cached.get("dates"), list):
        return [str(value) for value in cached["dates"]]
    result = fetch_stock_daily("000985", 1)
    dates = sorted({str(point.get("date") or "") for point in result.get("points") or [] if point.get("date")})
    if not dates:
        raise DataSourceError("未取得真实交易日历")
    write_cache(cache_key, {"dates": dates, "source": result.get("source", "")})
    return dates


def strategy_lab_exit_point(
    position: dict[str, Any],
    points: list[dict[str, Any]],
    verified: str,
    *,
    is_next_trading_day: bool = True,
) -> tuple[dict[str, Any] | None, str]:
    if str(position.get("entry_date") or "") >= str((points[0] if points else {}).get("date") or ""):
        return None, ""
    if not is_next_trading_day:
        return None, ""
    eligible = [point for point in points if str(point.get("time") or "") <= verified]
    if not eligible:
        return None, ""
    mode = str(position.get("exit_mode") or "hold")
    if mode == "next_open":
        return eligible[0], "次一交易日开盘"
    if mode == "next_0931":
        point = next((item for item in eligible if str(item.get("time") or "") >= "09:31"), None)
        return point, "次一交易日09:31" if point else ""
    if mode == "risk_close":
        entry = to_float(position.get("entry_price"))
        take = entry * (1 + to_float(position.get("take_profit_pct")) / 100)
        stop = entry * (1 - to_float(position.get("stop_loss_pct")) / 100)
        for point in eligible:
            price = to_float(point.get("price"))
            if to_float(position.get("take_profit_pct")) > 0 and price >= take:
                return point, "止盈触发"
            if to_float(position.get("stop_loss_pct")) > 0 and price <= stop:
                return point, "止损触发"
        if verified >= "15:00":
            return eligible[-1], "次一交易日收盘"
    return None, ""


def process_continuous_strategy_lab(market: dict[str, Any]) -> None:
    state = STORE.load_strategy_lab_state()
    account = state.get("account")
    if not account:
        return
    trading_date = str(market["date"])
    verified = str(market["verifiedThrough"])
    updated_at = now_cn().isoformat(timespec="seconds")
    config = strategy_lab_active_config(state)
    index_points = list((market.get("index") or {}).get("points") or [])
    index_latest = point_at_or_before(index_points, verified)

    previous_date = str(account.get("last_processed_date") or "")
    previous_time = str(account.get("last_processed_time") or "")
    if previous_date and previous_date < trading_date and previous_time and previous_time < "15:00":
        STORE.append_strategy_lab_event({
            "occurred_at": updated_at, "trading_date": trading_date, "event_time": "09:30",
            "event_type": "data_gap", "title": "上一交易日存在未采集区间",
            "detail": f"{previous_date} 仅处理至 {previous_time}；缺失区间未估算，也未补造交易。",
            "strategy_version_id": int(account.get("active_version_id") or 0),
        })

    # Existing positions keep the exit rule from the version that opened them.
    current_positions = [item for item in state.get("positions") or [] if item.get("status") == "open"]
    needs_exit_calendar = any(
        str(item.get("entry_date") or "") < trading_date and str(item.get("exit_mode") or "hold") != "hold"
        for item in current_positions
    )
    trading_dates: list[str] = []
    if needs_exit_calendar:
        try:
            trading_dates = recent_strategy_trading_dates()
        except Exception:
            trading_dates = []
    for position in current_positions:
        try:
            trend = fetch_verified_stock_trends(str(position["stock_code"]), int(position.get("market") or 0))
            points = [
                point for point in trend.get("points") or []
                if str(point.get("date") or "") == trading_date and str(point.get("time") or "") <= verified
            ]
            if not points:
                continue
            latest = points[-1]
            STORE.mark_strategy_lab_position(int(position["id"]), to_float(latest["price"]), str(latest["time"]), updated_at)
            entry_date = str(position.get("entry_date") or "")
            expected_exit_date = next((date for date in trading_dates if date > entry_date), "")
            is_next_trading_day = bool(expected_exit_date and expected_exit_date == trading_date)
            if (
                expected_exit_date
                and trading_date > expected_exit_date
                and previous_date < trading_date
                and str(position.get("exit_mode") or "hold") != "hold"
            ):
                STORE.append_strategy_lab_event({
                    "occurred_at": updated_at, "trading_date": trading_date, "event_time": str(latest["time"]),
                    "event_type": "data_gap", "stock_code": str(position["stock_code"]),
                    "stock_name": str(position["stock_name"]), "title": f"未补造 {position['stock_name']} 的历史卖出",
                    "detail": f"规则要求在 {expected_exit_date} 执行，但应用未取得该日所需的精确分钟价；持仓继续保留，不用 {trading_date} 的价格冒充。",
                    "strategy_version_id": int(position["strategy_version_id"]),
                })
            exit_point, reason = strategy_lab_exit_point(
                position, points, verified, is_next_trading_day=is_next_trading_day,
            )
            if exit_point:
                raw_exit = to_float(exit_point["price"])
                exit_price = raw_exit * (1 - STRATEGY_SLIPPAGE_RATE)
                gross = int(position["quantity"]) * exit_price
                exit_cost = gross * (STRATEGY_COMMISSION_RATE + STRATEGY_TRANSFER_AND_REGULATORY_RATE + STRATEGY_STAMP_DUTY_RATE)
                sold = STORE.sell_strategy_lab_position(
                    int(position["id"]), trading_date, str(exit_point["time"]), exit_price, exit_cost, updated_at,
                )
                if sold:
                    STORE.append_strategy_lab_event({
                        "occurred_at": updated_at, "trading_date": trading_date, "event_time": str(exit_point["time"]),
                        "event_type": "sell", "stock_code": str(position["stock_code"]),
                        "stock_name": str(position["stock_name"]), "title": f"卖出 {position['stock_name']}",
                        "detail": f"{reason} · 已实现盈亏 {to_float(sold['realized_pnl']):+,.2f}元",
                        "price": exit_price, "quantity": int(position["quantity"]), "amount": gross - exit_cost,
                        "strategy_version_id": int(position["strategy_version_id"]),
                    })
        except Exception:
            continue

    state = STORE.load_strategy_lab_state()
    account = state.get("account") or account
    if str(account.get("status")) == "running":
        cursor = previous_time if previous_date == trading_date else "09:29"
        rows = [
            row for row in STORE.load_strategy_signals_for_date(trading_date)
            if str(row.get("signal_time") or "") > cursor and strategy_lab_signal_matches(row, config, verified)
        ]
        trends = strategy_lab_prefetch_trends(rows)
        open_codes = {str(item["stock_code"]) for item in state.get("positions") or [] if item.get("status") == "open"}
        open_count = len(open_codes)
        retry_signal_times: list[str] = []
        for row in rows:
            if open_count >= int(config["maxPositions"]):
                break
            code = str(row["stock_code"])
            if code in open_codes:
                continue
            trend = trends.get(code)
            if not isinstance(trend, dict):
                retry_signal_times.append(str(row["signal_time"]))
                continue
            points = [point for point in trend.get("points") or [] if str(point.get("date") or "") == trading_date and str(point.get("time") or "") <= verified]
            if not points:
                retry_signal_times.append(str(row["signal_time"]))
                continue
            execution = strategy_lab_execution_point(points, str(row["signal_time"]), int(config["buyDelayMinutes"]))
            if not execution:
                if verified < "15:00":
                    retry_signal_times.append(str(row["signal_time"]))
                else:
                    STORE.append_strategy_lab_event({
                        "occurred_at": updated_at, "trading_date": trading_date,
                        "event_time": str(row["signal_time"]), "event_type": "order_unfilled",
                        "stock_code": code, "stock_name": str(row["stock_name"]),
                        "title": f"未成交 {row['stock_name']}",
                        "detail": "收盘前没有取得满足延迟条件的后续真实分钟成交，未建立持仓。",
                        "strategy_version_id": int(account.get("active_version_id") or 0),
                    })
                continue
            raw_price = strategy_lab_entry_price(execution, str(config["entryPriceMode"]))
            if raw_price <= 0 or is_probably_unbuyable_limit_up(code, to_float(trend.get("preClose")), execution, point_at_or_before(points, str(row["signal_time"]))):
                continue
            price = raw_price * (1 + STRATEGY_SLIPPAGE_RATE)
            refreshed = STORE.load_strategy_lab_state()
            account_now = refreshed.get("account") or account
            cash_now = to_float(account_now.get("cash"))
            remaining_slots = max(1, int(config["maxPositions"]) - open_count)
            budget = cash_now / remaining_slots if config["allocationMode"] == "equal_slots" else to_float(account_now.get("initial_cash")) * to_float(config["positionPct"]) / 100
            per_share_debit = price * (1 + STRATEGY_COMMISSION_RATE + STRATEGY_TRANSFER_AND_REGULATORY_RATE)
            quantity = strategy_lab_order_quantity(code, budget, cash_now, per_share_debit)
            if quantity <= 0:
                continue
            gross = quantity * price
            entry_cost = gross * (STRATEGY_COMMISSION_RATE + STRATEGY_TRANSFER_AND_REGULATORY_RATE)
            debit = gross + entry_cost
            position_id = STORE.buy_strategy_lab_position({
                "stock_code": code, "market": int(row.get("market") or 0), "stock_name": str(row["stock_name"]),
                "quantity": quantity, "entry_date": trading_date, "entry_time": str(execution["time"]),
                "entry_price": price, "entry_cost": entry_cost,
                "strategy_version_id": int(account_now.get("active_version_id") or 0),
                "exit_mode": str(config["exitMode"]), "take_profit_pct": to_float(config["takeProfitPct"]),
                "stop_loss_pct": to_float(config["stopLossPct"]), "updated_at": updated_at,
            }, debit)
            if position_id:
                open_codes.add(code)
                open_count += 1
                STORE.append_strategy_lab_event({
                    "occurred_at": updated_at, "trading_date": trading_date, "event_time": str(execution["time"]),
                    "event_type": "buy", "stock_code": code, "stock_name": str(row["stock_name"]),
                    "title": f"买入 {row['stock_name']}",
                    "detail": f"{row['event_label']} · 一分钟 {to_float(row['one_minute_return']):+.2f}% · {quantity}股",
                    "price": price, "quantity": quantity, "amount": debit,
                    "strategy_version_id": int(account_now.get("active_version_id") or 0),
                })

    processed_through = verified
    if str(account.get("status")) == "running" and retry_signal_times:
        retry_minute = max(0, minute_number(min(retry_signal_times)) - 1)
        processed_through = f"{retry_minute // 60:02d}:{retry_minute % 60:02d}"
    benchmark_start = to_float(account.get("benchmark_start")) or to_float((index_latest or {}).get("value"))
    STORE.update_strategy_lab_progress(trading_date, processed_through, updated_at, benchmark_start)
    final_state = STORE.load_strategy_lab_state()
    final_account = final_state.get("account") or account
    open_positions = [item for item in final_state.get("positions") or [] if item.get("status") == "open"]
    market_value = sum(int(item["quantity"]) * to_float(item.get("last_price"), to_float(item.get("entry_price"))) for item in open_positions)
    portfolio = to_float(final_account.get("cash")) + market_value
    initial_cash = to_float(final_account.get("initial_cash"))
    benchmark_value = to_float((index_latest or {}).get("value"))
    STORE.save_strategy_lab_equity({
        "trading_date": trading_date, "point_time": verified, "portfolio_value": portfolio,
        "cash": to_float(final_account.get("cash")), "market_value": market_value,
        "benchmark_value": benchmark_value,
        "return_pct": (portfolio / initial_cash - 1) * 100 if initial_cash else 0,
        "benchmark_return_pct": (benchmark_value / benchmark_start - 1) * 100 if benchmark_start else 0,
        "source": "真实分钟成交与持仓估值", "recorded_at": updated_at,
    })


def serialize_strategy_lab_state(
    state: dict[str, Any],
    market: dict[str, Any],
    preview: dict[str, Any] | None = None,
) -> dict[str, Any]:
    account = state.get("account")
    positions: list[dict[str, Any]] = []
    for item in state.get("positions") or []:
        quantity = int(item.get("quantity") or 0)
        entry_value = quantity * to_float(item.get("entry_price")) + to_float(item.get("entry_cost"))
        current_value = quantity * to_float(item.get("last_price"), to_float(item.get("entry_price")))
        pnl = to_float(item.get("realized_pnl")) if item.get("status") == "closed" else current_value - entry_value
        positions.append({
            "id": int(item["id"]), "code": str(item["stock_code"]), "market": int(item["market"]),
            "name": str(item["stock_name"]), "quantity": quantity, "entryDate": str(item["entry_date"]),
            "entryTime": str(item["entry_time"]), "entryPrice": to_float(item["entry_price"]),
            "entryCost": to_float(item["entry_cost"]), "status": str(item["status"]),
            "lastPrice": to_float(item["last_price"]), "lastPriceTime": str(item["last_price_time"]),
            "exitDate": str(item["exit_date"]), "exitTime": str(item["exit_time"]),
            "exitPrice": to_float(item["exit_price"]), "exitCost": to_float(item["exit_cost"]),
            "pnl": pnl, "returnPct": pnl / entry_value * 100 if entry_value else 0,
            "strategyVersionId": int(item["strategy_version_id"]), "exitMode": str(item["exit_mode"]),
        })
    open_positions = [item for item in positions if item["status"] == "open"]
    closed_positions = [item for item in positions if item["status"] == "closed"]
    market_value = sum(item["quantity"] * item["lastPrice"] for item in open_positions)
    cash = to_float((account or {}).get("cash"))
    portfolio = cash + market_value if account else 0
    initial_cash = to_float((account or {}).get("initial_cash"))
    versions = [
        {
            "id": int(item["id"]), "createdAt": str(item["created_at"]),
            "effectiveDate": str(item["effective_date"]), "effectiveTime": str(item["effective_time"]),
            "summary": str(item["summary"]), "config": item["config"],
        }
        for item in state.get("versions") or []
    ]
    active_config = strategy_lab_active_config(state) if account else validate_strategy_lab_config({})
    return {
        "date": str(market["date"]), "verifiedThrough": str(market["verifiedThrough"]),
        "updatedAt": now_cn().isoformat(timespec="seconds"), "source": STOCK_SOURCE_NAME,
        "defaultConfig": validate_strategy_lab_config({}), "activeConfig": active_config,
        "strategySummary": strategy_lab_summary(active_config), "preview": preview,
        "account": None if not account else {
            "status": str(account["status"]), "initialCash": initial_cash, "cash": cash,
            "portfolioValue": portfolio, "marketValue": market_value,
            "returnPct": (portfolio / initial_cash - 1) * 100 if initial_cash else 0,
            "realizedPnl": sum(item["pnl"] for item in closed_positions),
            "unrealizedPnl": sum(item["pnl"] for item in open_positions),
            "openPositions": len(open_positions), "closedTrades": len(closed_positions),
            "startedAt": str(account["started_at"]), "activeVersionId": int(account["active_version_id"]),
            "lastProcessedDate": str(account["last_processed_date"]),
            "lastProcessedTime": str(account["last_processed_time"]),
        },
        "positions": positions,
        "versions": versions,
        "events": [
            {
                "id": int(item["id"]), "date": str(item["trading_date"]), "time": str(item["event_time"]),
                "type": str(item["event_type"]), "code": str(item["stock_code"]), "name": str(item["stock_name"]),
                "title": str(item["title"]), "detail": str(item["detail"]), "price": to_float(item["price"]),
                "quantity": int(item["quantity"]), "amount": to_float(item["amount"]),
                "strategyVersionId": int(item["strategy_version_id"]),
            }
            for item in state.get("events") or []
        ],
        "equity": [
            {
                "date": str(item["trading_date"]), "time": str(item["point_time"]),
                "portfolioValue": to_float(item["portfolio_value"]), "cash": to_float(item["cash"]),
                "marketValue": to_float(item["market_value"]), "returnPct": to_float(item["return_pct"]),
                "benchmarkReturnPct": to_float(item["benchmark_return_pct"]),
            }
            for item in state.get("equity") or []
        ],
        "costs": {
            "commissionRate": STRATEGY_COMMISSION_RATE * 100,
            "regulatoryAndTransferRate": STRATEGY_TRANSFER_AND_REGULATORY_RATE * 100,
            "stampDutyRate": STRATEGY_STAMP_DUTY_RATE * 100,
            "slippagePerSide": STRATEGY_SLIPPAGE_RATE * 100,
        },
        "dataNotice": "只使用各时点已经出现的真实信号和分钟价格；缺失交易日不会估算。若错过规则要求的次日精确分钟价，持仓会保留并记录缺口，不会用更晚日期冒充。持续模拟不连接券商，不会产生真实订单。",
    }


def prepare_strategy_lab_market() -> dict[str, Any]:
    market = fetch_strategy_market()
    with _STRATEGY_LOCK:
        capture_strategy_signals(market)
    return market


def collect_strategy_lab() -> dict[str, Any]:
    with _STRATEGY_LAB_LOCK:
        market = prepare_strategy_lab_market()
        process_continuous_strategy_lab(market)
        return serialize_strategy_lab_state(STORE.load_strategy_lab_state(), market)


def handle_strategy_lab_action(payload: dict[str, Any]) -> dict[str, Any]:
    with _STRATEGY_LAB_LOCK:
        action = str(payload.get("action") or "preview")
        market = prepare_strategy_lab_market()
        config = validate_strategy_lab_config(payload.get("config"))
        if action == "preview":
            preview = build_strategy_lab_preview(config, market)
            return serialize_strategy_lab_state(STORE.load_strategy_lab_state(), market, preview)
        if action in {"start", "update", "resume"}:
            # A rule change becomes effective at the current verified minute.
            # First finish any unprocessed interval with the previous version,
            # so clicking “save” cannot silently skip valid old-rule signals.
            before_update = STORE.load_strategy_lab_state().get("account")
            if before_update:
                process_continuous_strategy_lab(market)
                processed = STORE.load_strategy_lab_state().get("account") or {}
                if (
                    action == "update"
                    and str(before_update.get("status")) == "running"
                    and str(processed.get("last_processed_date")) == str(market["date"])
                    and str(processed.get("last_processed_time")) < str(market["verifiedThrough"])
                ):
                    raise DataSourceError("仍有分钟信号等待真实成交价核验；请稍后重试，再保存新规则。")
            effective_date = str(market["date"])
            effective_time = str(market["verifiedThrough"])
            created_at = now_cn().isoformat(timespec="seconds")
            STORE.start_or_update_strategy_lab(
                config, strategy_lab_summary(config), to_float(config["initialCapital"]),
                effective_date, effective_time, created_at,
            )
            index_point = point_at_or_before(list((market.get("index") or {}).get("points") or []), effective_time)
            STORE.update_strategy_lab_progress(
                effective_date, effective_time, created_at, to_float((index_point or {}).get("value")),
            )
            process_continuous_strategy_lab(market)
            return serialize_strategy_lab_state(STORE.load_strategy_lab_state(), market)
        if action == "pause":
            STORE.set_strategy_lab_status("paused", str(market["date"]), str(market["verifiedThrough"]), now_cn().isoformat(timespec="seconds"))
            process_continuous_strategy_lab(market)
            return serialize_strategy_lab_state(STORE.load_strategy_lab_state(), market)
        raise ValueError("不支持的策略模拟操作")


def cached_stock_page(payload_kind: str, identity: str, warning: str) -> dict[str, Any] | None:
    cached = STORE.load_latest_payload(payload_kind, identity)
    if cached:
        result = dict(cached)
        result["warning"] = warning
        result["isStale"] = True
        return result
    return None


def build_stock_market(force: bool = False) -> dict[str, Any]:
    cache_key = "stock-market-latest"
    if not force:
        cached = read_cache(cache_key, max_age_seconds=55)
        if cached and not cached.get("isDemo"):
            return cached
    try:
        broad = fetch_broad_market_trend()
        points = broad["points"]
        trading_date = str(points[-1]["date"])
        verified = str(points[-1]["time"])
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            speed_future = executor.submit(build_exact_speed_rankings, verified)
            turnover_future = executor.submit(fetch_turnover_ranking)
            events_future = executor.submit(fetch_stock_events, trading_date, verified)
            fastest_rise, fastest_fall = speed_future.result()
            turnover = turnover_future.result()
            events = events_future.result()
        pre_close = to_float(broad.get("preClose"))
        index_points = [
            {
                "date": point["date"],
                "time": point["time"],
                "value": point["price"],
                "changePct": (point["price"] / pre_close - 1) * 100 if pre_close else 0,
            }
            for point in points
            if point["time"] <= verified
        ]
        result = {
            "date": trading_date,
            "updatedAt": f"{trading_date}T{verified}:00",
            "verifiedThrough": verified,
            "source": STOCK_SOURCE_NAME,
            "isDemo": False,
            "isStale": False,
            "warning": "",
            "index": {
                "code": "000985",
                "name": "中证全指",
                "preClose": pre_close,
                "points": index_points,
            },
            "events": events,
            "fastestRise": fastest_rise,
            "fastestFall": fastest_fall,
            "highestTurnover": turnover,
            "rankingMethod": "东方财富全市场短周期异动候选池（上涨/下跌各80只）中，以真实相邻分钟成交价重新计算一分钟涨跌速",
        }
        write_cache(cache_key, result)
        STORE.save_payload(
            "stock-market",
            "",
            trading_date,
            verified,
            STOCK_SOURCE_NAME,
            now_cn().isoformat(timespec="seconds"),
            result,
        )
        return result
    except Exception as exc:
        cached = read_cache(cache_key) or cached_stock_page(
            "stock-market", "", f"公开行情暂时无法完整核验，显示本机最近一次成功记录：{exc}"
        )
        if cached and not cached.get("isDemo"):
            result = dict(cached)
            result["warning"] = f"公开行情暂时无法完整核验，显示本机最近一次成功记录：{exc}"
            result["isStale"] = True
            return result
        raise DataSourceError(f"个股异动页未取得完整真实行情：{exc}") from exc


def fetch_eastmoney_stock_quote(code: str, market: int) -> dict[str, Any]:
    payload = fetch_eastmoney_json(
        "/api/qt/stock/get",
        {
            "secid": stock_secid(code, market),
            "fltt": 2,
            "invt": 2,
            "fields": "f57,f58,f43,f170,f44,f45,f46,f60,f47,f48,f168,f162,f167,f116,f117,f124",
            "ut": EASTMONEY_UT,
        },
        prefer_delayed=True,
        attempts=2,
    )
    data = payload.get("data") or {}
    if not data:
        raise DataSourceError(f"{code} 个股快照为空")
    market_cap = to_float(data.get("f116"))
    float_market_cap = to_float(data.get("f117"))
    # With fltt=2 these two fields are commonly returned in 亿元 while amount
    # remains yuan. Normalize everything to yuan for one truthful UI formatter.
    if 0 < market_cap < 10_000_000:
        market_cap *= 100_000_000
    if 0 < float_market_cap < 10_000_000:
        float_market_cap *= 100_000_000
    return {
        "code": code,
        "market": market,
        "name": str(data.get("f58") or code),
        "price": to_float(data.get("f43")),
        "changePct": to_float(data.get("f170")),
        "high": to_float(data.get("f44")),
        "low": to_float(data.get("f45")),
        "open": to_float(data.get("f46")),
        "preClose": to_float(data.get("f60")),
        # Eastmoney reports A-share volume in hands; normalize to shares so all
        # providers and the order book use the same unit.
        "volume": to_float(data.get("f47")) * 100,
        "amount": to_float(data.get("f48")),
        "turnover": to_float(data.get("f168")),
        "pe": to_float(data.get("f162")),
        "pb": to_float(data.get("f167")),
        "marketCap": market_cap,
        "floatMarketCap": float_market_cap,
        "bidPrice": 0.0,
        "bidVolume": 0.0,
        "askPrice": 0.0,
        "askVolume": 0.0,
        "bidLevels": [],
        "askLevels": [],
        "sourceTime": "",
        "source": STOCK_SOURCE_NAME,
    }


def fetch_sina_stock_quote(code: str, market: int) -> dict[str, Any]:
    """Fetch the public Sina quote and its five bid/ask levels."""
    symbol = public_stock_symbol(code, market)
    raw = fetch_text_url(
        f"https://hq.sinajs.cn/list={symbol}",
        encoding="gb18030",
        referer="https://finance.sina.com.cn/",
        attempts=2,
    )
    match = re.search(r'="(.*)";?\s*$', raw.strip())
    if not match:
        raise DataSourceError(f"新浪财经未返回 {code} 的个股快照")
    parts = match.group(1).split(",")
    if len(parts) < 32 or not parts[0]:
        raise DataSourceError(f"新浪财经 {code} 快照字段不足")
    bid_levels = [
        {"level": index + 1, "volume": to_float(parts[10 + index * 2]), "price": to_float(parts[11 + index * 2])}
        for index in range(5)
    ]
    ask_levels = [
        {"level": index + 1, "volume": to_float(parts[20 + index * 2]), "price": to_float(parts[21 + index * 2])}
        for index in range(5)
    ]
    price = to_float(parts[3])
    pre_close = to_float(parts[2])
    return {
        "code": code,
        "market": market,
        "name": parts[0],
        "price": price,
        "changePct": (price / pre_close - 1) * 100 if pre_close else 0.0,
        "high": to_float(parts[4]),
        "low": to_float(parts[5]),
        "open": to_float(parts[1]),
        "preClose": pre_close,
        "volume": to_float(parts[8]),
        "amount": to_float(parts[9]),
        "turnover": 0.0,
        "pe": 0.0,
        "pb": 0.0,
        "marketCap": 0.0,
        "floatMarketCap": 0.0,
        "bidPrice": to_float(parts[6]) or bid_levels[0]["price"],
        "bidVolume": bid_levels[0]["volume"],
        "askPrice": to_float(parts[7]) or ask_levels[0]["price"],
        "askVolume": ask_levels[0]["volume"],
        "bidLevels": bid_levels,
        "askLevels": ask_levels,
        "sourceTime": f"{parts[30]} {parts[31]}" if len(parts) > 31 else "",
        "source": SINA_STOCK_SOURCE,
    }


def stable_quote_fields_match(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Compare fields that should not drift because of a delayed last price."""
    for key in ("open", "preClose"):
        left, right = to_float(first.get(key)), to_float(second.get(key))
        if left <= 0 or right <= 0:
            continue
        if abs(left - right) > max(0.02, left * 0.002):
            return False
    return True


def fetch_stock_quote(code: str, market: int) -> dict[str, Any]:
    eastmoney: dict[str, Any] | None = None
    sina: dict[str, Any] | None = None
    errors: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            "em": executor.submit(fetch_eastmoney_stock_quote, code, market),
            "sina": executor.submit(fetch_sina_stock_quote, code, market),
        }
        for provider, future in futures.items():
            try:
                if provider == "em":
                    eastmoney = future.result()
                else:
                    sina = future.result()
            except Exception as exc:
                errors.append(f"{provider}: {exc}")
    if eastmoney and sina:
        if not stable_quote_fields_match(eastmoney, sina):
            # Both values are still real, but do not claim a successful check.
            result = dict(sina)
            result["verificationNote"] = "两源开盘/昨收字段不一致，采用新浪快照并明确标注"
            result["verifiedBy"] = [SINA_STOCK_SOURCE]
            return result
        result = dict(eastmoney)
        for key in ("bidPrice", "bidVolume", "askPrice", "askVolume", "bidLevels", "askLevels", "sourceTime"):
            result[key] = sina[key]
        result["verifiedBy"] = [STOCK_SOURCE_NAME, SINA_STOCK_SOURCE]
        result["source"] = f"{STOCK_SOURCE_NAME} · {SINA_STOCK_SOURCE}交叉核验"
        return result
    if eastmoney:
        eastmoney["verifiedBy"] = [STOCK_SOURCE_NAME]
        eastmoney["verificationNote"] = "新浪快照暂不可用；买卖盘缺失字段不作推算"
        return eastmoney
    if sina:
        sina["verifiedBy"] = [SINA_STOCK_SOURCE]
        sina["verificationNote"] = "东方财富快照暂不可用；估值字段缺失时显示 --"
        return sina
    raise DataSourceError("；".join(errors) or f"{code} 个股快照为空")


def add_daily_rolling_means(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    closes = [row["close"] for row in rows]
    ma5 = rolling_mean(closes, 5)
    ma20 = rolling_mean(closes, 20)
    return [{**row, "ma5": ma5[index], "ma20": ma20[index]} for index, row in enumerate(rows)]


def fetch_eastmoney_stock_daily(code: str, market: int) -> dict[str, Any]:
    cache_key = f"stock-daily-{market}-{code}"
    cached = read_cache(cache_key, max_age_seconds=300)
    if cached and isinstance(cached, dict) and len(cached.get("points") or []) >= 20:
        return cached
    payload = fetch_json(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        {
            "secid": stock_secid(code, market),
            "klt": 101,
            "fqt": 1,
            "lmt": 100,
            "end": 20500101,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "ut": EASTMONEY_UT,
        },
        attempts=2,
        referer=f"https://quote.eastmoney.com/{code}.html",
    )
    rows: list[dict[str, Any]] = []
    cutoff = now_cn().date() - dt.timedelta(days=96)
    for raw in (payload.get("data") or {}).get("klines") or []:
        parts = str(raw).split(",")
        if len(parts) < 11:
            continue
        try:
            trading_day = dt.date.fromisoformat(parts[0])
        except ValueError:
            continue
        if trading_day < cutoff:
            continue
        rows.append(
            {
                "date": parts[0],
                "open": to_float(parts[1]),
                "close": to_float(parts[2]),
                "high": to_float(parts[3]),
                "low": to_float(parts[4]),
                "volume": to_float(parts[5]) * 100,
                "amount": to_float(parts[6]),
                "changePct": to_float(parts[8]),
                "turnover": to_float(parts[10]),
            }
        )
    if len(rows) < 20:
        raise DataSourceError(f"{code} 近三个月日线不足")
    result = {
        "points": add_daily_rolling_means(rows),
        "source": "东方财富公开前复权日线",
        "adjustment": "前复权",
    }
    write_cache(cache_key, result)
    return result


def fetch_sina_stock_daily(code: str, market: int) -> dict[str, Any]:
    symbol = public_stock_symbol(code, market)
    raw = fetch_text_url(
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen=100",
        referer=f"https://finance.sina.com.cn/realstock/company/{symbol}/nc.shtml",
        attempts=2,
    )
    payload = json.loads(raw)
    rows: list[dict[str, Any]] = []
    cutoff = now_cn().date() - dt.timedelta(days=96)
    for item in payload if isinstance(payload, list) else []:
        date_label = str(item.get("day") or "")[:10]
        try:
            trading_day = dt.date.fromisoformat(date_label)
        except ValueError:
            continue
        if trading_day < cutoff:
            continue
        open_price = to_float(item.get("open"))
        close_price = to_float(item.get("close"))
        rows.append(
            {
                "date": date_label,
                "open": open_price,
                "close": close_price,
                "high": to_float(item.get("high")),
                "low": to_float(item.get("low")),
                "volume": to_float(item.get("volume")),
                "amount": 0.0,
                "changePct": (close_price / open_price - 1) * 100 if open_price else 0.0,
                "turnover": 0.0,
            }
        )
    if len(rows) < 20:
        raise DataSourceError(f"新浪财经未返回 {code} 足够的真实日线")
    previous_close = 0.0
    for row in rows:
        row["changePct"] = (row["close"] / previous_close - 1) * 100 if previous_close else 0.0
        previous_close = row["close"]
    return {
        "points": add_daily_rolling_means(rows),
        "source": "新浪财经公开不复权日线",
        "adjustment": "不复权",
    }


def fetch_tencent_stock_daily(code: str, market: int) -> dict[str, Any]:
    symbol = public_stock_symbol(code, market)
    raw = fetch_text_url(
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,100,qfq",
        referer=f"https://gu.qq.com/{symbol}/gp",
        attempts=2,
    )
    payload = json.loads(raw)
    container = (payload.get("data") or {}).get(symbol) or {}
    source_rows = container.get("qfqday") or container.get("day") or []
    rows: list[dict[str, Any]] = []
    cutoff = now_cn().date() - dt.timedelta(days=96)
    prior_close = 0.0
    for parts in source_rows:
        if not isinstance(parts, list) or len(parts) < 6:
            continue
        try:
            trading_day = dt.date.fromisoformat(str(parts[0])[:10])
        except ValueError:
            continue
        close_price = to_float(parts[2])
        if trading_day >= cutoff:
            rows.append(
                {
                    "date": str(parts[0])[:10],
                    "open": to_float(parts[1]),
                    "close": close_price,
                    "high": to_float(parts[3]),
                    "low": to_float(parts[4]),
                    "volume": to_float(parts[5]) * 100,
                    "amount": 0.0,
                    "changePct": (close_price / prior_close - 1) * 100 if prior_close else 0.0,
                    "turnover": 0.0,
                }
            )
        prior_close = close_price
    if len(rows) < 20:
        raise DataSourceError(f"腾讯证券未返回 {code} 足够的真实日线")
    return {
        "points": add_daily_rolling_means(rows),
        "source": "腾讯证券公开前复权日线",
        "adjustment": "前复权",
    }


def verify_daily_result(primary: dict[str, Any], comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    result = dict(primary)
    result["verifiedBy"] = [primary["source"]]
    for comparison in comparisons:
        left = result["points"][-1]
        right = comparison["points"][-1]
        if left["date"] == right["date"] and abs(left["close"] - right["close"]) <= max(0.02, left["close"] * 0.003):
            result["verifiedBy"].append(comparison["source"])
    if len(result["verifiedBy"]) > 1:
        result["source"] += " · 多源最新收盘交叉核验"
    return result


def fetch_stock_daily(code: str, market: int) -> dict[str, Any]:
    eastmoney: dict[str, Any] | None = None
    sina: dict[str, Any] | None = None
    tencent: dict[str, Any] | None = None
    errors: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            "em": executor.submit(fetch_eastmoney_stock_daily, code, market),
            "sina": executor.submit(fetch_sina_stock_daily, code, market),
            "tencent": executor.submit(fetch_tencent_stock_daily, code, market),
        }
        for provider, future in futures.items():
            try:
                if provider == "em":
                    eastmoney = future.result()
                elif provider == "tencent":
                    tencent = future.result()
                else:
                    sina = future.result()
            except Exception as exc:
                errors.append(f"{provider}: {exc}")
    if eastmoney:
        return verify_daily_result(eastmoney, [item for item in (tencent, sina) if item])
    if tencent:
        result = verify_daily_result(tencent, [item for item in (sina,) if item])
        result["verificationNote"] = "东方财富日线暂不可用；整段改用腾讯真实前复权序列，未混合"
        return result
    if sina:
        sina["verifiedBy"] = [sina["source"]]
        sina["verificationNote"] = "东方财富与腾讯日线暂不可用；整段改用新浪真实不复权序列，未混合"
        return sina
    raise DataSourceError("；".join(errors) or f"{code} 近三个月日线不足")


def build_stock_detail(code: str, market: Any = None, name: str = "", force: bool = False) -> dict[str, Any]:
    if not code.isdigit():
        raise ValueError("个股代码格式不正确")
    market_number = infer_stock_market(code, market)
    cache_key = f"stock-detail-v2-{market_number}-{code}"
    if not force:
        cached = read_cache(cache_key, max_age_seconds=55)
        if cached and not cached.get("isDemo"):
            return cached
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            quote_future = executor.submit(fetch_stock_quote, code, market_number)
            trends_future = executor.submit(fetch_verified_stock_trends, code, market_number)
            daily_future = executor.submit(fetch_stock_daily, code, market_number)
            quote = quote_future.result()
            trends = trends_future.result()
            daily_result = daily_future.result()
        if not is_allowed_stock(code, quote["name"]):
            raise ValueError("本页不展示 ST 或 *ST 股票")
        intraday = trends["points"]
        daily = daily_result["points"]
        trading_date = str(intraday[-1]["date"])
        verified = str(intraday[-1]["time"])
        result = {
            "date": trading_date,
            "updatedAt": f"{trading_date}T{verified}:00",
            "verifiedThrough": verified,
            "source": " · ".join(dict.fromkeys(source for source in [quote.get("source", ""), trends.get("source", ""), daily_result.get("source", "")] if source)),
            "isDemo": False,
            "isStale": False,
            "warning": "",
            "quote": {
                **quote,
                "name": quote["name"] or name or code,
                "close": daily[-1]["close"],
                "dayRange": [quote["low"], quote["high"]],
            },
            "intraday": intraday,
            "daily": daily,
            "dailyAdjustment": daily_result.get("adjustment", ""),
            "verifiedSources": list(dict.fromkeys(
                (quote.get("verifiedBy") or [])
                + (trends.get("verifiedBy") or [])
                + (daily_result.get("verifiedBy") or [])
            )),
            "verificationNotes": [
                note for note in (
                    quote.get("verificationNote"),
                    trends.get("verificationNote"),
                    daily_result.get("verificationNote"),
                ) if note
            ],
        }
        write_cache(cache_key, result)
        STORE.save_payload(
            "stock-detail",
            f"{market_number}-{code}",
            trading_date,
            verified,
            STOCK_SOURCE_NAME,
            now_cn().isoformat(timespec="seconds"),
            result,
        )
        return result
    except Exception as exc:
        identity = f"{market_number}-{code}"
        cached = read_cache(cache_key) or cached_stock_page(
            "stock-detail", identity, f"公开行情暂不可用，显示本机最近记录：{exc}"
        )
        if cached and not cached.get("isDemo"):
            result = dict(cached)
            result["warning"] = f"公开行情暂不可用，显示本机最近记录：{exc}"
            result["isStale"] = True
            return result
        raise DataSourceError(f"未取得 {name or code} 的完整真实行情：{exc}") from exc


def collect_stock_market(force: bool = False) -> dict[str, Any]:
    with _STOCK_MARKET_LOCK:
        return build_stock_market(force=force)


def collect_stock_detail(code: str, market: Any, name: str, force: bool = False) -> dict[str, Any]:
    with _STOCK_DETAIL_LOCK:
        return build_stock_detail(code, market, name, force=force)


def find_ffmpeg() -> str | None:
    """Locate a system, explicitly configured, or imageio-bundled FFmpeg."""
    configured = os.environ.get("FUND_FLOW_FFMPEG", "").strip()
    if configured and Path(configured).is_file():
        return configured
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    try:
        import imageio_ffmpeg  # type: ignore[import-not-found]

        executable = imageio_ffmpeg.get_ffmpeg_exe()
        if executable and Path(executable).is_file():
            return executable
    except Exception:
        pass
    candidates = sorted(
        Path.home().glob("Library/Python/*/lib/python/site-packages/imageio_ffmpeg/binaries/ffmpeg-*"),
        reverse=True,
    )
    candidates.extend(sorted(Path.home().glob(".local/lib/python*/site-packages/imageio_ffmpeg/binaries/ffmpeg-*"), reverse=True))
    return str(candidates[0]) if candidates else None


def video_capability() -> dict[str, Any]:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return {"ok": False, "ffmpeg": False, "message": "本地转换服务已连接，但未找到 FFmpeg"}
    try:
        completed = subprocess.run(
            [ffmpeg, "-version"], capture_output=True, text=True, timeout=8,
        )
    except Exception as exc:
        return {"ok": False, "ffmpeg": True, "message": f"FFmpeg 无法启动：{exc}"}
    if completed.returncode != 0:
        return {"ok": False, "ffmpeg": True, "message": "FFmpeg 自检失败，请重新安装或配置路径"}
    first_line = (completed.stdout or completed.stderr).splitlines()
    return {
        "ok": True, "ffmpeg": True, "message": "本地 MP4 转换服务可用",
        "version": first_line[0] if first_line else "ffmpeg",
    }


def convert_video(raw: bytes, date_label: str) -> tuple[Path, str]:
    if len(raw) > 120 * 1024 * 1024:
        raise ValueError("视频数据超过 120MB 限制")
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg，无法生成 MP4")
    safe_date = "".join(ch for ch in date_label if ch.isdigit() or ch == "-") or now_cn().strftime("%Y-%m-%d")
    filename = f"{safe_date}_收盘资金流向.mp4"
    output_path = OUTPUT_DIR / filename
    with tempfile.TemporaryDirectory(prefix="fund-flow-video-") as temp_dir:
        input_path = Path(temp_dir) / "recording.webm"
        input_path.write_bytes(raw)
        command = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-vf",
            "fps=30",
            "-fps_mode",
            "cfr",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-an",
            str(output_path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "ffmpeg 转换失败")
    return output_path, filename


def collect_replay(force: bool = False) -> dict[str, Any]:
    with _REPLAY_LOCK:
        return build_replay(force=force)


def background_snapshot_collector(stop_event: threading.Event) -> None:
    """Persist one real ranking snapshot for every completed market slot."""
    last_attempt_key = ""
    last_attempt_at = 0.0
    if stop_event.wait(8):
        return
    while not stop_event.is_set():
        current = now_cn()
        slot = market_slot(current.isoformat(timespec="seconds"), current.date().isoformat())
        attempt_key = f"{current.date().isoformat()}-{slot}"
        should_attempt = (
            current.weekday() < 5
            and bool(slot)
            and not STORE.has_capture(current.date().isoformat(), slot)
            and (attempt_key != last_attempt_key or time.monotonic() - last_attempt_at >= 120)
        )
        if should_attempt:
            last_attempt_key = attempt_key
            last_attempt_at = time.monotonic()
            try:
                collect_overview(force=True)
            except Exception as exc:
                print(f"后台五分钟采集暂未完成：{exc}", flush=True)
        stop_event.wait(20)


def background_strategy_collector(stop_event: threading.Event) -> None:
    """Record signals and advance the paper account while the server runs.

    This collector is intentionally lightweight and runs only during the two
    A-share sessions after one startup reconciliation. It means the simulation
    continues when the user is on another page, and a server opened after the
    close can still process the latest completed day. Closing the starter
    window stops all work as before.
    """
    last_minute_key = ""
    startup_reconciled = False
    last_startup_attempt = 0.0
    if stop_event.wait(12):
        return
    while not stop_event.is_set():
        current = now_cn()
        current_minutes = current.hour * 60 + current.minute
        market_open = current.weekday() < 5 and (
            570 <= current_minutes <= 690 or 780 <= current_minutes <= 900
        )
        minute_key = current.strftime("%Y-%m-%d-%H-%M")
        startup_due = not startup_reconciled and time.monotonic() - last_startup_attempt >= 120
        live_due = market_open and minute_key != last_minute_key
        if startup_due or live_due:
            if live_due:
                last_minute_key = minute_key
            if startup_due:
                last_startup_attempt = time.monotonic()
            try:
                with _STRATEGY_LAB_LOCK:
                    market = prepare_strategy_lab_market()
                    process_continuous_strategy_lab(market)
                startup_reconciled = True
            except Exception as exc:
                print(f"后台策略信号暂未完成：{exc}", flush=True)
        stop_event.wait(10)


class Handler(BaseHTTPRequestHandler):
    server_version = "FundFlowLocal/1.0"

    def log_message(self, format_string: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format_string % args}")

    def cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def send_json(self, status: int, payload: Any) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        force = query.get("refresh", ["0"])[0] == "1"
        try:
            if parsed.path == "/api/health":
                self.send_json(200, {
                    "ok": True,
                    "app": "fund-flow",
                    "apiVersion": API_VERSION,
                    "ffmpeg": bool(find_ffmpeg()),
                    "time": now_cn().isoformat(timespec="seconds"),
                })
            elif parsed.path == "/api/video/capability":
                self.send_json(200, video_capability())
            elif parsed.path == "/api/overview":
                self.send_json(200, collect_overview(force=force))
            elif parsed.path == "/api/replay":
                self.send_json(200, collect_replay(force=force))
            elif parsed.path == "/api/history":
                code = query.get("code", [""])[0]
                name = query.get("name", [code])[0]
                if not code:
                    raise ValueError("缺少板块代码")
                self.send_json(200, fetch_history(code, name))
            elif parsed.path == "/api/stocks/market":
                self.send_json(200, collect_stock_market(force=force))
            elif parsed.path == "/api/stocks/detail":
                code = query.get("code", [""])[0]
                market = query.get("market", [""])[0]
                name = query.get("name", [code])[0]
                if not code:
                    raise ValueError("缺少个股代码")
                self.send_json(200, collect_stock_detail(code, market, name, force=force))
            elif parsed.path == "/api/stocks/strategy":
                self.send_json(200, collect_strategy(force=force))
            elif parsed.path == "/api/strategy/lab":
                self.send_json(200, collect_strategy_lab())
            elif parsed.path == "/api/files":
                files = [
                    {"name": path.name, "size": path.stat().st_size, "updatedAt": dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")}
                    for path in sorted(OUTPUT_DIR.glob("*.mp4"), key=lambda item: item.stat().st_mtime, reverse=True)
                ]
                self.send_json(200, {"files": files})
            else:
                self.send_json(404, {"error": "接口不存在"})
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/strategy/lab":
            content_length = int(self.headers.get("Content-Length") or "0")
            if content_length <= 0 or content_length > 256 * 1024:
                self.send_json(400, {"error": "策略配置为空或过大"})
                return
            try:
                payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("策略配置格式不正确")
                self.send_json(200, handle_strategy_lab_action(payload))
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_json(400, {"error": str(exc)})
            except Exception as exc:
                self.send_json(500, {"error": str(exc)})
            return
        if parsed.path != "/api/video/convert":
            self.send_json(404, {"error": "接口不存在"})
            return
        query = urllib.parse.parse_qs(parsed.query)
        content_length = int(self.headers.get("Content-Length") or "0")
        if content_length <= 0:
            self.send_json(400, {"error": "没有收到视频数据"})
            return
        try:
            raw = self.rfile.read(content_length)
            output_path, filename = convert_video(raw, query.get("date", [""])[0])
            body = output_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Disposition", f'attachment; filename="{urllib.parse.quote(filename)}"')
            self.send_header("X-Output-File", urllib.parse.quote(filename))
            self.send_header("Content-Length", str(len(body)))
            self.cors()
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            self.send_json(500, {"error": str(exc)})


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    collector_stop = threading.Event()
    collector_thread = threading.Thread(
        target=background_snapshot_collector,
        args=(collector_stop,),
        name="fund-flow-snapshot-collector",
        daemon=True,
    )
    strategy_thread = threading.Thread(
        target=background_strategy_collector,
        args=(collector_stop,),
        name="fund-flow-strategy-collector",
        daemon=True,
    )
    collector_thread.start()
    strategy_thread.start()
    print(f"资金流向本地数据服务：http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        collector_stop.set()
        collector_thread.join(timeout=2)
        strategy_thread.join(timeout=2)
        server.server_close()


if __name__ == "__main__":
    main()
