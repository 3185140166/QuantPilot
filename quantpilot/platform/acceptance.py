"""Separate observed exchange evidence from scenarios that remain untested."""

from datetime import datetime, timezone


def summarize(report):
    evidence = {
        key: value for row in report.get("checks", []) for key, value in row.items()
    }
    required = {
        "limit_order": "New",
        "limit_cancellation": "Cancelled",
        "market_order": "Filled",
        "exchange_stop_present": True,
        "closed_and_flat": True,
        "submission_response_recovered": True,
        "fresh_client_recovery": True,
    }
    checks = [
        {
            "name": name,
            "passed": evidence.get(name) == expected,
            "observed": evidence.get(name),
        }
        for name, expected in required.items()
    ]
    cycles = report.get("cycles", [])
    journal = bool(
        cycles
        and all(
            c.get("complete")
            and c.get("reconciled") is not False
            and c.get("net_pnl") is not None
            for c in cycles
        )
    )
    checks.append(
        {"name": "journal_reconciled", "passed": journal, "observed": journal}
    )
    flat = report.get("final_positions") == 0 and report.get("final_open_orders") == 0
    passed = (
        report.get("environment") == "bybit_testnet"
        and flat
        and not report.get("errors")
        and all(c["passed"] for c in checks)
    )
    return {
        "version": 1,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "passed": passed,
        "checks": checks,
        "final_flat": flat,
        "environment": report.get("environment"),
        "untested": [
            "真实部分成交",
            "交易所原生止损实际触发",
            "长时间断网",
            "资金费结算事件",
        ],
        "limitations": [
            "响应丢失场景是提交成功后模拟丢弃响应，不代表真实断网实验",
            "客户端与数据库会重新建立，尚未代替操作系统进程崩溃及长时间运行验收",
            "止损存在不代表已观察止损触发；未测试项目不计为通过",
        ],
    }
