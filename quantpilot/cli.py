"""Safe command-line smoke checks."""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import Settings
from .exchange.binance import BinanceFuturesClient
from .exchange.bybit import BybitClient
from .market_data import binance_klines_to_frame, closed_bars, klines_to_frame
from .strategy import generate_signal


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["connection", "signal", "balance"])
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    settings = Settings.from_env(args.env_file)
    try:
        is_private = args.command == "balance"
        if settings.exchange == "binance":
            client = BinanceFuturesClient(settings, require_private=is_private)
        else:
            client = BybitClient(settings, require_private=is_private)
        if args.command == "balance":
            if settings.exchange == "binance":
                client.get_account()
                testnet = settings.binance_testnet
            else:
                client.get_wallet_balance()
                testnet = settings.testnet
            print(
                f"private API OK exchange={settings.exchange} "
                f"symbol={settings.symbol} testnet={testnet}"
            )
            return
        raw_klines = client.get_klines()
        frame = (
            binance_klines_to_frame(raw_klines)
            if settings.exchange == "binance"
            else klines_to_frame(raw_klines)
        )
        if args.command == "connection":
            print(
                f"received={len(frame)} exchange={settings.exchange} "
                f"symbol={settings.symbol}"
            )
            return
        bars = closed_bars(frame)
        print(f"bars={len(bars)} signal={generate_signal(bars)}")
    except Exception as exc:
        raise SystemExit(f"{settings.exchange} read-only request failed: {exc}") from exc


if __name__ == "__main__":
    main()
