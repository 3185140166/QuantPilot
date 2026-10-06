"""Causal diagnostics, canonical feed fingerprints and honest acceptance evidence."""

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from test_platform import platform as platform  # noqa: PLC0414

from quantpilot.backtest import BacktestResult
from quantpilot.platform.acceptance import summarize
from quantpilot.platform.contract_history import snapshot
from quantpilot.platform.data_quality import (
    candle_audit,
    contract_audit,
    environment_tag,
)
from quantpilot.platform.health import latest_acceptance
from quantpilot.platform.market import HistoryError, validate_market
from quantpilot.platform.robustness import diagnose, neighbors
from quantpilot.platform.rules import StrategyRules


def test_public_risk_tiers_allow_blank_lowest_deduction_and_full_margin_rate():
    raw = {
        "status": "Trading",
        "contractType": "LinearPerpetual",
        "settleCoin": "USDT",
        "lotSizeFilter": {
            "qtyStep": ".001",
            "minOrderQty": ".001",
            "maxOrderQty": "100",
            "minNotionalValue": "5",
        },
        "priceFilter": {"tickSize": ".01"},
        "fundingInterval": 480,
    }
    tiers = [
        {
            "riskLimitValue": "100000",
            "maintenanceMargin": ".005",
            "mmDeduction": "",
            "isLowestRisk": 1,
        },
        {
            "riskLimitValue": "1000000",
            "maintenanceMargin": "1",
            "mmDeduction": "10000",
            "isLowestRisk": 0,
        },
    ]
    client = SimpleNamespace(get_instrument=lambda: raw, get_risk_limits=lambda: tiers)
    result = snapshot(SimpleNamespace(client=lambda *a: client), "ETHUSDT", "60")
    assert result["maintenance_tiers"][0]["deduction"] == 0
    assert result["maintenance_tiers"][1]["maintenance_rate"] == 1
    tiers[1]["mmDeduction"] = ""
    with pytest.raises(ValueError):
        snapshot(SimpleNamespace(client=lambda *a: client), "ETHUSDT", "60")


def test_environment_mismatch_fails_before_market_fetch():
    market = SimpleNamespace(
        source="exchange",
        exchange_settings=lambda *a: SimpleNamespace(
            testnet=True, binance_testnet=True
        ),
    )
    assert environment_tag(market, "bybit", "BTCUSDT", "60", "testnet") == "testnet"
    with pytest.raises(HistoryError, match="环境"):
        environment_tag(market, "bybit", "BTCUSDT", "60", "mainnet")


def test_demo_audit_never_loads_exchange_credentials():
    market = SimpleNamespace(source="demo")
    assert environment_tag(market, "bybit", "BTCUSDT", "60") == "demo"


def test_worker_rejects_cross_environment_job_without_history(platform, monkeypatch):
    from quantpilot.platform.models import BacktestJob
    from quantpilot.platform.research_worker import process_job
    from quantpilot.platform.schemas import BacktestCreate

    _, factory, market = platform
    market.source = "exchange"
    monkeypatch.setattr(
        market,
        "bars",
        lambda *a: pytest.fail("Wrong environment must never read prices"),
    )
    with factory.begin() as db:
        job = BacktestJob(
            parameters={
                **BacktestCreate().model_dump(mode="json"),
                "source": "exchange",
                "market_environment": "mainnet",
            }
        )
        db.add(job)
        db.flush()
        job_id = job.id
    process_job(factory, market, "research")
    with factory() as db:
        row = db.get(BacktestJob, job_id)
        assert row.status == "failed" and "环境" in row.error


def test_full_margin_tier_never_divides_by_zero(monkeypatch):
    from test_portfolio_operations import feeds

    from quantpilot.portfolio import run_portfolio

    f, m, contracts, c, t = feeds(monkeypatch)
    for contract in contracts.values():
        contract["maintenance_tiers"][0]["maintenance_rate"] = 1
    result = run_portfolio(f, m, {}, contracts, c, {"include_funding": False}, t[60])
    assert result["liquidations"] == 2
    assert result["curve"][-1]["equity"] > 0


def bars():
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2025-01-01", periods=600, freq="h", tz="UTC"),
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": 10,
            "turnover": 1000,
        }
    )


@pytest.mark.parametrize(
    "column,value",
    [("volume", -1), ("volume", float("nan")), ("turnover", float("inf"))],
)
def test_invalid_volume_never_enters_research(column, value):
    frame = bars()
    frame[column] = frame[column].astype(float)
    frame.loc[4, column] = value
    with pytest.raises(ValueError, match="volume"):
        validate_market(frame)


def test_canonical_fingerprint_ignores_column_order_numeric_dtype_and_time_unit():
    frame = bars()
    altered = frame[list(reversed(frame.columns))].copy()
    for name in frame.columns[1:]:
        altered[name] = altered[name].astype(float)
    altered["timestamp"] = altered.timestamp.astype("datetime64[us, UTC]")
    assert (
        candle_audit(frame, "60")["fingerprint"]
        == candle_audit(altered, "60")["fingerprint"]
    )
    altered.loc[10, "volume"] = 11
    assert (
        candle_audit(frame, "60")["fingerprint"]
        != candle_audit(altered, "60")["fingerprint"]
    )


def test_gaps_and_naive_timestamps_fail_quality_check():
    frame = bars().drop(index=5)
    with pytest.raises(ValueError):
        candle_audit(frame, "60")
    frame = bars()
    frame["timestamp"] = frame.timestamp.dt.tz_localize(None)
    with pytest.raises(ValueError):
        candle_audit(frame, "60")


def test_sparse_funding_warns_without_inventing_records():
    frame = bars()
    marks = frame.copy()
    marks[["open", "high", "low", "close"]] = (
        marks[["open", "high", "low", "close"]] * 1.02
    )
    records = [{"timestamp": frame.timestamp.iloc[100], "rate": 0.001}]
    original = copy.deepcopy(records)
    result = contract_audit(
        frame,
        marks,
        records,
        {"captured_at": "2025-01-01T00:00:00Z"},
        "60",
        frame.timestamp.iloc[0],
        frame.timestamp.iloc[-1],
        True,
    )
    assert (
        result["funding_records"] == 1 and result["funding_largest_gap_minutes"] > 480
    )
    assert result["max_mark_basis_pct"] == pytest.approx(2)
    assert len(result["warnings"]) == 3 and records == original


@pytest.mark.parametrize("rate", [float("nan"), float("inf"), 1, -1])
def test_bad_funding_rate_is_not_reported_as_valid(rate):
    frame = bars()
    with pytest.raises(ValueError, match="资金费率"):
        contract_audit(
            frame,
            frame,
            [{"timestamp": frame.timestamp.iloc[100], "rate": rate}],
            {"captured_at": "x"},
            "60",
            frame.timestamp.iloc[0],
            frame.timestamp.iloc[-1],
            True,
        )


def config():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return SimpleNamespace(
        base=SimpleNamespace(
            start_at=start,
            end_at=start + timedelta(hours=600),
            interval="60",
            fee_rate=0.001,
            slippage_bps=2,
        ),
        split_at=start + timedelta(hours=300),
        drawdown_penalty=1,
        objective="return_drawdown",
        min_trades=1,
        max_drawdown_pct=10,
        _history_warmup=100,
    )


def test_diagnostic_training_receives_only_preceding_closed_bars():
    frame = bars()
    settings = config()
    calls = []

    def evaluate(data, c, rules, start, end):
        # The diagnostic passes physically truncated data to each selection step.
        if pd.Timestamp(start) < settings.split_at:
            assert (data.timestamp + pd.Timedelta(hours=1) <= pd.Timestamp(end)).all()
        calls.append((start, end, c.base.fee_rate, rules["fast"]))
        index = pd.DatetimeIndex([pd.Timestamp(start), pd.Timestamp(end)])
        result = BacktestResult(201, pd.Series([200, 201], index=index), [])
        return result, data

    rules = StrategyRules(strategy_type="ema_cross", fast=20, slow=60).model_dump()
    original = copy.deepcopy(settings)
    result = diagnose(frame, settings, rules, evaluate)
    assert len(result["sensitivity"]) == 5 and len(result["rolling"]) == 3
    assert [r["fee_rate"] for r in result["cost_stress"]] == [0.001, 0.002, 0.003]
    assert not result["affects_search_ranking"]
    assert settings.base.fee_rate == original.base.fee_rate
    assert settings.base.slippage_bps == original.base.slippage_bps
    for fold in result["rolling"]:
        assert fold["train_end"] == fold["test_start"]
        assert pd.Timestamp(fold["test_end"]) > pd.Timestamp(fold["test_start"])
    assert len(calls) <= 30


def test_diagnostic_cancel_guard_stops_additional_work():
    def cancel():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        diagnose(
            bars(), config(), StrategyRules().model_dump(), lambda *a: None, cancel
        )


def test_neighborhoods_are_valid_and_bounded():
    for template in [
        "ema_cross",
        "ema_rsi",
        "rsi_reversion",
        "breakout",
        "bollinger_reversion",
    ]:
        found = neighbors({"strategy_type": template})
        assert 1 <= len(found) <= 5
        for rules in found:
            assert StrategyRules.model_validate(rules)


def successful_report():
    return {
        "environment": "bybit_testnet",
        "checks": [
            {"limit_order": "New"},
            {"limit_cancellation": "Cancelled"},
            {"market_order": "Filled"},
            {"exchange_stop_present": True},
            {"closed_and_flat": True},
            {"submission_response_recovered": True},
            {"fresh_client_recovery": True},
        ],
        "cycles": [{"complete": True, "reconciled": True, "net_pnl": "-.01"}],
        "errors": [],
        "final_positions": 0,
        "final_open_orders": 0,
    }


def test_acceptance_does_not_claim_unobserved_stop_or_partial_fill():
    summary = summarize(successful_report())
    assert summary["passed"] and len(summary["checks"]) == 8
    assert "交易所原生止损实际触发" in summary["untested"]
    assert "真实部分成交" in summary["untested"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("environment", "mainnet"),
        ("final_positions", None),
        ("final_open_orders", 1),
        ("cycles", [{"complete": True, "reconciled": False, "net_pnl": "0"}]),
        ("errors", ["Timeout"]),
    ],
)
def test_incomplete_acceptance_cannot_pass(field, value):
    report = successful_report()
    report[field] = value
    assert not summarize(report)["passed"]


def test_legacy_acceptance_is_not_upgraded_to_verified(tmp_path):
    path = tmp_path / "artifacts/testnet/smoke-old/report.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(successful_report()))
    summary = latest_acceptance(tmp_path)
    assert summary["legacy"] and not summary["passed"]


def test_readonly_preflight_supports_legacy_schema(tmp_path, monkeypatch):
    import importlib.util
    import sqlite3

    from quantpilot.platform.settings import PlatformSettings

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE testnet_accounts(id INTEGER,enabled BOOLEAN,flatten_requested BOOLEAN); CREATE TABLE testnet_orders(id INTEGER,status TEXT); INSERT INTO testnet_accounts VALUES(1,0,0);"
        )
    script = Path(__file__).resolve().parents[1] / "scripts/testnet_smoke.py"
    spec = importlib.util.spec_from_file_location("acceptance_smoke", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module.PlatformSettings,
        "from_env",
        lambda: PlatformSettings(f"sqlite:///{path}"),
    )
    monkeypatch.setattr(
        module,
        "TestnetBroker",
        lambda: SimpleNamespace(
            snapshot=lambda: {
                "positions": [],
                "open_orders": [],
                "available_balance": "200",
                "margin_mode": "ISOLATED_MARGIN",
            }
        ),
    )
    assert module.readiness()["ready"]
    with sqlite3.connect(path) as db:
        assert [r[1] for r in db.execute("PRAGMA table_info(testnet_accounts)")] == [
            "id",
            "enabled",
            "flatten_requested",
        ]
