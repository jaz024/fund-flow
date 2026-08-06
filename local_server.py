#!/usr/bin/env python3
"""Local data and video service for the A-share fund-flow dashboard.

The service intentionally uses only Python's standard library and ffmpeg so the
app does not require a paid market-data key. Data is fetched from public
Eastmoney quote endpoints, cached on disk, and always labelled with its source.
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import hashlib
import html
import json
import math
import os
import random
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


ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "data" / "cache"
OUTPUT_DIR = ROOT / "output"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

HOST = "127.0.0.1"
PORT = int(os.environ.get("FUND_FLOW_API_PORT", "8765"))
SOURCE_NAME = "东方财富公开行情"
THS_SOURCE_NAME = "同花顺公开网页"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": "https://quote.eastmoney.com/center/",
    "Accept": "application/json,text/plain,*/*",
}

_CACHE_LOCK = threading.Lock()


class DataSourceError(RuntimeError):
    pass


def now_cn() -> dt.datetime:
    return dt.datetime.now()


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def fetch_json(base_url: str, params: dict[str, Any], attempts: int = 3) -> dict[str, Any]:
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
                        f"Referer: {HEADERS['Referer']}",
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
                request = urllib.request.Request(url, headers=HEADERS)
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


def board_rank(category: str, order_desc: bool, limit: int = 120) -> list[dict[str, Any]]:
    board_type = "2" if category == "industry" else "3"
    payload = fetch_json(
        "https://push2.eastmoney.com/api/qt/clist/get",
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
    if not rows:
        raise DataSourceError(str(errors[0]) if errors else "未获取到同花顺板块资金数据")
    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["category"], row["name"])
        deduped[key] = row
    return sorted(deduped.values(), key=lambda item: item["mainFlow"], reverse=True)


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
    if not rows:
        return fetch_ths_rankings()
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        existing = deduped.get(row["code"])
        if existing is None or abs(row["mainFlow"]) > abs(existing["mainFlow"]):
            deduped[row["code"]] = row
    return sorted(deduped.values(), key=lambda item: item["mainFlow"], reverse=True)


def fetch_indexes() -> list[dict[str, Any]]:
    definitions = [("000001", "上证指数"), ("399001", "深证成指"), ("899050", "北证50")]
    try:
        payload = fetch_json(
            "https://push2.eastmoney.com/api/qt/ulist.np/get",
            {
                "fltt": 2,
                "invt": 2,
                "fields": "f12,f14,f2,f3",
                "secids": "1.000001,0.399001,0.899050",
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


def demo_rankings() -> list[dict[str, Any]]:
    names = [
        "算力", "云计算", "人工智能", "电力", "白酒", "有色金属", "保险", "银行",
        "煤炭开采", "证券", "AI应用", "AI智能体", "物联网", "国产芯片", "半导体",
        "5G", "商业航天", "存储芯片", "机器人", "消费电子", "电子元件", "新能源车",
        "锂电池", "PCB", "光纤", "低空经济", "固态电池", "通信设备", "光伏", "储能",
    ]
    seed = int(now_cn().strftime("%Y%m%d"))
    rng = random.Random(seed)
    rows = []
    for index, name in enumerate(names):
        sign = 1 if index < 13 else -1
        amount = sign * (4.5 + rng.random() * 77)
        rows.append(
            {
                "code": f"DEMO{index:03d}",
                "name": name,
                "category": "概念" if index % 3 else "行业",
                "price": 1000 + rng.random() * 900,
                "changePct": sign * (0.2 + rng.random() * 4.8),
                "mainFlow": amount * 100_000_000,
                "superFlow": amount * 55_000_000,
                "largeFlow": amount * 45_000_000,
                "mediumFlow": -amount * 20_000_000,
                "smallFlow": -amount * 80_000_000,
            }
        )
    return sorted(rows, key=lambda item: item["mainFlow"], reverse=True)


def build_overview(force: bool = False) -> dict[str, Any]:
    cache_key = "overview-latest"
    if not force:
        cached = read_cache(cache_key, max_age_seconds=180)
        if cached:
            return cached
    is_demo = False
    warning = ""
    try:
        boards = fetch_all_rankings()
    except Exception as exc:
        cached = read_cache(cache_key)
        if cached:
            cached["warning"] = f"实时源暂不可用，显示最近缓存：{exc}"
            return cached
        boards = demo_rankings()
        indexes = [
            {"code": "000001", "name": "上证指数", "price": 3814.92, "changePct": 1.35},
            {"code": "399001", "name": "深证成指", "price": 12180.45, "changePct": 1.82},
            {"code": "899050", "name": "北证50", "price": 1124.68, "changePct": 0.76},
        ]
        is_demo = True
        warning = f"公开数据源暂不可用，当前为明确标注的演示数据：{exc}"
    else:
        ranking_source = str(boards[0].get("dataSource") or SOURCE_NAME) if boards else SOURCE_NAME
        if ranking_source == THS_SOURCE_NAME:
            warning = "东方财富实时列表当前限流，已自动切换到同花顺公开网页的资金净额口径。"
        try:
            indexes = fetch_indexes()
        except Exception as exc:
            recent = read_cache(cache_key) or {}
            indexes = recent.get("indexes") or [
                {"code": "000001", "name": "上证指数", "price": 0, "changePct": 0},
                {"code": "399001", "name": "深证成指", "price": 0, "changePct": 0},
                {"code": "899050", "name": "北证50", "price": 0, "changePct": 0},
            ]
            warning = f"{warning} 指数暂用最近缓存：{exc}".strip()
    today = now_cn().strftime("%Y-%m-%d")
    result = {
        "date": today,
        "updatedAt": now_cn().isoformat(timespec="seconds"),
        "source": (
            str(boards[0].get("dataSource") or SOURCE_NAME)
            if not is_demo and boards
            else "离线演示数据"
        ),
        "isDemo": is_demo,
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
    write_cache(f"overview-{today}", result)
    return result


def fetch_intraday_for_board(board: dict[str, Any]) -> dict[str, Any] | None:
    code = board["code"]
    if not code.startswith("BK"):
        return None
    try:
        payload = fetch_json(
            "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get",
            {
                "lmt": 0,
                "klt": 1,
                "secid": f"90.{code}",
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
            },
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


def demo_replay(overview: dict[str, Any]) -> dict[str, Any]:
    # Keep one fixed identity set for the entire replay. A board may cross zero,
    # but its bubble must not disappear or be replaced by the current top list.
    boards = overview["topIn"][:15] + overview["topOut"][:15]
    frames = []
    times = elapsed_five_minute_times(overview.get("updatedAt", ""), overview.get("date", ""))
    for index, label in enumerate(times):
        progress = (index + 1) / len(times)
        values = []
        for board_index, board in enumerate(boards):
            close_value = board["mainFlow"]
            wave = math.sin(index * 0.35 + board_index * 0.7) * abs(close_value) * 0.12
            value = close_value * (0.08 + progress * 0.92) + wave
            values.append({**board, "mainFlow": value})
        positives = sorted((item for item in values if item["mainFlow"] >= 0), key=lambda x: x["mainFlow"], reverse=True)
        negatives = sorted((item for item in values if item["mainFlow"] < 0), key=lambda x: x["mainFlow"])
        frames.append({"time": label, "boards": values, "inflow": positives, "outflow": negatives})
    return {
        "date": overview["date"],
        "updatedAt": now_cn().isoformat(timespec="seconds"),
        "source": overview["source"],
        "isDemo": True,
        "warning": overview.get("warning") or "演示回放，不用于投资判断",
        "indexes": overview["indexes"],
        "schemaVersion": 3,
        "frames": frames,
    }


def build_replay(force: bool = False) -> dict[str, Any]:
    today = now_cn().strftime("%Y-%m-%d")
    cache_key = f"replay-v3-{today}"
    if not force:
        cached = read_cache(cache_key, max_age_seconds=600)
        if cached:
            return cached
    overview = build_overview(force=force)
    if overview.get("isDemo"):
        result = demo_replay(overview)
        write_cache(cache_key, result)
        return result

    candidates = overview["topIn"][:24] + overview["topOut"][:24]
    series: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(fetch_intraday_for_board, board) for board in candidates]
        for future in concurrent.futures.as_completed(futures):
            item = future.result()
            if item:
                series.append(item)
    positive_count = sum(1 for item in series if item["board"]["mainFlow"] > 0)
    negative_count = sum(1 for item in series if item["board"]["mainFlow"] < 0)
    if positive_count < 4 or negative_count < 4:
        result = demo_replay(overview)
        result["warning"] = "部分板块未提供分钟资金历史，回放采用明确标注的估算演示；收盘排名仍为真实数据。"
        write_cache(cache_key, result)
        return result

    # Select one fixed set by the largest absolute value reached during the day.
    # Those same identities are carried through every frame, including sign
    # changes, so the visual can animate a real bubble instead of swapping ranks.
    tracked_series = sorted(
        series,
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
            value = previous_point(item["points"], label)
            if value is not None:
                values.append({**item["board"], "mainFlow": value})
        if not values:
            continue
        positives = sorted((row for row in values if row["mainFlow"] >= 0), key=lambda row: row["mainFlow"], reverse=True)
        negatives = sorted((row for row in values if row["mainFlow"] < 0), key=lambda row: row["mainFlow"])
        frames.append({"time": label, "boards": values, "inflow": positives, "outflow": negatives})
    if not frames:
        return demo_replay(overview)
    result = {
        "date": series[0].get("date") or today,
        "updatedAt": now_cn().isoformat(timespec="seconds"),
        "source": f"{overview['source']} + 东方财富分钟资金历史",
        "isDemo": False,
        "warning": "",
        "indexes": overview["indexes"],
        "schemaVersion": 3,
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


def demo_history(code: str, name: str, current: float = 0.0) -> dict[str, Any]:
    seed = int(hashlib.sha256(code.encode("utf-8")).hexdigest()[:8], 16)
    rng = random.Random(seed)
    dates: list[str] = []
    cursor = now_cn().date() - dt.timedelta(days=92)
    value = current * 0.4 or (rng.random() - 0.5) * 4_000_000_000
    rows = []
    while cursor <= now_cn().date():
        if cursor.weekday() < 5:
            value = value * 0.45 + (rng.random() - 0.5) * 8_000_000_000
            dates.append(cursor.isoformat())
            rows.append(value)
        cursor += dt.timedelta(days=1)
    ma5 = rolling_mean(rows, 5)
    ma20 = rolling_mean(rows, 20)
    return {
        "code": code,
        "name": name,
        "source": "离线演示数据",
        "isDemo": True,
        "points": [
            {"date": date, "mainFlow": value, "ma5": ma5[index], "ma20": ma20[index]}
            for index, (date, value) in enumerate(zip(dates, rows))
        ],
    }


def fetch_history(code: str, name: str) -> dict[str, Any]:
    cache_key = f"history-{code}"
    cached = read_cache(cache_key, max_age_seconds=6 * 3600)
    if cached:
        return cached
    if not code.startswith("BK"):
        return demo_history(code, name)
    try:
        payload = fetch_json(
            "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
            {
                "lmt": 120,
                "klt": 101,
                "secid": f"90.{code}",
                "fields1": "f1,f2,f3,f7",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
            },
        )
        data = payload.get("data") or {}
        rows = data.get("klines") or []
        parsed: list[tuple[str, float]] = []
        cutoff = now_cn().date() - dt.timedelta(days=95)
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
    except Exception:
        current = 0.0
        overview = read_cache("overview-latest") or {}
        board = next((row for row in overview.get("boards", []) if row.get("code") == code), None)
        if board:
            current = to_float(board.get("mainFlow"))
        return demo_history(code, name, current)


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
                self.send_json(200, build_overview(force=force))
            elif parsed.path == "/api/replay":
                self.send_json(200, build_replay(force=force))
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
    print(f"资金流向本地数据服务：http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
