"""Order gate with a dry-run default."""

from __future__ import annotations

from typing import Any

from .config import Settings
from .exchange.binance import BinanceFuturesClient
from .exchange.bybit import BybitClient


class ExecutionGate:
    """Prevent accidental orders until both safety switches are enabled."""

    def __init__(
        self,
        settings: Settings,
        client: BybitClient | BinanceFuturesClient | None = None,
    ):
        self.settings = settings
        self.client = client

    def market_order(self, side: str, qty: str) -> dict[str, Any]:
        if self.settings.dry_run or not self.settings.trading_enabled:
            return {
                "dryRun": True,
                "side": side,
                "qty": qty,
                "symbol": self.settings.symbol,
                "exchange": self.settings.exchange,
            }
        if self.client is None:
            if self.settings.exchange == "binance":
                self.client = BinanceFuturesClient(
                    self.settings, require_private=True
                )
            else:
                self.client = BybitClient(self.settings, require_private=True)
        if self.settings.exchange == "binance":
            side = {"Buy": "BUY", "Sell": "SELL"}.get(side, side)
        return self.client.place_market(side=side, qty=qty)
