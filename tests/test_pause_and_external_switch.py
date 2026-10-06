"""Pausing and switching never mutate external exchange positions."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_platform import AUTH, TOKEN
from test_platform import platform as platform  # noqa: PLC0414
from test_testnet import ready

from quantpilot.platform.api import create_app
from quantpilot.platform.environment import BybitEnvironment, EnvironmentError
from quantpilot.platform.models import TestnetAccount as Account
from quantpilot.platform.settings import PlatformSettings
from quantpilot.platform.testnet import account


def test_pause_available_even_when_execution_disabled_or_switch_pending(
    platform, monkeypatch
):
    factory, market, broker = ready(platform, monkeypatch)
    with factory() as db:
        url = str(db.bind.url)
    environment = SimpleNamespace(
        active="mainnet", status=lambda: {"restart_required": True}
    )
    app = create_app(
        PlatformSettings(
            url, TOKEN, auth_enabled=True, testnet_execution_enabled=False
        ),
        market,
        environment,
    )
    with TestClient(app) as client:
        assert (
            client.post(
                "/api/trading/command",
                headers=AUTH,
                json={"action": "pause", "environment": "mainnet"},
            ).status_code
            == 200
        )
    assert not account(factory).enabled
    assert broker.calls == []


@pytest.mark.parametrize("unknown_order", [False, True])
def test_external_position_can_be_left_on_mainnet_when_switching(
    platform, tmp_path, monkeypatch, unknown_order
):
    factory, _market, broker = ready(platform, monkeypatch)
    monkeypatch.setenv("BYBIT_TESTNET", "false")
    position = {
        "symbol": "ETHUSDT",
        "side": "Buy",
        "size": ".1",
        "avgPrice": "100",
        "markPrice": "100",
        "stopLoss": "0",
        "positionIdx": 0,
    }
    with factory.begin() as db:
        current = db.get(Account, 1)
        current.enabled = False
        current.snapshot = {
            "positions": [position],
            "open_orders": [
                {
                    "symbol": "ETHUSDT",
                    "orderId": "manual",
                    "orderLinkId": "qp-lost" if unknown_order else "",
                    "qty": ".1",
                    "price": "99",
                    "reduceOnly": False,
                }
            ],
        }
        url = str(db.bind.url)
    path = tmp_path / ".env"
    original = "BYBIT_TESTNET=false\n"
    path.write_text(original, encoding="utf-8")
    (tmp_path / ".env.platform").write_text(f"DATABASE_URL={url}\n", encoding="utf-8")
    environment = BybitEnvironment(PlatformSettings(url), tmp_path)
    if unknown_order:
        with pytest.raises(EnvironmentError, match="归属不明"):
            environment.save(factory, "testnet")
        assert path.read_text(encoding="utf-8") == original
    else:
        status = environment.save(factory, "testnet")
        assert (
            status["selected"] == "testnet"
            and status["active"] == "mainnet"
            and status["restart_required"]
        )
        assert account(factory).snapshot["positions"] == [position]
    assert broker.calls == []
