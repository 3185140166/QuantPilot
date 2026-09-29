from decimal import Decimal

import pandas as pd

from quantpilot.config import Settings
from quantpilot.execution import ExecutionGate
from quantpilot.market_data import closed_bars, klines_to_frame
from quantpilot.risk import calculate_position_size, round_step
from quantpilot.strategy import generate_signal


def test_round_step_and_position_size():
    assert round_step("0.0667", "0.001") == Decimal("0.066")
    assert calculate_position_size(10000, 100000, 98500) == Decimal("0.066")


def test_closed_bars_drops_latest_bar():
    rows = [[str(i * 60_000), "1", "2", "0.5", "1.5", "10", "15"] for i in range(3)]
    frame = klines_to_frame(rows)
    assert len(closed_bars(frame)) == 2
    assert frame.iloc[-1]["timestamp"] > frame.iloc[-2]["timestamp"]


def test_strategy_holds_with_insufficient_history():
    assert generate_signal(pd.DataFrame({"close": [1, 2, 3]})) == "HOLD"


def test_settings_defaults_are_safe(monkeypatch):
    for key in ["BYBIT_TESTNET", "DRY_RUN", "TRADING_ENABLED", "LIVE_TRADING_CONFIRM"]:
        monkeypatch.delenv(key, raising=False)
    settings = Settings.from_env(env_file="missing.env")
    assert settings.testnet is True
    assert settings.dry_run is True
    assert settings.trading_enabled is False


def test_execution_gate_does_not_submit_by_default():
    settings = Settings(api_key=None, api_secret=None)
    result = ExecutionGate(settings).market_order("Buy", "0.001")
    assert result["dryRun"] is True
