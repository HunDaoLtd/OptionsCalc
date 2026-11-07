from __future__ import annotations

import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

# 确保日志输出到 stderr（Gunicorn 会重定向到 no_hup.log）
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

BASE_URL = "https://eapi.binance.com"
PUBLIC_DATA_ERROR = "Unable to load Binance option data"
DAY_MS = 24 * 60 * 60 * 1000
REQUEST_TIMEOUT = float(os.environ.get("BINANCE_REQUEST_TIMEOUT", "10"))
CACHE_TTL_SECONDS = float(os.environ.get("BINANCE_CACHE_TTL_SECONDS", "15"))

last_good_snapshot: dict[str, Any] | None = None
cached_snapshot: dict[str, Any] | None = None
cached_snapshot_at: int | None = None
snapshot_cache_lock = threading.Lock()


def reset_snapshot_cache() -> None:
    global cached_snapshot, cached_snapshot_at, last_good_snapshot
    cached_snapshot = None
    cached_snapshot_at = None
    last_good_snapshot = None


def is_cached_snapshot_fresh(now_ms: int) -> bool:
    if cached_snapshot is None or cached_snapshot_at is None:
        return False
    if CACHE_TTL_SECONDS <= 0:
        return False
    return now_ms - cached_snapshot_at < CACHE_TTL_SECONDS * 1000


def normalize_proxy_url(value: str | None) -> str | None:
    if not value:
        return None

    trimmed = value.strip()
    if not trimmed:
        return None

    parts = [part.strip() for part in trimmed.split(";") if part.strip()]
    proxy_entry = next(
        (part for part in parts if part.lower().startswith("https=")), None
    )
    proxy_entry = proxy_entry or next(
        (part for part in parts if part.lower().startswith("http=")), None
    )
    proxy_entry = proxy_entry or trimmed

    proxy_value = proxy_entry.split("=", 1)[1] if "=" in proxy_entry else proxy_entry
    if proxy_value.lower().startswith(("http://", "https://")):
        return proxy_value
    return f"http://{proxy_value}"


def get_proxy_url() -> str | None:
    return normalize_proxy_url(os.environ.get("BINANCE_PROXY", "http://127.0.0.1:7890"))


def _proxy_log_address(proxy_url: str) -> str:
    """Return only a safe host/port, never proxy credentials or URL details."""
    try:
        parsed = urlsplit(proxy_url)
        hostname = parsed.hostname
        port = parsed.port
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or re.fullmatch(r"[a-zA-Z0-9._:-]+", hostname) is None
        ):
            return "unavailable"
    except ValueError:
        return "unavailable"
    address = f"[{hostname}]" if ":" in hostname else hostname
    return f"{address}:{port}" if port is not None else address


def request_json(path: str) -> Any:
    proxy_url = get_proxy_url()
    if not proxy_url:
        return request_json_with_proxy(path, None)

    try:
        return request_json_with_proxy(path, proxy_url)
    except requests.RequestException as err:
        logger.warning(
            "Proxy request failed (proxy=%s, error=%s), retrying direct",
            _proxy_log_address(proxy_url),
            type(err).__name__,
        )
        return request_json_with_proxy(path, None)


def request_json_with_proxy(path: str, proxy_url: str | None) -> Any:
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    response = requests.get(
        f"{BASE_URL}{path}", proxies=proxies, timeout=REQUEST_TIMEOUT
    )
    if not response.ok:
        logger.error(
            "Binance public API request failed (status=%s)",
            response.status_code,
        )
        raise RuntimeError(PUBLIC_DATA_ERROR)
    return response.json()


def to_number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return (
        parsed
        if parsed == parsed and parsed not in (float("inf"), float("-inf"))
        else None
    )


def parse_option_symbol(symbol: str) -> dict[str, Any] | None:
    parts = symbol.split("-")
    if len(parts) != 4:
        return None

    underlying, date_part, strike_part, side_part = parts
    strike = to_number(strike_part)
    if not underlying or not date_part or strike is None:
        return None
    if side_part not in ("P", "C"):
        return None

    return {
        "underlying": underlying,
        "datePart": date_part,
        "strike": strike,
        "side": "PUT" if side_part == "P" else "CALL",
    }


def days_to_expiry(expiry_ms: float, now_ms: float) -> float:
    return max(0, (expiry_ms - now_ms) / DAY_MS)


def format_expiry(expiry_ms: float) -> str:
    return datetime.fromtimestamp(expiry_ms / 1000).strftime("%m/%d %H:%M")


def fetch_btc_put_snapshot(now_ms: int | None = None) -> dict[str, Any]:
    global cached_snapshot, cached_snapshot_at, last_good_snapshot

    now = now_ms or int(time.time() * 1000)

    with snapshot_cache_lock:
        if is_cached_snapshot_fresh(now):
            return {**cached_snapshot}

        snapshot = fetch_fresh_btc_put_snapshot(now)
        cached_snapshot = snapshot
        cached_snapshot_at = now
        last_good_snapshot = snapshot
        return {**snapshot}


def fetch_fresh_btc_put_snapshot(now: int) -> dict[str, Any]:
    global last_good_snapshot

    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            exchange_info_future = executor.submit(
                request_json, "/eapi/v1/exchangeInfo"
            )
            marks_future = executor.submit(request_json, "/eapi/v1/mark")
            index_future = executor.submit(
                request_json, "/eapi/v1/index?underlying=BTCUSDT"
            )
            exchange_info = exchange_info_future.result()
            marks = marks_future.result()
            index = index_future.result()

        mark_by_symbol = {
            item.get("symbol"): {
                "markPrice": to_number(item.get("markPrice")),
                "markIV": to_number(item.get("markIV")),
            }
            for item in marks
        }
        contracts = []

        for item in exchange_info.get("optionSymbols", []):
            symbol = item.get("symbol", "")
            parsed = parse_option_symbol(symbol)
            expiry = item.get("expiryDate") or 0
            strike = to_number(item.get("strikePrice"))
            if strike is None and parsed:
                strike = parsed["strike"]
            side = (
                "PUT"
                if item.get("side") == "PUT" or (parsed and parsed["side"] == "PUT")
                else "CALL"
            )

            if not parsed or parsed["underlying"] != "BTC":
                continue
            if side != "PUT":
                continue
            if not isinstance(expiry, (int, float)) or expiry <= now:
                continue
            if strike is None:
                continue
            if item.get("status") and item.get("status") != "TRADING":
                continue

            contracts.append(
                {
                    "symbol": symbol,
                    "expiry": expiry,
                    "expiryLabel": format_expiry(expiry),
                    "strike": strike,
                    "side": "PUT",
                    "markPrice": (mark_by_symbol.get(symbol) or {}).get("markPrice"),
                    "markIV": (mark_by_symbol.get(symbol) or {}).get("markIV"),
                    "daysToExpiry": days_to_expiry(expiry, now),
                }
            )

        snapshot = {
            "underlying": "BTC",
            "indexPrice": to_number(index.get("indexPrice")),
            "serverTime": exchange_info.get("serverTime") or now,
            "updatedAt": now,
            "contracts": contracts,
            "stale": False,
        }
        return snapshot
    except Exception as error:
        logger.error(
            "Failed to fetch fresh BTC put snapshot (error=%s)",
            type(error).__name__,
        )
        if last_good_snapshot:
            logger.info(
                "Returning last good snapshot (stale), cached at %s",
                datetime.fromtimestamp(
                    last_good_snapshot.get("updatedAt", 0) / 1000
                ).strftime("%H:%M:%S"),
            )
            return {
                **last_good_snapshot,
                "stale": True,
                "sourceError": PUBLIC_DATA_ERROR,
            }
        raise RuntimeError(PUBLIC_DATA_ERROR) from None
