"""Reproducible feed diagnostics; warnings do not invent missing settlements."""

import hashlib
from itertools import pairwise
from math import isfinite

import pandas as pd

from .market import INTERVAL_MINUTES, HistoryError, validate_market


def environment_tag(market, exchange, symbol, interval, expected=None):
    if market.source == "demo":
        return "demo"
    profile = market.exchange_settings(exchange, symbol, interval)
    actual = (
        "testnet"
        if (profile.testnet if exchange == "bybit" else profile.binance_testnet)
        else "mainnet"
    )
    if expected in {"testnet", "mainnet"} and actual != expected:
        raise HistoryError(
            "研究任务环境与当前行情进程不一致；请切回原环境或重新创建任务"
        )
    return actual


def candle_audit(frame, interval):
    validate_market(frame)
    stamps = pd.DatetimeIndex(frame.timestamp)
    step = pd.Timedelta(minutes=INTERVAL_MINUTES[interval])
    if stamps.tz is None or not stamps.to_series().diff().iloc[1:].eq(step).all():
        raise ValueError("行情时间必须包含时区且连续")
    columns = [
        c
        for c in ("timestamp", "open", "high", "low", "close", "volume", "turnover")
        if c in frame
    ]
    canonical = frame[columns].copy()
    for column in columns[1:]:
        canonical[column] = canonical[column].astype(float)
    canonical["timestamp"] = stamps.tz_convert("UTC").astype("datetime64[ns, UTC]")
    return {
        "bars": len(frame),
        "first": stamps[0].isoformat(),
        "last": stamps[-1].isoformat(),
        "zero_volume_bars": int(frame.volume.eq(0).sum())
        if "volume" in frame
        else None,
        "fingerprint": hashlib.sha256(
            canonical.to_json(
                orient="records", date_format="iso", date_unit="ns", double_precision=15
            ).encode()
        ).hexdigest(),
    }


def contract_audit(
    prices, marks, funding, contract, interval, start, end, include_funding
):
    price = candle_audit(prices, interval)
    mark = candle_audit(marks, interval)
    left = pd.DatetimeIndex(prices.timestamp).as_unit("ns")
    right = pd.DatetimeIndex(marks.timestamp).as_unit("ns")
    if not left.equals(right):
        raise ValueError("成交价格与标记价格必须逐根对齐")
    basis = (marks.close.to_numpy() / prices.close.to_numpy()) - 1
    max_basis = float(abs(basis).max() * 100)
    warnings = []
    if max_basis > 1:
        warnings.append("成交价与标记价最大偏离超过1%，请核对数据与止损触发口径")
    rows = sorted(pd.Timestamp(r["timestamp"]) for r in funding)
    if include_funding:
        if not rows:
            raise ValueError("资金费启用但没有历史记录")
        if len(set(rows)) != len(rows):
            raise ValueError("资金费记录重复")
        if any(
            r.tzinfo is None or not pd.Timestamp(start) <= r < pd.Timestamp(end)
            for r in rows
        ):
            raise ValueError("资金费时间必须包含时区并位于交易区间")
        if any(
            not isfinite(float(row["rate"])) or abs(float(row["rate"])) >= 1
            for row in funding
        ):
            raise ValueError("资金费率无效")
        boundaries = [pd.Timestamp(start), *rows, pd.Timestamp(end)]
        largest_gap = max((b - a).total_seconds() / 60 for a, b in pairwise(boundaries))
        current_interval = contract.get("funding_interval_minutes", 480)
        if largest_gap > current_interval * 1.05:
            warnings.append(
                "资金费记录存在较长空档；当前结算周期仅作参考，不能确认历史是否缺失"
            )
        warnings.append(
            "历史资金费覆盖依据接口已返回记录；结算频率可能变化，不将未返回记录补为零"
        )
    else:
        largest_gap = None
        warnings.append("本次关闭资金费，结果未包含该项成本")
    return {
        "price": price,
        "mark": mark,
        "max_mark_basis_pct": max_basis,
        "funding_records": len(rows),
        "funding_largest_gap_minutes": largest_gap,
        "contract_captured_at": contract["captured_at"],
        "warnings": warnings,
        "historical_contract_rules": False,
    }
