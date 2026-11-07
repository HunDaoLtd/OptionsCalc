from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode

import requests

try:
    from .binance import get_proxy_url, parse_option_symbol, request_json, to_number
    from .risk_accounts import RiskAccountConfig
    from .risk_types import (
        OptionSide,
        RiskContract,
        RiskIssueCode,
        RiskMarketSnapshot,
    )
except ImportError:
    from binance import get_proxy_url, parse_option_symbol, request_json, to_number
    from risk_accounts import RiskAccountConfig
    from risk_types import OptionSide, RiskContract, RiskIssueCode, RiskMarketSnapshot


logger = logging.getLogger(__name__)

BASE_URL = "https://eapi.binance.com"
ALLOWED_PRIVATE_PATHS = frozenset(
    {"/eapi/v1/marginAccount", "/eapi/v1/position"}
)
TIMESTAMP_ERROR_CODE = -1021
RECV_WINDOW_MS = 5000
TIME_OFFSET_TTL_MS = 60_000


@dataclass(frozen=True, slots=True)
class PrivateAccountSnapshot:
    margin_account: dict[str, Any] = field(repr=False)
    positions: tuple[dict[str, Any], ...] = field(repr=False)
    fetched_at_ms: int
    stale: bool = False
    source_issue_code: RiskIssueCode | None = None


@dataclass(frozen=True, slots=True)
class RiskPublicSnapshot:
    market: RiskMarketSnapshot
    contracts: tuple[RiskContract, ...]
    fetched_at_ms: int


class BinancePrivateError(RuntimeError):
    def __init__(
        self,
        issue_code: RiskIssueCode,
        endpoint: str,
        *,
        status_code: int | None = None,
        binance_code: int | str | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(f"Binance private GET failed: {endpoint}")
        self.issue_code = issue_code
        self.endpoint = endpoint
        self.status_code = status_code
        self.binance_code = binance_code
        self.retry_after_seconds = retry_after_seconds


def build_signed_query(
    parameters: tuple[tuple[str, int | str], ...],
    secret_key: str,
) -> str:
    query = urlencode(parameters)
    signature = hmac.new(
        secret_key.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{query}&signature={signature}"


def _retry_after_seconds(value: str | None, now_seconds: float) -> float:
    if not value:
        return 1.0
    try:
        return max(float(value), 1.0)
    except ValueError:
        try:
            retry_time = parsedate_to_datetime(value).timestamp()
        except (TypeError, ValueError, OverflowError):
            return 1.0
        return max(retry_time - now_seconds, 1.0)


class BinancePrivateClient:
    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        timeout_seconds: float = 10.0,
        now_seconds=time.time,
    ) -> None:
        self._session = session or requests.Session()
        self._timeout_seconds = timeout_seconds
        self._now_seconds = now_seconds
        self._time_offset_ms: int | None = None
        self._time_offset_fetched_at_ms: int | None = None
        self._cooldown_until_ms = 0
        self._state_lock = threading.Lock()

    def _now_ms(self) -> int:
        return int(self._now_seconds() * 1000)

    def _request_json(
        self,
        path: str,
        *,
        account_id: str | None = None,
        headers: dict[str, str] | None = None,
        query: str = "",
    ) -> Any:
        now_ms = self._now_ms()
        with self._state_lock:
            cooldown_until = self._cooldown_until_ms
        if now_ms < cooldown_until:
            raise BinancePrivateError(
                RiskIssueCode.UPSTREAM_UNAVAILABLE,
                path,
                retry_after_seconds=(cooldown_until - now_ms) / 1000,
            )

        proxy_url = get_proxy_url()
        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        url = f"{BASE_URL}{path}"
        if query:
            url = f"{url}?{query}"
        try:
            response = self._session.get(
                url,
                headers=headers,
                proxies=proxies,
                timeout=self._timeout_seconds,
            )
        except requests.RequestException as error:
            logger.warning(
                "Binance private request failed account=%s endpoint=%s",
                account_id or "public-time",
                path,
            )
            raise BinancePrivateError(
                RiskIssueCode.UPSTREAM_UNAVAILABLE, path
            ) from error

        try:
            payload = response.json()
        except ValueError:
            payload = None
        binance_code = payload.get("code") if isinstance(payload, dict) else None

        if response.status_code in (418, 429):
            retry_after = _retry_after_seconds(
                response.headers.get("Retry-After"), self._now_seconds()
            )
            with self._state_lock:
                self._cooldown_until_ms = max(
                    self._cooldown_until_ms,
                    self._now_ms() + int(retry_after * 1000),
                )
            logger.warning(
                "Binance private rate limit account=%s endpoint=%s status=%s code=%s",
                account_id or "public-time",
                path,
                response.status_code,
                binance_code,
            )
            raise BinancePrivateError(
                RiskIssueCode.UPSTREAM_UNAVAILABLE,
                path,
                status_code=response.status_code,
                binance_code=binance_code,
                retry_after_seconds=retry_after,
            )

        if not response.ok:
            logger.warning(
                "Binance private response error account=%s endpoint=%s status=%s code=%s",
                account_id or "public-time",
                path,
                response.status_code,
                binance_code,
            )
            raise BinancePrivateError(
                RiskIssueCode.UPSTREAM_UNAVAILABLE,
                path,
                status_code=response.status_code,
                binance_code=binance_code,
            )
        return payload

    def _server_timestamp(self, *, force_refresh: bool = False) -> int:
        now_ms = self._now_ms()
        with self._state_lock:
            cached_offset = self._time_offset_ms
            fetched_at = self._time_offset_fetched_at_ms
        if (
            not force_refresh
            and cached_offset is not None
            and fetched_at is not None
            and now_ms - fetched_at < TIME_OFFSET_TTL_MS
        ):
            return now_ms + cached_offset

        payload = self._request_json("/eapi/v1/time")
        if not isinstance(payload, dict):
            raise BinancePrivateError(
                RiskIssueCode.UPSTREAM_UNAVAILABLE, "/eapi/v1/time"
            )
        try:
            server_time = int(payload["serverTime"])
        except (KeyError, TypeError, ValueError) as error:
            raise BinancePrivateError(
                RiskIssueCode.UPSTREAM_UNAVAILABLE, "/eapi/v1/time"
            ) from error
        with self._state_lock:
            self._time_offset_ms = server_time - now_ms
            self._time_offset_fetched_at_ms = now_ms
        return server_time

    def get(
        self,
        account: RiskAccountConfig,
        path: str,
    ) -> Any:
        if path not in ALLOWED_PRIVATE_PATHS:
            raise ValueError("private endpoint is not allowlisted")
        if not account.api_key or not account.secret_key:
            raise ValueError("configured account credentials are required")

        for attempt in range(2):
            timestamp = self._server_timestamp(force_refresh=attempt > 0)
            query = build_signed_query(
                (
                    ("timestamp", timestamp),
                    ("recvWindow", RECV_WINDOW_MS),
                ),
                account.secret_key,
            )
            try:
                return self._request_json(
                    path,
                    account_id=account.account_id,
                    headers={"X-MBX-APIKEY": account.api_key},
                    query=query,
                )
            except BinancePrivateError as error:
                if error.binance_code == TIMESTAMP_ERROR_CODE and attempt == 0:
                    continue
                raise
        raise AssertionError("timestamp retry loop must return or raise")


class PrivateAccountCache:
    def __init__(
        self,
        client: BinancePrivateClient,
        *,
        ttl_seconds: float = 10.0,
        stale_max_seconds: float = 60.0,
        now_seconds=time.time,
    ) -> None:
        self._client = client
        self._ttl_ms = max(float(ttl_seconds), 0.0) * 1000
        self._stale_max_ms = max(float(stale_max_seconds), 0.0) * 1000
        self._now_seconds = now_seconds
        self._entries: dict[str, PrivateAccountSnapshot] = {}
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def fetch(
        self,
        account: RiskAccountConfig,
        *,
        force_refresh: bool = False,
        now_ms: int | None = None,
    ) -> PrivateAccountSnapshot:
        now = now_ms if now_ms is not None else int(self._now_seconds() * 1000)
        with self._lock:
            cached = self._entries.get(account.account_id)
        if (
            not force_refresh
            and cached is not None
            and now - cached.fetched_at_ms < self._ttl_ms
        ):
            return cached

        try:
            margin_account = self._client.get(account, "/eapi/v1/marginAccount")
            positions = self._client.get(account, "/eapi/v1/position")
            if not isinstance(margin_account, dict) or not isinstance(positions, list):
                raise BinancePrivateError(
                    RiskIssueCode.UPSTREAM_UNAVAILABLE, "account_snapshot"
                )
            fresh = PrivateAccountSnapshot(
                margin_account=margin_account,
                positions=tuple(
                    position for position in positions if isinstance(position, dict)
                ),
                fetched_at_ms=now,
            )
            with self._lock:
                self._entries[account.account_id] = fresh
            return fresh
        except BinancePrivateError as error:
            if cached is not None and now - cached.fetched_at_ms <= self._stale_max_ms:
                return replace(
                    cached,
                    stale=True,
                    source_issue_code=error.issue_code,
                )
            if cached is not None:
                raise BinancePrivateError(
                    RiskIssueCode.STALE_DATA_EXPIRED,
                    error.endpoint,
                    status_code=error.status_code,
                    binance_code=error.binance_code,
                ) from error
            raise


def normalize_risk_public_snapshot(
    exchange_info: Any,
    marks: Any,
    index: Any,
    *,
    fetched_at_ms: int,
    stale: bool = False,
) -> RiskPublicSnapshot:
    if not isinstance(exchange_info, dict) or not isinstance(index, dict):
        raise ValueError("invalid public risk snapshot")
    index_price = to_number(index.get("indexPrice"))
    if index_price is None or index_price <= 0:
        raise ValueError("invalid BTC index price")
    source_time = index.get("time", exchange_info.get("serverTime", fetched_at_ms))
    if isinstance(source_time, bool) or not isinstance(source_time, (int, float)):
        source_time = fetched_at_ms

    mark_by_symbol = {
        item.get("symbol"): item
        for item in marks if isinstance(item, dict) and item.get("symbol")
    } if isinstance(marks, list) else {}
    contracts: list[RiskContract] = []
    for item in exchange_info.get("optionSymbols", []):
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", ""))
        parsed = parse_option_symbol(symbol)
        if parsed is None or parsed["underlying"] != "BTC":
            continue
        underlying = str(item.get("underlying") or "BTCUSDT")
        quote_asset = str(item.get("quoteAsset") or "USDT")
        if underlying != "BTCUSDT" or quote_asset != "USDT":
            continue
        side_value = item.get("side") or parsed["side"]
        if side_value not in ("CALL", "PUT"):
            continue
        expiry = to_number(item.get("expiryDate"))
        strike = to_number(item.get("strikePrice"))
        if expiry is None or strike is None or expiry <= 0 or strike <= 0:
            continue
        mark = mark_by_symbol.get(symbol, {})
        contracts.append(
            RiskContract(
                symbol=symbol,
                underlying=underlying,
                quote_asset=quote_asset,
                option_side=OptionSide(side_value),
                expiry_ms=int(expiry),
                strike_price=strike,
                unit=to_number(item.get("unit")),
                maintenance_margin_rate=to_number(item.get("maintenanceMargin")),
                min_maintenance_margin_rate=to_number(
                    item.get("minMaintenanceMargin")
                ),
                liquidation_fee_rate=to_number(item.get("liquidationFeeRate")),
                mark_price=to_number(mark.get("markPrice")),
                mark_iv=to_number(mark.get("markIV")),
                risk_free_interest=to_number(mark.get("riskFreeInterest")),
            )
        )
    return RiskPublicSnapshot(
        market=RiskMarketSnapshot(
            index_price=index_price,
            source_time_ms=int(source_time),
            stale=stale,
        ),
        contracts=tuple(sorted(contracts, key=lambda contract: contract.symbol)),
        fetched_at_ms=fetched_at_ms,
    )


class RiskPublicCache:
    def __init__(
        self,
        *,
        ttl_seconds: float = 15.0,
        stale_max_seconds: float = 60.0,
        now_seconds=time.time,
        public_request=request_json,
    ) -> None:
        self._ttl_ms = max(float(ttl_seconds), 0.0) * 1000
        self._stale_max_ms = max(float(stale_max_seconds), 0.0) * 1000
        self._now_seconds = now_seconds
        self._public_request = public_request
        self._cached: RiskPublicSnapshot | None = None
        self._lock = threading.Lock()

    def clear(self) -> None:
        with self._lock:
            self._cached = None

    def fetch(self, *, now_ms: int | None = None) -> RiskPublicSnapshot:
        now = now_ms if now_ms is not None else int(self._now_seconds() * 1000)
        with self._lock:
            cached = self._cached
        if cached is not None and now - cached.fetched_at_ms < self._ttl_ms:
            return cached

        try:
            with ThreadPoolExecutor(max_workers=3) as executor:
                exchange_future = executor.submit(
                    self._public_request, "/eapi/v1/exchangeInfo"
                )
                marks_future = executor.submit(self._public_request, "/eapi/v1/mark")
                index_future = executor.submit(
                    self._public_request,
                    "/eapi/v1/index?underlying=BTCUSDT",
                )
                snapshot = normalize_risk_public_snapshot(
                    exchange_future.result(),
                    marks_future.result(),
                    index_future.result(),
                    fetched_at_ms=now,
                )
            with self._lock:
                self._cached = snapshot
            return snapshot
        except Exception as error:
            if cached is not None and now - cached.fetched_at_ms <= self._stale_max_ms:
                return replace(cached, market=replace(cached.market, stale=True))
            raise BinancePrivateError(
                RiskIssueCode.STALE_DATA_EXPIRED
                if cached is not None
                else RiskIssueCode.UPSTREAM_UNAVAILABLE,
                "public_risk_snapshot",
            ) from error
