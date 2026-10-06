"""Platform settings, separate from exchange secrets and execution switches."""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from quantpilot.config import _bool


@dataclass(frozen=True)
class PlatformSettings:
    database_url: str = field(repr=False)
    access_token: str = field(default="", repr=False)
    market_source: str = "demo"
    poll_seconds: float = 10
    auth_enabled: bool = False
    testnet_execution_enabled: bool = False
    backup_hours: int = 24
    backup_keep: int = 7
    restart_limit: int = 5
    testnet_stream_enabled: bool = True

    def __post_init__(self):
        if self.auth_enabled and len(self.access_token) < 32:
            raise ValueError(
                "PLATFORM_ACCESS_TOKEN must contain at least 32 characters"
            )
        if self.market_source not in {"demo", "exchange"}:
            raise ValueError("PLATFORM_MARKET_SOURCE must be demo or exchange")
        if not 1 <= self.poll_seconds <= 60:
            raise ValueError("PLATFORM_POLL_SECONDS must be between 1 and 60")
        if (
            not 1 <= self.backup_hours <= 168
            or not 1 <= self.backup_keep <= 30
            or not 1 <= self.restart_limit <= 10
        ):
            raise ValueError("Invalid backup or service restart settings")

    @classmethod
    def from_env(cls):
        load_dotenv(".env.platform", override=False)
        return cls(
            database_url=os.getenv("DATABASE_URL", "sqlite:///storage/platform.db"),
            access_token=os.getenv("PLATFORM_ACCESS_TOKEN", ""),
            market_source=os.getenv("PLATFORM_MARKET_SOURCE", "demo"),
            poll_seconds=float(os.getenv("PLATFORM_POLL_SECONDS", "10")),
            backup_hours=int(os.getenv("PLATFORM_BACKUP_HOURS", "24")),
            backup_keep=int(os.getenv("PLATFORM_BACKUP_KEEP", "7")),
            restart_limit=int(os.getenv("PLATFORM_RESTART_LIMIT", "5")),
            testnet_stream_enabled=_bool(
                "PLATFORM_TESTNET_STREAM_ENABLED",
                os.getenv("PLATFORM_TESTNET_STREAM_ENABLED"),
                True,
            ),
            auth_enabled=_bool(
                "PLATFORM_AUTH_ENABLED", os.getenv("PLATFORM_AUTH_ENABLED"), False
            ),
            testnet_execution_enabled=_bool(
                "PLATFORM_TESTNET_EXECUTION_ENABLED",
                os.getenv("PLATFORM_TESTNET_EXECUTION_ENABLED"),
                True,
            ),
        )
