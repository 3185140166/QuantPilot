"""Offline testnet execution checks; every exchange call is a deterministic fake."""

import copy
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_platform import AUTH, TOKEN
from test_platform import (
    platform as platform,  # noqa: PLC0414 - re-export pytest fixture
)

from quantpilot.config import Settings
from quantpilot.platform.api import create_app
from quantpilot.platform.environment import BybitEnvironment
from quantpilot.platform.leases import release_lease
from quantpilot.platform.models import TestnetAccount as AccountRecord
from quantpilot.platform.models import TestnetOrder as OrderRecord
from quantpilot.platform.models import TestnetPosition as PositionRecord
from quantpilot.platform.models import utcnow
from quantpilot.platform.schemas import TestnetSetup as Setup
from quantpilot.platform.settings import PlatformSettings
from quantpilot.platform.testnet import (
    OrderRejected,
    account,
    allocation,
    process_testnet,
)
from quantpilot.platform.testnet import (
    TestnetBroker as Broker,
)

INSTRUMENT = {
    "lotSizeFilter": {
        "qtyStep": "0.001",
        "minOrderQty": "0.001",
        "minNotionalValue": "5",
        "maxMktOrderQty": "100",
    },
    "priceFilter": {"tickSize": "0.01"},
}


class FakeBroker:
    def __init__(self):
        self.cash = Decimal(1000)
        self.positions = {}
        self.reports = {}
        self.calls = []
        self.hide_reports = False
        self.timeout = False
        self.reject = False
        self.margin = "ISOLATED_MARGIN"
        self.orders = []
        self.previous_cash = None
        self.transactions = []
        self.execution_rows = []

    def snapshot(self):
        pnl = sum(
            (Decimal(p["markPrice"]) - Decimal(p["avgPrice"]))
            * Decimal(p["size"])
            * (1 if p["side"] == "Buy" else -1)
            for p in self.positions.values()
        )
        return {
            "wallet_balance": str(self.cash),
            "unrealized_pnl": str(pnl),
            "at_ms": int(utcnow().timestamp() * 1000),
            "wallet_equity": str(self.cash + pnl),
            "available_balance": str(self.cash),
            "positions": copy.deepcopy(list(self.positions.values())),
            "open_orders": copy.deepcopy(self.orders),
            "margin_mode": self.margin,
            "at": utcnow().isoformat(),
        }

    def instrument(self, _symbol):
        return INSTRUMENT

    def quote(self, _symbol):
        return {
            "bid": "99.99",
            "ask": "100.01",
            "bid_size": "100",
            "ask_size": "100",
            "mark": "100",
            "age_seconds": 0,
        }

    def funding_balance(self):
        return "0"

    def lookup_id(self, _symbol, _order_id):
        return None

    def journal(self, _start, _end):
        if self.previous_cash is not None and self.previous_cash != self.cash:
            self.transactions.append(
                {
                    "id": str(len(self.transactions)),
                    "type": "TRADE",
                    "change": str(self.cash - self.previous_cash),
                    "transactionTime": str(_end),
                }
            )
        self.previous_cash = self.cash
        return self.transactions, self.execution_rows

    def cancel(self, _symbol, link_id):
        self.reports[link_id]["orderStatus"] = "Cancelled"

    def lookup(self, _symbol, link_id):
        return None if self.hide_reports else self.reports.get(link_id)

    def submit(self, order, _leverage):
        if order.action == "open" and getattr(order, "_entry_check", None):
            order._entry_check()
        self.calls.append((order.action, order.symbol, order.link_id))
        if self.reject:
            raise OrderRejected("Known rejection")
        if order.action == "open":
            self.positions[order.symbol] = {
                "symbol": order.symbol,
                "side": order.side,
                "size": str(order.quantity),
                "avgPrice": "100",
                "markPrice": "100",
                "stopLoss": str(order.stop_loss),
                "positionIdx": 0,
            }
        else:
            self.positions.pop(order.symbol, None)
        report = {
            "orderId": "exchange-" + order.link_id,
            "orderStatus": "Filled",
            "cumExecQty": str(order.quantity),
            "avgPrice": "100",
            "cumExecFee": "0",
        }
        self.reports[order.link_id] = report
        self.execution_rows.append(
            {
                "execId": "fill-" + order.link_id,
                "orderId": report["orderId"],
                "orderLinkId": order.link_id,
                "symbol": order.symbol,
                "side": order.side,
                "execType": "Trade",
                "execQty": str(order.quantity),
                "execPrice": "100",
                "execFee": "0",
                "closedSize": str(order.quantity) if order.action == "close" else "0",
                "execTime": str(int(utcnow().timestamp() * 1000)),
                "seq": str(len(self.execution_rows) + 1),
            }
        )
        if self.timeout:
            raise TimeoutError("Transport timeout after acceptance")
        return {"orderId": report["orderId"]}

    def prepare_isolated(self):
        if self.positions or self.orders:
            raise ValueError("Account is not flat")
        self.margin = "ISOLATED_MARGIN"


def ready(platform, monkeypatch, symbols=None):
    _client, factory, market = platform
    market.source = "exchange"
    account(factory)
    with factory.begin() as db:
        current = db.get(AccountRecord, 1)
        current.configuration = Setup(
            symbols=symbols or ["BTCUSDT"], max_correlation=1
        ).model_dump(mode="json")
        current.enabled = True
    monkeypatch.setattr(
        "quantpilot.platform.testnet.generate_signal", lambda *_args, **_kwargs: "LONG"
    )
    return factory, market, FakeBroker()


def test_allocation_includes_costs_and_respects_portfolio_and_exchange_minimums():
    config = Setup()
    plan = allocation(config, 200, 100, "LONG", INSTRUMENT)
    assert plan["accepted"] and Decimal(plan["planned_loss"]) <= 1
    assert Decimal(plan["quantity"]) % Decimal("0.001") == 0
    assert Decimal(plan["stop_loss"]) == 98
    assert Decimal(plan["notional"]) <= 200
    minimum = allocation(config, 200, 100000, "LONG", INSTRUMENT)
    assert not minimum["accepted"] and minimum["quantity"] == "0"
    exhausted = allocation(
        config, 200, 100, "LONG", INSTRUMENT, existing_risk=Decimal(2)
    )
    assert not exhausted["accepted"]
    capped = allocation(
        config, 200, 100, "SHORT", INSTRUMENT, existing_notional=Decimal(198)
    )
    assert not capped["accepted"]


def test_order_ack_is_not_a_fill_and_restart_never_resubmits(platform, monkeypatch):
    factory, market, broker = ready(platform, monkeypatch)
    process_testnet(factory, market, "first", broker)
    with factory() as db:
        assert db.scalar(select(OrderRecord)).status == "submitted"
        assert db.scalar(select(func.count()).select_from(PositionRecord)) == 0
    release_lease(factory, "testnet", "first")
    process_testnet(factory, market, "restarted", broker)
    process_testnet(factory, market, "restarted", broker)
    assert len(broker.calls) == 1
    with factory() as db:
        assert db.scalar(select(OrderRecord)).status == "Filled"
        assert (
            db.get(PositionRecord, "BTCUSDT").stop_loss
            == db.scalar(select(OrderRecord)).stop_loss
        )


def test_timeout_is_reconciled_by_link_id_without_duplicate_order(
    platform, monkeypatch
):
    factory, market, broker = ready(platform, monkeypatch)
    broker.timeout = True
    process_testnet(factory, market, "owner", broker)
    broker.hide_reports = True
    market.offset = 15
    fills = broker.execution_rows
    broker.execution_rows = []
    process_testnet(factory, market, "owner", broker)
    assert len(broker.calls) == 1
    with factory() as db:
        assert db.scalar(select(OrderRecord)).status == "unknown"
    broker.hide_reports = False
    broker.execution_rows = fills
    process_testnet(factory, market, "owner", broker)
    assert len(broker.calls) == 1
    with factory() as db:
        assert db.get(PositionRecord, "BTCUSDT") is not None


def test_multicoin_uses_one_budget_and_never_exceeds_two_positions(
    platform, monkeypatch
):
    factory, market, broker = ready(
        platform, monkeypatch, ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    )
    for _ in range(5):
        process_testnet(factory, market, "owner", broker)
    assert len(broker.calls) == 2
    assert (
        sum(
            Decimal(p["size"]) * Decimal(p["avgPrice"])
            for p in broker.positions.values()
        )
        <= 200
    )


def test_daily_loss_halts_and_only_closes_owned_positions(platform, monkeypatch):
    factory, market, broker = ready(platform, monkeypatch)
    process_testnet(factory, market, "owner", broker)
    process_testnet(factory, market, "owner", broker)
    broker.cash -= 5
    process_testnet(factory, market, "owner", broker)
    process_testnet(factory, market, "owner", broker)
    assert [call[0] for call in broker.calls] == ["open", "close"]
    with factory() as db:
        current = db.get(AccountRecord, 1)
        assert current.risk_halted and not current.enabled
        assert db.scalar(select(func.count()).select_from(PositionRecord)) == 0
        assert db.scalar(
            select(OrderRecord).where(OrderRecord.action == "open")
        ).position_closed


def test_pause_keeps_exchange_stop_and_missing_stop_forces_close(platform, monkeypatch):
    factory, market, broker = ready(platform, monkeypatch)
    process_testnet(factory, market, "owner", broker)
    process_testnet(factory, market, "owner", broker)
    with factory.begin() as db:
        db.get(AccountRecord, 1).enabled = False
    monkeypatch.setattr(
        "quantpilot.platform.testnet.generate_signal", lambda *_args, **_kwargs: "SHORT"
    )
    process_testnet(factory, market, "owner", broker)
    assert len(broker.calls) == 1
    broker.positions["BTCUSDT"]["stopLoss"] = "0"
    process_testnet(factory, market, "owner", broker)
    assert [call[0] for call in broker.calls] == ["open", "close"]


def test_manual_position_and_cross_margin_block_openings(platform, monkeypatch):
    factory, market, broker = ready(platform, monkeypatch)
    broker.positions["ETHUSDT"] = {
        "symbol": "ETHUSDT",
        "side": "Buy",
        "size": "1",
        "avgPrice": "100",
        "markPrice": "100",
        "stopLoss": "98",
        "positionIdx": 0,
    }
    process_testnet(factory, market, "owner", broker)
    assert broker.calls == []
    broker.positions.clear()
    broker.margin = "REGULAR_MARGIN"
    process_testnet(factory, market, "owner", broker)
    assert broker.calls == []
    assert account(factory).last_error


def test_exchange_rejection_is_terminal_and_not_retried_same_bar(platform, monkeypatch):
    factory, market, broker = ready(platform, monkeypatch)
    broker.reject = True
    process_testnet(factory, market, "owner", broker)
    process_testnet(factory, market, "owner", broker)
    assert len(broker.calls) == 1
    with factory() as db:
        assert db.scalar(select(OrderRecord)).status == "Rejected"


def test_initial_empty_wallet_funding_sets_budget_baseline(platform, monkeypatch):
    factory, market, broker = ready(platform, monkeypatch)
    with factory.begin() as db:
        db.get(AccountRecord, 1).enabled = False
    broker.cash = Decimal(0)
    process_testnet(factory, market, "owner", broker)
    assert account(factory).baseline_equity is None
    broker.cash = Decimal(1000)
    process_testnet(factory, market, "owner", broker)
    current = account(factory)
    assert current.baseline_equity == 1000
    assert current.equity == current.peak_equity == 200
    assert not current.risk_halted and not broker.calls


def test_hedge_position_is_not_claimed_or_closed(platform, monkeypatch):
    factory, market, broker = ready(platform, monkeypatch)
    process_testnet(factory, market, "owner", broker)
    broker.positions["BTCUSDT"]["positionIdx"] = 1
    process_testnet(factory, market, "owner", broker)
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(PositionRecord)) == 0
    assert len(broker.calls) == 1
    assert account(factory).last_error


def test_mainnet_broker_is_blocked_before_sdk_connection(monkeypatch):
    constructor = Mock()
    monkeypatch.setattr("quantpilot.platform.testnet.BybitClient", constructor)
    with pytest.raises(ValueError, match="confirmation"):
        Broker(Settings(api_key="main-key", api_secret="main-secret", testnet=False))
    constructor.assert_not_called()


@pytest.mark.parametrize("testnet", [True, False])
def test_native_submission_attaches_exchange_stop_and_reduce_only_close(
    monkeypatch, testnet
):
    client = Mock()
    client.settings = Settings(
        api_key="test-key",
        api_secret="test-secret",
        dry_run=False,
        trading_enabled=True,
        testnet=testnet,
        live_trading_confirm="I_UNDERSTAND_LIVE_TRADING" if not testnet else "",
    )
    client.session.set_leverage.return_value = {"retCode": 110043}
    client.session.place_order.return_value = {
        "retCode": 0,
        "result": {"orderId": "id"},
    }
    client._check.side_effect = lambda response: response
    monkeypatch.setattr(
        "quantpilot.platform.testnet.BybitClient", lambda *_args, **_kwargs: client
    )
    broker = Broker(client.settings)
    order = SimpleNamespace(
        action="open",
        symbol="BTCUSDT",
        side="Buy",
        quantity=Decimal("0.001"),
        link_id="stable-link",
        stop_loss=Decimal(98),
        take_profit=Decimal(104),
    )
    broker.submit(order, 2)
    params = client.session.place_order.call_args.kwargs
    assert params["stopLoss"] == "98" and params["slTriggerBy"] == "MarkPrice"
    assert params["orderLinkId"] == "stable-link" and params["positionIdx"] == 0
    assert not params["reduceOnly"]
    order.action, order.side = "close", "Sell"
    broker.submit(order, 2)
    params = client.session.place_order.call_args.kwargs
    assert params["reduceOnly"] and "stopLoss" not in params


def test_trading_api_start_requires_fresh_testnet_state_and_budget(
    platform, tmp_path, monkeypatch
):
    factory, market, broker = ready(platform, monkeypatch)
    with factory.begin() as db:
        db.get(AccountRecord, 1).enabled = False
    monkeypatch.delenv("BYBIT_TESTNET", raising=False)
    (tmp_path / ".env").write_text("BYBIT_TESTNET=true\n", encoding="utf-8")
    with factory() as db:
        url = str(db.bind.url)
    settings = PlatformSettings(
        url,
        TOKEN,
        auth_enabled=True,
        market_source="exchange",
        testnet_execution_enabled=True,
    )
    environment = BybitEnvironment(settings, tmp_path)
    app = create_app(settings, market, environment)
    with TestClient(app) as client:
        assert client.get("/api/trading/status").status_code == 401
        assert (
            client.post(
                "/api/trading/command", headers=AUTH, json={"action": "start"}
            ).status_code
            == 409
        )
        process_testnet(factory, market, "owner", broker)
        assert (
            client.post(
                "/api/trading/command", headers=AUTH, json={"action": "start"}
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/trading/configuration",
                headers=AUTH,
                json=Setup().model_dump(mode="json"),
            ).status_code
            == 409
        )
        assert (
            client.post(
                "/api/trading/command", headers=AUTH, json={"action": "pause"}
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/trading/configuration",
                headers=AUTH,
                json={"rules": {"stop_loss_pct": 0}},
            ).status_code
            == 422
        )
        environment.active = "mainnet"
        assert (
            client.post(
                "/api/trading/command", headers=AUTH, json={"action": "start"}
            ).status_code
            == 409
        )
        assert (
            client.post("/api/trading/prepare-isolated", headers=AUTH).status_code
            == 409
        )
