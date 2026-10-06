"""Start acknowledgement must match the saved rules and the latest command."""

from datetime import timedelta

from sqlalchemy import select
from test_platform import AUTH
from test_platform import platform as platform  # noqa: PLC0414
from test_testnet import ready
from test_testnet_recovery import api

from quantpilot.platform.execution_status import (
    configuration_fingerprint,
    strategy_status,
)
from quantpilot.platform.models import Event, utcnow
from quantpilot.platform.models import TestnetAccount as Account
from quantpilot.platform.testnet import account, process_testnet


def test_config_hash_is_normalized_and_changes_with_rules():
    fingerprint = configuration_fingerprint({})
    assert configuration_fingerprint({"budget": 200.0}) == fingerprint
    assert (
        configuration_fingerprint({"rules": {"fast": 10, "stop_loss_pct": 2}})
        != fingerprint
    )


def test_worker_ack_matches_current_saved_configuration_without_requiring_a_fill(
    platform, monkeypatch
):
    factory, market, broker = ready(platform, monkeypatch)
    monkeypatch.setattr(
        "quantpilot.platform.testnet.generate_signal", lambda *_args, **_kwargs: "HOLD"
    )
    process_testnet(factory, market, "owner", broker)
    current = account(factory)
    evidence = current.snapshot["strategy_execution"]
    assert evidence["scan"][0]["signal"] == "HOLD"
    assert not broker.calls
    with api(factory, market) as client:
        status = client.get("/api/trading/status", headers=AUTH).json()
        assert status["strategy_status"]["acknowledged"]
        assert status["execution_state"] == "waiting_signal"
        assert status["strategy_status"]["last_scan"][0]["signal"] == "HOLD"
        with factory.begin() as db:
            row = db.get(Account, 1)
            row.configuration = {**row.configuration, "budget": 150}
        changed = client.get("/api/trading/status", headers=AUTH).json()
        assert not changed["strategy_status"]["acknowledged"]
        assert changed["strategy_status"]["last_scan"] == []


def test_restart_command_requires_a_new_cycle_and_fingerprint_guards_stale_start(
    platform, monkeypatch
):
    factory, market, broker = ready(platform, monkeypatch)
    with factory.begin() as db:
        db.get(Account, 1).enabled = False
    process_testnet(factory, market, "owner", broker)
    current_hash = configuration_fingerprint(account(factory).configuration)
    with api(factory, market) as client:
        stale = client.post(
            "/api/trading/command",
            headers=AUTH,
            json={"action": "start", "configuration_fingerprint": "0" * 64},
        )
        assert stale.status_code == 409
        assert not account(factory).enabled
        response = client.post(
            "/api/trading/command",
            headers=AUTH,
            json={"action": "start", "configuration_fingerprint": current_hash},
        )
        assert response.status_code == 200
        status = client.get("/api/trading/status", headers=AUTH).json()
        assert not status["strategy_status"]["acknowledged"]
        assert status["execution_state"] == "recovering"
        monkeypatch.setattr(
            "quantpilot.platform.testnet.generate_signal",
            lambda *_args, **_kwargs: "HOLD",
        )
        process_testnet(factory, market, "owner", broker)
        assert client.get("/api/trading/status", headers=AUTH).json()[
            "strategy_status"
        ]["acknowledged"]
        client.post("/api/trading/command", headers=AUTH, json={"action": "pause"})
        client.post(
            "/api/trading/command",
            headers=AUTH,
            json={"action": "start", "configuration_fingerprint": current_hash},
        )
        assert not client.get("/api/trading/status", headers=AUTH).json()[
            "strategy_status"
        ]["acknowledged"]
    with factory() as db:
        assert (
            db.scalar(select(Event).where(Event.message == "测试网交易助手指令：start"))
            is not None
        )


def test_offline_old_failed_and_command_preceding_evidence_never_confirm():
    config = {"budget": 200}
    value = {
        "configuration": config,
        "enabled": True,
        "snapshot": {
            "strategy_execution": {
                "configuration_fingerprint": configuration_fingerprint(config),
                "successful": True,
                "enabled": True,
                "completed_at": utcnow().isoformat(),
            }
        },
    }
    assert strategy_status(value, True)["acknowledged"]
    assert not strategy_status(value, False)["acknowledged"]
    assert not strategy_status(value, True, utcnow() + timedelta(seconds=1))[
        "acknowledged"
    ]
    value["snapshot"]["strategy_execution"]["observed_at"] = (
        utcnow() - timedelta(seconds=2)
    ).isoformat()
    assert not strategy_status(value, True, utcnow() - timedelta(seconds=1))[
        "acknowledged"
    ]
    value["snapshot"]["strategy_execution"]["completed_at"] = (
        utcnow() - timedelta(seconds=61)
    ).isoformat()
    assert not strategy_status(value, True)["acknowledged"]
    value["snapshot"]["strategy_execution"].update(
        completed_at=utcnow().isoformat(), successful=False
    )
    assert not strategy_status(value, True)["acknowledged"]


def test_applied_validation_source_is_not_claimed_unchanged_after_edits():
    value = {
        "configuration": {"budget": 150},
        "enabled": False,
        "snapshot": {},
        "research_origin": {
            "job_id": 42,
            "modified": False,
            "applied_configuration": {"budget": 200},
            "unvalidated_symbols": ["ETHUSDT"],
        },
    }
    result = strategy_status(value, True)
    assert result["source"] == "validation_modified" and result["source_modified"]
    assert result["source_job_id"] == 42 and result["unvalidated_symbols"] == [
        "ETHUSDT"
    ]
