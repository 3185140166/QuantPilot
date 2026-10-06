"""Manual order coexistence using local accounts and a fake broker only."""

from decimal import Decimal
from unittest.mock import Mock

import pytest
from sqlalchemy import select
from test_platform import AUTH
from test_platform import platform as platform  # noqa: PLC0414
from test_testnet import ready
from test_testnet_recovery import api

from quantpilot.platform.models import TestnetOrder as Order
from quantpilot.platform.ownership import classify
from quantpilot.platform.testnet import account, candidates, process_testnet


def manual(symbol="ETHUSDT", quantity="0.1", price="100"):
    return {
        "orderId": "manual-id",
        "orderLinkId": "",
        "symbol": symbol,
        "side": "Buy",
        "qty": quantity,
        "cumExecQty": "0",
        "price": price,
        "orderStatus": "New",
        "reduceOnly": False,
    }


def test_manual_order_leaves_other_coin_and_remaining_cash_available(
    platform, monkeypatch
):
    factory, market, broker = ready(
        platform, monkeypatch, symbols=["BTCUSDT", "ETHUSDT"]
    )
    broker.orders = [manual()]
    original = broker.snapshot

    def snapshot():
        return {**original(), "available_balance": "10", "order_margin": "990"}

    broker.snapshot = snapshot
    broker.cancel = Mock(
        side_effect=AssertionError("External orders must not be cancelled")
    )
    process_testnet(factory, market, "owner", broker)
    assert [(action, symbol) for action, symbol, _ in broker.calls] == [
        ("open", "BTCUSDT")
    ]
    with factory() as db:
        order = db.scalar(select(Order))
        assert (
            order.quantity * Decimal("100.01") * (Decimal("0.5") + Decimal("0.00055"))
            <= 10
        )
    ownership = account(factory).snapshot["ownership"]
    assert ownership["orders"][0]["source"] == "external"
    assert ownership["blocked_symbols"] == ["ETHUSDT"]
    assert Decimal(ownership["reserved_notional"]) == 10
    assert broker.orders == [manual()]
    broker.cancel.assert_not_called()


@pytest.mark.parametrize(
    "order",
    [
        manual("BTCUSDT"),
        manual(quantity="3"),
        {**manual(), "orderLinkId": "qp-lost"},
        manual(price="0"),
    ],
)
def test_conflicting_or_unverified_orders_do_not_open(platform, monkeypatch, order):
    factory, market, broker = ready(platform, monkeypatch)
    broker.orders = [order]
    process_testnet(factory, market, "owner", broker)
    assert broker.calls == []


def test_pending_orders_reserve_slots_and_only_remaining_notional(
    platform, monkeypatch
):
    factory, market, broker = ready(platform, monkeypatch)
    broker.orders = [manual("ETHUSDT"), {**manual("SOLUSDT"), "orderId": "other"}]
    process_testnet(factory, market, "owner", broker)
    assert broker.calls == []
    partial = {**manual(quantity="1"), "cumExecQty": "0.8"}
    assert (
        Decimal(classify(factory, {"open_orders": [partial]})["reserved_notional"])
        == 20
    )


def test_no_cash_or_unprotected_manual_position_blocks_new_entries(
    platform, monkeypatch
):
    factory, market, broker = ready(platform, monkeypatch)
    original = broker.snapshot
    broker.snapshot = lambda: {**original(), "available_balance": "0"}
    process_testnet(factory, market, "owner", broker)
    assert broker.calls == []
    broker.snapshot = original
    broker.positions["ETHUSDT"] = {
        "symbol": "ETHUSDT",
        "side": "Buy",
        "size": ".01",
        "avgPrice": "100",
        "markPrice": "100",
        "stopLoss": "0",
        "positionIdx": 0,
    }
    process_testnet(factory, market, "owner", broker)
    assert broker.calls == []
    assert "无止损" in candidates(factory, market, broker)[0]["reason"]


def test_manual_close_order_stops_system_exit_without_cancelling_it(
    platform, monkeypatch
):
    factory, market, broker = ready(platform, monkeypatch)
    process_testnet(factory, market, "owner", broker)
    process_testnet(factory, market, "owner", broker)
    broker.orders = [{**manual("BTCUSDT"), "side": "Sell", "reduceOnly": True}]
    broker.cancel = Mock(side_effect=AssertionError("Do not cancel manual orders"))
    from quantpilot.platform.models import TestnetAccount as Account

    with factory.begin() as db:
        current = db.get(Account, 1)
        current.enabled, current.flatten_requested = False, True
    process_testnet(factory, market, "owner", broker)
    assert [action for action, _, _ in broker.calls] == ["open"]
    broker.cancel.assert_not_called()


def test_start_allows_budget_larger_than_residual_balance(platform, monkeypatch):
    factory, market, broker = ready(platform, monkeypatch)
    from quantpilot.platform.models import TestnetAccount as Account

    with factory.begin() as db:
        db.get(Account, 1).enabled = False
    broker.orders = [manual()]
    original = broker.snapshot
    broker.snapshot = lambda: {**original(), "available_balance": "10"}
    process_testnet(factory, market, "owner", broker)
    with api(factory, market) as client:
        configuration = account(factory).configuration.copy()
        assert configuration["budget"] == 200
        configuration["budget"] = 150
        assert (
            client.post(
                "/api/trading/configuration", headers=AUTH, json=configuration
            ).status_code
            == 200
        )
        assert account(factory).configuration["budget"] == 150
        assert not account(factory).enabled
        response = client.post(
            "/api/trading/command", headers=AUTH, json={"action": "start"}
        )
        assert response.status_code == 200, response.text
        status = client.get("/api/trading/status", headers=AUTH).json()
        assert status["snapshot"]["ownership"]["orders"][0]["source"] == "external"
    assert broker.calls == []


def test_small_protected_manual_position_does_not_block_other_coin(
    platform, monkeypatch
):
    factory, market, broker = ready(platform, monkeypatch)
    broker.positions["ETHUSDT"] = {
        "symbol": "ETHUSDT",
        "side": "Buy",
        "size": ".01",
        "avgPrice": "100",
        "markPrice": "100",
        "stopLoss": "98",
        "positionIdx": 0,
    }
    process_testnet(factory, market, "owner", broker)
    assert [(action, symbol) for action, symbol, _ in broker.calls] == [
        ("open", "BTCUSDT")
    ]


def test_mixed_position_is_displayed_as_unconfirmed(platform, monkeypatch):
    factory, market, broker = ready(platform, monkeypatch)
    process_testnet(factory, market, "owner", broker)
    process_testnet(factory, market, "owner", broker)
    broker.positions["BTCUSDT"]["size"] = "1"
    process_testnet(factory, market, "owner", broker)
    position = account(factory).snapshot["ownership"]["positions"][0]
    assert position["source"] == "unknown" and not position["managed"]
    assert position["unrealisedPnl"] == "0"
    assert len(broker.calls) == 1
