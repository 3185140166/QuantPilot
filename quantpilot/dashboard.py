"""Read-only quantitative research workspace."""

from dataclasses import asdict
from datetime import datetime, timezone

import altair as alt
import pandas as pd
import streamlit as st

from quantpilot.backtest import run_backtest
from quantpilot.config import Settings
from quantpilot.exchange.binance import BinanceFuturesClient
from quantpilot.exchange.bybit import BybitClient
from quantpilot.indicators import ema, rsi
from quantpilot.market_data import binance_klines_to_frame, closed_bars, klines_to_frame
from quantpilot.metrics import report
from quantpilot.strategy import generate_signal


@st.cache_data(ttl=60, max_entries=32)
def load_bars(exchange, symbol, interval, limit, testnet, category):
    settings = Settings(
        api_key=None,
        api_secret=None,
        exchange=exchange,
        symbol=symbol,
        interval=interval,
        testnet=testnet,
        binance_testnet=testnet,
        category=category,
    )
    client = (
        BinanceFuturesClient(settings)
        if exchange == "binance"
        else BybitClient(settings)
    )
    rows = client.get_klines(limit=limit)
    frame = (
        binance_klines_to_frame(rows)
        if exchange == "binance"
        else klines_to_frame(rows)
    )
    return closed_bars(frame), datetime.now(timezone.utc)


def price_chart(bars, show_ema):
    base = alt.Chart(bars).encode(x=alt.X("timestamp:T", title="时间 · UTC"))
    color = alt.condition(
        "datum.close >= datum.open", alt.value("#39d5ab"), alt.value("#f47787")
    )
    wick = base.mark_rule().encode(
        y=alt.Y("low:Q", scale=alt.Scale(zero=False), title="价格"),
        y2="high:Q",
        color=color,
    )
    body = base.mark_bar(size=4).encode(
        y="open:Q",
        y2="close:Q",
        color=color,
        tooltip=["timestamp:T", "open:Q", "high:Q", "low:Q", "close:Q", "volume:Q"],
    )
    layers = wick + body
    if show_ema:
        for field, shade in [("EMA 20", "#7ca7ff"), ("EMA 60", "#efc477")]:
            layers += base.mark_line(color=shade).encode(
                y=alt.Y(f"{field}:Q", title="价格")
            )
    volume = (
        base.mark_bar()
        .encode(y=alt.Y("volume:Q", title="成交量"), color=color)
        .properties(height=85)
    )
    return (
        alt.vconcat(layers.properties(height=340).interactive(bind_y=False), volume)
        .resolve_scale(x="shared")
        .configure_view(strokeOpacity=0)
    )


@st.cache_data(max_entries=16, show_spinner=False)
def analyze_bars(bars, capital, quantity, fee, slippage):
    return run_backtest(
        bars,
        initial_equity=capital,
        quantity=quantity,
        fee_rate=fee / 100,
        slippage_bps=slippage,
    )


def main():
    st.set_page_config(
        page_title="QuantPilot · 量化研究工作台", page_icon="◈", layout="wide"
    )
    st.markdown(
        """<style>
    .stApp {background:#0b101b;color:#e4eaf5;}
    [data-testid="stSidebar"] {background:#101827;border-right:1px solid #243047;}
    .block-container {padding-top:2rem;max-width:1600px;}
    [data-testid="stMetric"] {background:#131e30;border:1px solid #273650;border-radius:12px;padding:16px;}
    [data-testid="stMetricLabel"], [data-testid="stCaptionContainer"] {color:#9cacc5;}
    [data-testid="stMetricValue"] {font-size:1.65rem;}
    h1 {letter-spacing:-1.5px;}
    .eyebrow {color:#62d9bc;letter-spacing:3px;font-size:12px;font-weight:700;}
    .stTabs [data-baseweb="tab-list"] {gap:24px;border-bottom:1px solid #273650;}
    </style>""",
        unsafe_allow_html=True,
    )
    try:
        settings = Settings.from_env(".env")
    except ValueError:
        st.error("配置无法加载，请检查 .env 中的交易所与环境配置。")
        st.stop()
    with st.sidebar:
        st.markdown("## ◈ QuantPilot")
        st.caption("QUANTITATIVE RESEARCH")
        st.divider()
        st.markdown("#### 行情工作区")
        exchange = st.selectbox(
            "交易所",
            ["bybit", "binance"],
            index=0 if settings.exchange == "bybit" else 1,
        )
        symbol = st.selectbox(
            "交易对",
            list(dict.fromkeys([settings.symbol, "BTCUSDT", "ETHUSDT", "SOLUSDT"])),
        )
        intervals = {
            "1 分钟": "1",
            "5 分钟": "5",
            "15 分钟": "15",
            "30 分钟": "30",
            "1 小时": "60" if exchange == "bybit" else "1h",
        }
        values = list(intervals.values())
        default = settings.interval if settings.interval in values else "15"
        period = st.selectbox("K 线周期", list(intervals), index=values.index(default))
        limit = st.select_slider("样本数量", options=[100, 200, 500, 1000], value=200)
        testnet = settings.testnet if exchange == "bybit" else settings.binance_testnet
        st.caption(("测试网" if testnet else "主网") + "行情 · 已收盘 K 线")
        if st.button("↻ 刷新行情", width="stretch"):
            load_bars.clear()
        st.divider()
        st.markdown("#### 回测配置")
        capital = st.number_input(
            "初始资金（报价币）", min_value=1.0, value=10000.0, step=1000.0
        )
        quantity = st.number_input(
            "每次交易数量（基础币）", min_value=0.000001, value=0.001, format="%.6f"
        )
        fee = st.number_input(
            "单边手续费（%）", min_value=0.0, max_value=10.0, value=0.055, format="%.3f"
        )
        slippage = st.number_input(
            "单边滑点（基点）", min_value=0.0, max_value=1000.0, value=2.0
        )
        st.caption("调整后自动重新计算。1 基点 = 0.01%。")
        st.divider()
        st.caption("研究模式 · 本页面仅访问公开行情")
    st.markdown(
        '<div class="eyebrow">MARKET / STRATEGY / RESEARCH</div>',
        unsafe_allow_html=True,
    )
    st.title("量化研究工作台")
    st.caption(f"{exchange.upper()} / {symbol} / {period} · EMA 趋势交叉 + RSI 过滤")
    try:
        with st.spinner("正在同步交易所行情…"):
            bars, fetched_at = load_bars(
                exchange, symbol, intervals[period], limit, testnet, settings.category
            )
    except Exception:  # noqa: BLE001 - isolate exchange SDK failures at the UI boundary
        st.error(
            "行情连接失败。请检查代理、交易所网络与交易对，然后点击「刷新行情」重试。"
        )
        st.stop()
    if bars.empty:
        st.warning("没有可用的已收盘 K 线，请刷新或切换交易对。")
        st.stop()
    bars = bars.copy()
    bars["EMA 20"] = ema(bars.close, 20)
    bars["EMA 60"] = ema(bars.close, 60)
    bars["RSI 14"] = rsi(bars.close, 14)
    result = analyze_bars(bars, capital, quantity, fee, slippage)
    stats = report(result)
    last = bars.iloc[-1]
    change = last.close / bars.iloc[-2].close - 1 if len(bars) > 1 else 0
    signal = {"LONG": "做多信号", "SHORT": "做空信号", "HOLD": "等待信号"}[
        generate_signal(bars)
    ]
    st.caption(
        f"行情获取 {fetched_at:%Y-%m-%d %H:%M:%S} UTC · 最新 K 线起始 {last.timestamp:%Y-%m-%d %H:%M} UTC · {len(bars)} 根 · 缓存 60 秒，手动刷新"
    )
    cols = st.columns(5)
    cols[0].metric("最新收盘价", f"{last.close:,.2f}", f"{change:+.2%} 较上一根")
    cols[1].metric("当前策略", signal)
    cols[2].metric("回测净收益率", f"{stats['total_return']:+.3%}")
    cols[3].metric("含浮盈亏权益最大回撤", f"{stats['max_drawdown']:.3%}")
    cols[4].metric(
        "回测成交 / 胜率", f"{int(stats['trades'])} / {stats['win_rate']:.0%}"
    )
    market, research, trades, data = st.tabs(
        ["行情总览", "策略与回测", "回测成交记录", "数据中心"]
    )
    with market:
        chart, detail = st.columns([3, 1])
        with chart:
            st.subheader(f"{symbol} · 价格走势")
            show_ema = st.checkbox("显示 EMA 20 / EMA 60", value=True)
            st.altair_chart(price_chart(bars, show_ema), width="stretch")
        with detail:
            st.subheader("市场快照")
            st.caption("当前样本统计，非 24 小时行情")
            st.metric("样本最高价", f"{bars.high.max():,.2f}")
            st.metric("样本最低价", f"{bars.low.min():,.2f}")
            st.metric("RSI · 14", f"{last['RSI 14']:.1f}")
            st.markdown("**均线状态**")
            st.write(
                "快线高于慢线"
                if last["EMA 20"] > last["EMA 60"]
                else "快线低于或等于慢线"
            )
            st.caption("蓝线 EMA 20 · 金线 EMA 60 · 绿涨红跌")
    with research:
        left, right = st.columns([2, 1])
        with left:
            st.subheader("回测权益（含浮动盈亏）")
            st.caption("固定数量交易；每根收盘盯市，包含手续费与持仓浮动盈亏。")
            if not result.equity_curve.empty:
                st.line_chart(result.equity_curve.rename("权益"), color="#62d9bc")
                st.subheader("盯市权益回撤")
                st.area_chart(
                    (
                        (result.equity_curve / result.equity_curve.cummax() - 1) * 100
                    ).rename("回撤（%）"),
                    color="#f47787",
                )
            else:
                st.info("至少需要 63 根已收盘 K 线才能形成回测权益序列。")
        with right:
            st.subheader("策略逻辑")
            st.markdown(
                "**01 · 趋势触发**\n\nEMA 20 上穿 EMA 60 产生多头候选，下穿产生空头候选。\n\n**02 · 动量过滤**\n\n做多要求 RSI < 70；做空要求 RSI > 30。\n\n**03 · 模拟执行**\n\n收盘后计算信号，下一根开盘成交；反向信号平仓并反手，样本末尾平仓。"
            )
            st.metric("期末回测资金", f"{result.final_equity:,.2f}")
            st.metric("净盈亏（报价币）", f"{result.final_equity - capital:+,.4f}")
            st.info("等待信号表示最新 K 线未触发入场条件，不代表账户当前空仓。")
            st.caption("未模拟资金费率、强平、保证金及最小下单量。")
    with trades:
        st.subheader("模拟成交明细")
        st.caption("价格包含滑点，净盈亏扣除双边手续费；非交易所真实订单。")
        frame = pd.DataFrame([asdict(trade) for trade in result.trades])
        if frame.empty:
            st.info("当前样本没有触发成交。可以扩大样本或切换交易周期。")
        else:
            st.dataframe(
                frame.rename(
                    columns={
                        "side": "方向",
                        "entry_time": "入场时间",
                        "exit_time": "出场时间",
                        "entry_price": "入场价",
                        "exit_price": "出场价",
                        "quantity": "数量",
                        "pnl": "净盈亏",
                    }
                ),
                width="stretch",
                hide_index=True,
            )
            st.download_button(
                "下载回测成交 CSV",
                frame.to_csv(index=False).encode("utf-8-sig"),
                f"{symbol}_backtest.csv",
                "text/csv",
            )
    with data:
        st.subheader("行情与技术指标")
        st.caption("时间为 UTC；已移除可能尚未收盘的最新 K 线。")
        st.dataframe(
            bars.sort_values("timestamp", ascending=False),
            width="stretch",
            hide_index=True,
        )
        st.download_button(
            "下载行情 CSV",
            bars.to_csv(index=False).encode("utf-8-sig"),
            f"{symbol}_bars.csv",
            "text/csv",
        )
    st.divider()
    st.caption("QuantPilot / Research workspace · 公开行情、策略研究、成本回测")


if __name__ == "__main__":
    main()
