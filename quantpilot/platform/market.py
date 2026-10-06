"""Explicit demo or real public market source. Never submits exchange orders."""

from dataclasses import replace
from datetime import datetime, timezone
from math import isfinite
from threading import Lock
from time import monotonic

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from quantpilot.config import Settings
from quantpilot.exchange.binance import BinanceFuturesClient
from quantpilot.exchange.bybit import BybitClient
from quantpilot.market_data import binance_klines_to_frame, klines_to_frame

INTERVAL_MINUTES = {"1": 1, "5": 5, "15": 15, "30": 30, "60": 60, "240": 240, "D": 1440}
BINANCE_INTERVALS = {
    "1": "1m",
    "5": "5m",
    "15": "15m",
    "30": "30m",
    "60": "1h",
    "240": "4h",
    "D": "1d",
}

DEMO_PRICES = {
    "BTCUSDT": 64000,
    "ETHUSDT": 3200,
    "SOLUSDT": 150,
    "BNBUSDT": 580,
    "XRPUSDT": 0.55,
    "DOGEUSDT": 0.12,
    "ADAUSDT": 0.36,
    "AVAXUSDT": 28,
    "LINKUSDT": 14,
    "DOTUSDT": 4.6,
    "LTCUSDT": 70,
    "SUIUSDT": 1.4,
    "APTUSDT": 7,
    "NEARUSDT": 4,
    "TONUSDT": 5.2,
    "TRXUSDT": 0.15,
}


class UnsupportedSymbol(ValueError):
    pass


class HistoryError(ValueError):
    """Safe, user-readable history coverage error."""


def demo_price(symbol, observations):
    phase = sum(map(ord, symbol)) / 17
    ticks = observations + phase
    return DEMO_PRICES[symbol] * (
        1
        + 0.028 * np.sin(ticks / 23)
        + 0.012 * np.sin(ticks / 5)
        + 0.008 * np.sin(ticks * 1.7)
    )


def demo_bars(symbol, times, minutes, now=None):
    """A closed synthetic candle depends only on symbol, interval and timestamp."""
    ticks = times.as_unit("ns").astype("int64").to_numpy() / (minutes * 60 * 1e9)
    close, opening = demo_price(symbol, ticks), demo_price(symbol, ticks - 1)
    frame = pd.DataFrame(
        {
            "timestamp": times,
            "open": opening,
            "high": np.maximum(opening, close) * 1.002,
            "low": np.minimum(opening, close) * 0.998,
            "close": close,
            "volume": 100 + 30 * np.cos(ticks / 7),
        }
    )
    frame["turnover"] = frame.volume * frame.close
    if now is not None and not frame.empty:
        # Only the unfinished candle evolves. Closed bars remain reproducible.
        index = frame.index[-1]
        progress = float(
            (now - frame.at[index, "timestamp"]) / pd.Timedelta(minutes=minutes)
        )
        if 0 <= progress < 1:
            opening = frame.at[index, "open"]
            live = opening + (frame.at[index, "close"] - opening) * progress
            frame.at[index, "close"] = live
            frame.at[index, "high"] = max(opening, live) * (1 + 0.002 * progress)
            frame.at[index, "low"] = min(opening, live) * (1 - 0.002 * progress)
            frame.at[index, "volume"] *= progress
            frame.at[index, "turnover"] = frame.at[index, "volume"] * live
    return frame


class MarketService:
    def __init__(self, source: str):
        self.source = source
        if source == "exchange":
            # Freeze this process's exchange profile before any lazy request.
            # A saved web selection must only take effect after restart.
            load_dotenv(".env", override=False)
        self._cache = {}
        self._catalog_lock = Lock()
        self._ticker_lock = Lock()
        self._history_cache = {}

    def instruments(self, exchange: str):
        with self._catalog_lock:
            key = ("instruments", exchange)
            cached = self._cache.get(key)
            if cached and cached[0] > monotonic():
                return cached[1]
            if self.source == "demo":
                instruments = [
                    {
                        "symbol": symbol,
                        "base_coin": symbol.removesuffix("USDT"),
                        "quote_coin": "USDT",
                    }
                    for symbol in DEMO_PRICES
                ]
            else:
                client = self.client(exchange)
                instruments = []
                for item in client.get_instruments():
                    if exchange == "bybit":
                        allowed = (
                            item.get("status") == "Trading"
                            and item.get("quoteCoin") == "USDT"
                            and item.get("settleCoin") == "USDT"
                            and item.get("contractType") == "LinearPerpetual"
                            and not item.get("isPreListing", False)
                        )
                        base = item.get("baseCoin")
                    else:
                        allowed = (
                            item.get("status") == "TRADING"
                            and item.get("quoteAsset") == "USDT"
                            and item.get("marginAsset") == "USDT"
                            and item.get("contractType") == "PERPETUAL"
                        )
                        base = item.get("baseAsset")
                    if allowed:
                        instruments.append(
                            {
                                "symbol": item["symbol"],
                                "base_coin": base,
                                "quote_coin": "USDT",
                            }
                        )
            instruments = sorted(
                {item["symbol"]: item for item in instruments}.values(),
                key=lambda item: item["symbol"],
            )
            self._cache[key] = (monotonic() + 300, instruments)
            return instruments

    def require_symbol(self, exchange: str, symbol: str):
        if not any(item["symbol"] == symbol for item in self.instruments(exchange)):
            raise UnsupportedSymbol("Symbol is not an active USDT perpetual contract")

    def client(self, exchange, symbol="BTCUSDT", interval="15"):
        settings = self.exchange_settings(exchange, symbol, interval)
        return (
            BinanceFuturesClient(settings)
            if exchange == "binance"
            else BybitClient(settings)
        )

    def tickers(self, exchange: str):
        # Bulk snapshots avoid one request for every listed coin. Independent locks
        # let a catalog fetch and a ticker fetch share the cache without deadlock.
        with self._ticker_lock:
            key = ("tickers", exchange)
            cached = self._cache.get(key)
            if cached and cached[0] > monotonic():
                return cached[1]
            instruments = {item["symbol"]: item for item in self.instruments(exchange)}
            now = datetime.now(timezone.utc)
            rows = []
            if self.source == "demo":
                observations = now.timestamp() / 900
                for symbol, instrument in instruments.items():
                    samples = demo_price(
                        symbol, np.linspace(observations - 96, observations, 97)
                    )
                    price, previous = float(samples[-1]), float(samples[0])
                    rows.append(
                        {
                            **instrument,
                            "last_price": price,
                            "change_24h_pct": (price / previous - 1) * 100,
                            "high_24h": float(samples.max()),
                            "low_24h": float(samples.min()),
                            "volume_24h": 9700.0,
                            "turnover_24h": float(samples.sum() * 100),
                        }
                    )
            else:
                for item in self.client(exchange).get_tickers():
                    symbol = item.get("symbol")
                    if symbol not in instruments:
                        continue
                    fields = (
                        (
                            "lastPrice",
                            "price24hPcnt",
                            "highPrice24h",
                            "lowPrice24h",
                            "volume24h",
                            "turnover24h",
                        )
                        if exchange == "bybit"
                        else (
                            "lastPrice",
                            "priceChangePercent",
                            "highPrice",
                            "lowPrice",
                            "volume",
                            "quoteVolume",
                        )
                    )
                    try:
                        price, change, high, low, volume, turnover = [
                            float(item[field]) for field in fields
                        ]
                    except (KeyError, TypeError, ValueError):
                        continue
                    if (
                        not all(
                            isfinite(value)
                            for value in [price, change, high, low, volume, turnover]
                        )
                        or price <= 0
                        or min(high, low, volume, turnover) < 0
                    ):
                        continue
                    rows.append(
                        {
                            **instruments[symbol],
                            "last_price": price,
                            "change_24h_pct": change * 100
                            if exchange == "bybit"
                            else change,
                            "high_24h": high,
                            "low_24h": low,
                            "volume_24h": volume,
                            "turnover_24h": turnover,
                        }
                    )
            snapshot = {
                "source": self.source,
                "exchange": exchange,
                "fetched_at": now.isoformat(),
                "tickers": sorted(
                    rows, key=lambda row: row["turnover_24h"], reverse=True
                ),
            }
            self._cache[key] = (monotonic() + 10, snapshot)
            return snapshot

    def bars(self, exchange: str, symbol: str, interval: str, limit: int = 200):
        self.require_symbol(exchange, symbol)
        minutes = INTERVAL_MINUTES[interval]
        now = pd.Timestamp(datetime.now(timezone.utc))
        if self.source == "demo":
            times = pd.date_range(
                end=now.floor(f"{minutes}min"), periods=limit, freq=f"{minutes}min"
            )
            frame = demo_bars(symbol, times, minutes, now=now)
        else:
            client = self.client(exchange, symbol, interval)
            rows = client.get_klines(limit=limit)
            frame = (
                binance_klines_to_frame(rows)
                if exchange == "binance"
                else klines_to_frame(rows)
            )
        validate_market(frame)
        expected = pd.Timedelta(minutes=minutes)
        if not frame.timestamp.diff().iloc[1:].eq(expected).all():
            raise ValueError("Market history contains missing intervals")
        if self.source == "exchange" and not -pd.Timedelta(
            seconds=30
        ) <= now - frame.timestamp.iloc[-1] <= expected + pd.Timedelta(seconds=60):
            raise ValueError("Exchange market prices are stale or in the future")
        closed = frame.loc[
            frame.timestamp + pd.Timedelta(minutes=minutes) <= now
        ].reset_index(drop=True)
        return frame, closed

    def history(self, exchange, symbol, interval, start_at, end_at, warmup=61):
        """Fetch a closed, contiguous range plus indicator warmup, never live freshness-check it."""
        self.require_symbol(exchange, symbol)
        minutes = INTERVAL_MINUTES[interval]
        step = pd.Timedelta(minutes=minutes)
        start = pd.Timestamp(start_at).ceil(f"{minutes}min")
        end = pd.Timestamp(end_at)
        first = start - step * (warmup + 1)
        last = (end - step).floor(f"{minutes}min")
        if last < start:
            raise HistoryError("区间内没有完整 K 线，请扩大时间范围")
        count = int((last - first) / step) + 1
        if count > 100600:
            raise HistoryError("区间超过单次历史数据上限")
        cache_key = (exchange, symbol, interval, first.isoformat(), last.isoformat())
        if cache_key in self._history_cache:
            return self._history_cache[cache_key].copy()
        if self.source == "demo":
            times = pd.date_range(first, last, freq=step)
            frame = demo_bars(symbol, times, minutes)
        else:
            client = self.client(exchange, symbol, interval)
            pages, cursor = [], first
            for _ in range(102):
                if cursor > last:
                    break
                # Bound each window to 1000 bars so both newest-first Bybit and
                # oldest-first Binance return the same deterministic range.
                window_end = min(cursor + step * 999, last)
                raw = client.get_klines(
                    limit=1000,
                    start=int(cursor.timestamp() * 1000),
                    end=int(window_end.timestamp() * 1000),
                )
                part = (
                    binance_klines_to_frame(raw)
                    if exchange == "binance"
                    else klines_to_frame(raw)
                )
                if part.empty:
                    raise HistoryError(
                        "历史数据不足，可能早于交易对上市时间，请缩短区间"
                    )
                pages.append(part)
                cursor = window_end + step
            frame = (
                pd.concat(pages)
                .drop_duplicates("timestamp")
                .sort_values("timestamp")
                .reset_index(drop=True)
            )
            frame = frame.loc[
                (frame.timestamp >= first) & (frame.timestamp <= last)
            ].reset_index(drop=True)
        validate_market(frame)
        if (
            len(frame) != count
            or frame.timestamp.iloc[0] != first
            or frame.timestamp.iloc[-1] != last
            or not frame.timestamp.diff().iloc[1:].eq(step).all()
        ):
            raise HistoryError("历史区间缺少 K 线或指标预热数据，请调整起止时间")
        if len(self._history_cache) >= 3:
            self._history_cache.pop(next(iter(self._history_cache)))
        self._history_cache[cache_key] = frame.copy()
        return frame

    @staticmethod
    def exchange_settings(exchange, symbol="BTCUSDT", interval="15"):
        settings = replace(
            Settings.from_env(".env"),
            exchange=exchange,
            symbol=symbol,
            interval=interval,
        )
        if exchange == "bybit" and settings.category != "linear":
            raise ValueError(
                "The platform currently supports Bybit linear contracts only"
            )
        if exchange == "binance":
            settings = replace(settings, interval=BINANCE_INTERVALS[interval])
        testnet = settings.testnet if exchange == "bybit" else settings.binance_testnet
        if not testnet and settings.live_trading_confirm != "I_UNDERSTAND_LIVE_TRADING":
            raise ValueError("Mainnet read-only access requires confirmation in .env")
        return settings


def validate_market(frame):
    if frame.empty:
        raise ValueError("No market bars available")
    if (
        frame.timestamp.isna().any()
        or frame.timestamp.duplicated().any()
        or not frame.timestamp.is_monotonic_increasing
    ):
        raise ValueError("Invalid market timestamps")
    prices = frame[["open", "high", "low", "close"]].to_numpy(dtype=float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("Invalid market prices")
    if (frame.high < frame[["open", "close", "low"]].max(axis=1)).any() or (
        frame.low > frame[["open", "close", "high"]].min(axis=1)
    ).any():
        raise ValueError("Invalid OHLC bounds")
    for column in ("volume", "turnover"):
        if column in frame:
            values = frame[column].to_numpy(dtype=float)
            if not np.isfinite(values).all() or (values < 0).any():
                raise ValueError("Invalid market volume or turnover")


def serialize_bars(frame):
    values = frame.copy()
    values["timestamp"] = values.timestamp.map(lambda t: t.isoformat())
    return values.to_dict(orient="records")
