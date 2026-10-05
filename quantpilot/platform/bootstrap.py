"""Create local platform configuration without changing exchange .env."""

import secrets
from pathlib import Path


def main():
    target = Path(".env.platform")
    if target.exists():
        print(".env.platform already exists; preserved")
        return
    with target.open("x", encoding="utf-8") as stream:
        stream.write("DATABASE_URL=sqlite:///storage/platform.db\n")
        stream.write("PLATFORM_AUTH_ENABLED=false\n")
        stream.write(f"PLATFORM_ACCESS_TOKEN={secrets.token_urlsafe(36)}\n")
        stream.write("PLATFORM_MARKET_SOURCE=demo\nPLATFORM_POLL_SECONDS=10\n")
        stream.write(f"POSTGRES_PASSWORD={secrets.token_hex(24)}\n")
    print("Created .env.platform. Personal mode opens the local UI directly.")


if __name__ == "__main__":
    main()
