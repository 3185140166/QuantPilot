"""Fixed-budget account limits shared by research, execution and status views."""

from dataclasses import dataclass
from decimal import Decimal

from .sizing import number


@dataclass(frozen=True)
class AccountRisk:
    budget: Decimal
    equity: Decimal
    peak_equity: Decimal
    day_equity: Decimal
    drawdown_amount: Decimal
    daily_loss_amount: Decimal
    drawdown_limit: Decimal
    daily_loss_limit: Decimal
    halted: bool

    def as_dict(self):
        return {
            "policy": "fixed_budget_v1",
            "basis": "fixed_budget",
            "day_timezone": "Asia/Shanghai",
            **{
                key: str(getattr(self, key))
                for key in (
                    "budget",
                    "equity",
                    "peak_equity",
                    "day_equity",
                    "drawdown_amount",
                    "daily_loss_amount",
                    "drawdown_limit",
                    "daily_loss_limit",
                )
            },
            "drawdown_pct": str(self.drawdown_amount / self.budget * 100),
            "daily_loss_pct": str(self.daily_loss_amount / self.budget * 100),
            "halted": self.halted,
        }


def evaluate_account_risk(
    *, budget, equity, peak_equity, day_equity, daily_loss_pct, max_drawdown_pct
):
    budget, equity = number(budget), number(equity)
    peak_equity, day_equity = number(peak_equity), number(day_equity)
    daily_pct, drawdown_pct = number(daily_loss_pct), number(max_drawdown_pct)
    if budget <= 0 or daily_pct <= 0 or drawdown_pct <= 0:
        raise ValueError("Budget and account risk percentages must be positive")
    drawdown = max(Decimal(0), peak_equity - equity)
    daily = max(Decimal(0), day_equity - equity)
    drawdown_limit, daily_limit = budget * drawdown_pct / 100, budget * daily_pct / 100
    return AccountRisk(
        budget,
        equity,
        peak_equity,
        day_equity,
        drawdown,
        daily,
        drawdown_limit,
        daily_limit,
        equity <= 0 or drawdown >= drawdown_limit or daily >= daily_limit,
    )
