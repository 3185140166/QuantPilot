"""Thin Bybit V5 adapter. Private calls are never made implicitly."""

from __future__ import annotations

import threading
import time
import uuid
from types import MethodType
from typing import Any

from ..config import Settings


def synchronize_request_clock(session):
    """Sign private requests against exchange time using a monotonic clock.

    The hook belongs to this session only; public requests remain unauthenticated.
    A failed time request prevents signing rather than falling back to a bad clock.
    """
    lock = threading.Lock()
    anchor = None

    def headers(client, payload, recv_window, content_type="application/json"):
        nonlocal anchor
        with lock:
            now = time.monotonic()
            if anchor is None or now - anchor[1] >= 60:
                for _ in range(3):
                    before = time.monotonic()
                    response = client.get_server_time()
                    after = time.monotonic()
                    if after - before <= 4:
                        break
                else:
                    raise RuntimeError("Bybit time request is too slow; retry later")
                if response.get("retCode") != 0:
                    raise RuntimeError("Cannot synchronize Bybit request time")
                server_ms = int(
                    response.get("time")
                    or int(response["result"]["timeNano"]) // 1_000_000
                )
                # Do not guess one-way latency: a conservative timestamp never
                # exceeds the exchange's strict +1000 ms future bound.
                anchor = (server_ms - 100, after)
            timestamp = int(anchor[0] + (time.monotonic() - anchor[1]) * 1000)
        sign = (client._auth_binary if isinstance(payload, bytes) else client._auth)(
            payload, recv_window, timestamp
        )
        return {
            "Content-Type": content_type,
            "X-BAPI-API-KEY": client.api_key,
            "X-BAPI-SIGN": sign,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": str(timestamp),
            "X-BAPI-RECV-WINDOW": str(recv_window),
        }

    session._prepare_headers = MethodType(headers, session)


class BybitClient:
    def __init__(self, settings: Settings, require_private: bool = False):
        if require_private:
            settings.require_private_api()
        try:
            from pybit.unified_trading import HTTP
        except ImportError as exc:
            raise RuntimeError("Install dependencies with: pip install -e .") from exc
        self.settings = settings
        self.session = HTTP(
            testnet=settings.testnet,
            api_key=settings.api_key,
            api_secret=settings.api_secret,
            force_retry=False,
            log_requests=False,
        )
        synchronize_request_clock(self.session)

    @staticmethod
    def _check(response: dict[str, Any]) -> dict[str, Any]:
        if response.get("retCode") != 0:
            raise RuntimeError(
                f"Bybit request failed: retCode={response.get('retCode')} "
                f"retMsg={response.get('retMsg')}"
            )
        return response

    def get_klines(
        self, limit: int = 200, start: int | None = None, end: int | None = None
    ) -> list[list[Any]]:
        bounds = {}
        if start is not None:
            bounds["start"] = start
        if end is not None:
            bounds["end"] = end
        response = self.session.get_kline(
            category=self.settings.category,
            symbol=self.settings.symbol,
            interval=self.settings.interval,
            limit=limit,
            **bounds,
        )
        return self._check(response)["result"]["list"]

    def get_instrument(self) -> dict[str, Any]:
        response = self.session.get_instruments_info(
            category=self.settings.category, symbol=self.settings.symbol
        )
        return self._check(response)["result"]["list"][0]

    def get_mark_klines(self, start: int, end: int, limit: int = 1000):
        return self._check(
            self.session.get_mark_price_kline(
                category="linear",
                symbol=self.settings.symbol,
                interval=self.settings.interval,
                start=start,
                end=end,
                limit=limit,
            )
        )["result"]["list"]

    def get_funding_history(self, start: int, end: int, limit: int = 200):
        return self._check(
            self.session.get_funding_rate_history(
                category="linear",
                symbol=self.settings.symbol,
                startTime=start,
                endTime=end,
                limit=limit,
            )
        )["result"]["list"]

    def get_risk_limits(self):
        rows, cursor, seen = [], "", set()
        for _ in range(50):
            result = self._check(
                self.session.get_risk_limit(
                    category="linear",
                    symbol=self.settings.symbol,
                    **({"cursor": cursor} if cursor else {}),
                )
            )["result"]
            rows.extend(
                row for row in result["list"] if row["symbol"] == self.settings.symbol
            )
            cursor = result.get("nextPageCursor", "")
            if not cursor:
                return rows
            if cursor in seen:
                raise ValueError("Repeated risk-limit cursor")
            seen.add(cursor)
        raise ValueError("Risk-limit pagination exceeded limit")

    def get_instruments(self) -> list[dict[str, Any]]:
        """Read every instruments page; the linear catalog exceeds one page."""
        instruments, seen = [], set()
        cursor = ""
        for _ in range(50):
            params = {"category": self.settings.category, "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            result = self._check(self.session.get_instruments_info(**params))["result"]
            instruments.extend(result["list"])
            cursor = result.get("nextPageCursor", "")
            if not cursor:
                return instruments
            if cursor in seen:
                raise RuntimeError("Bybit returned a repeated instruments cursor")
            seen.add(cursor)
        raise RuntimeError("Bybit instruments pagination exceeded its safety limit")

    def get_tickers(self) -> list[dict[str, Any]]:
        response = self.session.get_tickers(category=self.settings.category)
        return self._check(response)["result"]["list"]

    def get_wallet_balance(self) -> dict[str, Any]:
        response = self.session.get_wallet_balance(accountType="UNIFIED")
        return self._check(response)

    def place_market(
        self, side: str, qty: str, reduce_only: bool = False
    ) -> dict[str, Any]:
        self.settings.require_order_submission("bybit", qty)
        self.settings.require_private_api()
        if side not in {"Buy", "Sell"}:
            raise ValueError("side must be Buy or Sell")
        response = self.session.place_order(
            category=self.settings.category,
            symbol=self.settings.symbol,
            side=side,
            orderType="Market",
            qty=qty,
            reduceOnly=reduce_only,
            orderLinkId="quantpilot-" + uuid.uuid4().hex[:20],
        )
        return self._check(response)
