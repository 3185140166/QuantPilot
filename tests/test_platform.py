"""Offline integration checks for durable state, authentication and worker recovery."""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from quantpilot.config import Settings
from quantpilot.exchange.binance import BinanceFuturesClient
from quantpilot.exchange.bybit import BybitClient
from quantpilot.platform.api import create_app
from quantpilot.platform.database import make_database
from quantpilot.platform.engine import guard_lease, process_strategy
from quantpilot.platform.leases import renew_lease
from quantpilot.platform.market import MarketService
from quantpilot.platform.models import (
    BacktestJob,
    Event,
    PaperOrder,
    Position,
    Strategy,
    WorkerLease,
    utcnow,
)
from quantpilot.platform.research_worker import process_job
from quantpilot.platform.settings import PlatformSettings

TOKEN = "test-owner-token-" + "x" * 32
AUTH = {"Authorization": f"Bearer {TOKEN}"}


class FakeMarket:
    source = "demo"

    def __init__(self):
        self.offset = 0
        self.price = 100.0

    def instruments(self, exchange):
        return MarketService("demo").instruments(exchange)

    def require_symbol(self, exchange, symbol):
        return MarketService("demo").require_symbol(exchange, symbol)

    def tickers(self, exchange):
        return MarketService("demo").tickers(exchange)

    def history(self, *args, **kwargs):
        return MarketService("demo").history(*args, **kwargs)

    def bars(self, *args, **kwargs):
        timestamps = pd.date_range(
            "2026-01-01", periods=200, freq="15min", tz="UTC"
        ) + pd.Timedelta(minutes=self.offset)
        frame = pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": 100.0,
                "high": 120.0,
                "low": 80.0,
                "close": 100.0,
                "volume": 10.0,
                "turnover": 1000.0,
            }
        )
        frame.loc[frame.index[-1], "close"] = self.price
        return frame, frame.iloc[:-1].copy()


@pytest.fixture
def platform(tmp_path, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Tests must never access the network")

    monkeypatch.setattr("requests.sessions.Session.request", blocked)
    monkeypatch.setattr("requests.sessions.Session.send", blocked)
    settings = PlatformSettings(
        f"sqlite:///{tmp_path / 'platform.db'}", TOKEN, auth_enabled=True
    )
    monkeypatch.setattr(PlatformSettings, "from_env", lambda: settings)
    command.upgrade(
        Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head"
    )
    market = FakeMarket()
    app = create_app(settings, market)
    with TestClient(app) as client:
        yield client, app.state.session_factory, market


def add_strategy(client, **kwargs):
    response = client.post(
        "/api/strategies",
        headers=AUTH,
        json={
            "name": "测试策略",
            "quantity": 1,
            "initial_equity": 1000,
            "max_notional": 500,
            **kwargs,
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def start(client, strategy_id):
    assert (
        client.post(
            f"/api/strategies/{strategy_id}/command",
            headers=AUTH,
            json={"action": "start"},
        ).status_code
        == 200
    )


def test_api_requires_owner_token_and_keeps_secrets_private(platform):
    client, _, _ = platform
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/overview").status_code == 401
    assert (
        client.get(
            "/api/overview", headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    for path in [
        "/overview",
        "/strategies",
        "/positions",
        "/orders",
        "/events",
        "/backtests",
        "/market",
        "/account",
        "/instruments",
        "/tickers",
        "/watchlist",
    ]:
        response = client.get("/api" + path, headers=AUTH)
        assert response.status_code == 200
        assert TOKEN not in response.text
        assert "api_secret" not in response.text
    assert client.get("/api/account", headers=AUTH).json()["connected"] is False


def test_personal_mode_reads_and_commands_work_without_access_token(platform):
    _, factory, market = platform
    with factory() as db:
        database_url = str(db.bind.url)
    app = create_app(PlatformSettings(database_url), market)
    with TestClient(app) as client:
        assert client.get("/api/health").json()["auth_required"] is False
        for path in [
            "/overview",
            "/strategies",
            "/positions",
            "/orders",
            "/events",
            "/backtests",
            "/market",
            "/account",
        ]:
            assert client.get("/api" + path).status_code == 200
        response = client.post("/api/strategies", json={"name": "个人策略"})
        assert response.status_code == 201
        strategy_id = response.json()["id"]
        assert (
            client.post(
                f"/api/strategies/{strategy_id}/command", json={"action": "start"}
            ).status_code
            == 200
        )
        assert client.post("/api/backtests", json={}).status_code == 202


def test_optional_authentication_requires_valid_configuration():
    assert PlatformSettings("sqlite:///:memory:").auth_enabled is False
    with pytest.raises(ValueError, match="ACCESS_TOKEN"):
        PlatformSettings("sqlite:///:memory:", auth_enabled=True)


def test_multi_coin_catalog_quotes_and_additional_strategy_symbols(platform):
    client, _, _ = platform
    instruments = client.get("/api/instruments", headers=AUTH).json()["instruments"]
    assert len(instruments) > 3
    assert "DOGEUSDT" in {item["symbol"] for item in instruments}
    snapshot = client.get("/api/tickers", headers=AUTH).json()
    assert snapshot["source"] == "demo"
    assert len(snapshot["tickers"]) == len(instruments)
    assert all(item["last_price"] > 0 for item in snapshot["tickers"])
    assert client.get("/api/market?symbol=DOGEUSDT", headers=AUTH).status_code == 200
    strategy_id = add_strategy(client, symbol="DOGEUSDT", quantity=10)
    strategies = client.get("/api/strategies", headers=AUTH).json()
    assert (
        next(item for item in strategies if item["id"] == strategy_id)["symbol"]
        == "DOGEUSDT"
    )
    assert (
        client.post(
            "/api/backtests", headers=AUTH, json={"symbol": "DOGEUSDT"}
        ).status_code
        == 202
    )
    for path in ["/api/instruments", "/api/tickers", "/api/watchlist"]:
        assert (
            client.get(path, headers=AUTH, params={"exchange": "invalid"}).status_code
            == 422
        )
        assert client.get(path).status_code == 401


def test_watchlist_is_persistent_idempotent_and_exchange_specific(platform):
    client, factory, market = platform
    for _ in range(2):
        assert (
            client.post(
                "/api/watchlist",
                headers=AUTH,
                json={"exchange": "bybit", "symbol": "DOGEUSDT"},
            ).status_code
            == 200
        )
    client.post(
        "/api/watchlist",
        headers=AUTH,
        json={"exchange": "binance", "symbol": "DOGEUSDT"},
    )
    assert len(client.get("/api/watchlist", headers=AUTH).json()) == 1
    with factory() as db:
        url = str(db.bind.url)
    with TestClient(
        create_app(PlatformSettings(url, TOKEN, auth_enabled=True), market)
    ) as restarted:
        assert (
            restarted.get("/api/watchlist", headers=AUTH).json()[0]["symbol"]
            == "DOGEUSDT"
        )
        assert (
            restarted.delete("/api/watchlist/bybit/DOGEUSDT", headers=AUTH).status_code
            == 204
        )
        assert (
            restarted.delete("/api/watchlist/bybit/DOGEUSDT", headers=AUTH).status_code
            == 204
        )
        assert restarted.get("/api/watchlist", headers=AUTH).json() == []
        assert (
            len(restarted.get("/api/watchlist?exchange=binance", headers=AUTH).json())
            == 1
        )


@pytest.mark.parametrize("symbol", ["UNKNOWNUSDT", "BTCUSD", "dogeusdt", "BTCUSDT/../"])
def test_unavailable_or_invalid_symbols_cannot_be_saved(platform, symbol):
    client, _, _ = platform
    assert (
        client.post("/api/watchlist", headers=AUTH, json={"symbol": symbol}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/strategies", headers=AUTH, json={"name": "invalid", "symbol": symbol}
        ).status_code
        == 422
    )
    assert (
        client.post("/api/backtests", headers=AUTH, json={"symbol": symbol}).status_code
        == 422
    )


def test_bybit_catalog_reads_all_pages_and_rejects_repeated_cursor():
    client = BybitClient.__new__(BybitClient)
    client.settings, client.session = Settings(None, None), Mock()
    client.session.get_instruments_info.side_effect = [
        {
            "retCode": 0,
            "result": {"list": [{"symbol": "BTCUSDT"}], "nextPageCursor": "next"},
        },
        {
            "retCode": 0,
            "result": {"list": [{"symbol": "DOGEUSDT"}], "nextPageCursor": ""},
        },
    ]
    assert [item["symbol"] for item in client.get_instruments()] == [
        "BTCUSDT",
        "DOGEUSDT",
    ]
    assert client.session.get_instruments_info.call_args.kwargs["cursor"] == "next"
    client.session.get_instruments_info.side_effect = None
    client.session.get_instruments_info.return_value = {
        "retCode": 0,
        "result": {"list": [], "nextPageCursor": "repeat"},
    }
    with pytest.raises(RuntimeError, match="repeated"):
        client.get_instruments()


@pytest.mark.parametrize("exchange", ["bybit", "binance"])
def test_exchange_catalog_filters_and_ticker_percentage_normalization(
    exchange, monkeypatch
):
    market = MarketService("exchange")
    client = Mock()
    monkeypatch.setattr(market, "client", lambda *args: client)
    if exchange == "bybit":
        good = {
            "symbol": "DOGEUSDT",
            "baseCoin": "DOGE",
            "quoteCoin": "USDT",
            "settleCoin": "USDT",
            "status": "Trading",
            "contractType": "LinearPerpetual",
        }
        client.get_instruments.return_value = [
            good,
            {**good, "symbol": "OLDUSDT", "status": "Settled"},
            {**good, "symbol": "BTCUSDC", "quoteCoin": "USDC"},
            {**good, "symbol": "PREUSDT", "isPreListing": True},
        ]
        ticker = {
            "symbol": "DOGEUSDT",
            "lastPrice": "0.12",
            "price24hPcnt": "0.025",
            "highPrice24h": "0.13",
            "lowPrice24h": "0.11",
            "volume24h": "100",
            "turnover24h": "12",
        }
    else:
        good = {
            "symbol": "DOGEUSDT",
            "baseAsset": "DOGE",
            "quoteAsset": "USDT",
            "marginAsset": "USDT",
            "status": "TRADING",
            "contractType": "PERPETUAL",
        }
        client.get_instruments.return_value = [
            good,
            {**good, "symbol": "OLDUSDT", "status": "BREAK"},
            {**good, "symbol": "DELIVERYUSDT", "contractType": "CURRENT_QUARTER"},
        ]
        ticker = {
            "symbol": "DOGEUSDT",
            "lastPrice": "0.12",
            "priceChangePercent": "2.5",
            "highPrice": "0.13",
            "lowPrice": "0.11",
            "volume": "100",
            "quoteVolume": "12",
        }
    client.get_tickers.return_value = [ticker, {**ticker, "symbol": "OLDUSDT"}]
    assert [item["symbol"] for item in market.instruments(exchange)] == ["DOGEUSDT"]
    first = market.tickers(exchange)
    assert first["tickers"][0]["change_24h_pct"] == pytest.approx(2.5)
    assert first["tickers"][0]["turnover_24h"] == 12
    assert market.tickers(exchange) == first
    assert client.get_tickers.call_count == 1
    assert client.get_instruments.call_count == 1


@pytest.mark.parametrize(
    "invalid",
    [
        {"quantity": 0},
        {"initial_equity": -1},
        {"max_notional": 0},
        {"fee_rate": -1},
        {"interval": "999"},
        {"mode": "live"},
    ],
)
def test_invalid_strategy_configuration_rejected(platform, invalid):
    client, _, _ = platform
    assert (
        client.post(
            "/api/strategies", headers=AUTH, json={"name": "test", **invalid}
        ).status_code
        == 422
    )


def test_engine_restarts_without_duplicate_fills_and_marks_paused_position(
    platform, monkeypatch
):
    client, factory, market = platform
    strategy_id = add_strategy(client)
    start(client, strategy_id)
    monkeypatch.setattr(
        "quantpilot.platform.engine.generate_signal", lambda _, **kwargs: "LONG"
    )
    process_strategy(factory, strategy_id, market)
    # A second session factory represents a process restart against the same database.
    with factory() as db:
        url = str(db.bind.url)
    engine, restarted = make_database(url)
    process_strategy(restarted, strategy_id, market)
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(PaperOrder)) == 1
        assert db.get(Position, strategy_id).side == "LONG"
        assert db.get(Strategy, strategy_id).cash == pytest.approx(
            Decimal("999.944989")
        )
    client.post(
        f"/api/strategies/{strategy_id}/command", headers=AUTH, json={"action": "pause"}
    )
    market.price = 105
    process_strategy(restarted, strategy_id, market)
    with factory() as db:
        assert db.get(Position, strategy_id).mark_price == 105
        assert db.scalar(select(func.count()).select_from(PaperOrder)) == 1
    engine.dispose()


def optimization_payload(**kwargs):
    payload = {
        "base": {
            "symbol": "DOGEUSDT",
            "quantity": 10,
            "fee_rate": 0,
            "slippage_bps": 0,
            "start_at": "2026-01-01T00:00:00Z",
            "end_at": "2026-02-01T00:00:00Z",
            "rules": {"strategy_type": "ema_cross"},
        },
        "split_at": "2026-01-16T00:00:00Z",
        "ranges": {
            "fast": {"low": 5, "high": 10, "step": 1},
            "slow": {"low": 30, "high": 40, "step": 1},
        },
        "trials": 8,
        "top_k": 2,
        "min_trades": 1,
        "max_drawdown_pct": 100,
        "require_positive_validation": False,
    }
    payload.update(kwargs)
    return payload


def test_optimization_runs_validates_and_promotes_exact_rules(
    platform, tmp_path, monkeypatch
):
    client, factory, market = platform
    monkeypatch.chdir(tmp_path)
    response = client.post(
        "/api/optimizations",
        headers=AUTH,
        json=optimization_payload(validation_windows=2, trials=12),
    )
    assert response.status_code == 202
    job_id = response.json()["id"]
    process_job(factory, market, "optimizer")
    job = client.get(f"/api/optimizations/{job_id}", headers=AUTH).json()
    assert job["status"] == "completed", job.get("error")
    assert job["completed_trials"] == 12
    candidates = job["result"]["candidates"]
    assert len(candidates) == 2
    assert candidates[0]["score"] >= candidates[1]["score"]
    for candidate in candidates:
        assert candidate["eligible"] and len(candidate["windows"]) == 2
        assert all(
            pd.Timestamp(window["start_at"])
            >= pd.Timestamp(job["parameters"]["split_at"])
            for window in candidate["windows"]
        )
        validation = client.get(
            f"/api/backtests/{candidate['backtest_id']}", headers=AUTH
        ).json()
        assert validation["parameters"]["evaluation_phase"] == "validation"
        assert all(
            pd.Timestamp(point["timestamp"]) >= pd.Timestamp("2026-01-16T00:00:00Z")
            for point in validation["result"]["curve"]
        )
    candidate = candidates[0]
    promoted = client.post(
        f"/api/backtests/{candidate['backtest_id']}/promote",
        headers=AUTH,
        json={"name": "optimized"},
    )
    assert promoted.status_code == 201
    assert promoted.json()["rules"] == candidate["rules"]
    assert promoted.json()["status"] == "paused"
    assert "result" not in client.get("/api/optimizations", headers=AUTH).json()[0]


def test_holdout_prices_do_not_change_training_search(platform, tmp_path, monkeypatch):
    client, factory, market = platform
    monkeypatch.chdir(tmp_path)
    first_id = client.post(
        "/api/optimizations", headers=AUTH, json=optimization_payload()
    ).json()["id"]
    process_job(factory, market, "optimizer")
    first = client.get(f"/api/optimizations/{first_id}", headers=AUTH).json()
    assert first["status"] == "completed", first.get("error")
    original = market.history

    def changed_holdout(*args, **kwargs):
        frame = original(*args, **kwargs)
        future = frame.timestamp >= pd.Timestamp("2026-01-16T00:00:00Z")
        for field in ["open", "high", "low", "close"]:
            frame.loc[future, field] *= 2
        return frame

    monkeypatch.setattr(market, "history", changed_holdout)
    second_id = client.post(
        "/api/optimizations", headers=AUTH, json=optimization_payload()
    ).json()["id"]
    process_job(factory, market, "optimizer")
    second = client.get(f"/api/optimizations/{second_id}", headers=AUTH).json()
    assert second["status"] == "completed", second.get("error")
    assert first["result"]["trials"] == second["result"]["trials"]
    assert first["result"]["dataset"]["sha256"] != second["result"]["dataset"]["sha256"]


def test_optimization_resume_uses_snapshot_and_does_not_repeat_trials(
    platform, tmp_path, monkeypatch
):
    from quantpilot.platform import optimization

    client, factory, market = platform
    monkeypatch.chdir(tmp_path)
    job_id = client.post(
        "/api/optimizations", headers=AUTH, json=optimization_payload()
    ).json()["id"]
    original = optimization.checkpoint

    def interrupt_after_three(*args):
        original(*args)
        if len(args[-1].get("trials", [])) == 3:
            raise KeyboardInterrupt("simulated process interruption")

    monkeypatch.setattr(optimization, "checkpoint", interrupt_after_three)
    with pytest.raises(KeyboardInterrupt):
        process_job(factory, market, "optimizer")
    partial = client.get(f"/api/optimizations/{job_id}", headers=AUTH).json()
    assert partial["status"] == "running" and partial["completed_trials"] == 3
    monkeypatch.setattr(optimization, "checkpoint", original)
    monkeypatch.setattr(
        market,
        "history",
        lambda *args, **kwargs: pytest.fail("resume must use the saved snapshot"),
    )
    process_job(factory, market, "optimizer")
    finished = client.get(f"/api/optimizations/{job_id}", headers=AUTH).json()
    assert finished["status"] == "completed", finished.get("error")
    assert len(finished["result"]["trials"]) == 8
    assert finished["result"]["trials"][:3] == partial["result"]["trials"]
    assert len({trial["number"] for trial in finished["result"]["trials"]}) == 8


def test_optimizer_constraints_block_promotion_and_cancellation_is_durable(
    platform, tmp_path, monkeypatch
):
    client, factory, market = platform
    monkeypatch.chdir(tmp_path)
    job_id = client.post(
        "/api/optimizations", headers=AUTH, json=optimization_payload(min_trades=10000)
    ).json()["id"]
    process_job(factory, market, "optimizer")
    job = client.get(f"/api/optimizations/{job_id}", headers=AUTH).json()
    assert job["status"] == "completed", job.get("error")
    assert not any(candidate["eligible"] for candidate in job["result"]["candidates"])
    candidate = job["result"]["candidates"][0]
    assert (
        client.post(
            f"/api/backtests/{candidate['backtest_id']}/promote",
            headers=AUTH,
            json={"name": "blocked"},
        ).status_code
        == 409
    )
    queued = client.post(
        "/api/optimizations", headers=AUTH, json=optimization_payload()
    ).json()
    assert (
        client.post(
            f"/api/optimizations/{queued['id']}/cancel", headers=AUTH, json={}
        ).json()["status"]
        == "cancelled"
    )
    assert client.get("/api/optimizations").status_code == 401


@pytest.mark.parametrize(
    "change",
    [
        {"split_at": "2026-01-01T00:00:00Z"},
        {"split_at": "2026-02-01T00:00:00Z"},
        {"split_at": "2026-01-16T00:00:00"},
        {"ranges": {"quantity": {"low": 1, "high": 2}}},
        {"ranges": {"fast": {"low": 1, "high": 10}}},
        {"ranges": {"fast": {"low": 5.5, "high": 10.5}}},
        {"ranges": {"fast": {"low": 10, "high": 5}}},
        {"ranges": {"fast": {"low": 5, "high": 10, "step": 2}}},
    ],
)
def test_optimizer_rejects_overlapping_intervals_and_invalid_dimensions(
    platform, change
):
    client, _, _ = platform
    assert (
        client.post(
            "/api/optimizations", headers=AUTH, json=optimization_payload(**change)
        ).status_code
        == 422
    )


def test_reverse_and_flatten_reconcile_cash_and_clear_position(platform, monkeypatch):
    client, factory, market = platform
    strategy_id = add_strategy(client)
    start(client, strategy_id)
    signal = ["LONG"]
    monkeypatch.setattr(
        "quantpilot.platform.engine.generate_signal", lambda _, **kwargs: signal[0]
    )
    process_strategy(factory, strategy_id, market)
    market.offset += 15
    signal[0] = "SHORT"
    process_strategy(factory, strategy_id, market)
    with factory() as db:
        assert db.get(Position, strategy_id).side == "SHORT"
        assert db.scalar(select(func.count()).select_from(PaperOrder)) == 3
    assert (
        client.post(
            f"/api/strategies/{strategy_id}/command",
            headers=AUTH,
            json={"action": "flatten"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/strategies/{strategy_id}/command",
            headers=AUTH,
            json={"action": "start"},
        ).status_code
        == 409
    )
    process_strategy(factory, strategy_id, market)
    process_strategy(factory, strategy_id, market)
    with factory() as db:
        strategy = db.get(Strategy, strategy_id)
        assert db.get(Position, strategy_id) is None
        assert strategy.status == "paused"
        rows = list(db.scalars(select(PaperOrder)))
        assert len(rows) == 4
        assert strategy.cash == pytest.approx(
            strategy.initial_equity + sum(o.realized_pnl for o in rows)
        )


def test_failed_transaction_does_not_advance_cursor(platform, monkeypatch):
    from quantpilot.platform import engine

    client, factory, market = platform
    strategy_id = add_strategy(client)
    start(client, strategy_id)
    monkeypatch.setattr(engine, "generate_signal", lambda _, **kwargs: "LONG")
    original = engine.record_fill

    def fail_after_fill(*args):
        original(*args)
        raise RuntimeError("simulated crash before commit")

    monkeypatch.setattr(engine, "record_fill", fail_after_fill)
    with pytest.raises(RuntimeError):
        process_strategy(factory, strategy_id, market)
    with factory() as db:
        assert db.get(Strategy, strategy_id).last_bar is None
        assert db.get(Position, strategy_id) is None
        assert db.scalar(select(func.count()).select_from(PaperOrder)) == 0
    monkeypatch.setattr(engine, "record_fill", original)
    process_strategy(factory, strategy_id, market)
    assert len(client.get("/api/orders", headers=AUTH).json()) == 1


def test_risk_rejects_excess_exposure_and_source_mismatch(platform, monkeypatch):
    client, factory, market = platform
    strategy_id = add_strategy(client, max_notional=50)
    start(client, strategy_id)
    monkeypatch.setattr(
        "quantpilot.platform.engine.generate_signal", lambda _, **kwargs: "LONG"
    )
    process_strategy(factory, strategy_id, market)
    with factory() as db:
        assert db.get(Position, strategy_id) is None
        assert (
            db.scalar(
                select(func.count()).select_from(Event).where(Event.level == "warning")
            )
            == 1
        )
    market.source = "exchange"
    with pytest.raises(ValueError, match="source"):
        process_strategy(factory, strategy_id, market)


def test_leases_exclude_second_worker_and_allow_expired_takeover(platform):
    _, factory, _ = platform
    assert renew_lease(factory, "engine", "first")
    assert not renew_lease(factory, "engine", "second")
    with factory.begin() as db:
        db.get(WorkerLease, "engine").expires_at = utcnow() - timedelta(seconds=1)
    assert renew_lease(factory, "engine", "second")
    with factory.begin() as db, pytest.raises(RuntimeError, match="lease"):
        guard_lease(db, "engine", "first")


def test_backtest_queue_recovers_running_job(platform, tmp_path, monkeypatch):
    client, factory, market = platform
    monkeypatch.chdir(tmp_path)
    response = client.post("/api/backtests", headers=AUTH, json={})
    assert response.status_code == 202
    job_id = response.json()["id"]
    with factory.begin() as db:
        db.get(BacktestJob, job_id).status = "running"
    process_job(factory, market, "new-worker")
    data = client.get(f"/api/backtests/{job_id}", headers=AUTH).json()
    assert data["status"] == "completed"
    assert data["result"]["source"] == "demo"
    assert data["result"]["metrics"]["final_equity"] == 10000
    assert list((tmp_path / "storage/market").glob("*.parquet"))


def test_demo_bars_are_closed_and_valid_without_credentials():
    frame, bars = MarketService("demo").bars("bybit", "BTCUSDT", "60", 100)
    assert len(frame) == 100 and len(bars) == 99
    assert bars.timestamp.iloc[-1] + pd.Timedelta(hours=1) <= pd.Timestamp.now(tz="UTC")


def test_demo_history_can_exercise_strategy_without_fabricated_orders():
    from quantpilot.strategy import generate_signals

    _, bars = MarketService("demo").bars("bybit", "BTCUSDT", "15", 1000)
    assert generate_signals(bars).isin(["LONG", "SHORT"]).any()


@pytest.mark.parametrize("exchange", ["bybit", "binance"])
def test_adapter_cannot_bypass_execution_switches(exchange):
    settings = Settings(
        "dummy", "dummy", binance_api_key="dummy", binance_api_secret="dummy"
    )
    if exchange == "bybit":
        client = BybitClient.__new__(BybitClient)
        client.settings, client.session = settings, Mock()
        side = "Buy"
    else:
        client = BinanceFuturesClient(settings)
        client.session = Mock()
        side = "BUY"
    with pytest.raises(ValueError, match="disabled"):
        client.place_market(side, "1")
    assert not client.session.mock_calls


def test_mainnet_confirmation_and_quantity_enforced_at_submission():
    settings = Settings(
        "dummy", "dummy", testnet=False, dry_run=False, trading_enabled=True
    )
    with pytest.raises(ValueError, match="confirmation"):
        settings.require_order_submission("bybit", "1")
    settings = Settings("dummy", "dummy", dry_run=False, trading_enabled=True)
    for quantity in ["0", "-1", "NaN", "Infinity", "bad"]:
        with pytest.raises(ValueError, match="Quantity"):
            settings.require_order_submission("bybit", quantity)


def test_binance_hour_interval_is_normalized():
    client = BinanceFuturesClient(Settings(None, None, interval="60"))
    client._request = Mock(return_value=[])
    client.get_klines()
    assert client._request.call_args.args[2]["interval"] == "1h"


def test_rules_batch_and_promotion_keep_exact_configuration(
    platform, tmp_path, monkeypatch
):
    client, factory, _ = platform
    monkeypatch.chdir(tmp_path)
    templates = client.get("/api/strategy-templates", headers=AUTH).json()
    assert {item["id"] for item in templates} == {
        "ema_rsi",
        "ema_cross",
        "rsi_reversion",
        "breakout",
        "bollinger_reversion",
    }
    assert client.get("/api/strategy-templates").status_code == 401
    variants = [
        {
            "strategy_type": "ema_cross",
            "fast": 10,
            "slow": 40,
            "stop_loss_pct": 2,
            "max_drawdown_pct": 5,
        },
        {
            "strategy_type": "ema_cross",
            "fast": 20,
            "slow": 60,
            "stop_loss_pct": 2,
            "max_drawdown_pct": 5,
        },
    ]
    response = client.post(
        "/api/backtests/batch",
        headers=AUTH,
        json={
            "base": {
                "symbol": "DOGEUSDT",
                "quantity": 10,
                "fee_rate": 0.001,
                "max_notional": 50,
                "start_at": "2026-01-01T00:00:00Z",
                "end_at": "2026-02-01T00:00:00Z",
            },
            "variants": variants,
        },
    )
    assert response.status_code == 202
    jobs = response.json()["jobs"]
    assert (
        len(jobs) == 2
        and jobs[0]["parameters"]["batch_id"] == jobs[1]["parameters"]["batch_id"]
    )
    assert jobs[0]["parameters"]["history_warmup"] == 61
    first_id = jobs[0]["id"]
    assert (
        client.post(
            f"/api/backtests/{first_id}/promote", headers=AUTH, json={"name": "waiting"}
        ).status_code
        == 409
    )
    market = MarketService("demo")
    for _ in jobs:
        process_job(factory, market, "research-owner")
    completed = client.get(f"/api/backtests/{first_id}", headers=AUTH).json()
    assert completed["status"] == "completed"
    assert completed["result"]["bars"] > 1000
    assert completed["result"]["equity_kind"] == "marked"
    assert completed["result"]["dataset"]["sha256"]
    assert all(
        pd.Timestamp(point["timestamp"]) >= pd.Timestamp("2026-01-01T00:00:00Z")
        for point in completed["result"]["curve"]
    )
    second = client.get(f"/api/backtests/{jobs[1]['id']}", headers=AUTH).json()
    assert (
        second["result"]["dataset"]["sha256"]
        == completed["result"]["dataset"]["sha256"]
    )
    promoted = client.post(
        f"/api/backtests/{first_id}/promote",
        headers=AUTH,
        json={"name": "DOGE research candidate"},
    )
    assert promoted.status_code == 201
    strategy = promoted.json()
    assert strategy["status"] == "paused" and strategy["origin_job_id"] == first_id
    assert strategy["rules"] == completed["parameters"]["rules"]
    assert (
        float(strategy["quantity"]) == 10
        and float(strategy["fee_rate"]) == 0.001
        and float(strategy["max_notional"]) == 50
    )
    assert client.get("/api/backtests", headers=AUTH).json()[0]["summary"] is not None
    with factory.begin() as db:
        db.get(BacktestJob, first_id).parameters = {
            **db.get(BacktestJob, first_id).parameters,
            "source": "exchange",
        }
    assert (
        client.post(
            f"/api/backtests/{first_id}/promote",
            headers=AUTH,
            json={"name": "wrong source"},
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"rules": {"fast": 60, "slow": 20}},
        {"rules": {"rsi_lower": 80, "rsi_upper": 70}},
        {"rules": {"strategy_type": "unknown"}},
        {"start_at": "2026-01-01T00:00:00Z"},
        {"start_at": "2026-02-01T00:00:00Z", "end_at": "2026-01-01T00:00:00Z"},
        {"start_at": "2026-01-01T00:00:00", "end_at": "2026-02-01T00:00:00"},
        {
            "start_at": "2025-01-01T00:00:00Z",
            "end_at": "2026-01-01T00:00:00Z",
            "interval": "1",
        },
    ],
)
def test_research_rejects_invalid_rules_and_ranges(platform, payload):
    client, _, _ = platform
    assert client.post("/api/backtests", headers=AUTH, json=payload).status_code == 422


def test_batch_recent_samples_share_frozen_dates_and_queue_limit(platform):
    client, _, _ = platform
    response = client.post(
        "/api/backtests/batch",
        headers=AUTH,
        json={
            "base": {},
            "variants": [{"fast": 10, "slow": 40}, {"fast": 20, "slow": 90}],
        },
    )
    assert response.status_code == 202
    jobs = response.json()["jobs"]
    assert jobs[0]["parameters"]["start_at"] == jobs[1]["parameters"]["start_at"]
    assert jobs[0]["parameters"]["end_at"] == jobs[1]["parameters"]["end_at"]
    assert (
        client.post(
            "/api/backtests/batch",
            headers=AUTH,
            json={"base": {}, "variants": [{}] * 9},
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "risk,price,paused",
    [
        ({"stop_loss_pct": 2}, 95, False),
        ({"take_profit_pct": 2}, 105, False),
        ({"max_drawdown_pct": 0.01}, 95, True),
    ],
)
def test_paper_risk_exits_record_equity_and_drawdown(
    platform, monkeypatch, risk, price, paused
):
    client, factory, market = platform
    strategy_id = add_strategy(client, rules=risk)
    start(client, strategy_id)
    monkeypatch.setattr(
        "quantpilot.platform.engine.generate_signal", lambda _, **kwargs: "LONG"
    )
    process_strategy(factory, strategy_id, market)
    market.price = price
    process_strategy(factory, strategy_id, market)
    with factory() as db:
        strategy = db.get(Strategy, strategy_id)
        assert db.get(Position, strategy_id) is None
        assert strategy.risk_halted == paused
        assert strategy.status == ("paused" if paused else "running")
        assert db.scalar(select(func.count()).select_from(PaperOrder)) == 2
        assert strategy.max_drawdown < 0
    data = client.get("/api/strategies", headers=AUTH).json()[0]
    assert len(data["curve"]) == 1
    if paused:
        assert (
            client.post(
                f"/api/strategies/{strategy_id}/command",
                headers=AUTH,
                json={"action": "start"},
            ).status_code
            == 409
        )


def test_research_migration_preserves_existing_positions(tmp_path, monkeypatch):
    settings = PlatformSettings(f"sqlite:///{tmp_path / 'legacy.db'}")
    monkeypatch.setattr(PlatformSettings, "from_env", lambda: settings)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "0002_watchlist")
    engine, _ = make_database(settings.database_url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO strategies (id,name,exchange,source,symbol,interval,quantity,initial_equity,cash,max_notional,fee_rate,slippage_bps,status,signal,created_at) VALUES (1,'legacy','bybit','demo','BTCUSDT','15',1,10000,10000,1000,0.00055,2,'paused','HOLD','2026-01-01')"
        )
        connection.exec_driver_sql(
            "INSERT INTO positions (strategy_id,side,quantity,entry_price,mark_price,opened_at) VALUES (1,'LONG',1,100,101,'2026-01-01')"
        )
    command.upgrade(config, "head")
    command.check(config)
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT name,rules,risk_halted FROM strategies WHERE id=1"
        ).one() == ("legacy", "{}", 0)
        assert (
            connection.exec_driver_sql(
                "SELECT mark_price FROM positions WHERE strategy_id=1"
            ).scalar()
            == 101
        )
        assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    command.downgrade(config, "0002_watchlist")
    with engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT mark_price FROM positions WHERE strategy_id=1"
            ).scalar()
            == 101
        )
    engine.dispose()
