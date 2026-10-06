"""Offline integration checks for durable state, authentication and worker recovery."""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
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


def test_bybit_environment_switch_preserves_credentials_and_separates_databases(
    platform, tmp_path, monkeypatch
):
    from dotenv import dotenv_values

    from quantpilot.platform.environment import BybitEnvironment

    _client, factory, market = platform
    monkeypatch.delenv("BYBIT_TESTNET", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "BYBIT_TESTNET=true\nBYBIT_API_KEY=main-key\nBYBIT_API_SECRET=main-secret\nBYBIT_TESTNET_API_KEY=test-key\nBYBIT_TESTNET_API_SECRET=test-secret\nLIVE_TRADING_CONFIRM=I_UNDERSTAND_LIVE_TRADING\nDRY_RUN=true\nTRADING_ENABLED=false\n",
        encoding="utf-8",
    )
    with factory() as db:
        original_url = str(db.bind.url)
    platform_path = tmp_path / ".env.platform"
    platform_path.write_text(
        f"DATABASE_URL={original_url}\nPLATFORM_MARKET_SOURCE=demo\n", encoding="utf-8"
    )
    settings = PlatformSettings(original_url, TOKEN, auth_enabled=True)
    environment = BybitEnvironment(settings, tmp_path)
    app = create_app(settings, market, environment=environment)
    with TestClient(app) as web:
        assert web.get("/api/bybit-environment").status_code == 401
        initial = web.get("/api/bybit-environment", headers=AUTH).json()
        assert initial["active"] == initial["selected"] == "testnet"
        assert (
            initial["testnet_credentials_configured"]
            and initial["mainnet_credentials_configured"]
        )
        assert "main-key" not in str(initial) and "test-key" not in str(initial)
        saved = web.post(
            "/api/bybit-environment", headers=AUTH, json={"environment": "mainnet"}
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["restart_required"] and saved.json()["active"] == "testnet"
        assert saved.json()["selected"] == "mainnet"
        values = dotenv_values(env_path)
        assert (
            values["BYBIT_API_KEY"] == "main-key"
            and values["BYBIT_TESTNET_API_KEY"] == "test-key"
        )
        assert values["DRY_RUN"] == "true" and values["TRADING_ENABLED"] == "false"
        config = dotenv_values(platform_path)
        main_url = config["DATABASE_URL"]
        assert main_url != original_url
        assert config["PLATFORM_BYBIT_TESTNET_DATABASE_URL"] == original_url
        restarted = BybitEnvironment(PlatformSettings(main_url), tmp_path)
        assert (
            restarted.status()["active"] == "mainnet"
            and not restarted.status()["restart_required"]
        )
        reverted = restarted.save(factory, "testnet")
        assert reverted["restart_required"]
        assert dotenv_values(platform_path)["DATABASE_URL"] == original_url
        assert (
            dotenv_values(platform_path)["PLATFORM_BYBIT_MAINNET_DATABASE_URL"]
            == main_url
        )
    assert (
        web.post(
            "/api/bybit-environment", headers=AUTH, json={"environment": "invalid"}
        ).status_code
        == 422
    )


def test_bybit_environment_switch_rejects_active_work_without_writing_files(
    platform, tmp_path, monkeypatch
):
    from quantpilot.platform.environment import BybitEnvironment, EnvironmentError

    client, factory, _ = platform
    monkeypatch.delenv("BYBIT_TESTNET", raising=False)
    env_path = tmp_path / ".env"
    text = "BYBIT_TESTNET=true\nLIVE_TRADING_CONFIRM=I_UNDERSTAND_LIVE_TRADING\n"
    env_path.write_text(text, encoding="utf-8")
    with factory() as db:
        url = str(db.bind.url)
    environment = BybitEnvironment(PlatformSettings(url), tmp_path)
    strategy_id = add_strategy(client)
    start(client, strategy_id)
    with pytest.raises(EnvironmentError, match="暂停"):
        environment.save(factory, "mainnet")
    assert env_path.read_text(encoding="utf-8") == text
    assert not (tmp_path / ".env.platform").exists()


def test_exchange_profile_is_frozen_until_process_restart(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BYBIT_TESTNET", raising=False)
    path = tmp_path / ".env"
    path.write_text("BYBIT_TESTNET=true\n", encoding="utf-8")
    MarketService("exchange")
    path.write_text("BYBIT_TESTNET=false\n", encoding="utf-8")
    assert Settings.from_env(path).testnet is True


def test_environment_save_rolls_back_when_second_file_cannot_be_written(
    platform, tmp_path, monkeypatch
):
    from quantpilot.platform.environment import BybitEnvironment, EnvironmentError

    _client, factory, _market = platform
    monkeypatch.delenv("BYBIT_TESTNET", raising=False)
    path = tmp_path / ".env"
    original = "BYBIT_TESTNET=true\nBYBIT_API_KEY=main-key\nLIVE_TRADING_CONFIRM=I_UNDERSTAND_LIVE_TRADING\n"
    path.write_text(original, encoding="utf-8")
    with factory() as db:
        url = str(db.bind.url)
    environment = BybitEnvironment(PlatformSettings(url), tmp_path)
    replace = Path.replace

    def fail_platform_replace(self, target):
        if Path(target).name == ".env.platform":
            raise OSError("Permission denied")
        return replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_platform_replace)
    with pytest.raises(EnvironmentError):
        environment.save(factory, "mainnet")
    assert path.read_text(encoding="utf-8") == original
    assert not (tmp_path / ".env.platform").exists()
    assert not list(tmp_path.glob("*.switch.tmp"))


class FakeMarket:
    source = "demo"

    def exchange_settings(self, *_args):
        return SimpleNamespace(testnet=True, binance_testnet=True)

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
    data = client.get(f"/api/strategies/{strategy_id}/curve", headers=AUTH).json()
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


@pytest.mark.parametrize(
    "symbol,interval", [("BTCUSDT", "15"), ("SOLUSDT", "60"), ("DOGEUSDT", "D")]
)
def test_demo_recent_and_history_are_identical(symbol, interval):
    from quantpilot.platform.market import INTERVAL_MINUTES
    from quantpilot.strategy import generate_signals

    market = MarketService("demo")
    _, recent = market.bars("bybit", symbol, interval, 240)
    step = pd.Timedelta(minutes=INTERVAL_MINUTES[interval])
    history = market.history(
        "bybit",
        symbol,
        interval,
        recent.timestamp.iloc[80],
        recent.timestamp.iloc[-1] + step,
        79,
    )
    pd.testing.assert_frame_equal(recent, history)
    again = MarketService("demo").history(
        "bybit",
        symbol,
        interval,
        recent.timestamp.iloc[80],
        recent.timestamp.iloc[-1] + step,
        79,
    )
    pd.testing.assert_frame_equal(history, again)
    pd.testing.assert_series_equal(
        generate_signals(recent, volume_ratio=1.1),
        generate_signals(history, volume_ratio=1.1),
    )


@pytest.mark.parametrize(
    "risk,price,halt",
    [
        ({"stop_loss_pct": 1}, 98, False),
        ({"take_profit_pct": 1}, 102, False),
        ({"max_drawdown_pct": 0.1}, 98, True),
    ],
)
def test_paused_positions_keep_protection_after_restart(
    platform, monkeypatch, risk, price, halt
):
    client, factory, market = platform
    strategy_id = add_strategy(client, fee_rate=0, slippage_bps=0, rules=risk)
    start(client, strategy_id)
    monkeypatch.setattr(
        "quantpilot.platform.engine.generate_signal", lambda _, **kwargs: "LONG"
    )
    process_strategy(factory, strategy_id, market)
    client.post(
        f"/api/strategies/{strategy_id}/command", headers=AUTH, json={"action": "pause"}
    )
    with factory() as db:
        url = str(db.bind.url)
    engine, restarted = make_database(url)
    market.price = price
    process_strategy(restarted, strategy_id, market)
    market.offset = 15
    process_strategy(restarted, strategy_id, market)
    with factory() as db:
        assert db.get(Position, strategy_id) is None
        assert db.get(Strategy, strategy_id).status == "paused"
        assert db.get(Strategy, strategy_id).risk_halted is halt
        assert db.scalar(select(func.count()).select_from(PaperOrder)) == 2
    if halt:
        assert (
            client.post(
                f"/api/strategies/{strategy_id}/command",
                headers=AUTH,
                json={"action": "start"},
            ).status_code
            == 409
        )
    engine.dispose()


def test_pause_does_not_reverse_or_open_positions(platform, monkeypatch):
    client, factory, market = platform
    strategy_id = add_strategy(client)
    start(client, strategy_id)
    monkeypatch.setattr(
        "quantpilot.platform.engine.generate_signal", lambda _, **kwargs: "LONG"
    )
    process_strategy(factory, strategy_id, market)
    client.post(
        f"/api/strategies/{strategy_id}/command", headers=AUTH, json={"action": "pause"}
    )
    market.offset = 15
    monkeypatch.setattr(
        "quantpilot.platform.engine.generate_signal", lambda _, **kwargs: "SHORT"
    )
    process_strategy(factory, strategy_id, market)
    with factory() as db:
        assert db.get(Position, strategy_id).side == "LONG"
        assert db.scalar(select(func.count()).select_from(PaperOrder)) == 1
    empty_id = add_strategy(client)
    process_strategy(factory, empty_id, market)
    with factory() as db:
        assert db.get(Position, empty_id) is None


def test_curve_is_on_demand_and_incrementally_updates_current_bar(
    platform, monkeypatch
):
    client, factory, market = platform
    strategy_id = add_strategy(client, fee_rate=0, slippage_bps=0)
    start(client, strategy_id)
    monkeypatch.setattr(
        "quantpilot.platform.engine.generate_signal", lambda _, **kwargs: "LONG"
    )
    process_strategy(factory, strategy_id, market)
    assert "curve" not in client.get("/api/strategies", headers=AUTH).json()[0]
    path = f"/api/strategies/{strategy_id}/curve"
    first = client.get(path, headers=AUTH).json()
    cursor = first["curve"][-1]["id"]
    market.price = 95
    process_strategy(factory, strategy_id, market)
    market.price = 100
    process_strategy(factory, strategy_id, market)
    same = client.get(path + f"?since_id={cursor}", headers=AUTH).json()
    assert len(same["curve"]) == 1
    assert same["curve"][0]["id"] == cursor
    assert first["sampling"] == "bar" and first["drawdown_sampling"] == "poll"
    with factory() as db:
        assert db.get(Strategy, strategy_id).max_drawdown == Decimal("-0.005")
    market.offset = 15
    process_strategy(factory, strategy_id, market)
    assert (
        len(client.get(path + f"?since_id={cursor}", headers=AUTH).json()["curve"]) == 2
    )
    assert client.get(path + "?limit=1", headers=AUTH).json()["curve"][0]["id"] > cursor
    assert client.get(path).status_code == 401
    assert client.get("/api/strategies/999999/curve", headers=AUTH).status_code == 404


def seed_archive_data(client, factory):
    from quantpilot.platform.models import EquityPoint, OptimizationJob

    strategy_id = add_strategy(client)
    old = utcnow() - timedelta(days=120)
    payload = {
        "metrics": {"total_return": 0.1},
        "curve": [{"timestamp": "2026-01-01", "equity": 1100}],
        "trades": [],
        "dataset": {"path": "storage/market/shared.parquet", "sha256": "preserved"},
    }
    with factory.begin() as db:
        strategy = db.get(Strategy, strategy_id)
        strategy.max_drawdown = Decimal("-0.05")
        db.add_all(
            [
                EquityPoint(
                    strategy_id=strategy_id,
                    bar_time="old",
                    equity=950,
                    drawdown=-0.05,
                    created_at=old,
                ),
                EquityPoint(
                    strategy_id=strategy_id,
                    bar_time="latest",
                    equity=1000,
                    drawdown=0,
                    created_at=old,
                ),
                Event(strategy_id=None, message="old event", created_at=old),
                Event(
                    strategy_id=strategy_id,
                    message="old strategy event",
                    created_at=old,
                ),
                BacktestJob(
                    status="completed",
                    parameters={"source": "demo"},
                    result=payload,
                    completed_at=old,
                ),
                BacktestJob(
                    status="running",
                    parameters={"source": "demo"},
                    result=payload,
                    completed_at=old,
                ),
                OptimizationJob(
                    status="completed",
                    parameters={},
                    result={"trials": [1], "dataset": payload["dataset"]},
                    completed_at=old,
                ),
                OptimizationJob(
                    status="running",
                    parameters={},
                    result={"trials": [1]},
                    completed_at=old,
                ),
            ]
        )
    return strategy_id, payload


def test_archive_verified_exports_preserve_summary_and_history(
    platform, tmp_path, monkeypatch
):
    import hashlib
    import json

    from quantpilot.platform.models import EquityPoint, OptimizationJob

    client, factory, _ = platform
    monkeypatch.chdir(tmp_path)
    strategy_id, payload = seed_archive_data(client, factory)
    shared = Path("storage/market/shared.parquet")
    shared.parent.mkdir(parents=True)
    shared.write_bytes(b"shared market snapshot")
    preview_path = "/api/maintenance/archive/preview"
    assert client.post(preview_path, json={"retention_days": 90}).status_code == 401
    plan = client.post(preview_path, headers=AUTH, json={"retention_days": 90}).json()
    assert plan["counts"] == {
        "equity": 1,
        "events": 2,
        "backtests": 1,
        "optimizations": 1,
    }
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(EquityPoint)) == 2
    done = client.post(
        "/api/maintenance/archive/execute", headers=AUTH, json={"id": plan["id"]}
    )
    assert done.status_code == 200, done.text
    manifest = json.loads(Path(done.json()["manifest"]).read_text(encoding="utf-8"))
    for ref in manifest["files"]:
        assert (
            hashlib.sha256(Path(ref["path"]).read_bytes()).hexdigest() == ref["sha256"]
        )
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(EquityPoint)) == 1
        assert db.get(Strategy, strategy_id).max_drawdown == Decimal("-0.05")
        assert db.get(BacktestJob, 2).result == payload
        assert db.get(OptimizationJob, 2).result == {"trials": [1]}
    assert client.get("/api/backtests/1", headers=AUTH).json()["result"] == payload
    assert (
        client.get("/api/backtests", headers=AUTH).json()[1]["summary"]
        == payload["metrics"]
    )
    assert client.get("/api/optimizations/1", headers=AUTH).json()["result"][
        "trials"
    ] == [1]
    assert shared.read_bytes() == b"shared market snapshot"
    promoted = client.post(
        "/api/backtests/1/promote",
        headers=AUTH,
        json={"name": "Archived configuration"},
    )
    assert promoted.status_code == 201
    assert promoted.json()["origin_job_id"] == 1
    assert (
        client.post(
            "/api/maintenance/archive/execute", headers=AUTH, json={"id": plan["id"]}
        ).status_code
        == 409
    )
    again = client.post(preview_path, headers=AUTH, json={"retention_days": 90}).json()
    assert sum(again["counts"].values()) == 0
    ref = next(ref for ref in manifest["files"] if ref["kind"] == "backtests")
    Path(ref["path"]).write_bytes(b"corrupted")
    assert client.get("/api/backtests/1", headers=AUTH).status_code == 409


def test_unfinished_demo_candle_evolves_without_changing_closed_history():
    from quantpilot.platform.market import demo_bars, validate_market

    times = pd.date_range("2026-01-01", periods=5, freq="15min", tz="UTC")
    earlier = demo_bars("BTCUSDT", times, 15, times[-1] + pd.Timedelta(minutes=3))
    later = demo_bars("BTCUSDT", times, 15, times[-1] + pd.Timedelta(minutes=12))
    final = demo_bars("BTCUSDT", times, 15)
    pd.testing.assert_frame_equal(earlier.iloc[:-1], later.iloc[:-1])
    pd.testing.assert_frame_equal(earlier.iloc[:-1], final.iloc[:-1])
    assert earlier.iloc[-1].close != later.iloc[-1].close
    assert earlier.iloc[-1].volume < later.iloc[-1].volume < final.iloc[-1].volume
    assert later.iloc[-1].close == pytest.approx(
        final.iloc[-1].open + (final.iloc[-1].close - final.iloc[-1].open) * 0.8
    )
    validate_market(earlier)
    validate_market(later)


def test_archive_expired_previews_and_path_escape_are_rejected(
    platform, tmp_path, monkeypatch
):
    import json

    from quantpilot.platform import archive

    client, factory, _ = platform
    monkeypatch.chdir(tmp_path)
    seed_archive_data(client, factory)
    plan = archive.preview(factory, 90)
    path = archive.root_path() / "plans" / f"{plan['id']}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["created_at"] = (utcnow() - timedelta(hours=2)).isoformat()
    path.write_text(json.dumps(data), encoding="utf-8")
    assert (
        client.post(
            "/api/maintenance/archive/execute", headers=AUTH, json={"id": plan["id"]}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/maintenance/archive/execute",
            headers=AUTH,
            json={"id": "../../escape"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/maintenance/archive/preview", headers=AUTH, json={"retention_days": 1}
        ).status_code
        == 422
    )
    with pytest.raises(archive.ArchiveError):
        archive.load_result({"archived_result": {"path": "../escape", "sha256": "x"}})


def test_archive_concurrent_execution_cannot_duplicate_deletions(
    platform, tmp_path, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor

    from quantpilot.platform import archive
    from quantpilot.platform.models import EquityPoint

    client, factory, _ = platform
    monkeypatch.chdir(tmp_path)
    seed_archive_data(client, factory)
    plan = archive.preview(factory, 90)

    def run_archive():
        try:
            archive.execute(factory, plan["id"])
            return "completed"
        except archive.ArchiveError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_archive) for _ in range(2)]
        assert sorted(future.result() for future in futures) == [
            "completed",
            "rejected",
        ]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(EquityPoint)) == 1


@pytest.mark.parametrize("failure", ["changed", "write", "verify"])
def test_archive_failures_preserve_database(platform, tmp_path, monkeypatch, failure):
    from quantpilot.platform import archive
    from quantpilot.platform.models import EquityPoint

    client, factory, _ = platform
    monkeypatch.chdir(tmp_path)
    seed_archive_data(client, factory)
    plan = archive.preview(factory, 90)
    if failure == "changed":
        with factory.begin() as db:
            db.get(EquityPoint, 1).equity = 940
    elif failure == "write":

        def fail(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(pd.DataFrame, "to_parquet", fail)
    else:
        monkeypatch.setattr(pd, "read_parquet", lambda _: pd.DataFrame([{"id": -1}]))
    with pytest.raises((archive.ArchiveError, OSError)):
        archive.execute(factory, plan["id"])
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(EquityPoint)) == 2
        assert "archived_result" not in db.get(BacktestJob, 1).result
        assert (
            db.scalar(
                select(func.count())
                .select_from(Event)
                .where(Event.message == "old event")
            )
            == 1
        )
