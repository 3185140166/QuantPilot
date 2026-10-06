"""Read-only private-stream connectivity acceptance. Never submits an order."""

import argparse
import json
import logging
import time
from pathlib import Path

from quantpilot.config import Settings
from quantpilot.platform.models import utcnow
from quantpilot.platform.testnet_stream import TestnetStream


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=30)
    args = parser.parse_args()
    if not 5 <= args.seconds <= 60:
        parser.error("seconds must be between 5 and 60")
    logging.getLogger("websocket").setLevel(logging.CRITICAL)
    stream = TestnetStream(Settings.from_env(".env"))
    observed = False
    stream.start()
    try:
        end = time.monotonic() + args.seconds
        while time.monotonic() < end:
            stream.wait(min(1, end - time.monotonic()))
            observed = observed or stream.health()["ready"]
        report = {
            "at": utcnow().isoformat(),
            "read_only": True,
            "subscription_observed": observed,
            **stream.health(),
        }
    finally:
        stream.close()
    path = Path("artifacts/testnet/stream-check.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
