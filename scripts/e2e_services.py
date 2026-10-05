"""Isolated UI test services; synthetic market and disposable workspace database."""

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def main():
    workspace = Path(__file__).resolve().parents[1]
    artifacts = workspace / "artifacts"
    artifacts.mkdir(exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix="ui-test-", dir=artifacts)
    test_directory = Path(temporary.name).resolve()
    if not test_directory.is_relative_to(artifacts.resolve()):
        raise RuntimeError("Test directory must remain inside workspace artifacts")
    environment = os.environ.copy()
    environment.update(
        DATABASE_URL="sqlite:///" + (test_directory / "platform.db").as_posix(),
        PLATFORM_MARKET_SOURCE="demo",
        PLATFORM_POLL_SECONDS="1",
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=workspace,
        env=environment,
        check=True,
    )
    children = []

    def stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        for args in [
            [
                "-m",
                "uvicorn",
                "quantpilot.platform.api:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                "8011",
            ],
            ["-m", "quantpilot.platform.engine"],
            ["-m", "quantpilot.platform.research_worker"],
        ]:
            children.append(
                subprocess.Popen(
                    [sys.executable, *args], cwd=workspace, env=environment
                )
            )
        while all(child.poll() is None for child in children):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        if not test_directory.is_relative_to(artifacts.resolve()):
            raise RuntimeError("Refusing to clean an unexpected test directory")
        temporary.cleanup()


if __name__ == "__main__":
    main()
