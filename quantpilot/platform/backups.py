"""Online SQLite snapshots plus referenced research files, verified without restore."""

import hashlib
import json
import os
import shutil
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import make_url


def atomic_json(path, value):
    """Preserve the last complete document while retrying short Windows locks."""
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temp.write_text(payload, encoding="utf-8")
        for attempt in range(6):
            try:
                os.replace(temp, path)
                return
            except OSError as error:
                transient = isinstance(error, PermissionError) or getattr(
                    error, "winerror", None
                ) in {5, 32, 33}
                if not transient or attempt == 5:
                    raise
                time.sleep(0.025 * 2**attempt)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            # Cleanup must not obscure the original write error.
            pass


def fingerprint(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_bundle(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["files"]:
        path = (directory / item["file"]).resolve()
        if (
            not path.is_relative_to(directory)
            or not path.is_file()
            or fingerprint(path) != item["sha256"]
        ):
            raise ValueError("备份文件缺失或指纹不一致")
    database = (directory / "database.db").resolve()
    if not any(item["file"] == "database.db" for item in manifest["files"]):
        raise ValueError("备份缺少数据库记录")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        if (
            db.execute("PRAGMA quick_check").fetchone()[0] != "ok"
            or db.execute("PRAGMA foreign_key_check").fetchone()
        ):
            raise ValueError("备份数据库完整性检查失败")
        version = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    return {
        "verified": True,
        "schema_version": version,
        "files": len(manifest["files"]),
        "missing_research_files": manifest.get("missing_research_files", 0),
        "created_at": manifest["created_at"],
    }


def create_backup(database_url, workspace, keep=7):
    workspace = Path(workspace).resolve()
    root = workspace / "storage/backups/scheduled"
    status_path = workspace / "storage/run/backup.json"
    url = make_url(database_url)
    if (
        url.get_backend_name() != "sqlite"
        or not url.database
        or url.database == ":memory:"
    ):
        result = {
            "supported": False,
            "message": "自动文件备份只支持SQLite；其他数据库需使用部署备份服务",
        }
        atomic_json(status_path, result)
        return result
    source = Path(url.database)
    if not source.is_absolute():
        source = workspace / source
    source = source.resolve()
    profile = hashlib.sha256(str(source).encode()).hexdigest()[:10]
    target = root / (
        "snapshot-"
        + profile
        + "-"
        + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    )
    target.mkdir(parents=True, exist_ok=False)
    with (
        sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as db,
        sqlite3.connect(target / "database.db") as copied,
    ):
        db.backup(copied)
    files = [{"file": "database.db", "sha256": fingerprint(target / "database.db")}]
    references = set()

    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if (
                    key in {"dataset", "archived_result"}
                    and isinstance(item, dict)
                    and item.get("path")
                ):
                    references.add(item["path"])
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    with sqlite3.connect(target / "database.db") as db:
        for table in ("backtest_jobs", "optimization_jobs"):
            for (raw,) in db.execute(
                f"SELECT result FROM {table} WHERE result IS NOT NULL"
            ):
                collect(json.loads(raw))
    missing = 0
    storage = (workspace / "storage").resolve()
    processed = set()
    while references - processed:
        relative = min(references - processed)
        processed.add(relative)
        original = Path(relative)
        original = (
            (workspace / original).resolve()
            if not original.is_absolute()
            else original.resolve()
        )
        allowed = any(
            original.is_relative_to(storage / name) for name in ("market", "archive")
        )
        if (
            not allowed
            or original.suffix not in {".json", ".parquet"}
            or not original.is_file()
        ):
            missing += 1
            continue
        name = f"research/{len(files)}{original.suffix}"
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination)
        files.append(
            {
                "file": name,
                "sha256": fingerprint(destination),
                "restore_path": original.relative_to(workspace).as_posix(),
            }
        )
        if original.suffix == ".json":
            # Archived payloads may reference a market snapshot outside the archive.
            collect(json.loads(destination.read_text(encoding="utf-8")))
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
        "missing_research_files": missing,
    }
    atomic_json(target / "manifest.json", manifest)
    checked = verify_bundle(target)
    result = {
        "supported": True,
        "directory": target.relative_to(workspace).as_posix(),
        **checked,
        "database_name": source.name,
        "profile": profile,
        "message": "数据库和已找到的研究文件已校验"
        if not missing
        else "数据库已校验，但部分研究文件缺失，备份不完整",
    }
    atomic_json(status_path, result)
    successful = sorted(
        (
            p
            for p in root.glob(f"snapshot-{profile}-*")
            if (p / "manifest.json").is_file()
        ),
        reverse=True,
    )
    for old in successful[keep:]:
        checked_path = old.resolve()
        if (
            checked_path.is_relative_to(root.resolve())
            and (checked_path / "manifest.json").is_file()
        ):
            shutil.rmtree(checked_path)
    return result


def verify_latest(workspace):
    workspace = Path(workspace).resolve()
    status = json.loads(
        (workspace / "storage/run/backup.json").read_text(encoding="utf-8")
    )
    path = (workspace / status["directory"]).resolve()
    if not path.is_relative_to((workspace / "storage/backups/scheduled").resolve()):
        raise ValueError("备份路径无效")
    result = verify_bundle(path)
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    atomic_json(workspace / "storage/run/backup-check.json", result)
    return result
