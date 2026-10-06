"""Status-file lock failures must preserve JSON and launcher availability."""

import json
from unittest.mock import Mock

import pytest

from quantpilot.platform import backups, launch


def test_atomic_json_retries_lock_without_partial_overwrite(tmp_path, monkeypatch):
    path = tmp_path / "status.json"
    path.write_text('{"old": true}', encoding="utf-8")
    replace = backups.os.replace
    calls = []

    def locked_replace(source, target):
        calls.append(source)
        assert json.loads(path.read_text()) == {"old": True}
        if len(calls) < 3:
            raise PermissionError("locked")
        replace(source, target)

    monkeypatch.setattr(backups.os, "replace", locked_replace)
    sleep = Mock()
    monkeypatch.setattr(backups.time, "sleep", sleep)
    backups.atomic_json(path, {"new": True})
    assert json.loads(path.read_text()) == {"new": True}
    assert len(calls) == 3
    assert sleep.call_count == 2
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize(
    "error,retries", [(PermissionError("locked"), 6), (OSError("disk full"), 1)]
)
def test_atomic_json_failure_preserves_previous_file(
    tmp_path, monkeypatch, error, retries
):
    path = tmp_path / "status.json"
    path.write_text('{"old": true}', encoding="utf-8")
    replace = Mock(side_effect=error)
    monkeypatch.setattr(backups.os, "replace", replace)
    monkeypatch.setattr(backups.time, "sleep", Mock())
    with pytest.raises(OSError):
        backups.atomic_json(path, {"new": True})
    assert replace.call_count == retries
    assert json.loads(path.read_text()) == {"old": True}
    assert not list(tmp_path.glob("*.tmp"))


def test_status_publisher_survives_persistent_failure_and_recovers(
    tmp_path, monkeypatch, capsys
):
    publisher = launch.StatusPublisher(tmp_path / "status.json")
    writer = Mock(
        side_effect=[
            PermissionError("private detail"),
            OSError("private detail"),
            None,
            None,
        ]
    )
    monkeypatch.setattr(launch, "atomic_json", writer)
    assert publisher.publish({}) is False
    assert publisher.publish({}) is False
    output = capsys.readouterr().out
    assert output.count("运行状态文件写入失败") == 1
    assert "private detail" not in output
    assert publisher.publish({}) is True
    assert publisher.publish({}) is True
    assert capsys.readouterr().out.count("已恢复") == 1
