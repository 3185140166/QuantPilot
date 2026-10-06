"""Public-only contract snapshots, mark candles and historical funding records."""

from math import isfinite

import pandas as pd

from quantpilot.market_data import klines_to_frame

from .market import INTERVAL_MINUTES, HistoryError, validate_market
from .models import utcnow
from .schemas import ContractSnapshot


def snapshot(market, symbol, interval):
    client = market.client("bybit", symbol, interval)
    raw, tiers = client.get_instrument(), client.get_risk_limits()
    if (
        raw.get("status") != "Trading"
        or raw.get("contractType") != "LinearPerpetual"
        or raw.get("settleCoin") != "USDT"
    ):
        raise HistoryError("组合回测只支持可交易的USDT永续合约")
    lot = raw["lotSizeFilter"]
    tiers = sorted(tiers, key=lambda row: float(row["riskLimitValue"]))
    return ContractSnapshot(
        constraints={
            "qty_step": lot["qtyStep"],
            "min_qty": lot["minOrderQty"],
            "max_qty": lot.get("maxMktOrderQty") or lot["maxOrderQty"],
            "min_notional": lot.get("minNotionalValue", 0),
            "tick_size": raw["priceFilter"]["tickSize"],
        },
        maintenance_tiers=[
            {
                "notional_limit": row["riskLimitValue"],
                "maintenance_rate": row["maintenanceMargin"],
                "deduction": row.get("mmDeduction")
                or (0 if str(row.get("isLowestRisk")) == "1" else None),
            }
            for row in tiers
        ],
        captured_at=utcnow(),
        funding_interval_minutes=raw.get("fundingInterval", 480),
    ).model_dump(mode="json")


def load_inputs(market, symbol, interval, start, end, warmup, include_funding=True):
    bars = market.history("bybit", symbol, interval, start, end, warmup)
    bars = bars.copy()
    bars["timestamp"] = pd.to_datetime(bars.timestamp, utc=True).astype(
        "datetime64[ns, UTC]"
    )
    client = market.client("bybit", symbol, interval)
    step = pd.Timedelta(minutes=INTERVAL_MINUTES[interval])
    cursor, last, pages = bars.timestamp.iloc[0], bars.timestamp.iloc[-1], []
    while cursor <= last:
        stop = min(cursor + step * 999, last)
        raw = client.get_mark_klines(
            int(cursor.timestamp() * 1000), int(stop.timestamp() * 1000)
        )
        if not raw:
            raise HistoryError(f"{symbol} 缺少标记价格历史，无法检查强平与保护价")
        pages.append(klines_to_frame([list(row) + ["0", "0"] for row in raw]))
        cursor = stop + step
    marks = pd.concat(pages).sort_values("timestamp").reset_index(drop=True)
    marks["timestamp"] = pd.to_datetime(marks.timestamp, utc=True).astype(
        "datetime64[ns, UTC]"
    )
    validate_market(marks)
    if not marks.timestamp.equals(bars.timestamp.reset_index(drop=True)):
        raise HistoryError(f"{symbol} 标记价格与成交价格K线未完整对齐")
    funding = []
    if include_funding:
        first = int(pd.Timestamp(start).timestamp() * 1000)
        cursor = int(pd.Timestamp(end).timestamp() * 1000) - 1
        for _ in range(400):
            raw = client.get_funding_history(first, cursor)
            if not raw:
                break
            stamps = [int(row["fundingRateTimestamp"]) for row in raw]
            if min(stamps) < first or max(stamps) > cursor:
                raise HistoryError("资金费分页返回了区间外记录")
            for row, stamp in zip(raw, stamps, strict=True):
                rate = float(row["fundingRate"])
                if row.get("symbol") != symbol or not isfinite(rate) or abs(rate) >= 1:
                    raise HistoryError("资金费数据无效")
                funding.append(
                    {
                        "timestamp": pd.Timestamp(stamp, unit="ms", tz="UTC"),
                        "rate": rate,
                    }
                )
            cursor = min(stamps) - 1
            if cursor < first or len(raw) < 200:
                break
        else:
            raise HistoryError("资金费记录超出单次回测限制")
        if not funding:
            raise HistoryError(f"{symbol} 未获取到区间内资金费记录；不能默认为零")
        if len({row["timestamp"] for row in funding}) != len(funding):
            raise HistoryError("资金费记录重复")
    return (
        bars.reset_index(drop=True),
        marks,
        sorted(funding, key=lambda row: row["timestamp"]),
    )
