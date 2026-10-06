"""Fault acceptance with deterministic exchange data and no network access."""

import asyncio
import copy
import json
import time
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select
from test_platform import platform as platform  # noqa: PLC0414
from test_testnet import INSTRUMENT, ready

from quantpilot.config import Settings
from quantpilot.platform.models import TestnetAccount as Account
from quantpilot.platform.models import TestnetOrder as Order
from quantpilot.platform.schemas import TestnetSetup as Setup
from quantpilot.platform.testnet import account, candidates, process_testnet
from quantpilot.platform.testnet_stream import TestnetStream as Stream


def connected():
    stream = Stream(Settings(api_key="offline-key", api_secret="offline-secret"))
    ws = Mock()
    stream._message(ws, json.dumps({"op": "auth", "success": True}))
    assert not stream.valid(stream.checkpoint())
    stream._message(ws, json.dumps({"op": "subscribe", "success": True}))
    return stream, ws


def test_stream_requires_ack_and_reconciliation_and_invalidates_on_events():
    stream, ws = connected()
    token = stream.checkpoint()
    assert stream.valid(token) and not stream.health()["reconciled"]
    assert stream.reconciled(token)
    for status in ["Filled", "Filled", "New"]:
        stream._message(
            ws, json.dumps({"topic": "order", "data": [{"orderStatus": status}]})
        )
    assert not stream.valid(token)
    assert stream.health()["events"] == 3
    assert not stream.health()["reconciled"]
    assert "offline-secret" not in json.dumps(stream.health())
    token = stream.checkpoint()
    stream._invalidate()
    assert not stream.valid(token) and not stream.reconciled(token)
    stream, _ws = connected()
    stream.reconciled(stream.checkpoint())
    stream.forget_reconciliation()
    assert stream.health()["ready"] and not stream.health()["reconciled"]


@pytest.mark.parametrize(
    "payload",
    [
        "invalid-json",
        "[]",
        '{"op":"auth","success":false}',
        '{"op":"subscribe","success":false}',
    ],
)
def test_bad_stream_messages_fail_closed(payload):
    stream, ws = connected()
    stream._message(ws, payload)
    assert not stream.health()["ready"]
    ws.close.assert_called_once()


def test_stale_connection_cannot_enable_entries_and_is_closed():
    stream, ws = connected()
    stream._socket = ws
    stream._received = time.monotonic() - 61
    assert not stream.valid(stream.checkpoint())
    stream.wait(0)
    ws.close.assert_called_once()


def test_stream_rejects_mainnet_without_connecting():
    with pytest.raises(ValueError, match="confirmation"):
        Stream(
            Settings(api_key="offline-key", api_secret="offline-secret", testnet=False)
        )


def test_stream_uses_same_system_proxy_as_rest_without_exposing_it(monkeypatch):
    monkeypatch.setattr(
        "requests.utils.get_environ_proxies",
        lambda _url: {"https": "http://offline-user:offline%20password@127.0.0.1:7890"},
    )
    options = Stream._proxy_options()
    assert options["http_proxy_host"] == "127.0.0.1"
    assert options["http_proxy_port"] == 7890
    assert options["http_proxy_auth"] == ("offline-user", "offline password")
    stream, _ws = connected()
    assert "offline password" not in json.dumps(stream.health())


@pytest.mark.parametrize(
    "updates",
    [
        {"age_seconds": 6},
        {"age_seconds": -1},
        {"bid": "99", "ask": "101"},
        {"bid": "101", "ask": "100"},
        {"bid": "101", "ask": "101.01", "mark": "101"},
        {"ask_size": "0.001"},
        {"mark": "97"},
    ],
)
def test_bad_quotes_do_not_submit_or_consume_signal(platform, monkeypatch, updates):
    factory, market, broker = ready(platform, monkeypatch)
    original = broker.quote("BTCUSDT")
    broker.quote = lambda _symbol: {**original, **updates}
    process_testnet(factory, market, "owner", broker)
    assert broker.calls == []
    with factory() as db:
        assert db.scalar(select(Order)) is None
        current = db.get(Account, 1)
        assert not current.last_bars and current.snapshot["entry_check"]["reason"]
    broker.quote = lambda _symbol: original
    process_testnet(factory, market, "owner", broker)
    assert len(broker.calls) == 1


def test_fresh_contract_minimum_and_funding_change_block_opening(platform, monkeypatch):
    factory, market, broker = ready(platform, monkeypatch)
    original = broker.instrument
    calls = 0

    def updated(symbol):
        nonlocal calls
        calls += 1
        result = copy.deepcopy(original(symbol))
        if calls >= 2:
            result["lotSizeFilter"]["minNotionalValue"] = "1000"
        return result

    broker.instrument = updated
    process_testnet(factory, market, "owner", broker)
    assert not broker.calls and not account(factory).last_bars
    broker.instrument = original
    snapshot = broker.snapshot
    calls = 0

    def moved_balance():
        nonlocal calls
        calls += 1
        result = snapshot()
        if calls >= 2:
            result["wallet_balance"] = "900"
        return result

    broker.snapshot = moved_balance
    process_testnet(factory, market, "owner", broker)
    assert not broker.calls and not account(factory).last_bars


def test_event_during_final_check_rejects_intent_without_exchange_call(
    platform, monkeypatch
):
    factory, market, broker = ready(platform, monkeypatch)
    guard = Mock(return_value=True)
    original = broker.quote
    calls = 0

    def event_after_quote(symbol):
        nonlocal calls
        calls += 1
        if calls == 2:
            guard.return_value = False
        return original(symbol)

    broker.quote = event_after_quote
    process_testnet(factory, market, "owner", broker, entry_guard=guard)
    assert not broker.calls
    with factory() as db:
        order = db.scalar(select(Order))
        assert order.status == "Rejected" and order.filled_quantity == 0
    process_testnet(factory, market, "owner", broker, entry_guard=guard)
    assert not broker.calls


def test_leverage_setup_event_does_not_consume_entry_bar(platform, monkeypatch):
    factory, market, broker = ready(platform, monkeypatch)
    guard = Mock(return_value=True)
    broker.prepare_leverage = Mock(
        side_effect=lambda *_args: setattr(guard, "return_value", False)
    )
    process_testnet(factory, market, "owner", broker, entry_guard=guard)
    assert not broker.calls and not account(factory).last_bars
    with factory() as db:
        assert db.scalar(select(Order)) is None


def test_pause_during_final_quote_prevents_exchange_submission(platform, monkeypatch):
    factory, market, broker = ready(platform, monkeypatch)
    original = broker.quote
    calls = 0

    def paused(symbol):
        nonlocal calls
        calls += 1
        if calls == 2:
            with factory.begin() as db:
                db.get(Account, 1).enabled = False
        return original(symbol)

    broker.quote = paused
    process_testnet(factory, market, "owner", broker)
    assert not broker.calls
    with factory() as db:
        assert db.scalar(select(Order)).status == "Rejected"


def test_disconnect_and_quote_failure_do_not_block_protective_close(
    platform, monkeypatch
):
    factory, market, broker = ready(platform, monkeypatch)
    process_testnet(factory, market, "owner", broker)
    process_testnet(factory, market, "owner", broker)
    broker.positions["BTCUSDT"]["stopLoss"] = "0"
    broker.quote = Mock(side_effect=TimeoutError())
    process_testnet(
        factory,
        market,
        "owner",
        broker,
        allow_new_entries=False,
        entry_guard=lambda: False,
    )
    assert [row[0] for row in broker.calls] == ["open", "close"]
    broker.quote.assert_not_called()


def test_preflight_quantity_respects_current_balance(platform, monkeypatch):
    from quantpilot.platform.entry_checks import preflight

    factory, market, broker = ready(platform, monkeypatch)
    with factory.begin() as db:
        current = db.get(Account, 1)
        current.snapshot = broker.snapshot()
        current.equity = current.day_equity = current.peak_equity = Decimal(200)
    config = Setup.model_validate(account(factory).configuration)
    card = candidates(factory, market, broker, config)[0]
    baseline = broker.snapshot()
    snapshot = broker.snapshot
    broker.snapshot = lambda: {**snapshot(), "available_balance": "5"}
    plan = preflight(factory, broker, market, config, card, baseline)
    assert Decimal(plan["quantity"]) < Decimal(card["quantity"])
    assert Decimal(plan["margin"]) < 5
    assert INSTRUMENT["lotSizeFilter"]["qtyStep"] == "0.001"


def test_worker_reconciles_twice_after_reconnect_before_opening(platform, monkeypatch):
    from quantpilot.platform import testnet_worker as worker
    from quantpilot.platform.settings import PlatformSettings

    factory, market, _broker = ready(platform, monkeypatch)
    stream, ws = connected()
    stream.start = Mock()
    stream.wait = Mock()
    stream.close = Mock()
    with factory() as db:
        url = str(db.bind.url)
    monkeypatch.setattr(
        worker.PlatformSettings,
        "from_env",
        lambda: PlatformSettings(
            url, market_source="exchange", testnet_execution_enabled=True
        ),
    )
    monkeypatch.setattr(
        worker.Settings,
        "from_env",
        lambda *_args: Settings(api_key="offline-key", api_secret="offline-secret"),
    )
    monkeypatch.setattr(worker, "MarketService", lambda *_args: market)
    monkeypatch.setattr(worker, "TestnetBroker", lambda *_args: Mock())
    monkeypatch.setattr(worker, "TestnetStream", lambda *_args: stream)
    monkeypatch.setattr(worker, "verified_snapshot", lambda *_args: True)
    allowed = []

    def cycle(factory, market, owner, broker, allow_new_entries, entry_guard):
        from quantpilot.platform.leases import renew_lease
        from quantpilot.platform.models import WorkerLease, utcnow

        with factory.begin() as db:
            if not db.get(WorkerLease, "testnet"):
                db.add(
                    WorkerLease(
                        name="testnet",
                        owner="",
                        expires_at=utcnow(),
                        heartbeat_at=utcnow(),
                    )
                )
        renew_lease(factory, "testnet", owner)
        allowed.append(allow_new_entries)
        if len(allowed) == 3:
            stream._invalidate()
            assert not entry_guard()
            stream._message(ws, json.dumps({"op": "auth", "success": True}))
            stream._message(ws, json.dumps({"op": "subscribe", "success": True}))

    monkeypatch.setattr(worker, "process_testnet", cycle)
    monkeypatch.setattr(
        worker.asyncio,
        "sleep",
        AsyncMock(side_effect=[None] * 5 + [RuntimeError("finished")]),
    )
    with pytest.raises(RuntimeError, match="finished"):
        asyncio.run(worker.run())
    assert allowed == [False, False, True, False, False, True]
    stream.close.assert_called_once()


def test_fresh_short_preflight_remains_protected(platform, monkeypatch):
    factory, market, broker = ready(platform, monkeypatch)
    monkeypatch.setattr(
        "quantpilot.platform.testnet.generate_signal", lambda *_args, **_kwargs: "SHORT"
    )
    process_testnet(factory, market, "owner", broker)
    with factory() as db:
        order = db.scalar(select(Order))
        assert order.side == "Sell" and order.stop_loss > 100
        assert order.context["decision"]["preflight"]["mark"] == "100"


def test_partial_fill_cancel_and_duplicate_notifications_do_not_double_inventory(
    platform, monkeypatch
):
    from sqlalchemy import func

    from quantpilot.platform.models import TestnetExecution as Execution
    from quantpilot.platform.models import TestnetPosition as Position

    factory, market, broker = ready(platform, monkeypatch)
    process_testnet(factory, market, "owner", broker)
    with factory.begin() as db:
        opening = db.scalar(select(Order))
        db.get(Account, 1).enabled = False
    half = opening.quantity / 2
    broker.reports[opening.link_id].update(
        orderStatus="PartiallyFilledCanceled", cumExecQty=str(half)
    )
    broker.execution_rows[0]["execQty"] = str(half)
    broker.execution_rows.append(copy.deepcopy(broker.execution_rows[0]))
    broker.positions["BTCUSDT"]["size"] = str(half)
    stream, ws = connected()
    for _ in range(3):
        stream._message(
            ws, json.dumps({"topic": "execution", "data": broker.execution_rows})
        )
        process_testnet(factory, market, "owner", broker, allow_new_entries=False)
    with factory() as db:
        order = db.get(Order, opening.id)
        assert (
            order.status == "PartiallyFilledCanceled" and order.filled_quantity == half
        )
        assert db.get(Position, "BTCUSDT").quantity == half
        assert db.scalar(select(func.count()).select_from(Execution)) == 1
    assert len(broker.calls) == 1


def test_settlement_replay_does_not_duplicate_funding(platform, monkeypatch):
    from quantpilot.platform.testnet_journal import ingest, trade_cycles

    factory, market, broker = ready(platform, monkeypatch)
    process_testnet(factory, market, "owner", broker)
    process_testnet(factory, market, "owner", broker)
    log = {
        "id": "offline-settlement",
        "type": "SETTLEMENT",
        "symbol": "BTCUSDT",
        "change": "-0.01",
        "funding": "-0.01",
        "transactionTime": str(int(broker.execution_rows[0]["execTime"]) + 1),
    }
    for _ in range(3):
        ingest(factory, broker, [log, log], [], "owner")
    with factory() as db:
        cycle = trade_cycles(db)[0]
        assert Decimal(cycle["funding"]) == Decimal("-0.01")
        assert Decimal(cycle["net_pnl"]) == Decimal("-0.01")
