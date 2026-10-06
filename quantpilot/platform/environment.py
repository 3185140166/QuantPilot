"""Persist a Bybit profile selection; processes apply it on restart."""

import os
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import func, select, update

from quantpilot.config import _bool

from .models import (
    BacktestJob,
    OptimizationJob,
    Position,
    Strategy,
    TestnetAccount,
    TestnetOrder,
    TestnetPosition,
    WorkerLease,
    utcnow,
)


class EnvironmentError(ValueError):
    pass


def update_text(path, values):
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    output, seen = [], set()
    for line in lines:
        key = (
            line.split("=", 1)[0].strip()
            if "=" in line and not line.lstrip().startswith("#")
            else None
        )
        if key in values:
            output.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            output.append(line)
    output.extend(f"{key}={value}" for key, value in values.items() if key not in seen)
    return "\n".join(output) + "\n"


class BybitEnvironment:
    def __init__(self, settings, directory=None):
        self.directory = Path(directory or Path.cwd())
        self.database_url = settings.database_url
        values = dotenv_values(self.directory / ".env")
        self.active = (
            "testnet"
            if _bool(
                "BYBIT_TESTNET",
                os.getenv("BYBIT_TESTNET", values.get("BYBIT_TESTNET")),
                True,
            )
            else "mainnet"
        )

    def status(self):
        values = dotenv_values(self.directory / ".env")
        platform = dotenv_values(self.directory / ".env.platform")
        selected = (
            "testnet"
            if _bool("BYBIT_TESTNET", values.get("BYBIT_TESTNET"), True)
            else "mainnet"
        )
        profile_url = platform.get(f"PLATFORM_BYBIT_{selected.upper()}_DATABASE_URL")
        separate = any(
            key in values
            for key in ("BYBIT_TESTNET_API_KEY", "BYBIT_TESTNET_API_SECRET")
        )
        test_prefix = "BYBIT_TESTNET_" if separate else "BYBIT_"
        return {
            "active": self.active,
            "selected": selected,
            "restart_required": selected != self.active
            or bool(
                profile_url
                and platform.get("DATABASE_URL") == profile_url
                and profile_url != self.database_url
            ),
            "testnet_credentials_configured": bool(
                values.get(test_prefix + "API_KEY")
                and values.get(test_prefix + "API_SECRET")
            ),
            "mainnet_credentials_configured": bool(
                values.get("BYBIT_API_KEY") and values.get("BYBIT_API_SECRET")
            ),
            "switch_available": self.database_url.startswith("sqlite:///")
            and ":memory:" not in self.database_url,
            **self.runtime_status(),
        }

    def runtime_status(self):
        from .environment_runtime import status

        return status(self.directory, self.database_url)

    def save(self, factory, selected):
        if selected not in {"testnet", "mainnet"}:
            raise EnvironmentError("环境参数无效")
        runtime = self.runtime_status()
        if runtime["switch_state"] in {"pending", "switching"}:
            raise EnvironmentError("环境切换正在进行，请等待完成")
        if not self.status()["switch_available"]:
            raise EnvironmentError(
                "网页环境切换目前支持本地 SQLite；其他部署请分别配置独立数据库"
            )
        env_path, platform_path = (
            self.directory / ".env",
            self.directory / ".env.platform",
        )
        values = dotenv_values(env_path)
        if (
            selected == "mainnet"
            and values.get("LIVE_TRADING_CONFIRM") != "I_UNDERSTAND_LIVE_TRADING"
        ):
            raise EnvironmentError(
                "请先在 .env 填写主网访问确认 LIVE_TRADING_CONFIRM=I_UNDERSTAND_LIVE_TRADING"
            )
        # SQLite's writer lock serializes saves and blocks workers from starting a
        # strategy between the activity check and the config write.
        with factory.begin() as db:
            from sqlalchemy.dialects.sqlite import insert

            db.execute(
                insert(WorkerLease)
                .values(
                    name="environment",
                    owner="",
                    heartbeat_at=utcnow(),
                    expires_at=utcnow(),
                )
                .on_conflict_do_nothing(index_elements=["name"])
            )
            db.execute(
                update(WorkerLease)
                .where(WorkerLease.name == "environment")
                .values(heartbeat_at=utcnow())
            )
            runtime = self.runtime_status()
            if runtime["switch_state"] in {"pending", "switching"}:
                raise EnvironmentError("环境切换正在进行，请等待完成")
            busy = (
                db.scalar(
                    select(func.count())
                    .select_from(Strategy)
                    .where(Strategy.status.in_(["running", "flattening"]))
                )
                or db.scalar(select(func.count()).select_from(Position))
                or db.scalar(
                    select(func.count())
                    .select_from(BacktestJob)
                    .where(BacktestJob.status.in_(["pending", "running"]))
                )
                or db.scalar(
                    select(func.count())
                    .select_from(OptimizationJob)
                    .where(
                        OptimizationJob.status.in_(["pending", "running", "cancelling"])
                    )
                )
            )
            busy = (
                busy
                or db.scalar(
                    select(func.count())
                    .select_from(TestnetAccount)
                    .where(TestnetAccount.enabled.is_(True))
                )
                or db.scalar(select(func.count()).select_from(TestnetPosition))
                or db.scalar(
                    select(func.count())
                    .select_from(TestnetOrder)
                    .where(
                        TestnetOrder.status.in_(
                            [
                                "intent",
                                "submitted",
                                "unknown",
                                "New",
                                "PartiallyFilled",
                                "Untriggered",
                            ]
                        )
                    )
                )
                or any(
                    row.flatten_requested for row in db.scalars(select(TestnetAccount))
                )
            )
            from .ownership import classify

            if not busy:
                for row in db.scalars(select(TestnetAccount)):
                    ownership = classify(factory, row.snapshot)
                    if any(
                        item["source"] != "external"
                        for item in ownership["positions"] + ownership["orders"]
                    ):
                        busy = True
                        break
            if busy:
                raise EnvironmentError(
                    "请先暂停并处理本系统持仓、未确认订单或归属不明记录，等待回测／优化结束；外部手工持仓与订单可保留"
                )
            platform = dotenv_values(platform_path)
            active_key = f"PLATFORM_BYBIT_{self.active.upper()}_DATABASE_URL"
            selected_key = f"PLATFORM_BYBIT_{selected.upper()}_DATABASE_URL"
            mappings = {active_key: platform.get(active_key) or self.database_url}
            file = Path(self.database_url.removeprefix("sqlite:///"))
            target = (
                platform.get(selected_key)
                or mappings.get(selected_key)
                or f"sqlite:///{file.with_name(file.stem + '-bybit-' + selected + file.suffix).as_posix()}"
            )
            if selected != self.active and target == self.database_url:
                raise EnvironmentError(
                    "主网与测试网必须使用独立数据库，请检查环境数据库配置"
                )
            mappings.update({selected_key: target, "DATABASE_URL": target})
            env_changes = {
                "BYBIT_TESTNET": "true" if selected == "testnet" else "false",
                "DRY_RUN": "true",
                "TRADING_ENABLED": "false",
            }
            # Explicit empty testnet slots prevent accidental mainnet-key fallback.
            if selected == "testnet":
                for key in ("BYBIT_TESTNET_API_KEY", "BYBIT_TESTNET_API_SECRET"):
                    if key not in values:
                        env_changes[key] = ""
            texts = [
                (env_path, update_text(env_path, env_changes)),
                (platform_path, update_text(platform_path, mappings)),
            ]
            originals = {
                path: path.read_bytes() if path.exists() else None for path, _ in texts
            }
            written = []
            try:
                for path, content in texts:
                    temporary = path.with_name(path.name + ".switch.tmp")
                    temporary.write_text(content, encoding="utf-8")
                    temporary.replace(path)
                    written.append(path)
                # Parse the results before returning; never expose credential text.
                if (
                    dotenv_values(env_path).get("BYBIT_TESTNET")
                    != env_changes["BYBIT_TESTNET"]
                    or dotenv_values(platform_path).get("DATABASE_URL") != target
                ):
                    raise OSError("Configuration verification failed")
                if selected != self.active and runtime["automatic_switch_available"]:
                    from .environment_runtime import queue

                    queue(self.directory, self.database_url, self.active, selected)
            except OSError as exc:
                for path in written:
                    original = originals[path]
                    if original is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.write_bytes(original)
                raise EnvironmentError("环境配置保存失败，请检查文件权限") from exc
            finally:
                for path, _ in texts:
                    path.with_name(path.name + ".switch.tmp").unlink(missing_ok=True)
        return self.status()
