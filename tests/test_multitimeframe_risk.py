"""Offline causality, account exposure and independent-window checks."""

import copy
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError
from test_platform import AUTH
from test_platform import platform as platform  # noqa: PLC0414
from test_testnet import INSTRUMENT, ready
from test_testnet_recovery import api

from quantpilot.multitimeframe import hourly_direction
from quantpilot.platform.account_exposure import summarize
from quantpilot.platform.models import BacktestJob
from quantpilot.platform.rolling_validation import plan, report
from quantpilot.platform.rules import StrategyRules
from quantpilot.platform.schemas import BacktestCreate, RollingValidation
from quantpilot.platform.schemas import TestnetSetup as Setup
from quantpilot.sizing import allocation, number, spendable
from quantpilot.strategy import generate_signal, generate_signals


def candles(count=500):
    x = np.arange(count)
    close = 100 + x * 0.15 + np.sin(x / 2) * 2
    return pd.DataFrame(
        {
            "timestamp": pd.date_range(
                "2025-01-01", periods=count, freq="15min", tz="UTC"
            ),
            "open": close,
            "close": close,
            "high": close + 1,
            "low": close - 1,
            "volume": 100,
        }
    )


def test_hourly_direction_ignores_unclosed_hour_and_resets_after_gaps():
    bars = candles()
    baseline = hourly_direction(bars, 2, 5, 0)
    prefix = bars.iloc[:31].copy()  # 07:30 opens, decision at 07:45.
    altered = prefix.copy()
    altered.loc[28:, "close"] = 1
    assert hourly_direction(prefix, 2, 5, 0).iloc[-1] == baseline.iloc[30]
    assert hourly_direction(altered, 2, 5, 0).iloc[-1] == baseline.iloc[30]
    broken = bars.drop(index=range(100, 104))
    direction = hourly_direction(broken, 2, 5, 0)
    assert direction.loc[104:123].eq("HOLD").all()
    assert baseline.iloc[40:].isin(["LONG", "SHORT"]).any()
    for end in (153, 250, 499):
        assert (
            hourly_direction(bars.iloc[end - 31 : end], 2, 5, 0).iloc[-1]
            == baseline.iloc[end - 1]
        )


def test_shared_signals_are_causal_and_live_matches_prefix():
    bars = candles()
    rules = StrategyRules(
        fast=2,
        slow=5,
        strategy_type="ema_cross",
        hourly_trend_filter=True,
        hourly_min_gap_pct=0,
    )
    signals = generate_signals(bars, **rules.signal_options)
    directions = hourly_direction(bars, 2, 5, 0)
    assert signals.ne("HOLD").any()
    assert signals[signals.ne("HOLD")].eq(directions[signals.ne("HOLD")]).all()
    changed = bars.copy()
    changed.loc[300:, "close"] *= 10
    pd.testing.assert_series_equal(
        signals.iloc[:300], generate_signals(changed, **rules.signal_options).iloc[:300]
    )
    for stop in (80, 151, 299):
        assert (
            generate_signal(bars.iloc[:stop], **rules.signal_options)
            == signals.iloc[stop - 1]
        )
    with pytest.raises(ValidationError):
        Setup(interval="60", rules=rules)
    with pytest.raises(ValidationError):
        BacktestCreate(interval="5", rules=rules)


def test_reserve_is_shared_and_external_exposure_is_visible_without_mutation():
    config = Setup(cash_reserve_pct=20, fee_rate=0, slippage_bps=0)
    assert spendable(config, 50) == 10
    result = allocation(config, 200, 100, "LONG", INSTRUMENT, available=50)
    assert number(result["margin"]) <= 10
    assert not allocation(config, 200, 100, "LONG", INSTRUMENT, available=39)[
        "accepted"
    ]
    rows = [
        {
            "symbol": "BTCUSDT",
            "source": "system",
            "size": "1",
            "avgPrice": "100",
            "markPrice": "101",
            "stopLoss": "99",
            "side": "Buy",
        },
        {
            "symbol": "ETHUSDT",
            "source": "external",
            "size": "2",
            "avgPrice": "100",
            "markPrice": "101",
            "stopLoss": "0",
            "side": "Sell",
        },
    ]
    ownership = {"positions": rows, "reserved_notional": "20", "uncertain": False}
    original = copy.deepcopy(ownership)
    value = summarize({"available_balance": "50"}, ownership, config)
    assert value["groups"]["external"]["short_notional"] == "202"
    assert value["known_stop_risk"] == "1"
    assert not value["risk_complete"]
    assert value["unprotected_symbols"] == ["ETHUSDT"]
    assert number(value["spendable_balance"]) == 10
    assert ownership == original


def test_hourly_policy_queues_one_shared_research_task(platform, monkeypatch, tmp_path):
    from quantpilot.platform.models import TestnetAccount as Account
    from quantpilot.platform.research_worker import process_job

    factory, market, broker = ready(platform, monkeypatch)
    monkeypatch.setattr("quantpilot.platform.api.TestnetBroker", lambda: broker)
    monkeypatch.chdir(tmp_path)
    rules = StrategyRules(
        fast=2,
        slow=5,
        strategy_type="ema_cross",
        stop_loss_pct=2,
        hourly_trend_filter=True,
    )
    with factory.begin() as db:
        current = db.get(Account, 1)
        current.enabled = False
        current.configuration = Setup(
            symbols=["BTCUSDT"], interval="15", rules=rules, cash_reserve_pct=20
        ).model_dump(mode="json")
    with api(factory, market) as client:
        response = client.post(
            "/api/trading/comparisons",
            headers=AUTH,
            json={
                "symbol": "BTCUSDT",
                "start_at": "2025-01-01T00:00:00Z",
                "end_at": "2025-04-01T00:00:00Z",
            },
        )
        assert response.status_code == 202, response.text
        jobs = response.json()["jobs"]
        assert len(jobs) == 1 and jobs[0]["parameters"]["interval"] == "15"
        assert jobs[0]["parameters"]["risk_sizing"]["cash_reserve_pct"] == 20
        process_job(factory, market, "hourly-research")
        detail = client.get(f"/api/backtests/{jobs[0]['id']}", headers=AUTH).json()
        assert detail["status"] == "completed", detail
        assert detail["result"]["rules"]["hourly_trend_filter"]
        assert not broker.calls


def source_parameters():
    config = Setup(symbols=["BTCUSDT", "ETHUSDT"])
    return {
        **BacktestCreate(
            start_at="2025-01-01T00:00:00Z",
            end_at="2025-04-01T00:00:00Z",
            rules=config.rules,
        ).model_dump(mode="json"),
        "job_type": "portfolio",
        "source": "exchange",
        "market_environment": "testnet",
        "portfolio_config": config.model_dump(mode="json"),
        "execution": {"order_type": "Market", "include_funding": True},
    }


def test_rolling_plan_is_disjoint_and_inherits_frozen_research():
    source = SimpleNamespace(
        id=1, status="completed", result={"metrics": {}}, parameters=source_parameters()
    )
    payload = RollingValidation(
        start_at="2025-04-01T00:00:00Z", end_at="2025-05-01T00:00:00Z", windows=3
    )
    jobs = plan(source, payload)
    assert len(jobs) == 3
    assert jobs[0]["start_at"] == "2025-04-01T00:00:00Z"
    assert jobs[-1]["end_at"] == "2025-05-01T00:00:00Z"
    for index, params in enumerate(jobs):
        assert params["rules"] == source.parameters["rules"]
        assert params["portfolio_config"] == source.parameters["portfolio_config"]
        assert params["validation_parent_job_id"] == 1
        if index:
            assert params["start_at"] == jobs[index - 1]["end_at"]
    with pytest.raises(ValueError):
        plan(
            source,
            RollingValidation(start_at="2025-03-01T00:00:00Z", end_at=payload.end_at),
        )


def test_rolling_queue_is_atomic_and_incomplete_windows_cannot_pass(
    platform, monkeypatch
):
    factory, market, broker = ready(platform, monkeypatch)
    with factory.begin() as db:
        source = BacktestJob(
            status="completed", parameters=source_parameters(), result={"metrics": {}}
        )
        db.add(source)
        db.flush()
        source_id = source.id
        db.add_all([BacktestJob(parameters={}) for _ in range(8)])
    with api(factory, market) as client:
        url = f"/api/trading/portfolio/{source_id}/rolling-validation"
        payload = {
            "start_at": "2025-04-01T00:00:00Z",
            "end_at": "2025-05-01T00:00:00Z",
            "windows": 3,
        }
        assert client.post(url, headers=AUTH, json=payload).status_code == 409
        with factory.begin() as db:
            from sqlalchemy import delete, select

            assert len(list(db.scalars(select(BacktestJob)))) == 9
            db.execute(delete(BacktestJob).where(BacktestJob.status == "pending"))
        response = client.post(url, headers=AUTH, json=payload)
        assert response.status_code == 202, response.text
        jobs = response.json()["jobs"]
        value = client.get(
            f"/api/trading/portfolio/{jobs[0]['id']}/rolling-validation", headers=AUTH
        ).json()
        assert len(value["windows"]) == 3
        assert not value["complete"] and not value["passed"]
        assert not broker.calls


def test_all_windows_must_pass_not_just_selected_window(platform):
    _client, factory, _market = platform
    from test_quantitative_workflow import passing_evidence

    from quantpilot.platform.models import TestnetAccount as Account
    from quantpilot.platform.strategy_review import current_review
    from quantpilot.platform.testnet import account

    config = Setup.model_validate(source_parameters()["portfolio_config"])
    config.research_guard.min_validation_windows = 3
    config.research_guard.enabled = True
    _, _, evidence = passing_evidence()
    account(factory)
    with factory.begin() as db:
        source = BacktestJob(
            status="completed", parameters=source_parameters(), result={"metrics": {}}
        )
        db.add(source)
        db.flush()
        params = plan(
            source,
            RollingValidation(
                start_at="2025-04-01T00:00:00Z", end_at="2025-05-01T00:00:00Z"
            ),
        )
        rows = [
            BacktestJob(
                status="completed",
                parameters=p,
                result=copy.deepcopy(evidence),
            )
            for p in params
        ]
        db.add_all(rows)
        db.flush()
        assert report(db, rows[0], config)["passed"]
        current = db.get(Account, 1)
        current.configuration = config.model_dump(mode="json")
        current.research_origin = {
            "job_id": rows[0].id,
            "applied_configuration": current.configuration,
            "modified": False,
        }
        assert current_review(db, current)["eligible"]
        bad = copy.deepcopy(evidence)
        bad["metrics"]["total_return"] = -0.1
        rows[-1].result = bad
        assert not report(db, rows[0], config)["passed"]
        assert "多窗口独立验证" in current_review(db, current)["failed"]
        rows[-1].result = copy.deepcopy(evidence)
        rows[-1].status = "failed"
        assert not report(db, rows[0], config)["passed"]
        rows[-1].status = "completed"
        rows[-1].parameters = {
            **rows[-1].parameters,
            "rules": StrategyRules(fast=21).model_dump(),
        }
        assert not report(db, rows[0], config)["passed"]
