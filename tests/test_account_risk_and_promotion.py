"""Behavioral checks for fixed-budget limits and explicitly reviewed promotion."""

from decimal import Decimal

import pandas as pd
import pytest
from test_platform import AUTH
from test_platform import platform as platform  # noqa: PLC0414
from test_testnet import ready
from test_testnet_recovery import api

from quantpilot.account_risk import evaluate_account_risk
from quantpilot.backtest import run_backtest
from quantpilot.metrics import report
from quantpilot.platform.models import BacktestJob
from quantpilot.platform.models import TestnetAccount as Account
from quantpilot.platform.schemas import BacktestCreate
from quantpilot.platform.schemas import TestnetSetup as Setup
from quantpilot.platform.testnet import account, process_testnet


@pytest.mark.parametrize("equity, expected", [(241, False), (240, True), (239, True)])
def test_prior_profit_does_not_expand_drawdown_allowance(equity, expected):
    state = evaluate_account_risk(
        budget=200,
        equity=equity,
        peak_equity=250,
        day_equity=200,
        daily_loss_pct=5,
        max_drawdown_pct=5,
    )
    assert state.halted is expected
    assert state.drawdown_limit == 10
    assert state.as_dict()["basis"] == "fixed_budget"


def test_daily_loss_uses_budget_not_starting_day_equity():
    state = evaluate_account_risk(
        budget=200,
        equity=246,
        peak_equity=250,
        day_equity=250,
        daily_loss_pct=2,
        max_drawdown_pct=5,
    )
    assert state.halted and state.daily_loss_limit == 4
    assert Decimal(state.as_dict()["daily_loss_pct"]) == 2
    with pytest.raises(ValueError):
        evaluate_account_risk(
            budget=0,
            equity=200,
            peak_equity=200,
            day_equity=200,
            daily_loss_pct=2,
            max_drawdown_pct=5,
        )


@pytest.mark.parametrize("close, halted", [(2050, True), (2150, False)])
def test_backtest_drawdown_matches_execution_limit_after_profit(
    monkeypatch, close, halted
):
    monkeypatch.setattr("quantpilot.backtest.signal_warmup", lambda _: 1)
    monkeypatch.setattr(
        "quantpilot.backtest.generate_signals",
        lambda bars, **_: pd.Series(["HOLD", "LONG", "HOLD", "HOLD", "HOLD"]),
    )
    bars = pd.DataFrame(
        {
            "timestamp": pd.date_range("2025-01-01", periods=5, freq="h", tz="UTC"),
            "open": [100, 100, 100, 2600, close],
            "close": [100, 100, 2600, close, close],
            "high": [100, 100, 2600, 2600, close],
            "low": [100, 100, 100, close, close],
        }
    )
    size = {
        "budget": 200,
        "risk_per_trade_pct": 0.5,
        "portfolio_risk_pct": 1,
        "leverage": 2,
        "daily_loss_pct": 5,
        "max_drawdown_pct": 5,
        "constraints": {
            "qty_step": 0.001,
            "min_qty": 0.001,
            "max_qty": 100,
            "min_notional": 0,
            "tick_size": 0.01,
        },
    }
    result = run_backtest(
        bars,
        initial_equity=200,
        fee_rate=0,
        slippage_bps=0,
        rules={"stop_loss_pct": 50, "max_drawdown_pct": 0.1},
        risk_sizing=size,
    )
    assert result.risk_halted is halted
    assert result.risk_summary["policy"] == "fixed_budget_v1"
    assert result.risk_summary["max_drawdown_pct"] == (5.5 if halted else 4.5)
    assert report(result)["max_drawdown"] == pytest.approx(-0.044 if halted else -0.036)


def test_executor_and_status_use_the_same_fixed_budget_calculation(
    platform, monkeypatch
):
    factory, market, broker = ready(platform, monkeypatch)
    monkeypatch.setattr(
        "quantpilot.platform.testnet.generate_signal", lambda *_a, **_k: "HOLD"
    )
    process_testnet(factory, market, "budget-owner", broker)
    with factory.begin() as db:
        row = db.get(Account, 1)
        row.peak_equity = 250
        row.day_equity = 200
    broker.cash += 39
    process_testnet(factory, market, "budget-owner", broker)
    current = account(factory)
    assert current.risk_halted and not current.enabled
    assert Decimal(current.snapshot["account_risk"]["drawdown_pct"]) == Decimal("5.5")
    with api(factory, market) as client:
        view = client.get("/api/trading/status", headers=AUTH).json()["account_risk"]
        assert view == current.snapshot["account_risk"]
    assert not broker.calls


def test_keep_or_replace_symbols_requires_matching_preview_and_confirmation(
    platform, monkeypatch
):
    factory, market, broker = ready(platform, monkeypatch)
    with factory.begin() as db:
        row = db.get(Account, 1)
        row.enabled = False
        row.configuration = Setup(symbols=["BTCUSDT", "ETHUSDT"]).model_dump(
            mode="json"
        )
        params = BacktestCreate(
            symbol="XRPUSDT",
            interval="240",
            evaluation_phase="validation",
            initial_equity=150,
            risk_sizing={
                "budget": 150,
                "risk_per_trade_pct": 0.4,
                "constraints": {
                    "qty_step": 0.001,
                    "min_qty": 0.001,
                    "max_qty": 100,
                    "min_notional": 5,
                    "tick_size": 0.01,
                },
            },
            rules={
                "strategy_type": "ema_cross",
                "fast": 10,
                "slow": 30,
                "stop_loss_pct": 2,
            },
        ).model_dump(mode="json")
        params.update(source="exchange", market_environment="testnet")
        job = BacktestJob(
            status="completed",
            parameters=params,
            result={"metrics": {}, "dataset": {"sha256": "example"}},
        )
        db.add(job)
        db.flush()
        ident = job.id
    with api(factory, market) as client:
        path = f"/api/trading/research/{ident}"
        keep = client.get(path + "/preview?symbols_mode=keep", headers=AUTH).json()
        replace = client.get(
            path + "/preview?symbols_mode=replace", headers=AUTH
        ).json()
        assert keep["configuration"]["symbols"] == ["BTCUSDT", "ETHUSDT"]
        assert replace["configuration"]["symbols"] == ["XRPUSDT"]
        assert keep["fingerprint"] != replace["fingerprint"]
        rows = {row["field"]: row for row in keep["differences"]}
        assert rows["budget"]["before"] == rows["budget"]["after"] == 200
        assert rows["budget"]["research"] == 150
        assert rows["rules.fast"]["before"] == 20 and rows["rules.fast"]["after"] == 10
        assert keep["unvalidated_symbols"] == ["BTCUSDT", "ETHUSDT"]
        body = {
            "fingerprint": keep["fingerprint"],
            "symbols_mode": "keep",
            "acknowledge_model_difference": True,
        }
        assert client.post(path + "/apply", headers=AUTH, json=body).status_code == 409
        body["acknowledge_unvalidated_symbols"] = True
        assert (
            client.post(
                path + "/apply", headers=AUTH, json={**body, "symbols_mode": "replace"}
            ).status_code
            == 409
        )
        with factory.begin() as db:
            stored_job = db.get(BacktestJob, ident)
            stored_job.result = {
                **stored_job.result,
                "risk_summary": {
                    "policy": "fixed_budget_v1",
                    "budget": "150",
                    "max_drawdown_pct": 1,
                },
            }
        assert client.post(path + "/apply", headers=AUTH, json=body).status_code == 409
        body["fingerprint"] = client.get(
            path + "/preview?symbols_mode=keep", headers=AUTH
        ).json()["fingerprint"]
        with factory.begin() as db:
            row = db.get(Account, 1)
            row.configuration = {
                **row.configuration,
                "rules": {**row.configuration["rules"], "fast": 25},
            }
        assert client.post(path + "/apply", headers=AUTH, json=body).status_code == 409
        refreshed = client.get(path + "/preview?symbols_mode=keep", headers=AUTH).json()
        body["fingerprint"] = refreshed["fingerprint"]
        applied = client.post(path + "/apply", headers=AUTH, json=body)
        assert applied.status_code == 200, applied.text
        assert applied.json()["configuration"]["symbols"] == ["BTCUSDT", "ETHUSDT"]
        assert not applied.json()["enabled"]
        assert applied.json()["research_origin"]["symbols_mode"] == "keep"
        assert (
            applied.json()["research_origin"]["configuration_before"]["rules"]["fast"]
            == 25
        )
        replacement = client.get(
            path + "/preview?symbols_mode=replace", headers=AUTH
        ).json()
        applied = client.post(
            path + "/apply",
            headers=AUTH,
            json={
                "fingerprint": replacement["fingerprint"],
                "acknowledge_model_difference": True,
            },
        )
        assert applied.status_code == 200 and applied.json()["configuration"][
            "symbols"
        ] == ["XRPUSDT"]
    assert not broker.calls
