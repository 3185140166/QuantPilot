"""Regression tests for indicators, causal signals and dashboard interactions."""

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from quantpilot.backtest import run_backtest
from quantpilot.config import Settings
from quantpilot.indicators import rsi
from quantpilot.strategy import generate_signal, generate_signals


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    """Fail immediately if any test attempts a real HTTP request."""

    def blocked(*args, **kwargs):
        raise AssertionError("Network access is disabled in local tests")

    monkeypatch.setattr("requests.sessions.Session.request", blocked)
    monkeypatch.setattr("requests.sessions.Session.send", blocked)


def sample_bars(count=200):
    close = 100 + np.sin(np.arange(count) / 12) * 12
    return pd.DataFrame(
        {
            "timestamp": pd.date_range(
                "2026-01-01", periods=count, freq="15min", tz="UTC"
            ),
            "open": close - 0.2,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": np.ones(count),
            "turnover": close,
        }
    )


@pytest.mark.parametrize(
    "values,expected",
    [
        (list(range(40)), 100.0),
        (list(range(40, 0, -1)), 0.0),
        ([10] * 40, 50.0),
    ],
)
def test_rsi_direction_and_warmup(values, expected):
    result = rsi(pd.Series(values, dtype=float))
    assert result.iloc[:14].isna().all()
    assert result.iloc[-1] == expected


def test_signals_do_not_look_into_future():
    bars = sample_bars()
    complete = generate_signals(bars)
    for end in [62, 90, 120, 160]:
        prefix = bars.iloc[:end]
        pd.testing.assert_series_equal(generate_signals(prefix), complete.iloc[:end])
        assert generate_signal(prefix) == complete.iloc[end - 1]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"quantity": 0},
        {"initial_equity": float("nan")},
        {"fee_rate": -0.01},
        {"slippage_bps": 10000},
    ],
)
def test_backtest_rejects_invalid_parameters(kwargs):
    with pytest.raises(ValueError):
        run_backtest(sample_bars(), **kwargs)


@pytest.mark.parametrize("problem", ["duplicate", "reverse", "nan", "zero"])
def test_backtest_rejects_invalid_data(problem):
    bars = sample_bars()
    if problem == "duplicate":
        bars.loc[1, "timestamp"] = bars.loc[0, "timestamp"]
    elif problem == "reverse":
        bars = bars.iloc[::-1]
    else:
        bars.loc[70, "close"] = float("nan") if problem == "nan" else 0
    with pytest.raises(ValueError):
        run_backtest(bars)


def test_signals_execute_next_open_and_costs_reconcile(monkeypatch):
    bars = sample_bars(70)
    signals = pd.Series("HOLD", index=bars.index)
    signals.iloc[61] = "LONG"
    signals.iloc[65] = "SHORT"
    monkeypatch.setattr("quantpilot.backtest.generate_signals", lambda _: signals)
    result = run_backtest(bars, quantity=1, fee_rate=0.001, slippage_bps=2)
    assert len(result.trades) == 2
    assert result.trades[0].entry_time == bars.iloc[62].timestamp
    assert result.trades[0].entry_price == pytest.approx(bars.iloc[62].open * 1.0002)
    assert result.trades[0].exit_time == bars.iloc[66].timestamp
    assert result.final_equity == pytest.approx(
        10000 + sum(t.pnl for t in result.trades)
    )
    assert result.equity_curve.iloc[-1] == result.final_equity


def test_dashboard_controls_and_exchange_switch():
    with (
        patch("quantpilot.config.Settings.from_env", return_value=Settings(None, None)),
        patch("quantpilot.exchange.bybit.BybitClient.get_klines", return_value=[]),
        patch(
            "quantpilot.exchange.binance.BinanceFuturesClient.get_klines",
            return_value=[],
        ),
        patch("quantpilot.market_data.klines_to_frame", return_value=sample_bars()),
        patch(
            "quantpilot.market_data.binance_klines_to_frame", return_value=sample_bars()
        ),
    ):
        app = AppTest.from_file(
            Path(__file__).resolve().parents[1] / "quantpilot/dashboard.py"
        ).run(timeout=30)
        assert not app.exception
        assert len(app.tabs) == 4
        app.number_input[0].set_value(20000.0).run(timeout=30)
        assert not app.exception
        app.checkbox[0].uncheck().run(timeout=30)
        assert not app.exception
        app.selectbox[0].select("binance").run(timeout=30)
        assert not app.exception
        assert not app.error


def test_dashboard_connection_error_is_recoverable():
    settings = Settings(None, None, symbol="UNAVAILABLE")
    with (
        patch("quantpilot.config.Settings.from_env", return_value=settings),
        patch(
            "quantpilot.exchange.bybit.BybitClient.get_klines",
            side_effect=RuntimeError("offline"),
        ),
    ):
        app = AppTest.from_file(
            Path(__file__).resolve().parents[1] / "quantpilot/dashboard.py"
        ).run(timeout=30)
        assert not app.exception
        assert len(app.error) == 1
        assert len(app.button) == 1
