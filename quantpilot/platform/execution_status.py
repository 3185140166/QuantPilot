"""Read-back evidence for configuration loading, distinct from a start request."""

import hashlib
import json
from datetime import datetime, timezone

from .schemas import TestnetSetup


def configuration_fingerprint(configuration):
    model = TestnetSetup.model_validate(configuration)
    # Revalidate defaults too, so 200 and 200.0 represent the same setting.
    normalized = TestnetSetup.model_validate(model.model_dump(mode="json")).model_dump(
        mode="json"
    )
    return hashlib.sha256(
        json.dumps(
            normalized, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def strategy_status(value, worker_online, command_at=None):
    fingerprint = configuration_fingerprint(value["configuration"])
    evidence = value["snapshot"].get("strategy_execution") or {}
    matched = evidence.get("configuration_fingerprint") == fingerprint
    completed = evidence.get("completed_at")
    fresh = False
    after_command = command_at is None
    try:
        stamp = datetime.fromisoformat(completed)
        fresh = (
            stamp.tzinfo is not None
            and 0 <= (datetime.now(timezone.utc) - stamp).total_seconds() <= 60
        )
        if command_at is not None:
            observed = datetime.fromisoformat(evidence.get("observed_at"))
            after_command = (
                observed.tzinfo is not None
                and observed >= command_at.replace(tzinfo=timezone.utc)
            )
    except (TypeError, ValueError):
        pass
    acknowledged = bool(
        worker_online
        and matched
        and fresh
        and after_command
        and evidence.get("enabled") == value["enabled"]
        and evidence.get("successful")
    )
    origin = value.get("research_origin") or {}
    modified = bool(origin.get("modified"))
    if origin.get("applied_configuration"):
        modified = (
            modified
            or configuration_fingerprint(origin["applied_configuration"]) != fingerprint
        )
    return {
        "configuration_fingerprint": fingerprint,
        "acknowledged": acknowledged,
        "confirmed_at": completed if acknowledged else None,
        "last_checked_at": completed,
        "loaded_configuration": evidence.get("configuration"),
        "loaded_fingerprint": evidence.get("configuration_fingerprint"),
        "source": "validation_modified"
        if origin and modified
        else "validation"
        if origin
        else "saved_configuration",
        "source_job_id": origin.get("job_id"),
        "source_modified": modified,
        "unvalidated_symbols": origin.get("unvalidated_symbols", []),
        "last_scan": evidence.get("scan", []) if matched else [],
        "last_scan_at": evidence.get("scan_at") if matched else None,
        "command_at": command_at.isoformat() if command_at else None,
    }
