"""Manual, verified archival; live state and shared market snapshots are retained."""

import hashlib
import json
import os
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
from sqlalchemy import delete, func, select, update

from .models import (
    BacktestJob,
    EquityPoint,
    Event,
    OptimizationJob,
    Strategy,
    WorkerLease,
    utcnow,
)

MODELS = {
    "equity": EquityPoint,
    "events": Event,
    "backtests": BacktestJob,
    "optimizations": OptimizationJob,
}
MAX_ROWS = 10000
MAX_JOB_ROWS = 10


class ArchiveError(ValueError):
    """Safe maintenance error, suitable for an API response."""


def root_path():
    return Path("storage/archive").resolve()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def row_value(row):
    result = {}
    for column in row.__table__.columns:
        value = getattr(row, column.name)
        if isinstance(value, Decimal):
            value = str(value)
        elif hasattr(value, "isoformat"):
            value = value.isoformat()
        result[column.name] = value
    return result


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def safe_path(value):
    path = Path(value).resolve()
    if not path.is_relative_to(root_path()):
        raise ArchiveError("归档路径超出允许目录")
    return path


def load_result(result):
    if not result or "archived_result" not in result:
        return result
    ref = result["archived_result"]
    try:
        data = safe_path(ref["path"]).read_bytes()
        if digest(data) != ref["sha256"]:
            raise ArchiveError("归档文件校验失败，请恢复备份后重试")
        return json.loads(data)["result"]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise ArchiveError("归档文件无法读取，请检查存储或恢复备份") from exc


def preview(factory, retention_days):
    if not 30 <= retention_days <= 3650:
        raise ArchiveError("保留天数必须在 30–3650 天之间")
    cutoff = utcnow() - timedelta(days=retention_days)
    selections = {}
    with factory() as db:
        database_id = digest(str(db.bind.url).encode())
        latest = select(func.max(EquityPoint.id)).group_by(EquityPoint.strategy_id)
        for name, model in MODELS.items():
            query = select(model)
            if model is EquityPoint:
                query = query.where(model.created_at < cutoff, model.id.not_in(latest))
            elif model is Event:
                query = query.where(model.created_at < cutoff)
            else:
                query = query.where(
                    model.status.in_(["completed", "failed", "cancelled"]),
                    model.completed_at < cutoff,
                    model.result.is_not(None),
                    model.result["archived_result"].as_string().is_(None),
                )
            # Detailed job results can each contain 100000 points; bound job batches separately.
            batch_size = MAX_ROWS if model in (EquityPoint, Event) else MAX_JOB_ROWS
            rows = list(db.scalars(query.order_by(model.id).limit(batch_size)))
            selections[name] = [
                {"id": row.id, "sha256": digest(encode(row_value(row)))}
                for row in rows
                if not (
                    isinstance(row, (BacktestJob, OptimizationJob))
                    and "archived_result" in (row.result or {})
                )
            ]
    plan_id = uuid.uuid4().hex
    plan = {
        "id": plan_id,
        "created_at": utcnow().isoformat(),
        "cutoff": cutoff.isoformat(),
        "selections": selections,
        "database_id": database_id,
    }
    atomic_write(root_path() / "plans" / f"{plan_id}.json", encode(plan))
    return {
        "id": plan_id,
        "cutoff": plan["cutoff"],
        "counts": {key: len(value) for key, value in selections.items()},
        "batch_limit": MAX_ROWS,
        "job_batch_limit": MAX_JOB_ROWS,
    }


def execute(factory, plan_id):
    try:
        if uuid.UUID(plan_id).hex != plan_id:
            raise ValueError
    except (ValueError, AttributeError):
        raise ArchiveError("归档编号无效") from None
    plan_file = root_path() / "plans" / f"{plan_id}.json"
    try:
        plan = json.loads(plan_file.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ArchiveError("归档预览不存在，请重新预览") from exc
    if utcnow() - pd.Timestamp(plan["created_at"]).to_pydatetime() > timedelta(hours=1):
        raise ArchiveError("归档预览已超过一小时，请重新预览")
    destination = root_path() / plan_id
    counts = {key: len(value) for key, value in plan["selections"].items()}
    if not sum(counts.values()):
        raise ArchiveError("当前没有需要归档的明细")
    with factory.begin() as db:
        if digest(str(db.bind.url).encode()) != plan["database_id"]:
            raise ArchiveError("数据库配置已变化，请重新预览")
        # Seed one portable mutex row without a schema migration.
        dialect = db.bind.dialect.name
        if dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        elif dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            raise ArchiveError("当前数据库不支持归档")
        db.execute(
            insert(WorkerLease)
            .values(
                name="archive", owner="", heartbeat_at=utcnow(), expires_at=utcnow()
            )
            .on_conflict_do_nothing(index_elements=["name"])
        )
        # A write acquires SQLite's writer lock; PostgreSQL locks this mutex row.
        db.execute(
            update(WorkerLease)
            .where(WorkerLease.name == "archive")
            .values(heartbeat_at=utcnow())
        )
        if destination.exists():
            raise ArchiveError("该预览已执行或已有归档文件，请重新预览")
        selected = {}
        # Lock strategy rows in the same order before reading mutable equity points.
        equity_ids = [item["id"] for item in plan["selections"]["equity"]]
        strategy_ids = list(
            db.scalars(
                select(EquityPoint.strategy_id)
                .where(EquityPoint.id.in_(equity_ids))
                .distinct()
            )
        )
        list(
            db.scalars(
                select(Strategy)
                .where(Strategy.id.in_(strategy_ids))
                .order_by(Strategy.id)
                .with_for_update()
            )
        )
        for name, model in MODELS.items():
            expected = plan["selections"][name]
            rows = list(
                db.scalars(
                    select(model)
                    .where(model.id.in_([item["id"] for item in expected]))
                    .order_by(model.id)
                    .with_for_update()
                )
            )
            actual = [
                {"id": row.id, "sha256": digest(encode(row_value(row)))} for row in rows
            ]
            if actual != expected:
                raise ArchiveError("预览中的数据已变化，请重新预览")
            selected[name] = rows
        destination.mkdir(parents=True)
        files = []
        replacements = []
        for name, rows in selected.items():
            if not rows:
                continue
            values = [row_value(row) for row in rows]
            if name in {"equity", "events"}:
                path = destination / f"{name}.parquet"
                pd.DataFrame(values).to_parquet(path, index=False)
                restored = pd.read_parquet(path).to_dict(orient="records")
                # nullable foreign keys can round-trip through pandas as NaN/float.
                for item in restored:
                    item["strategy_id"] = (
                        None
                        if pd.isna(item.get("strategy_id"))
                        else int(item["strategy_id"])
                    )
                if encode(restored) != encode(values):
                    raise ArchiveError("归档明细校验失败，原始数据未移除")
                data = path.read_bytes()
                files.append(
                    {
                        "path": path.relative_to(Path.cwd()).as_posix(),
                        "sha256": digest(data),
                        "rows": len(values),
                        "kind": name,
                    }
                )
            else:
                for row, value in zip(rows, values, strict=True):
                    path = destination / f"{name}-{row.id}.json"
                    data = encode(value)
                    atomic_write(path, data)
                    if path.read_bytes() != data or json.loads(data) != value:
                        raise ArchiveError("归档结果校验失败，原始数据未移除")
                    ref = {
                        "path": path.relative_to(Path.cwd()).as_posix(),
                        "sha256": digest(data),
                    }
                    files.append({**ref, "rows": 1, "kind": name, "job_id": row.id})
                    replacements.append(
                        (
                            row,
                            {
                                "metrics": (row.result or {}).get("metrics"),
                                "archived_result": ref,
                            },
                        )
                    )
        manifest = {
            **plan,
            "files": files,
            "verified_at": utcnow().isoformat(),
            "counts": counts,
        }
        # The recoverable manifest and every file exist before any DB row is removed.
        atomic_write(destination / "manifest.json", encode(manifest))
        for file in files:
            if digest(safe_path(file["path"]).read_bytes()) != file["sha256"]:
                raise ArchiveError("归档指纹校验失败，原始数据未移除")
        for row, result in replacements:
            row.result = result
        for name in ("equity", "events"):
            ids = [row.id for row in selected[name]]
            db.execute(delete(MODELS[name]).where(MODELS[name].id.in_(ids)))
        db.add(
            Event(
                message=f"手动归档 {plan_id}：{sum(counts.values())} 条明细已校验保存"
            )
        )
    return {
        "id": plan_id,
        "counts": counts,
        "manifest": (destination / "manifest.json").relative_to(Path.cwd()).as_posix(),
    }
