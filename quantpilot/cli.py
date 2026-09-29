"""Safe command-line smoke checks."""

from __future__ import annotations

import argparse
from pathlib import Path

from .config import Settings
from .exchange.bybit import BybitClient
from .market_data import closed_bars, klines_to_frame
from .strategy import generate_signal


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["connection", "signal"])
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    settings = Settings.from_env(args.env_file)
    try:
        client = BybitClient(settings)
        frame = klines_to_frame(client.get_klines())
        if args.command == "connection":
            print(
                f"received={len(frame)} symbol={settings.symbol} "
                f"testnet={settings.testnet}"
            )
            return
        bars = closed_bars(frame)
        print(f"bars={len(bars)} signal={generate_signal(bars)}")
    except Exception as exc:
        raise SystemExit(f"Bybit read-only request failed: {exc}") from exc


if __name__ == "__main__":
    main()
