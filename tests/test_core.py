from decimal import Decimal

import pandas as pd

from quantpilot.config import Settings
from quantpilot.execution import ExecutionGate
from quantpilot.market_data import (
    binance_klines_to_frame,
    closed_bars,
    klines_to_frame,
)
from quantpilot.risk import calculate_position_size, round_step
from quantpilot.strategy import generate_signal


def test_separate_bybit_testnet_credentials_do_not_fall_back_to_mainnet(
    monkeypatch, tmp_path
):
    path = tmp_path / "test.env"
    path.write_text(
        "EXCHANGE=bybit\nBYBIT_TESTNET=true\nDRY_RUN=true\nTRADING_ENABLED=false\n"
        "BYBIT_API_KEY=main-key\nBYBIT_API_SECRET=main-secret\n"
        "BYBIT_TESTNET_API_KEY=\nBYBIT_TESTNET_API_SECRET=\n",
        encoding="utf-8",
    )
    for key in (
        "EXCHANGE",
        "BYBIT_TESTNET",
        "BYBIT_API_KEY",
        "BYBIT_API_SECRET",
        "BYBIT_TESTNET_API_KEY",
        "BYBIT_TESTNET_API_SECRET",
        "DRY_RUN",
        "TRADING_ENABLED",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = Settings.from_env(path)
    assert settings.testnet and settings.api_key is None and settings.api_secret is None
    monkeypatch.setenv("BYBIT_TESTNET_API_KEY", "test-key")
    monkeypatch.setenv("BYBIT_TESTNET_API_SECRET", "test-secret")
    settings = Settings.from_env(path)
    assert settings.api_key == "test-key" and settings.api_secret == "test-secret"
    assert settings.dry_run and not settings.trading_enabled


def test_round_step_and_position_size():
    assert round_step("0.0667", "0.001") == Decimal("0.066")
    assert calculate_position_size(10000, 100000, 98500) == Decimal("0.066")


def test_closed_bars_drops_latest_bar():
    rows = [[str(i * 60_000), "1", "2", "0.5", "1.5", "10", "15"] for i in range(3)]
    frame = klines_to_frame(rows)
    assert len(closed_bars(frame)) == 2
    assert frame.iloc[-1]["timestamp"] > frame.iloc[-2]["timestamp"]


def test_binance_klines_are_normalized():
    rows = [[0, "1", "2", "0.5", "1.5", "10", 60_000, "15", 3, "5", "7", "0"]]
    frame = binance_klines_to_frame(rows)
    assert frame.iloc[0]["close"] == 1.5
    assert frame.iloc[0]["turnover"] == 15


def test_strategy_holds_with_insufficient_history():
    assert generate_signal(pd.DataFrame({"close": [1, 2, 3]})) == "HOLD"


def test_settings_defaults_are_safe(monkeypatch):
    for key in [
        "BYBIT_TESTNET",
        "BINANCE_TESTNET",
        "DRY_RUN",
        "TRADING_ENABLED",
        "LIVE_TRADING_CONFIRM",
    ]:
        monkeypatch.delenv(key, raising=False)
    settings = Settings.from_env(env_file="missing.env")
    assert settings.testnet is True
    assert settings.binance_testnet is True
    assert settings.dry_run is True
    assert settings.trading_enabled is False


def test_execution_gate_does_not_submit_by_default():
    settings = Settings(api_key=None, api_secret=None)
    result = ExecutionGate(settings).market_order("Buy", "0.001")
    assert result["dryRun"] is True


def test_backtest_curve_includes_initial_and_final_equity(monkeypatch):
    from quantpilot.backtest import run_backtest

    monkeypatch.setattr(
        "quantpilot.backtest.generate_signals",
        lambda bars: pd.Series("LONG", index=bars.index),
    )
    bars = pd.DataFrame(
        {"timestamp": range(65), "open": [100.0] * 65, "close": [100.0] * 64 + [90.0]}
    )
    result = run_backtest(bars, initial_equity=1000.0, quantity=1.0)
    assert result.equity_curve.iloc[0] == 1000.0
    assert result.equity_curve.iloc[-1] == result.final_equity
    assert result.final_equity < 1000.0
    assert abs(result.total_return - (result.final_equity / 1000.0 - 1)) < 1e-12
