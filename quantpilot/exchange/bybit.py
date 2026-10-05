"""Thin Bybit V5 adapter. Private calls are never made implicitly."""

from __future__ import annotations

import uuid
from typing import Any

from ..config import Settings


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
        )

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
