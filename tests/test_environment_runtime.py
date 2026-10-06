"""Offline automatic switching tests; no real credentials, processes or accounts."""

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_platform import AUTH, TOKEN
from test_platform import platform as platform  # noqa: PLC0414

from quantpilot.platform.api import create_app
from quantpilot.platform.backups import atomic_json
from quantpilot.platform.environment import BybitEnvironment, EnvironmentError
from quantpilot.platform.environment_runtime import (
    capability,
    child_environment,
    pause_target,
    read,
    transition,
)
from quantpilot.platform.health import profile_id
from quantpilot.platform.models import TestnetAccount as Account
from quantpilot.platform.models import TestnetOrder as Order
from quantpilot.platform.settings import PlatformSettings
from quantpilot.platform.testnet import account


def test_readonly_probe_retries_only_transient_errors(monkeypatch):
    from requests.exceptions import Timeout

    from quantpilot.platform.environment_runtime import readonly_snapshot

    monkeypatch.setattr("quantpilot.platform.environment_runtime.time.sleep", Mock())
    broker = Mock()
    broker.snapshot.side_effect = [Timeout("secret never displayed"), {"balance": 12}]
    assert readonly_snapshot(broker) == {"balance": 12}
    assert broker.snapshot.call_count == 2
    error = RuntimeError("private SDK payload")
    error.status_code = 10005
    broker.reset_mock()
    broker.snapshot.side_effect = error
    with pytest.raises(RuntimeError):
        readonly_snapshot(broker)
    assert broker.snapshot.call_count == 1


def test_failure_history_survives_a_later_success(platform, tmp_path, monkeypatch):
    import uuid

    from quantpilot.platform.environment_runtime import remember_result, status

    _factory, url, _environment = selection(platform, tmp_path, monkeypatch)
    first = {
        "id": uuid.uuid4().hex,
        "state": "failed",
        "target": "mainnet",
        "failed_phase": "account",
        "message": "连接超时",
        "finished_at": "2026-01-01T00:00:00+00:00",
        "private_token": "not-for-history",
    }
    remember_result(tmp_path, first)
    remember_result(
        tmp_path,
        {
            "id": uuid.uuid4().hex,
            "state": "completed",
            "target": "mainnet",
            "finished_at": "2026-01-02T00:00:00+00:00",
        },
    )
    results = status(tmp_path, url)["recent_switches"]
    assert len(results) == 2 and results[1]["failed_phase"] == "account"
    assert "not-for-history" not in str(results)


def test_preparation_keeps_heartbeat_and_stops_on_deadline(tmp_path, monkeypatch):
    import subprocess

    from quantpilot.platform.launch import run_preparation

    process = Mock()
    process.wait.side_effect = [subprocess.TimeoutExpired("fake", 1), 0]
    process.poll.return_value = 0
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    heartbeat = Mock()
    run_preparation(["fake"], tmp_path, {}, heartbeat, 10)
    assert heartbeat.call_count == 2
    process.kill.assert_not_called()

    process.reset_mock()
    process.poll.return_value = None
    process.wait.side_effect = None
    clock = iter([0, 11])
    monkeypatch.setattr(
        "quantpilot.platform.launch.time.monotonic", lambda: next(clock)
    )
    with pytest.raises(subprocess.TimeoutExpired):
        run_preparation(["fake"], tmp_path, {}, heartbeat, 10)
    process.kill.assert_called_once()
    process.wait.assert_called_once()


def test_existing_target_schema_skips_migration_without_creating_missing_database(
    platform, tmp_path
):
    from pathlib import Path

    from sqlalchemy import text

    from quantpilot.platform.launch import schema_current

    workspace = Path(__file__).resolve().parents[1]
    factory = platform[1]
    with factory() as db:
        url = str(db.bind.url)
        version = db.scalar(text("SELECT version_num FROM alembic_version"))
    assert version and schema_current(url, workspace)
    absent = tmp_path / "absent.db"
    assert not schema_current(f"sqlite:///{absent.as_posix()}", workspace)
    assert not absent.exists()
    with factory.begin() as db:
        db.execute(text("UPDATE alembic_version SET version_num = 'outdated'"))
    assert not schema_current(url, workspace)


def test_target_preparation_refreshes_readonly_account(platform, monkeypatch, tmp_path):
    from quantpilot.config import Settings
    from quantpilot.platform.environment_runtime import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PLATFORM_ENVIRONMENT_SWITCH_ID", raising=False)

    factory = platform[1]
    account(factory)
    with factory.begin() as db:
        current = db.get(Account, 1)
        current.enabled = True
        url = str(db.bind.url)
    monkeypatch.setattr(
        PlatformSettings,
        "from_env",
        lambda: PlatformSettings(url, TOKEN, market_source="exchange"),
    )
    monkeypatch.setattr(Settings, "from_env", lambda *args: object())
    broker = Mock()
    broker.snapshot.return_value = {"environment": "mainnet", "equity_usdt": 123}
    monkeypatch.setattr(
        "quantpilot.platform.testnet.TestnetBroker", lambda settings: broker
    )
    main()
    current = account(factory)
    assert not current.enabled
    assert current.snapshot["equity_usdt"] == 123
    assert current.snapshot["execution_ready"] is False
    assert current.snapshot["environment_switch_readonly"] is True
    assert broker.session.timeout == 5 and broker.session.max_retries == 1
    assert broker.method_calls == [("snapshot", (), {})]


def controller(tmp_path, url, run_id="offline-run"):
    atomic_json(
        tmp_path / "storage/run/supervisor.json",
        {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "automatic_environment_switch": True,
            "profile": profile_id(url, tmp_path),
        },
    )


def selection(platform, tmp_path, monkeypatch):
    _client, factory, _market = platform
    monkeypatch.delenv("BYBIT_TESTNET", raising=False)
    with factory() as db:
        url = str(db.bind.url)
    (tmp_path / ".env").write_text(
        "BYBIT_TESTNET=true\nBYBIT_API_KEY=fake-main\nBYBIT_API_SECRET=fake-secret\nBYBIT_TESTNET_API_KEY=fake-test\nBYBIT_TESTNET_API_SECRET=fake-test-secret\nLIVE_TRADING_CONFIRM=I_UNDERSTAND_LIVE_TRADING\n",
        encoding="utf-8",
    )
    (tmp_path / ".env.platform").write_text(f"DATABASE_URL={url}\n", encoding="utf-8")
    controller(tmp_path, url)
    return factory, url, BybitEnvironment(PlatformSettings(url), tmp_path)


def test_queue_requires_live_matching_launcher_and_serializes_requests(
    platform, tmp_path, monkeypatch
):
    factory, url, environment = selection(platform, tmp_path, monkeypatch)
    assert capability(tmp_path, url)
    assert not capability(tmp_path, "sqlite:///different.db")
    result = environment.save(factory, "mainnet")
    assert result["automatic_switch_available"] and result["switch_state"] == "pending"
    job = read(tmp_path / "storage/run/environment-switch.json")
    assert job["target"] == "mainnet" and job["previous"] == "testnet"
    assert "fake-secret" not in str(job) and "fake-test-secret" not in str(job)
    with pytest.raises(EnvironmentError, match="正在进行"):
        environment.save(factory, "testnet")
    assert read(tmp_path / "storage/run/environment-switch.json")["id"] == job["id"]


@pytest.mark.parametrize("failure", ["none", "prepare", "target"])
def test_transition_completes_or_restores_without_exchange_mutation(
    platform, tmp_path, monkeypatch, failure
):
    factory, url, environment = selection(platform, tmp_path, monkeypatch)
    environment.save(factory, "mainnet")
    before = (tmp_path / ".env").read_text(encoding="utf-8")
    events = []

    def prepare(job):
        events.append("prepare")
        if failure == "prepare":
            job["phase"] = "account"
            job["error_code"] = "credentials"
            raise ValueError("private-secret-must-not-appear")

    def ready(job, rollback):
        events.append("ready-old" if rollback else "ready-new")
        return rollback or failure != "target"

    result = transition(
        tmp_path,
        "offline-run",
        prepare,
        lambda: events.append("stop"),
        lambda job, rollback: events.append("start-old" if rollback else "start-new"),
        ready,
    )
    assert result["state"] == ("completed" if failure == "none" else "failed")
    assert result["phase"] == result["state"] and result["finished_at"]
    assert "private-secret" not in str(result)
    if failure == "prepare":
        assert result["failed_phase"] == "account"
        assert "密钥、权限" in result["message"]
    if failure == "none":
        assert events == ["prepare", "stop", "start-new", "ready-new"]
        assert (tmp_path / ".env").read_text(encoding="utf-8") == before
    else:
        child = child_environment(
            tmp_path,
            {"BYBIT_TESTNET": "false", "DATABASE_URL": "old", "PYTHONUTF8": "1"},
        )
        assert child["BYBIT_TESTNET"] == "true" and child["DATABASE_URL"] == url
        assert (
            child["BYBIT_API_KEY"] == "fake-main"
            and child["BYBIT_TESTNET_API_KEY"] == "fake-test"
        )
        assert (
            "start-new" not in events
            if failure == "prepare"
            else events[-2:] == ["start-old", "ready-old"]
        )
    assert transition(tmp_path, "offline-run", Mock(), Mock(), Mock(), Mock()) is None


def test_target_is_paused_and_pending_system_orders_rejected(platform):
    _client, factory, _market = platform
    account(factory)
    with factory.begin() as db:
        db.get(Account, 1).enabled = True
        url = str(db.bind.url)
    pause_target(url)
    assert not account(factory).enabled
    with factory.begin() as db:
        db.add(
            Order(
                link_id="qp-pending",
                symbol="BTCUSDT",
                side="Buy",
                action="open",
                quantity=1,
                bar_time="offline",
                status="unknown",
            )
        )
    with pytest.raises(ValueError, match="待确认"):
        pause_target(url)
    with factory() as db:
        assert db.scalar(select(Order)).status == "unknown"


def test_other_launcher_cannot_consume_job(platform, tmp_path, monkeypatch):
    factory, _url, environment = selection(platform, tmp_path, monkeypatch)
    environment.save(factory, "mainnet")
    stop = Mock()
    assert transition(tmp_path, "different-run", Mock(), stop, Mock(), Mock()) is None
    stop.assert_not_called()


def test_switch_in_progress_freezes_mutations_but_allows_pause(
    platform, tmp_path, monkeypatch
):
    factory, url, environment = selection(platform, tmp_path, monkeypatch)
    environment.save(factory, "mainnet")
    app = create_app(
        PlatformSettings(url, TOKEN, auth_enabled=True), platform[2], environment
    )
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert (
            client.post(
                "/api/trading/command",
                headers=AUTH,
                json={"action": "start", "environment": "testnet"},
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/trading/command",
                headers=AUTH,
                json={"action": "pause", "environment": "testnet"},
            ).status_code
            == 200
        )
        assert (
            client.post("/api/trading/configuration", headers=AUTH, json={}).status_code
            == 409
        )
    assert not account(factory).enabled
