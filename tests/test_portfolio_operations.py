"""Offline shared-budget replay and operational recovery checks."""

import json
import shutil
import sqlite3
from types import SimpleNamespace

import pandas as pd
import pytest
from test_platform import AUTH
from test_platform import platform as platform  # noqa: PLC0414
from test_testnet import ready
from test_testnet_recovery import api

from quantpilot.platform.backups import create_backup, verify_bundle
from quantpilot.platform.contract_history import load_inputs
from quantpilot.platform.market import HistoryError
from quantpilot.platform.models import BacktestJob
from quantpilot.platform.models import TestnetAccount as Account
from quantpilot.platform.models import TestnetOrder as Order
from quantpilot.platform.research_worker import process_job
from quantpilot.platform.schemas import TestnetSetup as Setup
from quantpilot.platform.supervisor import ManagedService
from quantpilot.platform.testnet import process_testnet
from quantpilot.portfolio import run_portfolio


def feeds(monkeypatch, side="LONG", volume=1000):
    stamps = pd.date_range("2025-01-01", periods=100, freq="h", tz="UTC")
    bars = pd.DataFrame(
        {
            "timestamp": stamps,
            "open": 100.0,
            "high": 100.1,
            "low": 99.9,
            "close": 100.0,
            "volume": float(volume),
            "turnover": 100.0 * volume,
        }
    )
    config = Setup(
        symbols=["BTCUSDT", "ETHUSDT"],
        interval="60",
        max_positions=2,
        max_correlation=1,
        fee_rate=0,
        slippage_bps=0,
        rules={
            "strategy_type": "ema_cross",
            "fast": 2,
            "slow": 3,
            "stop_loss_pct": 2,
            "take_profit_pct": 0,
        },
    )
    signals = ["HOLD"] * 100
    signals[60] = side
    monkeypatch.setattr(
        "quantpilot.portfolio.generate_signals", lambda *a, **kw: pd.Series(signals)
    )
    frames = {s: bars.copy() for s in config.symbols}
    contracts = {
        s: {
            "constraints": {
                "qty_step": 0.001,
                "min_qty": 0.001,
                "max_qty": 100,
                "min_notional": 0,
                "tick_size": 0.01,
            },
            "maintenance_tiers": [
                {"notional_limit": 1000000, "maintenance_rate": 0.005, "deduction": 0}
            ],
            "captured_at": stamps[0].isoformat(),
        }
        for s in config.symbols
    }
    return frames, {s: f.copy() for s, f in frames.items()}, contracts, config, stamps


def replay(
    monkeypatch, *, side="LONG", volume=1000, execution=None, modify=None, rates=None
):
    frames, marks, contracts, config, stamps = feeds(monkeypatch, side, volume)
    if modify:
        modify(frames, marks, config, stamps)
    funding = (
        {s: [{"timestamp": stamps[62], "rate": 0.001}] for s in config.symbols}
        if rates is None
        else rates
    )
    return run_portfolio(
        frames,
        marks,
        funding,
        contracts,
        config,
        execution or {"include_funding": False},
        stamps[60],
    )


def test_shared_risk_is_not_duplicated_per_coin(monkeypatch):
    def change(_f, _m, c, _t):
        c.portfolio_risk_pct = 0.5

    result = replay(monkeypatch, modify=change)
    entries = [e for e in result["events"] if e["kind"] == "open_fill"]
    assert len(entries) == 1 and result["rejected_entries"] == 1
    assert result["curve"][-1]["equity"] == 200


@pytest.mark.parametrize("side, sign", [("LONG", 1), ("SHORT", -1)])
def test_funding_sign_and_cash_identity(monkeypatch, side, sign):
    result = replay(monkeypatch, side=side, execution={"include_funding": True})
    assert result["funding_total"] == pytest.approx(0.1 * sign)
    assert result["curve"][-1]["equity"] == pytest.approx(
        200 - sum(t["funding"] for t in result["trades"])
    )
    assert result["curve"][-1]["equity"] == pytest.approx(
        200 + sum(t["pnl"] for t in result["trades"])
    )


def test_disabling_funding_ignores_supplied_records(monkeypatch):
    assert replay(monkeypatch)["funding_total"] == 0
    with pytest.raises(ValueError, match="records missing"):
        replay(monkeypatch, execution={"include_funding": True}, rates={})


def test_market_partial_fill_cancels_remaining_quantity(monkeypatch):
    result = replay(
        monkeypatch,
        volume=1,
        execution={"include_funding": False, "participation_rate": 0.01},
    )
    assert result["partial_fills"] == 2
    assert all(t["quantity"] == 0.01 for t in result["trades"])
    assert len([e for e in result["events"] if e["kind"] == "ioc_cancel"]) == 2


def test_slippage_does_not_expand_opening_notional_cap(monkeypatch):
    def change(_f, _m, c, _t):
        c.rules.stop_loss_pct = 0.1
        c.slippage_bps = 10

    result = replay(monkeypatch, modify=change)
    entries = [e for e in result["events"] if e["kind"] == "open_fill"]
    assert sum(float(e["quantity"]) * float(e["price"]) for e in entries) <= 200


def test_unfilled_limit_orders_expire_and_release_reserved_risk(monkeypatch):
    result = replay(
        monkeypatch,
        execution={
            "include_funding": False,
            "order_type": "Limit",
            "limit_offset_bps": 50,
            "limit_expiry_bars": 2,
        },
    )
    assert not result["trades"] and result["expired_orders"] == 2
    assert result["curve"][-1]["equity"] == 200


def test_mark_price_stop_precedes_deeper_liquidation(monkeypatch):
    def change(_f, m, _c, _t):
        for frame in m.values():
            frame.loc[62, "low"] = 40

    result = replay(monkeypatch, modify=change)
    assert result["liquidations"] == 0
    assert all(t["exit_reason"] == "止损" for t in result["trades"])
    assert result["curve"][-1]["equity"] == pytest.approx(198)


def test_gap_liquidation_is_capped_at_isolated_margin(monkeypatch):
    def change(f, m, _c, _t):
        for frame in [*f.values(), *m.values()]:
            frame.loc[62:, ["open", "close", "high", "low"]] = 10.0

    result = replay(monkeypatch, modify=change)
    assert result["liquidations"] == 2
    assert result["curve"][-1]["equity"] == 150
    assert sum(t["insurance_adjustment"] for t in result["trades"]) > 0
    assert result["curve"][-1]["equity"] == 200 + sum(
        t["pnl"] for t in result["trades"]
    )


def test_misaligned_or_naive_data_fails(monkeypatch):
    f, m, c, config, t = feeds(monkeypatch)
    m["ETHUSDT"].loc[80, "timestamp"] += pd.Timedelta(minutes=1)
    with pytest.raises(ValueError):
        run_portfolio(f, m, {}, c, config, {"include_funding": False}, t[60])


def test_restarts_are_bounded_and_do_not_restart_healthy_service():
    spawned = []

    def spawn():
        process = SimpleNamespace(code=None)
        process.poll = lambda: process.code
        spawned.append(process)
        return process

    service = ManagedService("research", spawn)
    assert service.step(0, limit=2)
    assert service.step(10, limit=2) and len(spawned) == 1
    spawned[-1].code = 1
    assert service.step(11, limit=2) and service.state == "waiting"
    assert service.step(12, limit=2) and len(spawned) == 2
    spawned[-1].code = 1
    service.step(13, limit=2)
    service.step(15, limit=2)
    spawned[-1].code = 1
    assert not service.step(16, limit=2)
    assert service.restart_count == 2 and service.state == "failed"


def test_spawn_failure_uses_backoff_without_unbounded_retry():
    calls = []

    def failing():
        calls.append(1)
        raise OSError("executable unavailable")

    service = ManagedService("research", failing)
    assert service.step(0, limit=1)
    assert service.state == "waiting" and len(calls) == 1
    assert service.step(0.5, limit=1) and len(calls) == 1
    assert service.step(1, limit=1) and len(calls) == 2
    assert not service.step(2, limit=1)


def test_backup_restore_rehearsal_and_tamper_detection(platform, tmp_path):
    _, factory, _ = platform
    asset = tmp_path / "storage/market/sample.parquet"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"research snapshot")
    archive = tmp_path / "storage/archive/result.json"
    archive.parent.mkdir(parents=True)
    archive.write_text(
        json.dumps({"dataset": {"path": "storage/market/sample.parquet"}})
    )
    (tmp_path / ".env").write_text("SECRET=never-copy")
    with factory.begin() as db:
        db.add(
            BacktestJob(
                parameters={"test": True},
                result={"archived_result": {"path": "storage/archive/result.json"}},
                status="completed",
            )
        )
        url = str(db.bind.url)
    backup = create_backup(url, tmp_path, keep=2)
    bundle = tmp_path / backup["directory"]
    assert backup["verified"] and backup["files"] == 3
    restored = tmp_path / "restore-rehearsal"
    restored.mkdir()
    shutil.copyfile(bundle / "database.db", restored / "database.db")
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        if "restore_path" in item:
            target = restored / item["restore_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(bundle / item["file"], target)
    with sqlite3.connect(restored / "database.db") as db:
        assert (
            db.execute("SELECT status FROM backtest_jobs").fetchone()[0] == "completed"
        )
    assert (
        restored / "storage/market/sample.parquet"
    ).read_bytes() == asset.read_bytes()
    assert not (restored / ".env").exists()
    (bundle / manifest["files"][1]["file"]).write_bytes(b"damaged")
    with pytest.raises(ValueError, match="指纹"):
        verify_bundle(bundle)


def test_recovery_gate_manages_account_without_opening(platform, monkeypatch):
    factory, market, broker = ready(platform, monkeypatch)
    process_testnet(factory, market, "worker", broker, allow_new_entries=False)
    assert not broker.calls
    process_testnet(factory, market, "worker", broker, allow_new_entries=True)
    assert broker.calls


def test_order_context_is_recorded_before_submit_and_replay_is_readonly(
    platform, monkeypatch
):
    factory, market, broker = ready(platform, monkeypatch)
    process_testnet(factory, market, "worker", broker)
    with factory() as db:
        order = db.query(Order).first()
        link = order.link_id
        assert order.context["decision"]["signal"] == "LONG"
        assert order.context["configuration"]["budget"] == 200
    with api(factory, market) as web:
        assert web.get(f"/api/trading/replay/{link}").status_code == 401
        response = web.get(f"/api/trading/replay/{link}", headers=AUTH)
        assert response.status_code == 200
        assert response.json()["context"]["decision"]["signal"] == "LONG"
        assert web.get("/api/trading/replay/unknown", headers=AUTH).status_code == 404


def test_missing_mark_or_funding_history_never_becomes_zero(monkeypatch):
    f, _, _, _, t = feeds(monkeypatch)
    bars = f["BTCUSDT"]

    def marks(start, end):
        rows = bars[
            (bars.timestamp >= pd.Timestamp(start, unit="ms", tz="UTC"))
            & (bars.timestamp <= pd.Timestamp(end, unit="ms", tz="UTC"))
        ]
        return [
            [
                str(int(r.timestamp.timestamp() * 1000)),
                str(r.open),
                str(r.high),
                str(r.low),
                str(r.close),
            ]
            for r in rows.itertuples()
        ]

    client = SimpleNamespace(
        get_mark_klines=marks, get_funding_history=lambda *args: []
    )
    market = SimpleNamespace(
        history=lambda *args: bars.copy(), client=lambda *args: client
    )
    with pytest.raises(HistoryError, match="资金费"):
        load_inputs(market, "BTCUSDT", "60", t[60], t[-1], 60)
    client.get_mark_klines = lambda *args: []
    with pytest.raises(HistoryError, match="标记价格"):
        load_inputs(market, "BTCUSDT", "60", t[60], t[-1], 60, False)


def test_portfolio_queue_worker_and_validation_freeze_configuration(
    platform, monkeypatch, tmp_path
):
    factory, market, broker = ready(platform, monkeypatch)
    f, m, contracts, config, _t = feeds(monkeypatch)
    with factory.begin() as db:
        db.get(Account, 1).configuration = config.model_dump(mode="json")
    monkeypatch.setattr(
        market,
        "exchange_settings",
        lambda *a: SimpleNamespace(testnet=True),
        raising=False,
    )
    monkeypatch.setattr(
        "quantpilot.platform.portfolio_research.snapshot",
        lambda _m, s, _i: contracts[s],
    )
    monkeypatch.setattr(
        "quantpilot.platform.portfolio_research.load_inputs",
        lambda _m, s, *a: (f[s], m[s], []),
    )
    monkeypatch.chdir(tmp_path)
    with api(factory, market) as web:
        response = web.post(
            "/api/trading/portfolio",
            headers=AUTH,
            json={
                "start_at": "2025-01-01T00:00:00Z",
                "end_at": "2025-01-10T00:00:00Z",
                "execution": {"include_funding": False},
            },
        )
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        assert not broker.calls
        process_job(factory, market, "research-owner")
        result = web.get(f"/api/backtests/{job_id}", headers=AUTH).json()
        assert result["status"] == "completed", result
        assert result["result"]["configuration"]["symbols"] == config.symbols
        assert result["result"]["dataset"]["sha256"]
        source_params = result["parameters"]
        listing = web.get("/api/trading/portfolio", headers=AUTH).json()
        assert listing[0]["id"] == job_id and "curve" not in listing[0]
        invalid = web.post(
            f"/api/trading/portfolio/{job_id}/validation",
            headers=AUTH,
            json={"start_at": "2025-01-05T00:00:00Z", "end_at": "2025-01-20T00:00:00Z"},
        )
        assert invalid.status_code == 422
        valid = web.post(
            f"/api/trading/portfolio/{job_id}/validation",
            headers=AUTH,
            json={"start_at": "2025-01-10T00:00:00Z", "end_at": "2025-01-20T00:00:00Z"},
        )
        assert valid.status_code == 202, valid.text
        assert valid.json()["parameters"]["contracts"] == source_params["contracts"]
        assert (
            valid.json()["parameters"]["portfolio_config"]
            == source_params["portfolio_config"]
        )
        assert (
            web.post(
                f"/api/backtests/{job_id}/promote",
                headers=AUTH,
                json={"name": "must-not-create-paper"},
            ).status_code
            == 409
        )


def test_funding_pagination_preserves_all_reported_settlements(monkeypatch):
    f, _, _, _, t = feeds(monkeypatch)
    bars = f["BTCUSDT"]
    mark_rows = [
        [
            str(int(r.timestamp.timestamp() * 1000)),
            str(r.open),
            str(r.high),
            str(r.low),
            str(r.close),
        ]
        for r in bars.itertuples()
    ]
    records = [
        {
            "symbol": "BTCUSDT",
            "fundingRate": "0.001",
            "fundingRateTimestamp": str(
                int((t[60] + pd.Timedelta(minutes=n)).timestamp() * 1000)
            ),
        }
        for n in range(401)
    ]
    pages = []

    def funding(start, end):
        pages.append((start, end))
        return sorted(
            [r for r in records if start <= int(r["fundingRateTimestamp"]) <= end],
            key=lambda r: int(r["fundingRateTimestamp"]),
            reverse=True,
        )[:200]

    client = SimpleNamespace(
        get_mark_klines=lambda *a: mark_rows, get_funding_history=funding
    )
    market = SimpleNamespace(history=lambda *a: bars.copy(), client=lambda *a: client)
    _, marks, result = load_inputs(market, "BTCUSDT", "60", t[60], t[-1], 60)
    assert len(result) == 401 and len(pages) == 3
    assert result[0]["timestamp"] == t[60] and result[-1]["timestamp"] == t[
        60
    ] + pd.Timedelta(minutes=400)
    assert marks.timestamp.iloc[0] == t[0]


def test_future_liquidity_does_not_change_existing_entry_decisions(monkeypatch):
    first = replay(monkeypatch)

    def change(f, _m, _c, _t):
        for frame in f.values():
            frame.loc[80:, "volume"] = 1e9

    changed = replay(monkeypatch, modify=change)

    def decisions(result):
        return [
            e
            for e in result["events"]
            if e["kind"] in {"open_intent", "open_fill", "rejected"}
        ]

    assert decisions(first) == decisions(changed)
