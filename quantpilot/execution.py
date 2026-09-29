"""Order gate with a dry-run default."""

from __future__ import annotations

from typing import Any

from .config import Settings
from .exchange.bybit import BybitClient


class ExecutionGate:
    """Prevent accidental orders until both safety switches are enabled."""

    def __init__(self, settings: Settings, client: BybitClient | None = None):
        self.settings = settings
        self.client = client

    def market_order(self, side: str, qty: str) -> dict[str, Any]:
        if self.settings.dry_run or not self.settings.trading_enabled:
            return {
                "dryRun": True,
                "side": side,
                "qty": qty,
                "symbol": self.settings.symbol,
            }
        client = self.client or BybitClient(self.settings, require_private=True)
        return client.place_market(side=side, qty=qty)
