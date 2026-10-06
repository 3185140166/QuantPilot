"""Launcher-owned environment transitions; no exchange order operations."""

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import select

from .backups import atomic_json
from .database import make_database
from .health import profile_id
from .models import Position, Strategy, TestnetAccount, TestnetOrder, TestnetPosition


def read(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def capability(directory, database_url):
    supervisor = read(Path(directory) / "storage/run/supervisor.json")
    try:
        age = (
            datetime.now(timezone.utc)
            - datetime.fromisoformat(supervisor["updated_at"])
        ).total_seconds()
        valid = (
            0 <= age < 8
            and supervisor.get("automatic_environment_switch") is True
            and bool(supervisor.get("run_id"))
            and supervisor.get("profile") == profile_id(database_url, directory)
        )
    except (KeyError, ValueError, TypeError):
        valid = False
    return supervisor if valid else {}


def status(directory, database_url):
    supervisor = capability(directory, database_url)
    job = read(Path(directory) / "storage/run/environment-switch.json")
    controller = read(Path(directory) / "storage/run/supervisor.json")
    if job.get("run_id") != controller.get("run_id"):
        job = {}
    elapsed = 0
    try:
        end = (
            datetime.fromisoformat(job["finished_at"])
            if job.get("finished_at")
            else datetime.now(timezone.utc)
        )
        elapsed = max(
            0, int((end - datetime.fromisoformat(job["created_at"])).total_seconds())
        )
    except (KeyError, ValueError, TypeError):
        pass
    return {
        "automatic_switch_available": bool(supervisor),
        "switch_state": job.get("state", "idle"),
        "switch_message": job.get("message", ""),
        "switch_id": job.get("id"),
        "switch_phase": job.get("phase", ""),
        "switch_elapsed_seconds": elapsed,
        "recent_switches": sorted(
            [
                read(path)
                for path in (
                    Path(directory) / "storage/run/environment-switch-history"
                ).glob("*.json")
            ],
            key=lambda item: str(item.get("finished_at", "")),
            reverse=True,
        )[:5],
    }


def queue(directory, database_url, previous, selected):
    supervisor = capability(directory, database_url)
    if not supervisor:
        raise OSError("Automatic environment controller unavailable")
    atomic_json(
        Path(directory) / "storage/run/environment-switch.json",
        {
            "id": uuid.uuid4().hex,
            "run_id": supervisor["run_id"],
            "state": "pending",
            "previous": previous,
            "previous_database": database_url,
            "target": selected,
            "message": "正在切换环境，自动交易保持暂停",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def child_environment(directory, previous):
    result = previous.copy()
    values = {
        **dotenv_values(Path(directory) / ".env"),
        **dotenv_values(Path(directory) / ".env.platform"),
    }
    result.update({key: value for key, value in values.items() if value is not None})
    return result


def pause_target(database_url):
    """Reject unmanaged target services and system exposure; never resume trading."""
    from .testnet import PENDING

    engine, factory = make_database(database_url)
    try:
        with factory.begin() as db:
            if (
                db.scalar(select(TestnetPosition.symbol).limit(1))
                or db.scalar(
                    select(TestnetOrder.id)
                    .where(TestnetOrder.status.in_(PENDING))
                    .limit(1)
                )
                or db.scalar(select(Position.strategy_id).limit(1))
            ):
                raise ValueError("目标环境存在本系统持仓或待确认订单")
            for account in db.scalars(select(TestnetAccount)):
                if account.flatten_requested:
                    raise ValueError("目标环境正在处理平仓请求")
                account.enabled = False
            for strategy in db.scalars(
                select(Strategy).where(Strategy.status.in_(["running", "flattening"]))
            ):
                strategy.status = "paused"
    finally:
        engine.dispose()


def restore(directory, job):
    from .environment import update_text

    directory = Path(directory)
    env_path, platform_path = directory / ".env", directory / ".env.platform"
    texts = [
        (
            env_path,
            update_text(
                env_path,
                {
                    "BYBIT_TESTNET": "true"
                    if job["previous"] == "testnet"
                    else "false",
                    "DRY_RUN": "true",
                    "TRADING_ENABLED": "false",
                },
            ),
        ),
        (
            platform_path,
            update_text(platform_path, {"DATABASE_URL": job["previous_database"]}),
        ),
    ]
    originals = {path: path.read_bytes() for path, _ in texts}
    written = []
    try:
        for path, content in texts:
            temporary = path.with_name(path.name + ".runtime-switch.tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
            written.append(path)
    except OSError:
        for path in written:
            path.write_bytes(originals[path])
        raise
    finally:
        for path, _ in texts:
            path.with_name(path.name + ".runtime-switch.tmp").unlink(missing_ok=True)


def progress(directory, job, phase, message):
    job.update(phase=phase, message=message)
    atomic_json(Path(directory) / "storage/run/environment-switch.json", job)


def preparation_failure(exc):
    from requests.exceptions import ConnectionError, Timeout

    code = getattr(exc, "status_code", None)
    if (
        isinstance(exc, (ConnectionError, Timeout))
        or code in {10002, 10006, 429, 500, 502, 503, 504}
        or (
            isinstance(exc, RuntimeError)
            and str(exc).startswith("Bybit time request is too slow")
        )
    ):
        return "network"
    if code in {10003, 10004, 10005, 10010}:
        return "credentials"
    if isinstance(exc, ValueError) and str(exc).startswith(
        ("目标环境存在本系统", "目标环境正在处理平仓")
    ):
        return "exposure"
    return "account"


def readonly_snapshot(broker):
    """Retry only transient read failures; never submit trading operations."""
    broker.session.timeout = 5
    broker.session.max_retries = 1
    broker.session.retry_delay = 0
    for attempt in range(2):
        try:
            return broker.snapshot()
        except Exception as exc:
            if attempt or preparation_failure(exc) != "network":
                raise
            time.sleep(0.5)


def remember_result(directory, job):
    keys = {
        "id",
        "state",
        "previous",
        "target",
        "phase",
        "failed_phase",
        "error_code",
        "message",
        "created_at",
        "finished_at",
    }
    history = Path(directory) / "storage/run/environment-switch-history"
    atomic_json(
        history / (str(uuid.UUID(job["id"])) + ".json"),
        {key: job[key] for key in keys if key in job},
    )
    for path in sorted(
        history.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )[20:]:
        path.unlink(missing_ok=True)


def transition(directory, run_id, prepare, stop, start, ready):
    """Callbacks isolate real process handling from deterministic regression tests."""
    path = Path(directory) / "storage/run/environment-switch.json"
    job = read(path)
    if job.get("state") != "pending" or job.get("run_id") != run_id:
        return None
    job.update(state="switching", message="正在更新后台环境，自动交易保持暂停")
    atomic_json(path, job)
    stopped = False
    try:
        prepare(job)
        progress(directory, job, "restarting", "正在重新加载后台服务，自动交易保持暂停")
        stop()
        stopped = True
        start(job, False)
        progress(directory, job, "checking", "正在确认新环境服务，自动交易保持暂停")
        if not ready(job, False):
            raise RuntimeError("Target services unavailable")
        job.update(
            state="completed",
            phase="completed",
            message="环境切换成功，自动交易保持暂停",
        )
    except Exception:  # noqa: BLE001 - no credentials or SDK details in status
        job["failed_phase"] = job.get("phase", "preparing")
        reason = {
            "network": "目标账户连接超时或暂时不可用",
            "timeout": "目标账户核查超时，请检查网络或数据库占用",
            "credentials": "目标账户密钥、权限或 IP 白名单核查失败",
            "account": "目标账户或本地持仓核查未通过",
            "exposure": "目标环境仍有本系统持仓、待确认订单或平仓请求",
            "database": "目标数据库准备未完成",
            "checking": "目标后台服务启动核查未通过",
            "restarting": "后台服务重载未完成",
        }.get(job.get("error_code", job["failed_phase"]), "目标环境准备未完成")
        progress(directory, job, "recovering", "切换未完成，正在恢复原环境")
        if stopped:
            stop()
        try:
            restore(directory, job)
        except OSError:
            job.update(
                state="failed",
                finished_at=datetime.now(timezone.utc).isoformat(),
                message="环境配置恢复失败，自动交易已暂停，请检查后台服务",
            )
            atomic_json(path, job)
            raise RuntimeError(
                "Environment configuration recovery unavailable"
            ) from None
        if stopped:
            try:
                start(job, True)
                recovered = ready(job, True)
            except Exception:  # noqa: BLE001 - report recovery without SDK details
                recovered = False
            if not recovered:
                job.update(
                    state="failed",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    message="切换失败，原环境恢复未完成；自动交易保持暂停，请检查后台服务",
                )
                atomic_json(path, job)
                raise RuntimeError("Environment recovery unavailable") from None
        job.update(
            state="failed",
            message=f"切换失败：{reason}；已恢复原环境，自动交易保持暂停",
        )
    job.update(phase=job["state"], finished_at=datetime.now(timezone.utc).isoformat())
    atomic_json(path, job)
    try:
        remember_result(directory, job)
    except OSError:
        pass  # Optional history must never stop healthy services.
    return job


def main():
    """Preparation runs in a fresh process with target credentials, read only."""
    from quantpilot.config import Settings

    from .models import utcnow
    from .settings import PlatformSettings
    from .testnet import TestnetBroker, account

    try:
        platform = PlatformSettings.from_env()
        pause_target(platform.database_url)
        if platform.market_source == "exchange":
            broker = TestnetBroker(Settings.from_env(".env"))
            snapshot = readonly_snapshot(broker)
            engine, factory = make_database(platform.database_url)
            try:
                account(factory)
                with factory.begin() as db:
                    current = db.get(TestnetAccount, 1)
                    current.enabled = False
                    current.snapshot = {
                        **snapshot,
                        "execution_ready": False,
                        "environment_switch_readonly": True,
                    }
                    current.updated_at = utcnow()
                    current.last_error = None
            finally:
                engine.dispose()
    except Exception as exc:  # noqa: BLE001 - suppress SDK metadata and secrets
        if os.getenv("PLATFORM_ENVIRONMENT_SWITCH_ID"):
            try:
                atomic_json(
                    Path.cwd() / "storage/run/environment-preparation.json",
                    {
                        "id": os.environ["PLATFORM_ENVIRONMENT_SWITCH_ID"],
                        "error_code": preparation_failure(exc),
                    },
                )
            except OSError:
                pass
        raise SystemExit("目标环境账户或数据库核查失败；未提交交易订单") from None


if __name__ == "__main__":
    main()
