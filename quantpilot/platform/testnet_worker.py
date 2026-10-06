"""Independent Testnet executor: paper strategies cannot delay its risk loop."""

import asyncio
import logging
import time
import uuid
from datetime import datetime

from quantpilot.config import Settings

from .database import make_database
from .engine import guard_lease
from .leases import release_lease
from .market import MarketService
from .models import TestnetAccount, WorkerLease, utcnow
from .settings import PlatformSettings
from .testnet import TestnetBroker, account, process_testnet
from .testnet_stream import TestnetStream


def verified_snapshot(factory, owner, started):
    current = account(factory)
    with factory() as db:
        lease = db.get(WorkerLease, "testnet")
    try:
        stamp = datetime.fromisoformat(current.snapshot["at"])
        return bool(
            lease
            and lease.owner == owner
            and stamp.tzinfo
            and stamp >= started
            and 0 <= (utcnow() - stamp).total_seconds() <= 30
            and not current.last_error
            and not current.ledger_warning
        )
    except (KeyError, ValueError, TypeError):
        return False


logger = logging.getLogger(__name__)


async def run():
    settings = PlatformSettings.from_env()
    engine, factory = make_database(settings.database_url)
    market = MarketService(settings.market_source)
    profile = Settings.from_env(".env")
    owner = uuid.uuid4().hex
    broker, verified, stream = None, 0, None
    try:
        while True:
            cycle_started = time.monotonic()
            try:
                if (
                    settings.testnet_execution_enabled
                    and market.source == "exchange"
                    and profile.exchange == "bybit"
                    and (
                        profile.testnet
                        or profile.live_trading_confirm == "I_UNDERSTAND_LIVE_TRADING"
                    )
                ):
                    broker = broker or TestnetBroker(profile)
                    if settings.testnet_stream_enabled and stream is None:
                        stream = TestnetStream(profile)
                        stream.start()
                    token = stream.checkpoint() if stream else None
                    ready = not stream or stream.valid(token)
                    if not ready or (stream and not stream.health()["reconciled"]):
                        verified = 0
                    started = utcnow()
                    await asyncio.to_thread(
                        process_testnet,
                        factory,
                        market,
                        owner,
                        broker,
                        allow_new_entries=verified >= 2 and ready,
                        entry_guard=(
                            lambda stream=stream, token=token: stream.valid(token)
                        )
                        if stream
                        else None,
                    )
                    rest_verified = verified_snapshot(factory, owner, started)
                    stream_verified = rest_verified and (
                        not stream or stream.reconciled(token)
                    )
                    if stream and not rest_verified:
                        stream.forget_reconciliation()
                    if stream:
                        with factory.begin() as db:
                            guard_lease(db, "testnet", owner)
                            current = db.get(TestnetAccount, 1)
                            current.snapshot = {
                                **current.snapshot,
                                "event_stream": stream.health(),
                            }
                    verified = min(2, verified + 1) if stream_verified else 0
                    if not verified:
                        broker = None
            except Exception:  # noqa: BLE001 - keep running; SDK details can contain secrets
                verified, broker = 0, None
                logger.warning("Bybit execution unavailable; retrying")
            if stream:
                await asyncio.to_thread(stream.wait, settings.poll_seconds)
                await asyncio.sleep(max(0, 2 - (time.monotonic() - cycle_started)))
            else:
                await asyncio.sleep(settings.poll_seconds)
    finally:
        if stream:
            stream.close()
        release_lease(factory, "testnet", owner)
        engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
