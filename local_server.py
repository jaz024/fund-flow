#!/usr/bin/env python3
"""Local data and video service for the A-share fund-flow dashboard.

The service intentionally uses only Python's standard library and ffmpeg so the
app does not require a paid market-data key. Data is fetched from public
Eastmoney quote endpoints, cached on disk, and always labelled with its source.
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
THS_SOURCE_NAME = "同花顺公开网页"
EASTMONEY_UT = "b2884a393a59ad64002292a3e90d46a5"
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
    if force or stored_maximum < 2:
        candidates = overview["topIn"][:24] + overview["topOut"][:24]
        series: list[dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
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


def convert_video(raw: bytes, date_label: str) -> tuple[Path, str]:
    if len(raw) > 120 * 1024 * 1024:
        raise ValueError("视频数据超过 120MB 限制")
    ffmpeg = shutil.which("ffmpeg")
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
                self.send_json(200, {"ok": True, "time": now_cn().isoformat(timespec="seconds")})
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
    collector_thread.start()
    print(f"资金流向本地数据服务：http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        collector_stop.set()
        collector_thread.join(timeout=2)
        server.server_close()


if __name__ == "__main__":
    main()
