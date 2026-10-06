"""Mainnet routing checks with synthetic accounts and mocked SDK; never connect."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from test_platform import AUTH, TOKEN
from test_platform import platform as platform  # noqa: PLC0414
from test_testnet import ready

from quantpilot.config import Settings
from quantpilot.platform.api import create_app
from quantpilot.platform.environment import BybitEnvironment, EnvironmentError
from quantpilot.platform.execution_status import configuration_fingerprint
from quantpilot.platform.models import BacktestJob
from quantpilot.platform.models import TestnetAccount as Account
from quantpilot.platform.settings import PlatformSettings
from quantpilot.platform.testnet import TestnetBroker as Broker
from quantpilot.platform.testnet import account, process_testnet
from quantpilot.platform.testnet_stream import TestnetStream as Stream


def test_environment_switch_rejects_shared_database(platform, tmp_path, monkeypatch):
    _client, factory, _market = platform
    monkeypatch.delenv("BYBIT_TESTNET", raising=False)
    with factory() as db:
        url = str(db.bind.url)
    (tmp_path / ".env").write_text(
        "BYBIT_TESTNET=true\nLIVE_TRADING_CONFIRM=I_UNDERSTAND_LIVE_TRADING\n",
        encoding="utf-8",
    )
    (tmp_path / ".env.platform").write_text(
        f"DATABASE_URL={url}\nPLATFORM_BYBIT_MAINNET_DATABASE_URL={url}\n",
        encoding="utf-8",
    )
    environment = BybitEnvironment(PlatformSettings(url), tmp_path)
    with pytest.raises(EnvironmentError, match="独立数据库"):
        environment.save(factory, "mainnet")
    assert environment.status()["selected"] == "testnet"


def test_mainnet_broker_and_stream_select_mainnet_without_network(monkeypatch):
    constructor = Mock()
    monkeypatch.setattr("quantpilot.platform.testnet.BybitClient", constructor)
    settings = Settings(
        api_key="fake",
        api_secret="fake",
        testnet=False,
        live_trading_confirm="I_UNDERSTAND_LIVE_TRADING",
    )
    Broker(settings)
    configured = constructor.call_args.args[0]
    assert (
        not configured.testnet and configured.trading_enabled and not configured.dry_run
    )
    stream = Stream(settings)
    assert stream.stream_url == "wss://stream.bybit.com/v5/private"
    assert stream.rest_url == "https://api.bybit.com"
    assert stream._thread is None
    response = Mock()
    response.json.return_value = {"retCode": 0, "time": 10000}
    clock = Mock(return_value=response)
    monkeypatch.setattr("quantpilot.platform.testnet_stream.requests.get", clock)
    stream._open(Mock())
    assert clock.call_args.args[0] == "https://api.bybit.com/v5/market/time"
    constructor.return_value.session.place_order.assert_not_called()


def test_mainnet_start_requires_environment_and_current_parameters(
    platform, monkeypatch
):
    factory, market, broker = ready(platform, monkeypatch)
    with factory.begin() as db:
        db.get(Account, 1).enabled = False
    process_testnet(factory, market, "offline", broker)
    monkeypatch.setattr(
        market,
        "exchange_settings",
        lambda *args: Settings(
            api_key="offline",
            api_secret="offline",
            testnet=False,
            live_trading_confirm="I_UNDERSTAND_LIVE_TRADING",
        ),
    )
    environment = SimpleNamespace(
        active="mainnet", status=lambda: {"restart_required": False}
    )
    with factory() as db:
        url = str(db.bind.url)
    app = create_app(
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
    with TestClient(app) as client:
        with factory.begin() as db:
            db.add(
                BacktestJob(
                    id=999,
                    status="completed",
                    result={},
                    parameters={
                        "market_environment": "testnet",
                        "evaluation_phase": "validation",
                        "exchange": "bybit",
                        "source": "exchange",
                        "rules": {"stop_loss_pct": 2},
                        "interval": "60",
                    },
                )
            )
        assert client.get("/api/trading/research", headers=AUTH).json() == []
        assert (
            client.get("/api/trading/research/999/preview", headers=AUTH).status_code
            == 409
        )
        assert client.get("/api/trading/status", headers=AUTH).json()["available"]
        assert (
            client.post("/api/trading/prepare-isolated", headers=AUTH, json={}).status_code
            == 409
        )
        assert (
            client.post(
                "/api/trading/orders/999/cancel?environment=testnet",
                headers=AUTH,
                json={},
            ).status_code
            == 409
        )
        for payload in [
            {"action": "start"},
            {"action": "start", "environment": "testnet"},
            {"action": "start", "environment": "mainnet"},
        ]:
            assert (
                client.post(
                    "/api/trading/command", headers=AUTH, json=payload
                ).status_code
                == 409
            )
        payload = {
            "action": "start",
            "environment": "mainnet",
            "configuration_fingerprint": "0" * 64,
        }
        assert (
            client.post("/api/trading/command", headers=AUTH, json=payload).status_code
            == 409
        )
        payload["configuration_fingerprint"] = configuration_fingerprint(
            account(factory).configuration
        )
        assert (
            client.post("/api/trading/command", headers=AUTH, json=payload).status_code
            == 200
        )
        assert broker.calls == []
        environment.status = lambda: {"restart_required": True}
        assert not client.get("/api/trading/status", headers=AUTH).json()["available"]
        assert (
            client.post("/api/trading/command", headers=AUTH, json=payload).status_code
            == 409
        )
