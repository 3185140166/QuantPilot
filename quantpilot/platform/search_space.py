"""Explicit limits and defaults for template-specific optimization dimensions."""

SPACE = {
    "fast": ("快 EMA", 2, 499, "int", 5, 40, 1),
    "slow": ("慢 EMA", 3, 500, "int", 40, 150, 1),
    "rsi_period": ("RSI 周期", 2, 100, "int", 7, 28, 1),
    "rsi_lower": ("RSI 下限", 1, 99, "float", 20, 40, 5),
    "rsi_upper": ("RSI 上限", 1, 99, "float", 60, 80, 5),
    "breakout_period": ("突破窗口", 5, 200, "int", 10, 60, 5),
    "bollinger_period": ("布林带窗口", 5, 200, "int", 10, 60, 5),
    "bollinger_std": ("布林带标准差倍数", 0.5, 5, "float", 1.5, 3, 0.25),
    "stop_loss_pct": ("止损 %", 0, 100, "float", 0, 5, 0.5),
    "take_profit_pct": ("止盈 %", 0, 1000, "float", 0, 10, 1),
    "volume_ratio": ("成交量倍数", 0, 10, "float", 0, 3, 0.5),
    "atr_pct_max": ("ATR / 价格上限 %", 0, 100, "float", 0, 5, 0.5),
    "adx_min": ("ADX 下限", 0, 100, "float", 0, 40, 5),
}
TEMPLATE_KEYS = {
    "ema_cross": ["fast", "slow"],
    "ema_rsi": ["fast", "slow", "rsi_period", "rsi_lower", "rsi_upper"],
    "rsi_reversion": ["rsi_period", "rsi_lower", "rsi_upper"],
    "breakout": ["breakout_period"],
    "bollinger_reversion": ["bollinger_period", "bollinger_std"],
}
COMMON_KEYS = [
    "stop_loss_pct",
    "take_profit_pct",
    "volume_ratio",
    "atr_pct_max",
    "adx_min",
]


def space_for(template):
    return [
        {
            "key": key,
            "label": SPACE[key][0],
            "min": SPACE[key][1],
            "max": SPACE[key][2],
            "kind": SPACE[key][3],
            "low": SPACE[key][4],
            "high": SPACE[key][5],
            "step": SPACE[key][6],
            "enabled": key in TEMPLATE_KEYS[template],
        }
        for key in TEMPLATE_KEYS[template] + COMMON_KEYS
    ]
