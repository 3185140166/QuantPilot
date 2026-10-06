"""Local health alerts; never queries exchange or sends external notifications."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import make_url

from .models import TestnetAccount, TestnetOrder, WorkerLease, utcnow


def read_status(path):
    try:
        if path.stat().st_size > 1000000:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def profile_id(database_url, workspace):
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        return None
    path = Path(url.database)
    path = path.resolve() if path.is_absolute() else (Path(workspace) / path).resolve()
    return hashlib.sha256(str(path).encode()).hexdigest()[:10]


def status(factory, settings, environment, workspace=None):
    workspace = Path(workspace or Path.cwd())
    now = utcnow()
    alerts, workers = [], []
    expected = ["engine", "research"] + (
        ["testnet"]
        if settings.testnet_execution_enabled
        else []
    )
    with factory() as db:
        for name in expected:
            row = db.get(WorkerLease, name)
            online = bool(
                row and row.owner and row.expires_at.replace(tzinfo=timezone.utc) > now
            )
            workers.append(
                {
                    "name": name,
                    "online": online,
                    "heartbeat_at": row.heartbeat_at.isoformat() if row else None,
                }
            )
            if not online:
                alerts.append(
                    {
                        "code": "worker_" + name,
                        "severity": "warning",
                        "message": {
                            "engine": "模拟引擎未在线",
                            "research": "研究进程未在线，回测任务会等待",
                            "testnet": "Bybit 执行未在线，请检查服务",
                        }[name],
                    }
                )
        current = db.get(TestnetAccount, 1)
        if current and "testnet" in expected:
            for code, message in (
                ("execution_error", current.last_error),
                ("ledger", current.ledger_warning),
            ):
                if message:
                    alerts.append(
                        {"code": code, "severity": "error", "message": message}
                    )
            if current.risk_halted:
                alerts.append(
                    {
                        "code": "risk_halted",
                        "severity": "error",
                        "message": "账户风控已暂停新开仓，请核对权益和仓位",
                    }
                )
            missing = [
                p["symbol"]
                for p in current.snapshot.get("positions", [])
                if float(p.get("stopLoss") or 0) <= 0
            ]
            if missing:
                alerts.append(
                    {
                        "code": "unprotected",
                        "severity": "error",
                        "message": "存在无止损仓位：" + "、".join(missing),
                    }
                )
            try:
                stamp = datetime.fromisoformat(current.snapshot["at"])
                if (now - stamp).total_seconds() > 60:
                    alerts.append(
                        {
                            "code": "snapshot_stale",
                            "severity": "warning",
                            "message": "交易账户快照超过60秒未更新，不能据此确认当前仓位",
                        }
                    )
            except (KeyError, TypeError, ValueError):
                pass
        unknown = db.scalars(
            select(TestnetOrder).where(
                TestnetOrder.status.in_(["intent", "submitted", "unknown"])
            )
        ).all()
        if "testnet" in expected and any(
            (now - o.created_at.replace(tzinfo=timezone.utc)).total_seconds() > 120
            for o in unknown
        ):
            alerts.append(
                {
                    "code": "unknown_orders",
                    "severity": "error",
                    "message": "订单结果超过2分钟未确认；保持查询对账，不要重复提交",
                }
            )
    supervisor = read_status(workspace / "storage/run/supervisor.json")
    try:
        stamp = datetime.fromisoformat(supervisor["updated_at"])
        if not stamp.tzinfo or not 0 <= (now - stamp).total_seconds() <= 60:
            supervisor = {}
    except (KeyError, ValueError, TypeError):
        supervisor = {}
    for service in supervisor.get("services", []):
        if service.get("state") in {"waiting", "failed"}:
            alerts.append(
                {
                    "code": "service_" + service["name"],
                    "severity": "warning",
                    "message": service["name"]
                    + "服务正在重启或已达到重试上限，请检查日志",
                }
            )
    backup = read_status(workspace / "storage/run/backup.json")
    if backup.get("profile") != profile_id(settings.database_url, workspace):
        backup = {}
    if backup.get("missing_research_files"):
        alerts.append(
            {
                "code": "backup_partial",
                "severity": "warning",
                "message": "最近备份的数据库已校验，但部分研究文件缺失",
            }
        )
    elif backup:
        try:
            if (
                now - datetime.fromisoformat(backup["created_at"])
            ).total_seconds() > settings.backup_hours * 3600 * 2:
                alerts.append(
                    {
                        "code": "backup_old",
                        "severity": "warning",
                        "message": "定期备份超过预期间隔，请检查统一启动器",
                    }
                )
        except (KeyError, ValueError, TypeError):
            pass
    error = read_status(workspace / "storage/run/backup-error.json")
    if error.get("profile") == profile_id(
        settings.database_url, workspace
    ) and error.get("at", "") > backup.get("created_at", ""):
        alerts.append(
            {
                "code": "backup_error",
                "severity": "warning",
                "message": error.get("message", "备份失败"),
            }
        )
    return {
        "workers": workers,
        "alerts": alerts,
        "backup": backup,
        "supervisor": supervisor,
        "checked_at": now.isoformat(),
        "testnet_acceptance": latest_acceptance(workspace),
    }


def latest_acceptance(workspace):
    reports = sorted(
        (workspace / "artifacts/testnet").glob("smoke-*/report.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not reports:
        return None
    report = read_status(reports[0])
    evidence = report.get("acceptance")
    if not isinstance(evidence, dict):
        return {
            "passed": False,
            "legacy": True,
            "untested": ["旧报告没有新版验收摘要"],
            "checks": [],
        }
    return evidence
