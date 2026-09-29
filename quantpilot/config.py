"""Typed, fail-closed application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _bool(name: str, value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true/false, got {value!r}")


@dataclass(frozen=True)
class Settings:
    api_key: str | None
    api_secret: str | None
    testnet: bool = True
    dry_run: bool = True
    trading_enabled: bool = False
    live_trading_confirm: str = ""
    category: str = "linear"
    symbol: str = "BTCUSDT"
    interval: str = "15"
    risk_per_trade: float = 0.01
    max_leverage: int = 2

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> Settings:
        # Explicit path makes startup independent of the process working directory.
        load_dotenv(dotenv_path=env_file, override=False)
        testnet = _bool("BYBIT_TESTNET", os.getenv("BYBIT_TESTNET"), True)
        dry_run = _bool("DRY_RUN", os.getenv("DRY_RUN"), True)
        trading_enabled = _bool(
            "TRADING_ENABLED", os.getenv("TRADING_ENABLED"), False
        )
        confirmation = os.getenv("LIVE_TRADING_CONFIRM", "")
        if not testnet and confirmation != "I_UNDERSTAND_LIVE_TRADING":
            raise ValueError(
                "Mainnet requires LIVE_TRADING_CONFIRM="
                "I_UNDERSTAND_LIVE_TRADING"
            )
        if not dry_run and not trading_enabled:
            raise ValueError("DRY_RUN=false requires TRADING_ENABLED=true")
        return cls(
            api_key=os.getenv("BYBIT_API_KEY") or None,
            api_secret=os.getenv("BYBIT_API_SECRET") or None,
            testnet=testnet,
            dry_run=dry_run,
            trading_enabled=trading_enabled,
            live_trading_confirm=confirmation,
            category=os.getenv("BYBIT_CATEGORY", "linear"),
            symbol=os.getenv("BYBIT_SYMBOL", "BTCUSDT"),
            interval=os.getenv("BYBIT_INTERVAL", "15"),
            risk_per_trade=float(os.getenv("RISK_PER_TRADE", "0.01")),
            max_leverage=int(os.getenv("MAX_LEVERAGE", "2")),
        )

    def require_private_api(self) -> None:
        if not self.api_key or not self.api_secret:
            raise ValueError(
                "BYBIT_API_KEY and BYBIT_API_SECRET are required for private API calls"
            )
