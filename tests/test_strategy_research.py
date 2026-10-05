"""Causal templates, marked equity and strict paginated historical coverage."""

from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from quantpilot.backtest import run_backtest
from quantpilot.indicators import adx, atr
from quantpilot.metrics import report
from quantpilot.platform.market import HistoryError, MarketService
from quantpilot.strategy import generate_signals


def bars(count=200):
    close = 100 + np.sin(np.arange(count) / 12) * 12
    return pd.DataFrame(
        {
            "timestamp": pd.date_range(
                "2026-01-01", periods=count, freq="15min", tz="UTC"
            ),
            "open": close,
            "close": close,
            "high": close + 1,
            "low": close - 1,
            "volume": np.ones(count),
            "turnover": close,
        }
    )


@pytest.mark.parametrize(
    "template", ["ema_rsi", "ema_cross", "rsi_reversion", "bollinger_reversion"]
)
def test_every_template_is_causal_and_respects_custom_warmup(template):
    frame = bars()
    config = {"strategy_type": template, "fast": 5, "slow": 30, "rsi_period": 10}
    if template == "ema_rsi":
        config.update(rsi_lower=1, rsi_upper=99)
    complete = generate_signals(frame, **config)
    assert complete.iloc[:31].eq("HOLD").all()
    assert complete.isin(["LONG", "SHORT"]).any()
    for end in [32, 60, 100, 160]:
        pd.testing.assert_series_equal(
            generate_signals(frame.iloc[:end], **config), complete.iloc[:end]
        )


def test_breakout_uses_prior_channel_and_filters_are_causal():
    frame = bars(160)
    frame.loc[:, ["open", "close"]] = 100.0
    frame.loc[:, "high"] = 100.5
    frame.loc[:, "low"] = 99.5
    frame.loc[80, ["open", "close", "high", "low"]] = [119, 120, 121, 118]
    frame.loc[120, ["open", "close", "high", "low"]] = [81, 80, 82, 79]
    frame.loc[80, "volume"] = 3
    plain = generate_signals(frame, strategy_type="breakout")
    assert plain.iloc[80] == "LONG" and plain.iloc[120] == "SHORT"
    filtered = generate_signals(frame, strategy_type="breakout", volume_ratio=2)
    assert filtered.iloc[80] == "LONG" and filtered.iloc[120] == "HOLD"
    assert (
        generate_signals(frame, strategy_type="breakout", atr_pct_max=0.1)
        .eq("HOLD")
        .all()
    )
    for end in [81, 100, 121]:
        pd.testing.assert_series_equal(
            generate_signals(
                frame.iloc[:end], strategy_type="breakout", volume_ratio=2
            ),
            filtered.iloc[:end],
        )


def test_atr_and_adx_have_warmup_and_detect_trend_without_future_data():
    frame = bars(100)
    frame.loc[:, "close"] = np.arange(100) + 100.0
    frame.loc[:, "high"] = frame.close + 1
    frame.loc[:, "low"] = frame.close - 1
    volatility, strength = atr(frame), adx(frame)
    assert volatility.iloc[:13].isna().all()
    assert strength.iloc[:26].isna().all()
    assert volatility.iloc[-1] == pytest.approx(2)
    assert strength.iloc[-1] == pytest.approx(100)
    pd.testing.assert_series_equal(adx(frame.iloc[:60]), strength.iloc[:60])


def test_marked_drawdown_includes_open_loss_and_fees_reconcile(monkeypatch):
    frame = bars(70)
    frame.loc[:, ["open", "close"]] = 100.0
    frame.loc[64:66, "close"] = 50.0
    signals = pd.Series("HOLD", index=frame.index)
    signals.iloc[61] = "LONG"
    monkeypatch.setattr(
        "quantpilot.backtest.generate_signals", lambda _, **kwargs: signals
    )
    result = run_backtest(frame, quantity=1, fee_rate=0.001, slippage_bps=0)
    assert result.equity_curve.iloc[3] < result.realized_curve.iloc[3]
    assert report(result)["max_drawdown"] < -0.004
    assert result.final_equity == pytest.approx(
        10000 + sum(trade.pnl for trade in result.trades)
    )
    assert result.final_equity == result.equity_curve.iloc[-1]


def test_backtest_custom_warmup_and_risk_prevent_reentry(monkeypatch):
    frame = bars(70)
    frame.loc[:, ["open", "close"]] = 100.0
    frame.loc[33:, ["open", "close"]] = 90.0
    signals = pd.Series("LONG", index=frame.index)
    monkeypatch.setattr(
        "quantpilot.backtest.generate_signals", lambda _, **kwargs: signals
    )
    result = run_backtest(
        frame,
        quantity=1,
        fee_rate=0,
        slippage_bps=0,
        rules={"fast": 5, "slow": 30, "max_drawdown_pct": 0.05},
        max_notional=1000,
    )
    assert result.risk_halted and len(result.trades) == 1
    assert result.trades[0].entry_time == frame.iloc[32].timestamp


@pytest.mark.parametrize("exchange", ["bybit", "binance"])
def test_history_pages_are_bounded_ordered_cached_and_reject_gaps(
    exchange, monkeypatch
):
    market = MarketService("exchange")
    monkeypatch.setattr(market, "require_symbol", lambda *args: None)
    client = Mock()
    monkeypatch.setattr(market, "client", lambda *args: client)

    def klines(limit, start, end):
        stamps = np.arange(start, end + 1, 900000, dtype=np.int64)
        rows = [
            [str(stamp), "100", "101", "99", "100", "10", "1000"]
            if exchange == "bybit"
            else [
                int(stamp),
                "100",
                "101",
                "99",
                "100",
                "10",
                int(stamp) + 899999,
                "1000",
                1,
                "1",
                "100",
                "0",
            ]
            for stamp in stamps
        ]
        return list(reversed(rows)) if exchange == "bybit" else rows

    client.get_klines.side_effect = klines
    first = market.history(
        exchange, "BTCUSDT", "15", "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z", 61
    )
    assert len(first) == 2976 + 62
    assert client.get_klines.call_count == 4
    assert first.timestamp.is_monotonic_increasing
    market.history(
        exchange, "BTCUSDT", "15", "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z", 61
    )
    assert client.get_klines.call_count == 4
    market._history_cache.clear()
    client.get_klines.side_effect = lambda **kwargs: klines(**kwargs)[1:]
    with pytest.raises(HistoryError, match="缺少"):
        market.history(
            exchange,
            "BTCUSDT",
            "15",
            "2026-01-01T00:00:00Z",
            "2026-02-01T00:00:00Z",
            61,
        )
