"""Fixed-parameter, consecutive out-of-sample windows; no validation tuning."""

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from .archive import load_result
from .models import BacktestJob
from .schemas import BacktestCreate


def plan(source, payload):
    params = source.parameters
    if (
        source.status != "completed"
        or not source.result
        or params.get("job_type") != "portfolio"
        or params.get("evaluation_phase") != "research"
    ):
        raise ValueError("请选择已完成的组合研究")
    start, end = payload.start_at, payload.end_at
    from .market import INTERVAL_MINUTES

    step = INTERVAL_MINUTES[params["interval"]] * 60
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or start < datetime.fromisoformat(params["end_at"])
    ):
        raise ValueError("所有验证窗口必须在研究结束之后且包含时区")
    if start.timestamp() % step or end.timestamp() % step:
        raise ValueError("验证时间必须对齐信号周期")
    bars = int((end - start).total_seconds() / step)
    width = bars // payload.windows
    if width < 100:
        raise ValueError("每个独立窗口至少需要100根K线，请扩大验证区间")
    group = uuid4().hex
    jobs = []
    for index in range(payload.windows):
        left = start + timedelta(seconds=index * width * step)
        right = (
            end
            if index == payload.windows - 1
            else left + timedelta(seconds=width * step)
        )
        checked = BacktestCreate.model_validate(
            {
                **{
                    key: value
                    for key, value in params.items()
                    if key in BacktestCreate.model_fields
                },
                "start_at": left,
                "end_at": right,
                "evaluation_phase": "validation",
            }
        )
        if (right - left).total_seconds() / step * len(
            params["portfolio_config"]["symbols"]
        ) > 500000:
            raise ValueError("单个窗口数据超过500000根K线")
        jobs.append(
            {
                **params,
                **checked.model_dump(mode="json"),
                "validation_parent_job_id": source.id,
                "rolling_group_id": group,
                "rolling_windows": payload.windows,
                "rolling_index": index + 1,
            }
        )
    return jobs


def report(db, job, config):
    group = job.parameters.get("rolling_group_id")
    if not group:
        return {
            "windows": [],
            "expected": 1,
            "complete": False,
            "passed": False,
            "semantics": "未关联多窗口独立验证",
        }
    rows = list(
        db.scalars(
            select(BacktestJob)
            .where(
                BacktestJob.parameters["rolling_group_id"].as_string() == group,
            )
            .order_by(BacktestJob.id)
        )
    )
    expected = job.parameters.get("rolling_windows", 0)
    windows = []
    parent = db.get(BacktestJob, job.parameters.get("validation_parent_job_id"))
    consistent = bool(
        parent
        and parent.status == "completed"
        and parent.parameters.get("evaluation_phase") == "research"
    )
    previous_end = parent.parameters.get("end_at") if parent else None
    for index, row in enumerate(rows, 1):
        params = row.parameters
        consistent &= (
            params.get("evaluation_phase") == "validation"
            and params.get("rolling_index") == index
            and params.get("rolling_windows") == expected
            and params.get("validation_parent_job_id")
            == job.parameters.get("validation_parent_job_id")
            and all(
                params.get(key) == job.parameters.get(key)
                for key in (
                    "portfolio_config",
                    "execution",
                    "rules",
                    "interval",
                    "market_environment",
                    "contracts",
                )
            )
            and parent is not None
            and all(
                params.get(key) == parent.parameters.get(key)
                for key in (
                    "portfolio_config",
                    "execution",
                    "rules",
                    "interval",
                    "market_environment",
                    "contracts",
                )
            )
            and previous_end is not None
            and datetime.fromisoformat(params["start_at"])
            >= datetime.fromisoformat(previous_end)
            and (index == 1 or params["start_at"] == previous_end)
        )
        previous_end = params["end_at"]
        metrics, passed, failure = {}, False, []
        if row.status == "completed" and row.result:
            try:
                from .strategy_review import assess

                value = load_result(row.result)
                base_config = config.model_copy(deep=True)
                base_config.research_guard.min_validation_windows = 1
                check = assess(value, params, base_config)
                metrics, passed, failure = (
                    value.get("metrics", {}),
                    check["eligible"],
                    check["failed"],
                )
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                failure = ["窗口结果不可读取"]
        else:
            failure = ["窗口尚未完成或失败"]
        windows.append(
            {
                "id": row.id,
                "index": index,
                "status": row.status,
                "start_at": params["start_at"],
                "end_at": params["end_at"],
                "metrics": metrics,
                "passed": passed,
                "failed": failure,
            }
        )
    complete = (
        len(rows) == expected
        and 2 <= expected <= 5
        and all(row.status == "completed" and row.result for row in rows)
    )
    return {
        "group_id": group,
        "expected": expected,
        "complete": bool(complete),
        "passed": bool(
            complete and consistent and all(row["passed"] for row in windows)
        ),
        "windows": windows,
        "semantics": "所有窗口沿用研究规则、风险预算和成交模型，独立重置资金，不拼接为一条实盘权益曲线；没有根据验证结果重新调参。",
    }
