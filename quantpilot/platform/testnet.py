"""Bybit execution. Durable intents are never blindly resubmitted."""

import time
import uuid
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from quantpilot.account_risk import evaluate_account_risk
from quantpilot.config import Settings
from quantpilot.exchange.bybit import BybitClient
from quantpilot.sizing import allocation
from quantpilot.strategy import describe_signal, generate_signal

from .engine import guard_lease
from .leases import renew_lease
from .models import (
    Event,
    TestnetAccount,
    TestnetExecution,
    TestnetOrder,
    TestnetPosition,
    WorkerLease,
    utcnow,
)
from .schemas import TestnetSetup
from .testnet_journal import cycle_inventory, flow_totals, synchronize_journal

PENDING = {"intent", "submitted", "unknown", "New", "PartiallyFilled", "Untriggered"}
TERMINAL = {"Filled", "Cancelled", "Rejected", "PartiallyFilledCanceled", "Deactivated"}


class OrderRejected(ValueError):
    """An order was definitively not accepted, rather than transport-uncertain."""


def number(value):
    result = Decimal(str(value or 0))
    if not result.is_finite():
        raise ValueError("Invalid exchange numeric value")
    return result


def safe_error(exc):
    code = getattr(exc, "status_code", None)
    messages = {
        10003: "API 密钥无效或环境不匹配",
        10004: "API 签名无效，请核对配套密钥",
        10005: "API 权限不足",
        10010: "API IP 白名单不匹配",
        10002: "请求时间校验失败",
        10006: "交易所请求频率过高，稍后重试",
        110007: "账户可用余额不足",
    }
    return (
        f"{messages[code]}（{code}）"
        if code in messages
        else "连接或数据核查失败，请检查日志、账户设置与网络"
    )


class TestnetBroker:
    def __init__(self, settings=None):
        settings = settings or Settings.from_env(".env")
        if settings.exchange != "bybit" or settings.category != "linear":
            raise ValueError("Executor only supports Bybit USDT contracts")
        if (
            not settings.testnet
            and settings.live_trading_confirm != "I_UNDERSTAND_LIVE_TRADING"
        ):
            raise ValueError("Mainnet order submission requires explicit confirmation")
        self.client = BybitClient(
            replace(settings, dry_run=False, trading_enabled=True), require_private=True
        )
        self.session = self.client.session

    def snapshot(self):
        response = self.client.get_wallet_balance()
        wallet = response["result"]["list"][0]
        info = self.client._check(self.session.get_account_info())["result"]
        positions, cursor, seen = [], "", set()
        for _ in range(20):
            params = {"category": "linear", "settleCoin": "USDT", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            result = self.client._check(self.session.get_positions(**params))["result"]
            positions.extend(
                row for row in result["list"] if number(row.get("size")) > 0
            )
            cursor = result.get("nextPageCursor", "")
            if not cursor:
                break
            if cursor in seen:
                raise ValueError("Repeated position cursor")
            seen.add(cursor)
        else:
            raise ValueError("Position pagination exceeded limit")
        orders = self._pages(
            self.session.get_open_orders,
            category="linear",
            settleCoin="USDT",
            openOnly=0,
            limit=50,
        )
        orders = [row for row in orders if row.get("orderStatus") not in TERMINAL]
        if len(orders) > 200:
            raise ValueError("Too many open orders for a small account")
        coins = wallet.get("coin", [])
        usdt = next((coin for coin in coins if coin.get("coin") == "USDT"), {})
        # Isolated UTA does not populate account-wide totalEquity/availableBalance.
        equity = number(usdt.get("walletBalance")) + number(usdt.get("unrealisedPnl"))
        available = (
            number(usdt.get("walletBalance"))
            - number(usdt.get("totalPositionIM"))
            - number(usdt.get("totalOrderIM"))
        )
        return {
            "wallet_balance": str(number(usdt.get("walletBalance"))),
            "unrealized_pnl": str(number(usdt.get("unrealisedPnl"))),
            "at_ms": int(response.get("time") or utcnow().timestamp() * 1000),
            "wallet_equity": str(equity),
            "available_balance": str(max(0, available)),
            "order_margin": str(number(usdt.get("totalOrderIM"))),
            "position_margin": str(number(usdt.get("totalPositionIM"))),
            "margin_mode": info.get("marginMode"),
            "positions": positions,
            "open_orders": orders,
            "at": utcnow().isoformat(),
        }

    def instrument(self, symbol):
        row = self.client._check(
            self.session.get_instruments_info(category="linear", symbol=symbol)
        )["result"]["list"][0]
        if (
            row.get("status") != "Trading"
            or row.get("settleCoin") != "USDT"
            or row.get("contractType") != "LinearPerpetual"
        ):
            raise ValueError("Only active USDT perpetual contracts are supported")
        return row

    def quote(self, symbol):
        started = time.monotonic()
        book = self.client._check(
            self.session.get_orderbook(category="linear", symbol=symbol, limit=1)
        )
        ticker = self.client._check(
            self.session.get_tickers(category="linear", symbol=symbol)
        )
        result = book["result"]
        bid, ask = result["b"][0], result["a"][0]
        return {
            "bid": bid[0],
            "bid_size": bid[1],
            "ask": ask[0],
            "ask_size": ask[1],
            "mark": ticker["result"]["list"][0]["markPrice"],
            # Exchange timestamps and elapsed IO avoid dependence on the PC clock.
            "age_seconds": (int(ticker["time"]) - int(result["ts"])) / 1000
            + time.monotonic()
            - started,
        }

    def lookup(self, symbol, link_id):
        params = {"category": "linear", "symbol": symbol, "orderLinkId": link_id}
        for method in (self.session.get_open_orders, self.session.get_order_history):
            rows = self.client._check(method(**params))["result"]["list"]
            if rows:
                return rows[0]
        return None

    def lookup_id(self, symbol, order_id):
        rows = self.client._check(
            self.session.get_order_history(
                category="linear", symbol=symbol, orderId=order_id
            )
        )["result"]["list"]
        return rows[0] if rows else None

    def _pages(self, method, **params):
        rows, seen, cursor = [], set(), ""
        for _ in range(100):
            result = self.client._check(
                method(**params, **({"cursor": cursor} if cursor else {}))
            )["result"]
            rows.extend(result["list"])
            if not result["list"]:
                return rows
            cursor = result.get("nextPageCursor", "")
            if not cursor:
                return rows
            if cursor in seen:
                raise ValueError("Repeated journal cursor")
            seen.add(cursor)
        raise ValueError("Journal pagination exceeds safety limit")

    def journal(self, start_ms, end_ms):
        transactions, executions = [], []
        while start_ms <= end_ms:
            stop = min(end_ms, start_ms + 7 * 86400000 - 1)
            transactions.extend(
                self._pages(
                    self.session.get_transaction_log,
                    accountType="UNIFIED",
                    currency="USDT",
                    startTime=start_ms,
                    endTime=stop,
                    limit=50,
                )
            )
            executions.extend(
                self._pages(
                    self.session.get_executions,
                    category="linear",
                    startTime=start_ms,
                    endTime=stop,
                    limit=100,
                )
            )
            start_ms = stop + 1
            if len(transactions) + len(executions) > 10000:
                raise ValueError("Journal volume exceeds small account limit")
        return transactions, executions

    def funding_balance(self):
        result = self.client._check(
            self.session.get_coin_balance(accountType="FUND", coin="USDT")
        )["result"]
        return str(number(result.get("balance", {}).get("walletBalance")))

    def cancel(self, symbol, link_id):
        self.client.settings.require_order_submission("bybit", "1")
        return self.client._check(
            self.session.cancel_order(
                category="linear", symbol=symbol, orderLinkId=link_id
            )
        )

    def prepare_leverage(self, symbol, leverage):
        self.client.settings.require_order_submission("bybit", "1")
        try:
            response = self.session.set_leverage(
                category="linear",
                symbol=symbol,
                buyLeverage=str(leverage),
                sellLeverage=str(leverage),
            )
        except Exception as exc:  # noqa: BLE001 - no order was submitted at this stage
            if getattr(exc, "status_code", None) == 110043:
                response = {"retCode": 0}
            else:
                raise OrderRejected(
                    "Leverage setup failed before submitting order"
                ) from None
        if response.get("retCode") not in (0, 110043):
            raise OrderRejected("Leverage rejected before submitting order")

    def submit(self, order, leverage):
        self.client.settings.require_order_submission("bybit", str(order.quantity))
        if order.action == "open":
            self.prepare_leverage(order.symbol, leverage)
            if getattr(order, "_entry_check", None):
                order._entry_check()
        params = {
            "category": "linear",
            "symbol": order.symbol,
            "side": order.side,
            "orderType": "Market",
            "qty": str(order.quantity),
            "orderLinkId": order.link_id,
            "positionIdx": 0,
            "reduceOnly": order.action == "close",
        }
        if getattr(order, "order_type", "Market") == "Limit":
            if order.action != "open" or number(order.limit_price) <= 0:
                raise OrderRejected("Only protected opening limit orders are supported")
            params.update(
                orderType="Limit", price=str(order.limit_price), timeInForce="PostOnly"
            )
        if order.action == "open":
            params.update(
                stopLoss=str(order.stop_loss),
                slTriggerBy="MarkPrice",
                tpslMode="Full",
                slOrderType="Market",
            )
            if order.take_profit:
                params.update(
                    takeProfit=str(order.take_profit),
                    tpTriggerBy="MarkPrice",
                    tpOrderType="Market",
                )
        try:
            response = self.session.place_order(**params)
        except Exception as exc:
            if getattr(exc, "status_code", None) in {
                10001,
                10005,
                110007,
                110013,
                110017,
                110020,
                110036,
                110037,
                110094,
            }:
                raise OrderRejected("Exchange rejected order") from None
            raise
        if response.get("retCode") not in (0, 10014, 110072):
            raise OrderRejected("Exchange rejected order")
        return self.client._check(response)["result"]

    def prepare_isolated(self):
        snapshot = self.snapshot()
        if snapshot["positions"] or snapshot["open_orders"]:
            raise ValueError("Account must be flat before changing margin mode")
        if snapshot["margin_mode"] != "ISOLATED_MARGIN":
            self.client._check(
                self.session.set_margin_mode(setMarginMode="ISOLATED_MARGIN")
            )
        if self.snapshot()["margin_mode"] != "ISOLATED_MARGIN":
            raise ValueError("Isolated margin could not be verified")
        try:
            response = self.session.switch_position_mode(
                category="linear", coin="USDT", mode=0
            )
            if response.get("retCode") not in (0, 110025):
                self.client._check(response)
        except Exception as exc:
            if getattr(exc, "status_code", None) != 110025:
                raise


def account(factory):
    with factory.begin() as db:
        row = db.get(TestnetAccount, 1)
        if row is None:
            row = TestnetAccount(
                id=1,
                configuration=TestnetSetup().model_dump(mode="json"),
                enabled=False,
                equity=200,
                peak_equity=200,
                day_equity=200,
                snapshot={},
                last_bars={},
            )
            db.add(row)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                return account(factory)
        return row


def position_risk(snapshot, config):
    risk, notional = Decimal(0), Decimal(0)
    for position in snapshot.get("positions", []):
        qty, entry = number(position["size"]), number(position["avgPrice"])
        stop = number(position.get("stopLoss"))
        if stop <= 0:
            return None, None
        # Stops that have moved beyond break-even do not create negative risk.
        distance = max(
            Decimal(0), entry - stop if position["side"] == "Buy" else stop - entry
        )
        risk += qty * (
            distance
            + entry
            * (2 * number(config.fee_rate) + 2 * number(config.slippage_bps) / 10000)
        )
        notional += qty * number(position.get("markPrice") or entry)
    return risk, notional


def candidates(factory, market, broker, config=None):
    from quantpilot.selection import budget_fit, observation, selection_features
    from quantpilot.selection import select as choose

    from .ownership import classify

    current = account(factory)
    config = config or TestnetSetup.model_validate(current.configuration)
    origin = current.research_origin or {}
    risk, notional = position_risk(current.snapshot, config)
    ownership = classify(factory, current.snapshot)
    if notional is not None:
        notional += number(ownership["reserved_notional"])
    results = []
    held = {row["symbol"] for row in current.snapshot.get("positions", [])}
    exposures = held | set(ownership["reserved_symbols"])
    held_returns = {}
    if exposures and config.max_correlation < 1:
        for symbol in exposures:
            _, bars = market.bars(
                "bybit", symbol, config.interval, max(200, config.rules.warmup + 3)
            )
            held_returns[symbol] = (
                bars.set_index("timestamp").close.pct_change().tail(100)
            )
    for symbol in config.symbols:
        try:
            frame, closed = market.bars(
                "bybit", symbol, config.interval, max(200, config.rules.warmup + 3)
            )
            signal = generate_signal(closed, **config.rules.signal_options)
            card = {
                "symbol": symbol,
                "signal": signal,
                "bar_time": closed.iloc[-1].timestamp.isoformat(),
                "execution": "bybit_"
                + (
                    "testnet"
                    if market.exchange_settings("bybit").testnet
                    else "mainnet"
                ),
                "actionable": False,
                "liquidity": float((closed.close * closed.volume).tail(24).median()),
                **observation(selection_features(closed, config.rules.model_dump())),
                **describe_signal(closed, signal, **config.rules.signal_options),
                "validation_status": "参数已修改，需要重新验证"
                if origin.get("modified")
                else "未关联该币种的验证回测"
                if not origin.get("job_id")
                or symbol in origin.get("unvalidated_symbols", [])
                else f"已关联验证 #{origin['job_id']}，准入结果另行核对",
            }
            correlated = None
            if config.selection.enabled:
                card.update(
                    budget_fit(config, closed.iloc[-1].close, broker.instrument(symbol))
                )
            returns = closed.set_index("timestamp").close.pct_change().tail(100)
            for other, other_returns in held_returns.items():
                pairs = pd.concat([returns, other_returns], axis=1).dropna()
                correlation = (
                    pairs.iloc[:, 0].corr(pairs.iloc[:, 1])
                    if len(pairs) >= 30
                    else float("nan")
                )
                if pd.isna(correlation) or abs(correlation) > config.max_correlation:
                    correlated = other
                    break
            if ownership["uncertain"]:
                card["reason"] = "外部订单来源或占用金额未确认，等待核查"
            elif symbol in ownership["blocked_symbols"]:
                card["reason"] = "该币种有外部／手工订单或持仓，系统不接管，跳过开仓"
            elif signal == "HOLD":
                card["reason"] = "当前已收盘 K 线没有入场信号"
            elif risk is None:
                card["reason"] = "账户存在无止损持仓，禁止增加风险"
            elif symbol in held:
                card["reason"] = "该币种已有持仓，由执行进程管理退出"
            elif len(held | set(ownership["reserved_symbols"])) >= config.max_positions:
                card["reason"] = "持仓及外部开仓挂单预留名额已达上限"
            elif correlated:
                card["reason"] = (
                    f"与已有 {correlated} 持仓相关性过高或数据不足，跳过增加风险"
                )
            else:
                card.update(
                    allocation(
                        config,
                        current.equity,
                        frame.iloc[-1].close,
                        signal,
                        broker.instrument(symbol),
                        risk,
                        notional,
                        current.snapshot.get("available_balance", 0),
                    )
                )
                card["actionable"] = card["accepted"]
            results.append(card)
        except Exception:  # noqa: BLE001 - never return SDK request metadata
            results.append(
                {
                    "symbol": symbol,
                    "signal": "HOLD",
                    "actionable": False,
                    "reason": "行情或合约规则读取失败，请检查当前环境连接",
                }
            )
    selected = choose(results, config.selection.model_dump())
    aligned = len({row.get("bar_time") for row in selected}) == 1
    for card in selected:
        if config.selection.enabled and not aligned:
            card.update(
                selected=False,
                selection_reason="观察池已收盘时间未对齐或行情缺失，等待下一轮完整数据",
            )
        if not card["selected"]:
            card.update(actionable=False, reason=card["selection_reason"])
    return sorted(
        selected,
        key=lambda row: (
            not row.get("actionable", False),
            row["selection_rank"],
            row["symbol"],
        ),
    )


def synchronize(factory, broker, snapshot, owner):
    with factory() as db:
        pending = list(
            db.scalars(select(TestnetOrder).where(TestnetOrder.status.in_(PENDING)))
        )
    for order in pending:
        renew_lease(factory, "testnet", owner)
        report = broker.lookup(order.symbol, order.link_id)
        if report is None:
            # Even an intent after a crash may have reached the exchange.
            with factory.begin() as db:
                guard_lease(db, "testnet", owner)
                db.get(TestnetOrder, order.id).status = "unknown"
            continue
        if order.cancel_requested and report.get("orderStatus") in {
            "New",
            "PartiallyFilled",
            "Untriggered",
        }:
            renew_lease(factory, "testnet", owner)
            try:
                broker.cancel(order.symbol, order.link_id)
                report = broker.lookup(order.symbol, order.link_id) or report
            except Exception:  # noqa: BLE001 - cancellation is uncertain; keep the durable request
                with factory.begin() as db:
                    guard_lease(db, "testnet", owner)
                    db.get(
                        TestnetOrder, order.id
                    ).error = "撤单尚未确认，继续查询原订单；不会重发开仓"
        with factory.begin() as db:
            guard_lease(db, "testnet", owner)
            current = db.get(TestnetOrder, order.id)
            status = report.get("orderStatus", "unknown")
            current.status = status if status in PENDING | TERMINAL else "unknown"
            if current.status in TERMINAL:
                current.cancel_requested = False
            current.error = None if current.status != "unknown" else current.error
            current.order_id = report.get("orderId")
            current.filled_quantity = number(report.get("cumExecQty"))
            current.average_price = number(report.get("avgPrice")) or None
            current.fee = (
                number(report.get("cumExecFee"))
                if report.get("cumExecFee")
                else number((report.get("cumFeeDetail") or {}).get("USDT"))
            )
    if pending:
        # Order acknowledgements are asynchronous; refresh positions after lookup.
        snapshot.clear()
        snapshot.update(broker.snapshot())
    with factory.begin() as db:
        guard_lease(db, "testnet", owner)
        existing = {p.symbol: p for p in db.scalars(select(TestnetPosition))}
        actual = {p["symbol"]: p for p in snapshot["positions"]}
        observed_ms = int(snapshot.get("at_ms") or utcnow().timestamp() * 1000)
        for opening in db.scalars(
            select(TestnetOrder).where(
                TestnetOrder.action == "open",
                TestnetOrder.filled_quantity > 0,
                TestnetOrder.position_closed.is_(False),
            )
        ):
            if opening.symbol in actual or opening.status not in TERMINAL:
                continue
            created = (
                opening.created_at.replace(tzinfo=ZoneInfo("UTC"))
                if opening.created_at.tzinfo is None
                else opening.created_at
            )
            fills = list(
                db.scalars(
                    select(TestnetExecution).where(
                        TestnetExecution.opening_link_id == opening.link_id,
                        TestnetExecution.exec_type == "Trade",
                    )
                )
            )
            inventory = cycle_inventory(fills, opening.order_id, opening.side)
            proven_exit = (
                bool(fills) and inventory["valid"] and inventory["remaining"] == 0
            )
            if (
                proven_exit
                or opening.symbol in existing
                or observed_ms - int(created.timestamp() * 1000) >= 30000
            ):
                opening.position_closed, opening.closed_at_ms = True, observed_ms
        for symbol, position in existing.items():
            if symbol not in actual:
                opening = db.scalar(
                    select(TestnetOrder).where(
                        TestnetOrder.link_id == position.opening_link_id
                    )
                )
                if opening and opening.status in TERMINAL:
                    opening.position_closed = True
                    opening.closed_at_ms = observed_ms
                db.delete(position)
        for symbol, raw in actual.items():
            opening = db.scalar(
                select(TestnetOrder)
                .where(
                    TestnetOrder.symbol == symbol,
                    TestnetOrder.action == "open",
                    TestnetOrder.filled_quantity > 0,
                    TestnetOrder.position_closed.is_(False),
                )
                .order_by(TestnetOrder.id.desc())
                .limit(1)
            )
            fills = (
                list(
                    db.scalars(
                        select(TestnetExecution).where(
                            TestnetExecution.opening_link_id == opening.link_id,
                            TestnetExecution.exec_type == "Trade",
                        )
                    )
                )
                if opening
                else []
            )
            inventory = (
                cycle_inventory(fills, opening.order_id, opening.side)
                if opening
                else None
            )
            earliest = min((row.at_ms for row in fills), default=observed_ms)
            foreign = db.scalar(
                select(TestnetExecution.id)
                .where(
                    TestnetExecution.symbol == symbol,
                    TestnetExecution.exec_type == "Trade",
                    TestnetExecution.at_ms >= earliest,
                    TestnetExecution.owned.is_(False),
                )
                .limit(1)
            )
            # Never claim or close a manual position without a current matching opening.
            if (
                int(raw.get("positionIdx", 0)) != 0
                or opening is None
                or not fills
                or not inventory["valid"]
                or sum(
                    (row.quantity for row in fills if row.order_id == opening.order_id),
                    Decimal(0),
                )
                != opening.filled_quantity
                or inventory["remaining"] != number(raw["size"])
                or foreign is not None
                or opening.side != raw["side"]
                or number(raw["size"]) > opening.filled_quantity
                or opening.average_price is None
                or abs(
                    inventory["cost"] / inventory["remaining"] - number(raw["avgPrice"])
                )
                > max(
                    Decimal("0.00000001"),
                    number(raw["avgPrice"]) * Decimal("0.00000001"),
                )
            ):
                if symbol in existing:
                    db.delete(existing[symbol])
                continue
            position = existing.get(symbol)
            if position is None:
                position = TestnetPosition(
                    symbol=symbol, opening_link_id=opening.link_id
                )
                db.add(position)
            position.side = raw["side"]
            position.opening_link_id = opening.link_id
            position.quantity = number(raw["size"])
            position.entry_price = number(raw["avgPrice"])
            position.mark_price = number(raw["markPrice"])
            position.stop_loss = number(raw.get("stopLoss"))


def submit_intent(
    factory,
    broker,
    owner,
    symbol,
    side,
    action,
    quantity,
    stamp,
    stop=None,
    take=None,
    config=None,
    order_type="Market",
    limit_price=None,
    context=None,
    entry_check=None,
):
    from .entry_checks import EntryBlocked

    with factory.begin() as db:
        guard_lease(db, "testnet", owner)
        old = db.scalar(
            select(TestnetOrder).where(
                TestnetOrder.symbol == symbol,
                TestnetOrder.bar_time == stamp,
                TestnetOrder.action == action,
            )
        )
        if old:
            return
        if action == "open":
            current = db.get(TestnetAccount, 1)
            if not current.enabled or current.risk_halted or current.flatten_requested:
                return
        row = TestnetOrder(
            link_id="qp-" + uuid.uuid4().hex,
            symbol=symbol,
            side=side,
            action=action,
            quantity=number(quantity),
            stop_loss=number(stop) if stop else None,
            take_profit=number(take) if take else None,
            bar_time=stamp,
            status="intent",
            order_type=order_type,
            limit_price=number(limit_price) if limit_price else None,
            context={
                "configuration": config.model_dump(mode="json") if config else None,
                "bar_time": stamp,
                "decision": context,
                "account_risk": (db.get(TestnetAccount, 1).snapshot or {}).get(
                    "account_risk"
                ),
            },
            opening_link_id=(
                db.get(TestnetPosition, symbol).opening_link_id
                if action == "close" and db.get(TestnetPosition, symbol)
                else None
            ),
        )
        db.add(row)
        db.flush()
        order_id = row.id
        if action == "open":
            current.last_bars = {**current.last_bars, symbol: stamp}
    # The intent is committed before IO. Any failure is uncertain, never a retry.
    try:
        if action == "open" and entry_check:

            def checked():
                try:
                    entry_check()
                    with factory() as db:
                        guard_lease(db, "testnet", owner)
                        latest = db.get(TestnetAccount, 1)
                        if (
                            not latest.enabled
                            or latest.risk_halted
                            or latest.flatten_requested
                            or TestnetSetup.model_validate(latest.configuration)
                            != config
                        ):
                            raise EntryBlocked(
                                "交易指令或配置在检查期间变化，未提交订单"
                            )
                except EntryBlocked:
                    raise
                except Exception:  # noqa: BLE001 - no order IO occurred; redact SDK data
                    raise EntryBlocked(
                        "下单前核查失败，未提交订单，等待重新对账"
                    ) from None

            row._entry_check = checked
        result = broker.submit(row, config.leverage if config else 2)
        state, exchange_id, error = "submitted", result["orderId"], None
    except EntryBlocked as exc:
        state, exchange_id, error = "Rejected", None, str(exc)
    except OrderRejected:
        state, exchange_id, error = (
            "Rejected",
            None,
            "交易所已拒绝订单，未重发；请检查交易权限、持仓模式和合约规则",
        )
    except Exception:  # noqa: BLE001 - lookup decides outcome after SDK/transport failures
        state, exchange_id, error = (
            "unknown",
            None,
            "提交结果未确认；先查询订单，禁止自动重复提交",
        )
    with factory.begin() as db:
        guard_lease(db, "testnet", owner)
        current = db.get(TestnetOrder, order_id)
        current.status, current.order_id, current.error = state, exchange_id, error
        db.add(Event(message=f"交易助手{action} {symbol}：{state}"))


def process_testnet(
    factory, market, owner, broker=None, allow_new_entries=True, entry_guard=None
):
    from .execution_status import configuration_fingerprint

    cycle_configuration, cycle_enabled, ranked, scan_at = None, None, None, None
    account(factory)
    with factory.begin() as db:
        if db.get(WorkerLease, "testnet") is None:
            db.add(
                WorkerLease(
                    name="testnet", owner="", expires_at=utcnow(), heartbeat_at=utcnow()
                )
            )
    if not renew_lease(factory, "testnet", owner):
        return
    current = account(factory)
    config = TestnetSetup.model_validate(current.configuration)
    try:
        broker = broker or TestnetBroker()
        snapshot = broker.snapshot()
        snapshot["execution_ready"] = allow_new_entries
        journal_ok = True
        try:
            synchronize_journal(factory, broker, snapshot, owner)
        except Exception as exc:  # noqa: BLE001 - stop openings without losing position protection
            journal_ok = False
            journal_error = safe_error(exc)
        synchronize(factory, broker, snapshot, owner)
        try:
            snapshot["funding_balance"] = broker.funding_balance()
        except Exception:  # noqa: BLE001 - optional balance; never expose SDK metadata
            snapshot["funding_error"] = "资金账户余额读取失败，请检查读取权限"
        now = utcnow()
        day = now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        with factory.begin() as db:
            guard_lease(db, "testnet", owner)
            current = db.get(TestnetAccount, 1)
            config = TestnetSetup.model_validate(current.configuration)
            wallet = number(snapshot["wallet_equity"])
            current.ledger_warning = (
                None
                if journal_ok
                else f"资金或成交流水同步失败，暂停新开仓：{journal_error}"
            )
            if current.journal_start_ms is None and journal_ok and wallet > 0:
                if current.baseline_equity is None:
                    current.baseline_equity = wallet
                current.journal_start_ms = int(snapshot["at_ms"])
                current.baseline_cash = number(snapshot["wallet_balance"])
            flows, changes = flow_totals(
                db, current.journal_start_ms or int(snapshot["at_ms"])
            )
            current.net_flows = flows
            if current.baseline_cash is not None and abs(
                number(snapshot["wallet_balance"]) - current.baseline_cash - changes
            ) > Decimal("0.00001"):
                current.ledger_warning = (
                    "钱包与资金流水尚未一致，等待交易所补齐记录，暂停新开仓"
                )
            baseline = (
                current.baseline_equity
                if current.baseline_equity is not None
                else wallet
            )
            trusted_float = number(
                current.snapshot.get(
                    "trusted_unrealized", current.snapshot.get("unrealized_pnl", 0)
                )
            )
            if current.ledger_warning is None:
                current.equity = number(config.budget) + wallet - baseline - flows
                current.peak_equity = max(current.peak_equity, current.equity)
                trusted_float = number(snapshot.get("unrealized_pnl"))
            protective_equity = (
                current.equity + number(snapshot.get("unrealized_pnl")) - trusted_float
            )
            snapshot["trusted_unrealized"] = str(trusted_float)
            snapshot["protective_equity"] = str(protective_equity)
            snapshot["risk_basis"] = (
                "verified" if current.ledger_warning is None else "floating_only"
            )
            if current.day != day:
                current.day, current.day_equity = day, protective_equity
            risk_state = evaluate_account_risk(
                budget=config.budget,
                equity=protective_equity,
                peak_equity=current.peak_equity,
                day_equity=current.day_equity,
                daily_loss_pct=config.daily_loss_pct,
                max_drawdown_pct=config.max_drawdown_pct,
            )
            snapshot["account_risk"] = risk_state.as_dict()
            if risk_state.halted:
                current.risk_halted, current.enabled = True, False
            current.snapshot, current.updated_at, current.last_error = (
                snapshot,
                now,
                None,
            )
        observed_at = utcnow().isoformat()
        current = account(factory)
        config = TestnetSetup.model_validate(current.configuration)
        cycle_configuration, cycle_enabled = (
            config.model_dump(mode="json"),
            current.enabled,
        )
        with factory() as db:
            owned = list(db.scalars(select(TestnetPosition)))
            pending = list(
                db.scalars(select(TestnetOrder).where(TestnetOrder.status.in_(PENDING)))
            )
        from .ownership import classify

        ownership = classify(factory, snapshot)
        from .account_exposure import summarize

        exposure = summarize(snapshot, ownership, config)
        with factory.begin() as db:
            guard_lease(db, "testnet", owner)
            latest = db.get(TestnetAccount, 1)
            latest.snapshot = {
                **latest.snapshot,
                "ownership": ownership,
                "account_exposure": exposure,
            }
        for position in owned:
            if position.symbol in ownership["blocked_symbols"]:
                continue
            exit_reason = (
                "用户请求平仓"
                if current.flatten_requested
                else "账户风控"
                if current.risk_halted
                else "缺少交易所止损"
                if position.stop_loss <= 0
                else "反向信号"
            )
            exit_needed = (
                current.flatten_requested
                or current.risk_halted
                or position.stop_loss <= 0
            )
            same_pending = [p for p in pending if p.symbol == position.symbol]
            if same_pending:
                if not exit_needed or any(p.action == "close" for p in same_pending):
                    continue
                # Cancel the unfilled opening remainder, while reducing only the
                # confirmed position. A still-uncertain close is never resubmitted.
                with factory.begin() as db:
                    guard_lease(db, "testnet", owner)
                    db.get(TestnetAccount, 1).enabled = False
                    for order in same_pending:
                        db.get(TestnetOrder, order.id).cancel_requested = True
            stamp = f"protect:{position.opening_link_id}:{now.strftime('%Y%m%d%H%M')}"
            if not exit_needed and current.enabled:
                _, closed = market.bars(
                    "bybit",
                    position.symbol,
                    config.interval,
                    max(200, config.rules.warmup + 3),
                )
                signal = generate_signal(closed, **config.rules.signal_options)
                exit_needed = signal == ("SHORT" if position.side == "Buy" else "LONG")
                stamp = closed.iloc[-1].timestamp.isoformat()
            if exit_needed:
                renew_lease(factory, "testnet", owner)
                submit_intent(
                    factory,
                    broker,
                    owner,
                    position.symbol,
                    "Sell" if position.side == "Buy" else "Buy",
                    "close",
                    position.quantity,
                    stamp,
                    config=config,
                    context={"reason": exit_reason},
                )
        if current.flatten_requested and not owned and not pending:
            with factory.begin() as db:
                guard_lease(db, "testnet", owner)
                db.get(TestnetAccount, 1).flatten_requested = False
        if ownership["uncertain"]:
            with factory.begin() as db:
                guard_lease(db, "testnet", owner)
                db.get(
                    TestnetAccount, 1
                ).last_error = (
                    "订单来源或占用金额未确认，已阻止新开仓；系统不会接管外部订单"
                )
        if (
            not current.enabled
            or current.risk_halted
            or current.flatten_requested
            or ownership["uncertain"]
            or pending
            or current.ledger_warning
            or not allow_new_entries
        ):
            return
        if config.research_guard.enabled or config.monitoring.enabled:
            from .strategy_monitor import assess as monitor_assess
            from .strategy_review import current_review

            with factory.begin() as db:
                guard_lease(db, "testnet", owner)
                latest = db.get(TestnetAccount, 1)
                qualification = (
                    current_review(db, latest)
                    if config.research_guard.enabled
                    else {"eligible": True}
                )
                monitoring = (
                    monitor_assess(db, latest)
                    if config.monitoring.enabled
                    else {"pause_new_entries": False}
                )
                if not qualification["eligible"] or monitoring["pause_new_entries"]:
                    latest.enabled = False
                    latest.snapshot = {
                        **latest.snapshot,
                        "strategy_monitor": monitoring,
                        "research_qualification": qualification,
                    }
                    db.add(
                        Event(message="策略研究准入或运行监测未通过，暂停本系统新开仓")
                    )
                    return
        if snapshot["margin_mode"] != "ISOLATED_MARGIN":
            raise ValueError("Testnet account must use isolated margin")
        if market.source != "exchange":
            raise ValueError("Testnet orders require exchange market data")
        # One committed opening per cycle; the next snapshot verifies its exposure.
        ranked = candidates(factory, market, broker, config)
        scan_at = utcnow().isoformat()
        for card in ranked:
            if not renew_lease(factory, "testnet", owner):
                return
            if not card.get("actionable"):
                continue
            if entry_guard and not entry_guard():
                return
            if utcnow() - pd.Timestamp(snapshot["at"]).to_pydatetime() > timedelta(
                seconds=30
            ):
                return
            with factory.begin() as db:
                guard_lease(db, "testnet", owner)
                current = db.get(TestnetAccount, 1)
                if (
                    not current.enabled
                    or current.risk_halted
                    or current.flatten_requested
                ):
                    return
                if current.last_bars.get(card["symbol"]) == card["bar_time"]:
                    continue
            from .entry_checks import EntryBlocked, preflight

            try:
                # Leverage updates may generate position events. Do them before
                # recording an intent so a required reconciliation consumes no bar.
                if hasattr(broker, "prepare_leverage"):
                    broker.prepare_leverage(card["symbol"], config.leverage)
                card = preflight(
                    factory, broker, market, config, card, snapshot, entry_guard
                )
            except EntryBlocked as exc:
                with factory.begin() as db:
                    guard_lease(db, "testnet", owner)
                    current = db.get(TestnetAccount, 1)
                    current.snapshot = {
                        **current.snapshot,
                        "entry_check": {"symbol": card["symbol"], "reason": str(exc)},
                    }
                return
            renew_lease(factory, "testnet", owner)
            submit_intent(
                factory,
                broker,
                owner,
                card["symbol"],
                "Buy" if card["signal"] == "LONG" else "Sell",
                "open",
                card["quantity"],
                card["bar_time"],
                card["stop_loss"],
                card.get("take_profit"),
                config,
                entry_check=lambda card=card: preflight(
                    factory,
                    broker,
                    market,
                    config,
                    card,
                    snapshot,
                    entry_guard,
                    fixed=True,
                ),
                context={
                    key: card.get(key)
                    for key in (
                        "signal",
                        "reason",
                        "signal_basis",
                        "trend",
                        "entry",
                        "quantity",
                        "planned_loss",
                        "margin",
                        "notional",
                        "stop_loss",
                        "take_profit",
                        "preflight",
                        "factors",
                    )
                },
            )
            break
    except Exception as exc:  # noqa: BLE001 - credentials and SDK request data stay private
        with factory.begin() as db:
            guard_lease(db, "testnet", owner)
            current = db.get(TestnetAccount, 1)
            current.last_error = (
                f"交易执行处理失败：{safe_error(exc)}；未确认订单不会重复提交"
            )
    finally:
        if cycle_configuration is not None:
            with factory.begin() as db:
                guard_lease(db, "testnet", owner)
                current = db.get(TestnetAccount, 1)
                current.snapshot = {
                    **current.snapshot,
                    "strategy_execution": {
                        "configuration": cycle_configuration,
                        "configuration_fingerprint": configuration_fingerprint(
                            cycle_configuration
                        ),
                        "enabled": cycle_enabled,
                        "completed_at": utcnow().isoformat(),
                        "observed_at": observed_at,
                        "successful": not current.last_error
                        and not current.ledger_warning,
                        "scan_at": scan_at,
                        "scan": [
                            {
                                key: card.get(key)
                                for key in (
                                    "symbol",
                                    "signal",
                                    "bar_time",
                                    "actionable",
                                    "reason",
                                    "signal_basis",
                                )
                            }
                            for card in (ranked or [])
                        ],
                    },
                }
