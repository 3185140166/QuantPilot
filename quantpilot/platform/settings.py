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

    def __post_init__(self):
        if self.auth_enabled and len(self.access_token) < 32:
            raise ValueError(
                "PLATFORM_ACCESS_TOKEN must contain at least 32 characters"
            )
        if self.market_source not in {"demo", "exchange"}:
            raise ValueError("PLATFORM_MARKET_SOURCE must be demo or exchange")
        if not 1 <= self.poll_seconds <= 60:
            raise ValueError("PLATFORM_POLL_SECONDS must be between 1 and 60")

    @classmethod
    def from_env(cls):
        load_dotenv(".env.platform", override=False)
        return cls(
            database_url=os.getenv("DATABASE_URL", "sqlite:///storage/platform.db"),
            access_token=os.getenv("PLATFORM_ACCESS_TOKEN", ""),
            market_source=os.getenv("PLATFORM_MARKET_SOURCE", "demo"),
            poll_seconds=float(os.getenv("PLATFORM_POLL_SECONDS", "10")),
            auth_enabled=_bool(
                "PLATFORM_AUTH_ENABLED", os.getenv("PLATFORM_AUTH_ENABLED"), False
            ),
        )
