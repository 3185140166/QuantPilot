"""Position sizing and guardrails."""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal


def round_step(value: Decimal | float | str, step: Decimal | float | str) -> Decimal:
    value, step = Decimal(str(value)), Decimal(str(step))
    if step <= 0:
        raise ValueError("step must be positive")
    return (value / step).quantize(Decimal(1), rounding=ROUND_DOWN) * step


def calculate_position_size(
    equity: Decimal | float | str,
    entry_price: Decimal | float | str,
    stop_price: Decimal | float | str,
    risk_ratio: Decimal | float | str = "0.01",
    qty_step: Decimal | float | str = "0.001",
    min_qty: Decimal | float | str = "0.001",
) -> Decimal:
    equity = Decimal(str(equity))
    entry = Decimal(str(entry_price))
    stop = Decimal(str(stop_price))
    risk = equity * Decimal(str(risk_ratio))
    distance = abs(entry - stop)
    if equity <= 0 or risk <= 0 or distance <= 0:
        return Decimal(0)
    quantity = round_step(risk / distance, qty_step)
    minimum = Decimal(str(min_qty))
    return quantity if quantity >= minimum else Decimal(0)
