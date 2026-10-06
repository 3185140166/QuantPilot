"""Run local services from one terminal; Ctrl+C stops owned processes."""

import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from urllib.request import urlopen

from .backups import atomic_json, create_backup
from .bootstrap import main as bootstrap
from .settings import PlatformSettings
from .supervisor import ManagedService


class StatusPublisher:
    """A failed diagnostic heartbeat must not terminate owned services."""

    def __init__(self, path):
        self.path = path
        self.failed = False

    def publish(self, value):
        try:
            atomic_json(self.path, value)
        except OSError:
            if not self.failed:
                print(
                    "运行状态文件写入失败，服务继续运行；若持续失败，请检查 "
                    "storage/run 的文件占用或写入权限。状态过期时环境切换暂不可用。",
                    flush=True,
                )
            self.failed = True
            return False
        if self.failed:
            print("运行状态文件写入已恢复。", flush=True)
        self.failed = False
        return True


def backup_before_migration(settings, workspace):
    """Use SQLite's backup API before changing an existing schema."""
    if (
        not settings.database_url.startswith("sqlite:///")
        or ":memory:" in settings.database_url
    ):
        return None
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy.engine import make_url

    path = Path(make_url(settings.database_url).database)
    if not path.exists():
        return None
    latest = ScriptDirectory.from_config(
        Config(str(workspace / "alembic.ini"))
    ).get_current_head()
    with sqlite3.connect(path) as source:
        try:
            current = source.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
        except sqlite3.OperationalError:
            current = None
        if current and current[0] == latest:
            return None
        target = (
            workspace
            / "storage/backups"
            / (
                "pre-migration-"
                + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
                + ".db"
            )
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(target) as destination:
            source.backup(destination)
    return target


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


def schema_current(database_url, workspace):
    """Read an existing SQLite schema without launching a migration process."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy.engine import make_url

    if not database_url.startswith("sqlite:///"):
        return False
    path = Path(make_url(database_url).database)
    if not path.is_absolute():
        path = workspace / path
    if not path.is_file():
        return False
    latest = ScriptDirectory.from_config(
        Config(str(workspace / "alembic.ini"))
    ).get_current_head()
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1) as db:
            row = db.execute("SELECT version_num FROM alembic_version").fetchone()
        return bool(row and row[0] == latest)
    except sqlite3.Error:
        return False


def run_preparation(args, workspace, environment, heartbeat, timeout):
    """Keep the controller alive while a bounded preparation subprocess runs."""
    process = subprocess.Popen(
        args,
        cwd=workspace,
        env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    deadline = time.monotonic() + timeout
    try:
        while True:
            heartbeat()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(args, timeout)
            try:
                code = process.wait(timeout=min(1, remaining))
            except subprocess.TimeoutExpired:
                continue
            if code:
                raise subprocess.CalledProcessError(code, args)
            return
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


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
    backup = backup_before_migration(settings, workspace)
    if backup:
        print("Database backup: " + str(backup), flush=True)
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=environment,
        check=True,
    )
    from .environment_runtime import pause_target, read

    interrupted_path = workspace / "storage/run/environment-switch.json"
    interrupted = read(interrupted_path)
    if interrupted.get("state") in {"pending", "switching"}:
        pause_target(settings.database_url)
        interrupted.update(
            state="failed", message="上次环境切换中断，当前环境已保持暂停"
        )
        atomic_json(interrupted_path, interrupted)
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
            "testnet",
            [sys.executable, "-m", "quantpilot.platform.testnet_worker"],
            workspace,
        ),
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
    children, streams, managed = [], [], []
    backup_thread, last_backup = None, float("-inf")
    status_path = workspace / "storage/run/supervisor.json"
    status_publisher = StatusPublisher(status_path)
    run_id = uuid.uuid4().hex

    def publish_status():
        from .health import profile_id

        return status_publisher.publish(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "run_id": run_id,
                "automatic_environment_switch": True,
                "profile": profile_id(settings.database_url, workspace),
                "services": [service.status() for service in managed],
            },
        )

    def prepare_switch(job):
        from .environment_runtime import child_environment, progress, read

        target_environment = child_environment(workspace, environment)
        target_environment["PLATFORM_ENVIRONMENT_SWITCH_ID"] = job["id"]
        progress(workspace, job, "database", "正在准备目标环境数据库")
        if not schema_current(target_environment["DATABASE_URL"], workspace):
            run_preparation(
                [sys.executable, "-m", "alembic", "upgrade", "head"],
                workspace,
                target_environment,
                publish_status,
                30,
            )
        progress(workspace, job, "account", "正在核对目标账户余额、持仓和挂单（只读）")
        try:
            run_preparation(
                [sys.executable, "-m", "quantpilot.platform.environment_runtime"],
                workspace,
                target_environment,
                publish_status,
                45,
            )
        except subprocess.TimeoutExpired:
            job["error_code"] = "timeout"
            raise
        except subprocess.CalledProcessError:
            result = read(workspace / "storage/run/environment-preparation.json")
            if result.get("id") == job["id"]:
                job["error_code"] = result.get("error_code", "account")
            raise
        job["target_database"] = target_environment["DATABASE_URL"]

    def stop_backend():
        backend = [service for service in managed if service.name != "frontend"]
        stop_children(
            [service.process for service in backend if service.process is not None]
        )
        for service in backend:
            service.process = None
            service.state = "starting"
            service.restart_at = None
            service.attempts.clear()

    def start_backend(job, rollback):
        nonlocal settings, last_backup
        from .environment_runtime import child_environment

        environment.update(child_environment(workspace, environment))
        environment["PLATFORM_ENVIRONMENT_SWITCH_ID"] = job["id"]
        settings = replace(settings, database_url=environment["DATABASE_URL"])
        for service in managed:
            if service.name != "frontend":
                service.launch()
        last_backup = float("-inf")
        publish_status()

    def backend_ready(job, rollback):
        from .health import profile_id

        target = job["previous"] if rollback else job["target"]
        database = job["previous_database"] if rollback else job["target_database"]
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            publish_status()
            if any(
                service.process is None or service.process.poll() is not None
                for service in managed
                if service.name != "frontend"
            ):
                return False
            try:
                with urlopen("http://127.0.0.1:8000/api/health", timeout=2) as response:
                    health = json.load(response)
                if (
                    health.get("environment") == target
                    and health.get("profile") == profile_id(database, workspace)
                    and health.get("switch_id") == job["id"]
                ):
                    return True
            except (OSError, ValueError):
                pass
            time.sleep(0.5)
        return False

    def scheduled_backup():
        try:
            create_backup(settings.database_url, workspace, settings.backup_keep)
        except Exception:  # noqa: BLE001 - keep database credentials out of status
            from .health import profile_id

            atomic_json(
                workspace / "storage/run/backup-error.json",
                {
                    "at": datetime.now(timezone.utc).isoformat(),
                    "profile": profile_id(settings.database_url, workspace),
                    "message": "定期备份失败，请检查存储空间、文件与数据库",
                },
            )

    try:
        for name, args, cwd in services:
            stream = (logs / f"{name}.log").open("a", encoding="utf-8")
            streams.append(stream)

            def spawn(args=args, cwd=cwd, stream=stream):
                child = subprocess.Popen(
                    args,
                    cwd=cwd,
                    env=environment,
                    stdout=stream,
                    stderr=stream,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    start_new_session=os.name != "nt",
                )
                children.append(child)
                return child

            service = ManagedService(name, spawn)
            service.step(time.monotonic(), settings.restart_limit)
            managed.append(service)
        print("QuantPilot: http://127.0.0.1:5173", flush=True)
        print(
            "Access token: .env.platform / PLATFORM_ACCESS_TOKEN"
            if settings.auth_enabled
            else "Personal mode: opens directly without an access token",
            flush=True,
        )
        print("Logs: storage/logs | Ctrl+C to stop all five services", flush=True)
        while True:
            now = time.monotonic()
            if backup_thread is None or not backup_thread.is_alive():
                from .environment_runtime import transition

                result = transition(
                    workspace,
                    run_id,
                    prepare_switch,
                    stop_backend,
                    start_backend,
                    backend_ready,
                )
                if result:
                    print(result["message"], flush=True)
            healthy = True
            for service in managed:
                previous = service.state
                healthy &= service.step(now, settings.restart_limit)
                if service.state != previous or service.state == "failed":
                    print(
                        f"Service {service.name}: {service.state}; restarts {service.restart_count}. Logs: storage/logs/{service.name}.log",
                        flush=True,
                    )
            publish_status()
            if not healthy:
                from .database import make_database
                from .models import TestnetAccount

                engine, factory = make_database(settings.database_url)
                with factory.begin() as db:
                    row = db.get(TestnetAccount, 1)
                    if row:
                        row.enabled = False
                        row.last_error = "服务多次退出，自动交易已暂停，请检查运行日志"
                engine.dispose()
                raise SystemExit(
                    "Service restart limit reached; stopping owned processes."
                )
            if now - last_backup >= settings.backup_hours * 3600 and (
                backup_thread is None or not backup_thread.is_alive()
            ):
                backup_thread = Thread(target=scheduled_backup, daemon=True)
                backup_thread.start()
                last_backup = now
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping QuantPilot...", flush=True)
    finally:
        stop_children(children)
        if backup_thread:
            backup_thread.join(timeout=3)
        for stream in streams:
            stream.close()


if __name__ == "__main__":
    main()
