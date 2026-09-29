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

    def get_klines(self, limit: int = 200) -> list[list[Any]]:
        response = self.session.get_kline(
            category=self.settings.category,
            symbol=self.settings.symbol,
            interval=self.settings.interval,
            limit=limit,
        )
        return self._check(response)["result"]["list"]

    def get_instrument(self) -> dict[str, Any]:
        response = self.session.get_instruments_info(
            category=self.settings.category, symbol=self.settings.symbol
        )
        return self._check(response)["result"]["list"][0]

    def place_market(self, side: str, qty: str, reduce_only: bool = False) -> dict[str, Any]:
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
