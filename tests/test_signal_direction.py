"""Signals choose both directions causally; Testnet translates short to Sell."""

import numpy as np
import pandas as pd
from sqlalchemy import select
from test_platform import platform as platform  # noqa: PLC0414
from test_testnet import ready

from quantpilot.platform.models import TestnetOrder as Order
from quantpilot.platform.testnet import process_testnet
from quantpilot.strategy import describe_signal, generate_signal, generate_signals


def test_both_cross_directions_and_trend_without_event():
    close = 100 + 10 * np.sin(np.arange(240) / 10)
    bars = pd.DataFrame({"close": close})
    rules = {"strategy_type": "ema_cross", "fast": 5, "slow": 20}
    signals = generate_signals(bars, **rules)
    assert {"LONG", "SHORT", "HOLD"}.issubset(set(signals))
    for signal in ("LONG", "SHORT"):
        index = signals[signals == signal].index[-1]
        causal = bars.iloc[: index + 1]
        assert generate_signal(causal, **rules) == signal
        assert "穿过" in describe_signal(causal, signal, **rules)["signal_basis"]
    index = signals[signals == "HOLD"].index[-1]
    causal = bars.iloc[: index + 1]
    detail = describe_signal(causal, "HOLD", **rules)
    assert detail["ema_fast"] != detail["ema_slow"]
    assert "没有新的" in detail["signal_basis"]


def test_short_signal_submits_sell_in_fake_execution(platform, monkeypatch):
    factory, market, broker = ready(platform, monkeypatch)
    monkeypatch.setattr(
        "quantpilot.platform.testnet.generate_signal", lambda *_a, **_k: "SHORT"
    )
    process_testnet(factory, market, "short-owner", broker)
    with factory() as db:
        opening = db.scalar(select(Order).where(Order.action == "open"))
        assert opening and opening.side == "Sell"
    assert broker.positions["BTCUSDT"]["side"] == "Sell"
