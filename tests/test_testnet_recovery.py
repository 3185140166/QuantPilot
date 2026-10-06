"""Regression checks for reviewed ownership, inventory, resume and research flow."""

import asyncio
import sqlite3
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_platform import AUTH, TOKEN
from test_platform import platform as platform  # noqa: PLC0414
from test_testnet import ready

from quantpilot.platform.api import create_app
from quantpilot.platform.models import BacktestJob, utcnow
from quantpilot.platform.models import TestnetAccount as Account
from quantpilot.platform.models import TestnetExecution as Execution
from quantpilot.platform.models import TestnetOrder as Order
from quantpilot.platform.models import TestnetPosition as Position
from quantpilot.platform.schemas import BacktestCreate
from quantpilot.platform.settings import PlatformSettings
from quantpilot.platform.testnet import account, process_testnet
from quantpilot.platform.testnet_journal import trade_cycles


def api(factory, market):
    with factory() as db:
        url = str(db.bind.url)
    environment = SimpleNamespace(
        active="testnet", status=lambda: {"restart_required": False}
    )
    return TestClient(
        create_app(
            PlatformSettings(
                url,
                TOKEN,
                market_source="exchange",
                auth_enabled=True,
                testnet_execution_enabled=True,
            ),
            market,
            environment,
        )
    )


def test_resume_with_margin_occupied_preserves_position_and_risk_checks(
    platform, monkeypatch
):
    factory, market, broker = ready(platform, monkeypatch)
    broker.cash = Decimal(200)
    process_testnet(factory, market, "owner", broker)
    process_testnet(factory, market, "owner", broker)
    with factory.begin() as db:
        current = db.get(Account, 1)
        current.enabled = False
        current.snapshot = {**current.snapshot, "available_balance": "180"}
    with api(factory, market) as client:
        assert (
            client.post(
                "/api/trading/command", headers=AUTH, json={"action": "start"}
            ).status_code
            == 200
        )
        client.post("/api/trading/command", headers=AUTH, json={"action": "pause"})
        with factory.begin() as db:
            current = db.get(Account, 1)
            positions = [
                dict(row, stopLoss="0") for row in current.snapshot["positions"]
            ]
            current.snapshot = {**current.snapshot, "positions": positions}
        assert (
            client.post(
                "/api/trading/command", headers=AUTH, json={"action": "start"}
            ).status_code
            == 409
        )


def test_old_unobserved_opening_is_retired_and_never_claims_manual_position(
    platform, monkeypatch
):
    factory, market, broker = ready(platform, monkeypatch)
    with factory.begin() as db:
        db.get(Account, 1).enabled = False
        old = Order(
            link_id="old-filled",
            symbol="BTCUSDT",
            side="Buy",
            action="open",
            quantity=1,
            filled_quantity=1,
            average_price=100,
            order_id="old-id",
            status="Filled",
            bar_time="old",
            created_at=utcnow() - timedelta(days=1),
        )
        db.add(old)
        db.add(
            Execution(
                id="old-exec",
                order_id="old-id",
                link_id="old-filled",
                opening_link_id="old-filled",
                symbol="BTCUSDT",
                side="Buy",
                quantity=1,
                price=100,
                fee=0,
                closed_size=0,
                exec_type="Trade",
                at_ms=int((utcnow() - timedelta(days=1)).timestamp() * 1000),
                sequence=1,
                owned=True,
            )
        )
    process_testnet(factory, market, "owner", broker)
    broker.positions["BTCUSDT"] = {
        "symbol": "BTCUSDT",
        "side": "Buy",
        "size": "1",
        "avgPrice": "100",
        "markPrice": "100",
        "stopLoss": "0",
        "positionIdx": 0,
    }
    process_testnet(factory, market, "owner", broker)
    with factory() as db:
        assert db.scalar(select(Order)).position_closed
        assert db.get(Position, "BTCUSDT") is None
    assert not broker.calls


def test_foreign_fill_disqualifies_even_identical_position(platform, monkeypatch):
    factory, market, broker = ready(platform, monkeypatch)
    process_testnet(factory, market, "owner", broker)
    process_testnet(factory, market, "owner", broker)
    foreign = dict(
        broker.execution_rows[0],
        execId="manual-fill",
        orderId="manual-order",
        orderLinkId="",
        seq="2",
    )
    broker.execution_rows.append(foreign)
    broker.positions["BTCUSDT"]["stopLoss"] = "0"
    process_testnet(factory, market, "owner", broker)
    with factory() as db:
        assert db.get(Position, "BTCUSDT") is None
    assert len(broker.calls) == 1


def test_interleaved_fills_use_chronological_remaining_cost(platform):
    _client, factory, _market = platform
    with factory.begin() as db:
        db.add(
            Order(
                link_id="cycle",
                order_id="entry",
                symbol="BTCUSDT",
                side="Buy",
                action="open",
                quantity=2,
                filled_quantity=2,
                status="Filled",
                bar_time="cycle",
            )
        )
        for identifier, quantity, price, closed, at, order_id in [
            ("first", 1, 100, 0, 1000, "entry"),
            ("exit", Decimal("0.5"), 110, Decimal("0.5"), 2000, "close"),
            ("second", 1, 120, 0, 3000, "entry"),
        ]:
            db.add(
                Execution(
                    id=identifier,
                    order_id=order_id,
                    link_id="cycle",
                    opening_link_id="cycle",
                    symbol="BTCUSDT",
                    side="Buy" if order_id == "entry" else "Sell",
                    exec_type="Trade",
                    quantity=quantity,
                    price=price,
                    fee=0,
                    closed_size=closed,
                    at_ms=at,
                    owned=True,
                )
            )
    with factory() as db:
        row = trade_cycles(db)[0]
        assert Decimal(row["gross_pnl"]) == 5
        assert row["reconciled"] and not row["complete"]


def test_journal_failure_still_checks_confirmed_floating_loss(platform, monkeypatch):
    factory, market, broker = ready(platform, monkeypatch)
    process_testnet(factory, market, "owner", broker)
    process_testnet(factory, market, "owner", broker)
    broker.journal = Mock(side_effect=TimeoutError())
    broker.positions["BTCUSDT"]["markPrice"] = "80"
    process_testnet(factory, market, "owner", broker)
    current = account(factory)
    assert current.risk_halted and current.ledger_warning
    assert current.snapshot["risk_basis"] == "floating_only"
    assert [row[0] for row in broker.calls] == ["open", "close"]


def test_research_preview_fingerprint_and_paused_apply_are_enforced(
    platform, monkeypatch
):
    factory, market, _broker = ready(platform, monkeypatch)
    with factory.begin() as db:
        db.get(Account, 1).enabled = False
        params = BacktestCreate(
            evaluation_phase="validation",
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
            result={
                "metrics": {"total_return": 0.02, "max_drawdown": -0.01, "trades": 20},
                "dataset": {"sha256": "sample"},
            },
        )
        db.add(job)
        db.flush()
        job_id = job.id
    with api(factory, market) as client:
        assert client.get("/api/trading/research").status_code == 401
        preview = client.get(
            f"/api/trading/research/{job_id}/preview", headers=AUTH
        ).json()
        assert preview["configuration"]["rules"]["fast"] == 10
        body = {
            "fingerprint": preview["fingerprint"],
            "acknowledge_model_difference": False,
        }
        assert (
            client.post(
                f"/api/trading/research/{job_id}/apply", headers=AUTH, json=body
            ).status_code
            == 409
        )
        body["acknowledge_model_difference"] = True
        body["fingerprint"] = "0" * 64
        assert (
            client.post(
                f"/api/trading/research/{job_id}/apply", headers=AUTH, json=body
            ).status_code
            == 409
        )
        body["fingerprint"] = preview["fingerprint"]
        response = client.post(
            f"/api/trading/research/{job_id}/apply", headers=AUTH, json=body
        )
        assert response.status_code == 200
        assert not response.json()["enabled"]
        assert response.json()["research_origin"]["job_id"] == job_id
        assert response.json()["configuration"]["budget"] == 200
        with factory.begin() as db:
            db.get(Account, 1).enabled = True
        assert (
            client.post(
                f"/api/trading/research/{job_id}/apply", headers=AUTH, json=body
            ).status_code
            == 409
        )


def test_demo_research_and_failed_optimization_cannot_be_applied(platform, monkeypatch):
    factory, market, _broker = ready(platform, monkeypatch)
    with api(factory, market) as client:
        for changes in (
            {"source": "demo"},
            {"optimization_eligible": False},
            {"evaluation_phase": "research"},
        ):
            params = BacktestCreate(
                evaluation_phase="validation", rules={"stop_loss_pct": 2}
            ).model_dump(mode="json")
            params.update(
                source="exchange", **changes
            ) if "source" not in changes else params.update(changes)
            with factory.begin() as db:
                job = BacktestJob(
                    status="completed", parameters=params, result={"metrics": {}}
                )
                db.add(job)
                db.flush()
                job_id = job.id
            assert (
                client.get(
                    f"/api/trading/research/{job_id}/preview", headers=AUTH
                ).status_code
                == 409
            )


def test_startup_sqlite_backup_preserves_pre_migration_data(tmp_path, monkeypatch):
    from quantpilot.platform.launch import backup_before_migration

    original = tmp_path / "original.db"
    with sqlite3.connect(original) as db:
        db.execute("CREATE TABLE alembic_version(version_num TEXT)")
        db.execute("INSERT INTO alembic_version VALUES ('0006_testnet_journal')")
        db.execute("CREATE TABLE probe(value TEXT)")
        db.execute("INSERT INTO probe VALUES ('preserve')")
    monkeypatch.setattr(
        "alembic.script.ScriptDirectory.from_config",
        lambda *_args: SimpleNamespace(
            get_current_head=lambda: "0007_testnet_recovery"
        ),
    )
    settings = PlatformSettings("sqlite:///" + original.as_posix())
    target = backup_before_migration(settings, tmp_path)
    assert target.is_relative_to(tmp_path / "storage/backups")
    with sqlite3.connect(target) as db:
        assert db.execute("SELECT value FROM probe").fetchone()[0] == "preserve"
    with sqlite3.connect(original) as db:
        db.execute("UPDATE alembic_version SET version_num='0007_testnet_recovery'")
    assert backup_before_migration(settings, tmp_path) is None


def test_independent_worker_never_calls_paper_tick(platform, monkeypatch):
    from quantpilot.platform import testnet_worker

    _client, factory, market = platform
    market.source = "exchange"
    with factory() as db:
        url = str(db.bind.url)
    monkeypatch.setattr(
        testnet_worker.PlatformSettings,
        "from_env",
        lambda: PlatformSettings(
            url,
            market_source="exchange",
            testnet_execution_enabled=True,
            testnet_stream_enabled=False,
        ),
    )
    monkeypatch.setattr(
        testnet_worker.Settings,
        "from_env",
        lambda *_args: SimpleNamespace(testnet=True, exchange="bybit"),
    )
    monkeypatch.setattr(testnet_worker, "MarketService", lambda *_args: market)
    process = Mock()
    monkeypatch.setattr(testnet_worker, "TestnetBroker", lambda *_args: Mock())
    monkeypatch.setattr(testnet_worker, "process_testnet", process)
    paper = Mock(side_effect=AssertionError("Paper processing must be isolated"))
    monkeypatch.setattr("quantpilot.platform.engine.tick", paper)
    monkeypatch.setattr(
        testnet_worker.asyncio, "sleep", AsyncMock(side_effect=RuntimeError("one tick"))
    )
    with pytest.raises(RuntimeError, match="one tick"):
        asyncio.run(testnet_worker.run())
    process.assert_called_once()
    paper.assert_not_called()
