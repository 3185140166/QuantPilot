"""Offline causality, shared selection and evidence/monitor enforcement checks."""

import copy
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from test_platform import AUTH
from test_platform import platform as platform  # noqa: PLC0414
from test_portfolio_operations import feeds
from test_testnet import INSTRUMENT, ready
from test_testnet_recovery import api

from quantpilot.platform.cross_sectional import analyze
from quantpilot.platform.models import TestnetAccount as Account
from quantpilot.platform.models import TestnetExecution as Execution
from quantpilot.platform.models import TestnetOrder as Order
from quantpilot.platform.schemas import TestnetSetup as Setup
from quantpilot.platform.strategy_monitor import assess as monitor_assess
from quantpilot.platform.strategy_review import assess
from quantpilot.platform.testnet import account, candidates, process_testnet
from quantpilot.portfolio import run_portfolio
from quantpilot.selection import (
    SelectionPolicy,
    budget_fit,
    observation,
    select,
    selection_features,
)


def bars(count=240, growth=0.002):
    close = 100 * np.exp(np.arange(count) * growth)
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2025-01-01", periods=count, freq="h", tz="UTC"),
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1000.0,
            "turnover": close * 1000,
        }
    )


def test_selection_uses_only_closed_past_and_filters_budget():
    config = Setup(selection={"enabled": True, "ranking": "trend_quality", "top_n": 1})
    original = bars()
    changed = original.copy()
    changed.loc[130:, ["close", "high", "low", "turnover"]] *= 10
    past = observation(selection_features(original, config.rules.model_dump()), 100)
    assert past == observation(
        selection_features(changed, config.rules.model_dump()), 100
    )
    rows = select(
        [
            {"symbol": "BTCUSDT", **past},
            {
                "symbol": "ETHUSDT",
                **past,
                "budget_fit": False,
                "budget_reason": "最小订单超出风险预算",
            },
        ],
        config.selection.model_dump(),
    )
    assert rows[0]["selected"] and not rows[1]["selected"]
    oversized = copy.deepcopy(INSTRUMENT)
    oversized["lotSizeFilter"]["minOrderQty"] = "10"
    assert not budget_fit(config, 100, oversized)["budget_fit"]
    with pytest.raises(ValueError):
        SelectionPolicy(max_atr_pct=float("nan"))


def test_portfolio_selection_is_causal_and_does_not_flatten_rank_exits(monkeypatch):
    frames, marks, contracts, config, stamps = feeds(monkeypatch)
    config.selection = SelectionPolicy(enabled=True, top_n=1)
    frames["ETHUSDT"]["volume"] *= 2
    first = run_portfolio(
        frames, marks, {}, contracts, config, {"include_funding": False}, stamps[60]
    )
    entries = [row for row in first["events"] if row["kind"] == "open_fill"]
    assert len(entries) == 1 and entries[0]["symbol"] == "ETHUSDT"
    changed = copy.deepcopy(frames)
    changed["BTCUSDT"].loc[80:, "volume"] *= 100
    later = run_portfolio(
        changed, marks, {}, contracts, config, {"include_funding": False}, stamps[60]
    )
    prefix = lambda result: [
        row
        for row in result["selection"]["history"]
        if pd.Timestamp(row["timestamp"]) < stamps[80]
    ]
    assert prefix(first) == prefix(later)
    assert all(row["exit_reason"] == "回测结束结算" for row in later["trades"])


def test_cross_sectional_evidence_requires_samples_and_does_not_fit():
    config = Setup()
    frames = {
        symbol: bars(growth=value)
        for symbol, value in zip(config.symbols, [0.001, 0.002, 0.003], strict=True)
    }
    report = analyze(
        frames,
        config.rules.model_dump(),
        "2025-01-04T00:00:00Z",
        "2025-01-11T00:00:00Z",
    )
    momentum = next(row for row in report["rows"] if row["key"] == "momentum")
    assert momentum["periods"] >= 30 and momentum["mean_rank_ic"] == pytest.approx(1)
    assert report["affects_execution"] is False
    short = analyze(
        frames,
        config.rules.model_dump(),
        "2025-01-04T00:00:00Z",
        "2025-01-05T00:00:00Z",
    )
    assert all(row["mean_rank_ic"] is None for row in short["rows"])


def passing_evidence():
    config = Setup()
    params = {
        "job_type": "portfolio",
        "evaluation_phase": "validation",
        "validation_parent_job_id": 1,
        "portfolio_config": config.model_dump(mode="json"),
        "execution": {"include_funding": True, "order_type": "Market"},
    }
    result = {
        "metrics": {"trades": 30, "total_return": 0.02},
        "risk_summary": {"max_drawdown_pct": 2},
        "liquidations": 0,
        "data_quality": {
            s: {
                "price": {"bars": 100, "fingerprint": "price"},
                "mark": {"bars": 100, "fingerprint": "mark"},
                "warnings": [],
            }
            for s in config.symbols
        },
        "strategy_review": {
            "stress": [
                {
                    "metrics": {"total_return": 0.01},
                    "liquidations": 0,
                    "risk_summary": {"max_drawdown_pct": 3},
                }
            ],
            "neighbors": [{"metrics": {"total_return": 0.01}}],
        },
    }
    return config, params, result


def test_research_gate_fails_closed_for_missing_or_mismatched_evidence():
    config, params, result = passing_evidence()
    assert assess(result, params, config)["eligible"]
    del result["strategy_review"]["stress"]
    assert "双倍成本压力" in assess(result, params, config)["failed"]
    config, params, result = passing_evidence()
    config.selection = SelectionPolicy(enabled=True)
    assert "组合与执行假设一致" in assess(result, params, config)["failed"]
    params["evaluation_phase"] = "research"
    assert "后续独立验证" in assess(result, params, config)["failed"]


def test_enabled_research_gate_blocks_start_and_worker_without_orders(
    platform, monkeypatch
):
    factory, market, broker = ready(platform, monkeypatch)
    with factory.begin() as db:
        db.get(Account, 1).enabled = False
    process_testnet(factory, market, "initial-sync", broker)
    from quantpilot.platform.leases import release_lease

    release_lease(factory, "testnet", "initial-sync")
    broker.calls.clear()
    with factory.begin() as db:
        row = db.get(Account, 1)
        row.configuration = {**row.configuration, "research_guard": {"enabled": True}}
    with api(factory, market) as web:
        response = web.post(
            "/api/trading/command", headers=AUTH, json={"action": "start"}
        )
        assert response.status_code == 409 and "研究准入" in response.json()["detail"]
    with factory.begin() as db:
        db.get(Account, 1).enabled = True
    process_testnet(factory, market, "guard-worker", broker)
    assert not account(factory).enabled and not broker.calls


def test_monitor_ignores_external_and_unreconciled_cycles_and_measures_fills(platform):
    factory = platform[1]
    current = account(factory)
    config = Setup(monitoring={"enabled": True, "consecutive_losses": 2})
    with factory.begin() as db:
        current = db.get(Account, 1)
        current.configuration = config.model_dump(mode="json")
        for index in (1, 2):
            db.add(
                Order(
                    link_id=f"qp-{index}",
                    symbol="BTCUSDT",
                    side="Buy",
                    action="open",
                    quantity=Decimal(1),
                    filled_quantity=Decimal(1),
                    bar_time=str(index),
                    status="Filled",
                    order_id=str(index),
                    context={
                        "configuration": config.model_dump(mode="json"),
                        "decision": {"entry": "100"},
                    },
                )
            )
            db.add(
                Execution(
                    id=str(index),
                    order_id=str(index),
                    link_id=f"qp-{index}",
                    opening_link_id=f"qp-{index}",
                    symbol="BTCUSDT",
                    side="Buy",
                    exec_type="Trade",
                    quantity=Decimal(1),
                    price=Decimal("100.1"),
                    fee=Decimal("0.1"),
                    closed_size=0,
                    at_ms=index,
                    owned=True,
                )
            )
    cycles = [
        {
            "opening_link_id": link,
            "complete": complete,
            "reconciled": True,
            "net_pnl": "-1",
            "fees": "0.2",
            "funding": "0",
        }
        for link, complete in [
            ("external", True),
            ("qp-2", False),
            ("qp-2", True),
            ("qp-1", True),
        ]
    ]
    with factory() as db:
        report = monitor_assess(db, db.get(Account, 1), cycles)
    assert report["completed_trades"] == 2 and report["consecutive_losses"] == 2
    assert report["pause_new_entries"] and report[
        "adverse_slippage_bps"
    ] == pytest.approx(10)
    cycles[-1].update(net_pnl="1", closed_at_ms=300)
    cycles[-2]["closed_at_ms"] = 200
    with factory() as db:
        report = monitor_assess(db, db.get(Account, 1), cycles)
    assert report["consecutive_losses"] == 0 and not report["pause_new_entries"]


def test_monitor_worker_pauses_without_submitting_or_flattening(platform, monkeypatch):
    factory, market, broker = ready(platform, monkeypatch)
    with factory.begin() as db:
        current = db.get(Account, 1)
        current.configuration = {
            **current.configuration,
            "monitoring": {"enabled": True},
        }
    monkeypatch.setattr(
        "quantpilot.platform.strategy_monitor.assess",
        lambda *args: {"pause_new_entries": True, "reasons": ["测试监测阈值触发"]},
    )
    process_testnet(factory, market, "monitor-worker", broker)
    current = account(factory)
    assert (
        not current.enabled
        and not current.flatten_requested
        and not current.risk_halted
    )
    assert not broker.calls


def test_live_selection_waits_for_same_closed_timestamp(platform, monkeypatch):
    factory, market, broker = ready(platform, monkeypatch, ["BTCUSDT", "ETHUSDT"])
    with factory.begin() as db:
        current = db.get(Account, 1)
        current.enabled = False
        current.configuration = {
            **current.configuration,
            "selection": {"enabled": True, "top_n": 1},
        }
    process_testnet(factory, market, "aligned-sync", broker)
    original = market.bars

    def lagged(exchange, symbol, interval, limit):
        frame, closed = original(exchange, symbol, interval, limit)
        if symbol == "ETHUSDT":
            frame, closed = frame.copy(), closed.copy()
            frame["timestamp"] -= pd.Timedelta(hours=1)
            closed["timestamp"] -= pd.Timedelta(hours=1)
        return frame, closed

    market.bars = lagged
    rows = candidates(factory, market, broker)
    assert all(not row["actionable"] and not row["selected"] for row in rows)
    assert all("未对齐" in row["reason"] for row in rows)
    assert not broker.calls
