"""Offline checks for identical sizing, conservative fills and comparison queue."""

from decimal import Decimal

import pandas as pd
import pytest
from test_platform import AUTH
from test_platform import platform as platform  # noqa: PLC0414
from test_testnet import ready
from test_testnet_recovery import api

from quantpilot.backtest import run_backtest
from quantpilot.platform.models import TestnetAccount as Account
from quantpilot.platform.research_worker import process_job
from quantpilot.platform.rules import StrategyRules
from quantpilot.platform.schemas import RiskSizing
from quantpilot.platform.schemas import TestnetSetup as Setup
from quantpilot.sizing import allocation


def sizing(**kwargs):
    return RiskSizing(
        constraints={
            "qty_step": 0.001,
            "min_qty": 0.001,
            "max_qty": 100,
            "min_notional": 5,
            "tick_size": 0.01,
        },
        **kwargs,
    )


def frame(monkeypatch, side="LONG"):
    monkeypatch.setattr("quantpilot.backtest.signal_warmup", lambda _: 1)
    monkeypatch.setattr(
        "quantpilot.backtest.generate_signals",
        lambda bars, **_: pd.Series(["HOLD", side, "HOLD", "HOLD"]),
    )
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2025-01-01", periods=4, freq="h", tz="UTC"),
            "open": [100.0] * 4,
            "close": [100.0] * 4,
            "high": [100.0] * 4,
            "low": [100.0] * 4,
        }
    )


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_backtest_uses_execution_quantity_and_costs(monkeypatch, side):
    bars = frame(monkeypatch, side)
    rules = StrategyRules(strategy_type="ema_cross", stop_loss_pct=2)
    size = sizing()
    result = run_backtest(
        bars,
        initial_equity=200,
        rules=rules.model_dump(),
        fee_rate=0.00055,
        slippage_bps=5,
        risk_sizing=size.model_dump(),
    )
    setup = Setup(rules=rules)
    expected = allocation(
        setup, 200, 100, side, size.constraints.instrument(), available=200
    )
    assert result.trades[0].quantity == float(expected["quantity"])
    trade = result.trades[0]
    assert result.final_equity == pytest.approx(200 + trade.pnl)
    assert trade.pnl < 0


def test_minimum_order_is_skipped_not_rounded_up(monkeypatch):
    bars = frame(monkeypatch)
    size = sizing()
    size.constraints.min_qty = 1
    result = run_backtest(
        bars,
        initial_equity=200,
        rules=StrategyRules(stop_loss_pct=2).model_dump(),
        risk_sizing=size.model_dump(),
    )
    assert result.rejected_entries == 1
    assert not result.trades and result.final_equity == 200


@pytest.mark.parametrize("side, expected", [("LONG", 98), ("SHORT", 102)])
def test_both_barriers_uses_stop_first(monkeypatch, side, expected):
    bars = frame(monkeypatch, side)
    bars.loc[2, ["high", "low"]] = [105, 95]
    result = run_backtest(
        bars,
        initial_equity=200,
        fee_rate=0,
        slippage_bps=0,
        rules=StrategyRules(stop_loss_pct=2, take_profit_pct=2).model_dump(),
        risk_sizing=sizing().model_dump(),
    )
    assert result.trades[0].exit_price == expected
    assert result.trades[0].pnl < 0


def test_gap_stop_precedes_reversal(monkeypatch):
    bars = frame(monkeypatch)
    monkeypatch.setattr(
        "quantpilot.backtest.generate_signals",
        lambda bars, **_: pd.Series(["HOLD", "LONG", "SHORT", "HOLD"]),
    )
    bars.loc[3, ["open", "close", "high", "low"]] = [90, 90, 90, 90]
    result = run_backtest(
        bars,
        initial_equity=200,
        fee_rate=0,
        slippage_bps=0,
        rules=StrategyRules(stop_loss_pct=2).model_dump(),
        risk_sizing=sizing().model_dump(),
    )
    assert len(result.trades) == 1
    assert result.trades[0].exit_price == 90


def test_invalid_ohlc_refused(monkeypatch):
    bars = frame(monkeypatch)
    bars.loc[2, "low"] = 110
    with pytest.raises(ValueError, match="OHLC"):
        run_backtest(
            bars,
            initial_equity=200,
            rules=StrategyRules(stop_loss_pct=2).model_dump(),
            risk_sizing=sizing().model_dump(),
        )


def test_daily_loss_halts_new_entries(monkeypatch):
    bars = frame(monkeypatch)
    bars.loc[2, "low"] = 95
    monkeypatch.setattr(
        "quantpilot.backtest.generate_signals",
        lambda bars, **_: pd.Series(["HOLD", "LONG", "LONG", "LONG"]),
    )
    result = run_backtest(
        bars,
        initial_equity=200,
        fee_rate=0,
        slippage_bps=0,
        rules={"stop_loss_pct": 2},
        risk_sizing=sizing(daily_loss_pct=0.1).model_dump(),
    )
    assert len(result.trades) == 1 and result.risk_halted


def test_budget_cap_uses_same_quantity_despite_entry_slippage(monkeypatch):
    bars = frame(monkeypatch)
    result = run_backtest(
        bars,
        initial_equity=200,
        fee_rate=0.00055,
        slippage_bps=5,
        rules={"stop_loss_pct": 0.1},
        max_notional=200,
        risk_sizing=sizing().model_dump(),
    )
    assert result.trades[0].quantity == 2
    assert result.rejected_entries == 0


@pytest.mark.parametrize("use_source", [False, True])
def test_three_period_queue_uses_saved_budget_and_same_dates(
    platform, monkeypatch, tmp_path, use_source
):
    factory, market, broker = ready(platform, monkeypatch)
    monkeypatch.setattr("quantpilot.platform.api.TestnetBroker", lambda: broker)
    monkeypatch.chdir(tmp_path)
    with api(factory, market) as client:
        payload = {
            "symbol": "BTCUSDT",
            "start_at": "2025-01-01T00:00:00Z",
            "end_at": "2025-04-01T00:00:00Z",
        }
        if use_source:
            response = client.post(
                "/api/strategies",
                headers=AUTH,
                json={
                    "name": "来源规则",
                    "exchange": "bybit",
                    "symbol": "BTCUSDT",
                    "rules": StrategyRules(fast=21, stop_loss_pct=2).model_dump(),
                },
            )
            assert response.status_code == 201, response.text
            payload["source_strategy_id"] = response.json()["id"]
            assert (
                client.post(
                    "/api/trading/comparisons",
                    headers=AUTH,
                    json={**payload, "evaluation_phase": "validation"},
                ).status_code
                == 422
            )
            assert (
                client.post(
                    "/api/trading/comparisons",
                    headers=AUTH,
                    json={**payload, "source_strategy_id": 999999},
                ).status_code
                == 404
            )
        with factory.begin() as db:
            row = db.get(Account, 1)
            row.configuration = Setup(symbols=["BTCUSDT"], budget=200).model_dump(
                mode="json"
            )
            row.enabled = False
        assert client.post("/api/trading/comparisons", json=payload).status_code == 401
        response = client.post("/api/trading/comparisons", headers=AUTH, json=payload)
        assert response.status_code == 202, response.text
        jobs = response.json()["jobs"]
        if use_source:
            from quantpilot.platform.models import Strategy

            with factory.begin() as db:
                source = db.get(Strategy, payload["source_strategy_id"])
                source.rules = StrategyRules(fast=33).model_dump()
            assert (
                client.get("/api/trading/status", headers=AUTH).json()["configuration"][
                    "rules"
                ]["fast"]
                == 20
            )
            assert all(job["parameters"]["rules"]["fast"] == 21 for job in jobs)
        assert [j["parameters"]["interval"] for j in jobs] == ["15", "60", "240"]
        for job in jobs:
            params = job["parameters"]
            assert params["initial_equity"] == params["risk_sizing"]["budget"] == 200
            assert params["start_at"] == "2025-01-01T00:00:00Z"
            assert (
                Decimal(str(params["risk_sizing"]["constraints"]["min_notional"])) == 5
            )
        assert broker.calls == []
        assert (
            client.post(
                "/api/trading/comparisons",
                headers=AUTH,
                json={**payload, "start_at": "2025-01-01T01:00:00Z"},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/trading/comparisons",
                headers=AUTH,
                json={**payload, "end_at": "2025-01-02T00:00:00Z"},
            ).status_code
            == 422
        )
        listed = client.get("/api/trading/comparisons", headers=AUTH).json()
        assert len(listed) == 3
        assert all(not row["research_worker_online"] for row in listed)
        for _ in range(3):
            process_job(factory, market, "risk-comparison")
        completed = client.get("/api/trading/comparisons", headers=AUTH).json()
        assert all(j["status"] == "completed" for j in completed), completed
        for job in completed:
            detail = client.get(f"/api/backtests/{job['id']}", headers=AUTH).json()
            assert pd.Timestamp(
                detail["result"]["curve"][0]["timestamp"]
            ) == pd.Timestamp(payload["start_at"])
            assert "average_trade_pnl" in detail["result"]["metrics"]
            assert (
                client.post(
                    f"/api/backtests/{job['id']}/promote",
                    headers=AUTH,
                    json={"name": "invalid fixed sizing"},
                ).status_code
                == 409
            )
        assert not client.get("/api/trading/status", headers=AUTH).json()["enabled"]
        selected = jobs[1]["id"]
        assert client.get("/api/trading/research", headers=AUTH).json() == []
        with factory.begin() as db:
            row = db.get(Account, 1)
            row.configuration = Setup(symbols=["BTCUSDT"], budget=300).model_dump(
                mode="json"
            )
        url = f"/api/trading/comparisons/{selected}/validation"
        assert (
            client.post(
                url,
                headers=AUTH,
                json={
                    "start_at": "2025-03-01T00:00:00Z",
                    "end_at": "2025-05-01T00:00:00Z",
                },
            ).status_code
            == 422
        )
        response = client.post(
            url,
            headers=AUTH,
            json={"start_at": "2025-04-01T00:00:00Z", "end_at": "2025-05-01T00:00:00Z"},
        )
        assert response.status_code == 202, response.text
        validation = response.json()["jobs"][0]
        assert validation["parameters"]["initial_equity"] == 200
        assert validation["parameters"]["risk_sizing"]["budget"] == 200
        assert validation["parameters"]["validation_parent_job_id"] == selected
        if use_source:
            assert validation["parameters"]["source_strategy"]["name"] == "来源规则"
            assert validation["parameters"]["source_strategy"]["rules"]["fast"] == 21
        assert client.get("/api/trading/research", headers=AUTH).json() == []
        process_job(factory, market, "risk-comparison")
        assert (
            client.get("/api/trading/research", headers=AUTH).json()[0]["id"]
            == validation["id"]
        )
        preview = client.get(
            f"/api/trading/research/{validation['id']}/preview", headers=AUTH
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["configuration"]["budget"] == 300
        assert (
            client.post(
                f"/api/trading/research/{validation['id']}/apply",
                headers=AUTH,
                json={
                    "fingerprint": preview.json()["fingerprint"],
                    "acknowledge_model_difference": True,
                },
            ).status_code
            == 200
        )
        assert not client.get("/api/trading/status", headers=AUTH).json()["enabled"]
        if use_source:
            origin = client.get("/api/trading/status", headers=AUTH).json()[
                "research_origin"
            ]
            assert origin["source_strategy"]["id"] == payload["source_strategy_id"]
            assert origin["source_strategy"]["rules"]["fast"] == 21
        assert not broker.calls


def test_health_uses_snapshot_time_not_recent_error_update(platform, monkeypatch):
    factory, market, _broker = ready(platform, monkeypatch)
    from quantpilot.platform.models import utcnow

    with factory.begin() as db:
        row = db.get(Account, 1)
        row.snapshot = {
            "at": "2020-01-01T00:00:00Z",
            "positions": [{"symbol": "BTCUSDT", "stopLoss": "0"}],
        }
        row.updated_at = utcnow()
    with api(factory, market) as client:
        health = client.get("/api/trading/status", headers=AUTH).json()["health"]
        assert not health["snapshot_fresh"]
        assert health["unprotected_symbols"] == ["BTCUSDT"]
