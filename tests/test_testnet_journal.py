"""Journal reconciliation and transfers, without touching an exchange."""

from decimal import Decimal
from unittest.mock import Mock

from sqlalchemy import func, select
from test_platform import platform as platform  # noqa: PLC0414
from test_testnet import ready

from quantpilot.platform.models import TestnetAccount as Account
from quantpilot.platform.models import TestnetExecution as Execution
from quantpilot.platform.models import TestnetLedger as Ledger
from quantpilot.platform.models import TestnetOrder as Order
from quantpilot.platform.testnet import TestnetBroker as Broker
from quantpilot.platform.testnet import account, process_testnet
from quantpilot.platform.testnet_journal import ingest, trade_cycles


def fill(exec_id, order, qty="0.1", price="100", closed="0", link=None):
    return {
        "execId": exec_id,
        "orderId": order.order_id or "exchange-id",
        "orderLinkId": link if link is not None else order.link_id,
        "symbol": order.symbol,
        "side": order.side,
        "execType": "Trade",
        "execQty": qty,
        "execPrice": price,
        "execFee": "0.01",
        "closedSize": closed,
        "execTime": "1000",
    }


def test_confirmed_fills_recover_unknown_without_resubmission(platform, monkeypatch):
    factory, market, broker = ready(platform, monkeypatch)
    broker.hide_reports = True
    broker.timeout = True
    process_testnet(factory, market, "owner", broker)
    with factory() as db:
        order = db.scalar(select(Order))
    broker.execution_rows = [fill("fill-1", order, qty=str(order.quantity))]
    process_testnet(factory, market, "owner", broker)
    process_testnet(factory, market, "owner", broker)
    with factory() as db:
        assert db.get(Order, order.id).status == "Filled"
        assert db.get(Order, order.id).fee == Decimal("0.01")
        assert db.scalar(select(func.count()).select_from(Execution)) == 1
    assert len(broker.calls) == 1


def test_native_stop_is_linked_and_net_pnl_includes_both_fees(platform, monkeypatch):
    factory, market, broker = ready(platform, monkeypatch)
    process_testnet(factory, market, "owner", broker)
    with factory() as db:
        order = db.scalar(select(Order))
    entry = fill("entry", order, qty=str(order.quantity))
    exit_fill = fill(
        "stop",
        order,
        qty=str(order.quantity),
        price="98",
        closed=str(order.quantity),
        link="",
    )
    exit_fill.update(orderId="stop-order", side="Sell", execTime="2000")
    broker.lookup_id = Mock(
        return_value={"parentOrderLinkId": order.link_id, "stopOrderType": "StopLoss"}
    )
    ingest(factory, broker, [], [entry, exit_fill], "owner")
    ingest(factory, broker, [], [entry, exit_fill], "owner")
    with factory() as db:
        cycle = trade_cycles(db)[0]
        assert cycle["complete"] and cycle["exit_reason"] == "StopLoss"
        assert Decimal(cycle["fees"]) == Decimal("0.02")
        assert Decimal(cycle["net_pnl"]) == -2 * order.quantity - Decimal("0.02")
        assert db.scalar(select(func.count()).select_from(Execution)) == 2


def test_transfer_in_and_out_do_not_change_equity_or_trigger_risk(
    platform, monkeypatch
):
    factory, market, broker = ready(platform, monkeypatch)
    with factory.begin() as db:
        db.get(Account, 1).enabled = False
    process_testnet(factory, market, "owner", broker)
    baseline = account(factory).journal_start_ms
    logs = [
        {
            "id": "deposit",
            "type": "TRANSFER_IN",
            "change": "300",
            "transactionTime": str(baseline + 1),
        },
        {
            "id": "withdraw",
            "type": "TRANSFER_OUT",
            "change": "-100",
            "transactionTime": str(baseline + 2),
        },
    ]
    broker.cash += 200
    broker.journal = lambda *_args: (logs, [])
    process_testnet(factory, market, "owner", broker)
    process_testnet(factory, market, "owner", broker)
    current = account(factory)
    assert current.net_flows == 200 and current.equity == current.peak_equity == 200
    assert not current.risk_halted and not current.ledger_warning
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(Ledger)) == 2


def test_delayed_transaction_log_blocks_opening_without_false_drawdown(
    platform, monkeypatch
):
    factory, market, broker = ready(platform, monkeypatch)
    with factory.begin() as db:
        db.get(Account, 1).enabled = False
    process_testnet(factory, market, "owner", broker)
    broker.cash -= 50
    broker.journal = lambda *_args: ([], [])
    with factory.begin() as db:
        db.get(Account, 1).enabled = True
    process_testnet(factory, market, "owner", broker)
    current = account(factory)
    assert current.ledger_warning and not current.risk_halted
    assert current.equity == 200 and not broker.calls


def test_journal_failure_preserves_position_protection(platform, monkeypatch):
    factory, market, broker = ready(platform, monkeypatch)
    process_testnet(factory, market, "owner", broker)
    process_testnet(factory, market, "owner", broker)
    broker.positions["BTCUSDT"]["stopLoss"] = "0"
    broker.journal = Mock(side_effect=TimeoutError())
    process_testnet(factory, market, "owner", broker)
    assert [call[0] for call in broker.calls] == ["open", "close"]
    assert account(factory).ledger_warning


def test_cancel_request_is_confirmed_before_releasing_channel(platform, monkeypatch):
    factory, market, broker = ready(platform, monkeypatch)
    process_testnet(factory, market, "owner", broker)
    with factory.begin() as db:
        row = db.scalar(select(Order))
        row.cancel_requested = True
        db.get(Account, 1).enabled = False
        broker.reports[row.link_id].update(
            orderStatus="New", cumExecQty="0", avgPrice="0"
        )
        broker.positions.clear()
        broker.execution_rows.clear()
    process_testnet(factory, market, "owner", broker)
    with factory() as db:
        row = db.scalar(select(Order))
        assert row.status == "Cancelled" and not row.cancel_requested
    assert len(broker.calls) == 1


def test_pagination_cursor_with_empty_following_page_is_not_too_many_orders():
    broker = object.__new__(Broker)
    broker.client = Mock()
    broker.client._check.side_effect = lambda response: response
    method = Mock(
        side_effect=[
            {
                "retCode": 0,
                "result": {"list": [{"orderId": "one"}], "nextPageCursor": "cursor"},
            },
            {"retCode": 0, "result": {"list": [], "nextPageCursor": "cursor"}},
        ]
    )
    assert broker._pages(method, limit=50) == [{"orderId": "one"}]
    assert method.call_count == 2


def test_partial_unknown_opening_does_not_block_protective_reduce_only_exit(
    platform, monkeypatch
):
    factory, market, broker = ready(platform, monkeypatch)
    broker.timeout = True
    process_testnet(factory, market, "owner", broker)
    with factory() as db:
        opening = db.scalar(select(Order))
    broker.reports[opening.link_id].update(
        orderStatus="PartiallyFilled", cumExecQty=str(opening.quantity / 2)
    )
    broker.positions["BTCUSDT"]["size"] = str(opening.quantity / 2)
    broker.positions["BTCUSDT"]["stopLoss"] = "0"
    broker.execution_rows[0]["execQty"] = str(opening.quantity / 2)
    process_testnet(factory, market, "owner", broker)
    assert [call[0] for call in broker.calls] == ["open", "close"]
    assert not account(factory).enabled
    with factory() as db:
        assert db.get(Order, opening.id).cancel_requested


def test_multiple_signals_rank_by_liquidity_and_correlated_holdings_block_additions(
    platform, monkeypatch
):
    from quantpilot.platform.testnet import candidates

    factory, market, broker = ready(platform, monkeypatch, ["BTCUSDT", "ETHUSDT"])
    bars = market.bars

    def varied(exchange, symbol, interval, limit):
        frame, _closed = bars(exchange, symbol, interval, limit)
        frame["close"] = [100 + (i % 9) * 0.1 for i in range(len(frame))]
        frame["volume"] = 100 if symbol == "ETHUSDT" else 10
        return frame, frame.iloc[:-1].copy()

    market.bars = varied
    process_testnet(factory, market, "owner", broker)
    assert broker.calls[0][1] == "ETHUSDT"
    process_testnet(factory, market, "owner", broker)
    with factory.begin() as db:
        current = db.get(Account, 1)
        current.configuration = {**current.configuration, "max_correlation": 0.85}
    plans = candidates(factory, market, broker)
    btc = next(row for row in plans if row["symbol"] == "BTCUSDT")
    assert not btc["actionable"] and "ETHUSDT" in btc["reason"]
