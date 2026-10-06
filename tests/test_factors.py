"""Factor diagnostics must stay causal, purged, finite and non-executing."""

import json

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select
from test_platform import AUTH
from test_platform import platform as platform  # noqa: PLC0414

from quantpilot.factors import DEFINITIONS, features, observations
from quantpilot.platform.factor_research import ablation, analyze
from quantpilot.platform.models import BacktestJob
from quantpilot.platform.models import TestnetOrder as Order
from quantpilot.platform.research_worker import process_job
from quantpilot.platform.rules import StrategyRules


def bars(count=1800):
    index = np.arange(count)
    close = 100 + index * 0.01 + 5 * np.sin(index / 9)
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2025-01-01", periods=count, freq="h", tz="UTC"),
            "open": close + np.cos(index / 3) * 0.1,
            "close": close,
            "high": close + 1,
            "low": close - 1,
            "volume": (10 + index % 31).astype(float),
        }
    )


def test_features_are_prefix_causal_and_missing_data_is_not_fabricated():
    frame = bars()
    rules = StrategyRules().model_dump()
    full = features(frame, rules)
    pd.testing.assert_frame_equal(full.iloc[:800], features(frame.iloc[:800], rules))
    changed = frame.copy()
    changed.loc[800:, ["open", "close", "high", "low", "volume"]] *= 4
    pd.testing.assert_frame_equal(full.iloc[:800], features(changed, rules).iloc[:800])
    close_only = observations(frame[["close"]], rules)
    assert next(row for row in close_only if row["key"] == "volume")["value"] is None
    json.dumps(close_only, allow_nan=False)


def test_volume_factor_uses_previous_window_and_filter_roles():
    frame = bars(100)
    frame["volume"] = 10
    frame.loc[99, "volume"] = 100
    result = observations(
        frame, {"volume_period": 20, "volume_ratio": 12, "atr_pct_max": 0, "adx_min": 1}
    )
    volume = next(row for row in result if row["key"] == "volume")
    assert volume["value"] == 10 and volume["passed"] is False
    assert next(row for row in result if row["key"] == "volatility")["passed"] is None
    assert next(row for row in result if row["key"] == "strength")["enabled"] is True


def test_training_thresholds_and_ic_never_use_validation_prices():
    frame = bars()
    rules = StrategyRules().model_dump()
    boundary = frame.timestamp.iloc[1000]
    baseline = analyze(frame, rules, split_at=boundary)
    changed = frame.copy()
    changed.loc[1000:, ["open", "close", "high", "low", "volume"]] *= np.linspace(
        1, 4, len(frame) - 1000
    )[:, None]
    modified = analyze(changed, rules, split_at=boundary)
    for before, after in zip(baseline["rows"], modified["rows"], strict=True):
        for key in ["train_ic", "lower", "upper", "direction", "train_samples"]:
            assert before[key] == after[key]
    assert baseline["redundancy"] == modified["redundancy"]
    assert not baseline["affects_execution"]
    json.dumps(modified, allow_nan=False)


def test_open_to_open_alignment_split_purge_and_nonoverlapping_samples(monkeypatch):
    frame = bars(1000)
    rules = StrategyRules().model_dump()
    boundary = frame.timestamp.iloc[600]
    feature_values = pd.DataFrame({key: np.arange(1000) for key in DEFINITIONS})
    monkeypatch.setattr(
        "quantpilot.platform.factor_research.features", lambda *_args: feature_values
    )
    result = analyze(frame, rules, split_at=boundary)
    for horizon in (1, 4, 12):
        row = next(
            row
            for row in result["rows"]
            if row["key"] == "trend" and row["horizon"] == horizon
        )
        train_indices = np.arange(61, 600 - horizon - 1, horizon)
        test_indices = np.arange(600, 1000 - horizon - 1, horizon)
        returns = frame.open.shift(-(horizon + 1)) / frame.open.shift(-1) - 1
        expected = (
            pd.Series(train_indices)
            .rank()
            .corr(returns.iloc[train_indices].reset_index(drop=True).rank())
        )
        assert row["train_samples"] == len(train_indices)
        assert row["test_samples"] == len(test_indices)
        assert row["train_ic"] == pytest.approx(expected)


def test_fee_and_slippage_only_change_tail_net_not_fit_or_direction():
    frame = bars(3000)
    rules = StrategyRules().model_dump()
    baseline = analyze(frame, rules, fee_rate=0, slippage_bps=0)
    stressed = analyze(frame, rules, fee_rate=0.001, slippage_bps=10)
    measured = 0
    for before, after in zip(baseline["rows"], stressed["rows"], strict=True):
        assert (
            before["train_ic"] == after["train_ic"]
            and before["direction"] == after["direction"]
        )
        if before["tail_net_mean"] is not None:
            measured += 1
            assert after["tail_net_mean"] == pytest.approx(
                before["tail_net_mean"] - 0.004
            )
    assert measured > 0


def test_constant_and_insufficient_features_are_finite_and_explicit():
    frame = bars(150)
    frame.loc[:, ["open", "high", "low", "close"]] = 100
    frame["volume"] = 0
    report = analyze(frame, StrategyRules().model_dump())
    assert report["warnings"]
    assert all(
        row["train_ic"] is None and row["tail_net_mean"] is None
        for row in report["rows"]
    )
    json.dumps(report, allow_nan=False)
    small = analyze(frame.iloc[:10], StrategyRules().model_dump())
    assert small["rows"] == [] and small["warnings"]


def test_ablation_changes_one_enabled_filter_and_never_mutates_source():
    rules = StrategyRules(volume_ratio=1, adx_min=20).model_dump()
    original = dict(rules)
    called = []

    def evaluate(candidate):
        called.append(candidate)
        return {"total_return": 0, "max_drawdown": 0, "trades": 0}

    report = ablation(rules, evaluate)
    assert len(report) == 3 and rules == original
    assert called[0] == rules
    assert [
        {key for key in rules if candidate[key] != rules[key]}
        for candidate in called[1:]
    ] == [{"volume_ratio"}, {"adx_min"}]
    assert ablation(StrategyRules().model_dump(), evaluate) == []


def test_queued_backtest_persists_factor_and_ablation_without_orders(platform):
    client, factory, market = platform
    response = client.post(
        "/api/backtests",
        headers=AUTH,
        json={"symbol": "BTCUSDT", "rules": {"volume_ratio": 1}, "limit": 500},
    )
    assert response.status_code == 202
    identifier = response.json()["id"]
    process_job(factory, market, "factor-research-owner")
    with factory() as db:
        job = db.get(BacktestJob, identifier)
        assert job.status == "completed", job.error
        report = job.result["factor_analysis"]
        assert report["split_kind"] == "internal_70_30" and len(report["rows"]) == 18
        assert len(report["ablation"]) == 2
        assert db.scalar(select(Order)) is None


def test_order_replay_keeps_observed_factors(platform, monkeypatch):
    from test_testnet import ready

    from quantpilot.platform.testnet import process_testnet

    factory, market, broker = ready(platform, monkeypatch)
    process_testnet(factory, market, "factor-execution-owner", broker)
    with factory() as db:
        decision = db.scalar(select(Order)).context["decision"]
        assert len(decision["factors"]) == 6
        assert (
            next(row for row in decision["factors"] if row["key"] == "volume")["value"]
            == 1
        )
        json.dumps(decision, allow_nan=False)
