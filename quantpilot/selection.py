"""Frozen, causal contract selection shared by replay and execution."""

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .factors import features
from .sizing import allocation


def budget_fit(config, price, instrument):
    plans = [
        allocation(
            config, config.budget, price, side, instrument, available=config.budget
        )
        for side in ("LONG", "SHORT")
    ]
    accepted = any(plan["accepted"] for plan in plans)
    return {
        "budget_fit": accepted,
        "budget_reason": "当前预算与止损风险可满足最小订单"
        if accepted
        else plans[0]["reason"],
    }


class SelectionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    enabled: bool = False
    ranking: Literal["liquidity", "trend_quality"] = "liquidity"
    top_n: int = Field(default=3, ge=1, le=10)
    min_turnover: float = Field(default=0, ge=0)
    max_atr_pct: float = Field(default=0, ge=0, le=100)


def selection_features(bars, rules):
    frame = features(bars, rules)
    frame["liquidity"] = (
        (bars.close * bars.volume)
        .rolling(24, min_periods=24)
        .median()
    )
    # Fixed heuristic, never a fitted expected return or probability.
    aligned = frame.trend * frame.momentum > 0
    frame["selection_score"] = (
        frame.trend.abs() / frame.volatility.clip(lower=0.01)
    ).where(aligned, 0)
    return frame


def observation(frame, index=-1):
    row = frame.iloc[index]
    return {
        key: float(row[key]) if math.isfinite(float(row[key])) else None
        for key in (
            "trend",
            "momentum",
            "volatility",
            "strength",
            "liquidity",
            "selection_score",
        )
    }


def select(rows, policy):
    policy = SelectionPolicy.model_validate(policy)
    enriched = []
    for raw in rows:
        row = dict(raw)
        reasons = []
        if row.get("budget_fit") is False:
            reasons.append(row.get("budget_reason", "预算不适配最小订单"))
        if any(
            row.get(key) is None
            for key in ("liquidity", "volatility", "selection_score")
        ):
            reasons.append("因子或成交额样本不足")
        if row.get("liquidity") is not None and row["liquidity"] < policy.min_turnover:
            reasons.append("每根K线中位成交额低于筛选门槛")
        if (
            policy.max_atr_pct
            and row.get("volatility") is not None
            and row["volatility"] > policy.max_atr_pct
        ):
            reasons.append("ATR波动率超过筛选上限")
        row["selection_reasons"] = reasons
        enriched.append(row)
    metric = (
        "selection_score"
        if policy.enabled and policy.ranking == "trend_quality"
        else "liquidity"
    )
    enriched.sort(
        key=lambda row: (
            bool(row["selection_reasons"]) if policy.enabled else False,
            -(row.get(metric) or 0),
            row["symbol"],
        )
    )
    qualified = 0
    for rank, row in enumerate(enriched, 1):
        row["selection_rank"] = rank
        if not row["selection_reasons"]:
            qualified += 1
        selected = not row["selection_reasons"] and qualified <= policy.top_n
        row["selected"] = selected if policy.enabled else True
        if policy.enabled and not row["selection_reasons"] and not selected:
            row["selection_reasons"] = ["未进入本根已收盘K线的候选前列"]
        row["selection_reason"] = "；".join(row["selection_reasons"]) or (
            "已进入候选池，仍需入场信号与风控通过"
            if policy.enabled
            else "未启用动态筛选，沿用已配置币种"
        )
    return enriched
