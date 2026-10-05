"""Run all four local services from one terminal; Ctrl+C stops owned processes."""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from .bootstrap import main as bootstrap
from .settings import PlatformSettings


def stop_children(children):
    for child in children:
        if child.poll() is not None:
            continue
        if os.name == "nt":
            # Only terminate the process trees created by this launcher.
            subprocess.run(
                ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.killpg(child.pid, signal.SIGTERM)
    for child in children:
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


def main():
    workspace = Path(__file__).resolve().parents[2]
    os.chdir(workspace)
    if not (workspace / "frontend/node_modules").exists():
        raise SystemExit(
            "Install frontend dependencies first: cd frontend && npm.cmd ci"
        )
    for port in [8000, 5173]:
        with socket.socket() as connection:
            connection.settimeout(1)
            if connection.connect_ex(("127.0.0.1", port)) == 0:
                raise SystemExit(
                    f"Port {port} is in use. Stop the existing service first."
                )
    bootstrap()
    settings = PlatformSettings.from_env()
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=environment,
        check=True,
    )
    logs = workspace / "storage/logs"
    logs.mkdir(parents=True, exist_ok=True)
    services = [
        (
            "api",
            [
                sys.executable,
                "-m",
                "uvicorn",
                "quantpilot.platform.api:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            workspace,
        ),
        ("engine", [sys.executable, "-m", "quantpilot.platform.engine"], workspace),
        (
            "research",
            [sys.executable, "-m", "quantpilot.platform.research_worker"],
            workspace,
        ),
        (
            "frontend",
            ["cmd.exe", "/d", "/c", "npm.cmd", "run", "dev"]
            if os.name == "nt"
            else ["npm", "run", "dev"],
            workspace / "frontend",
        ),
    ]
    children, streams = [], []
    try:
        for name, args, cwd in services:
            stream = (logs / f"{name}.log").open("a", encoding="utf-8")
            streams.append(stream)
            children.append(
                subprocess.Popen(
                    args,
                    cwd=cwd,
                    env=environment,
                    stdout=stream,
                    stderr=stream,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    start_new_session=os.name != "nt",
                )
            )
        print("QuantPilot: http://127.0.0.1:5173", flush=True)
        print(
            "Access token: .env.platform / PLATFORM_ACCESS_TOKEN"
            if settings.auth_enabled
            else "Personal mode: opens directly without an access token",
            flush=True,
        )
        print("Logs: storage/logs | Ctrl+C to stop all four services", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(1)
        print("A service stopped. Inspect storage/logs before restarting.", flush=True)
    except KeyboardInterrupt:
        print("Stopping QuantPilot...", flush=True)
    finally:
        stop_children(children)
        for stream in streams:
            stream.close()


if __name__ == "__main__":
    main()
