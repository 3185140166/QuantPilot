from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from quantpilot.exchange.bybit import synchronize_request_clock


def test_private_signing_uses_server_clock_and_reuses_monotonic_anchor(monkeypatch):
    clock = [20.0]
    monkeypatch.setattr("quantpilot.exchange.bybit.time.monotonic", lambda: clock[0])
    session = SimpleNamespace(
        api_key="test",
        get_server_time=Mock(return_value={"retCode": 0, "time": 500_000}),
        _auth=Mock(return_value="signature"),
        _auth_binary=Mock(return_value="binary"),
    )
    synchronize_request_clock(session)
    result = session._prepare_headers("payload", 5000)
    assert result["X-BAPI-TIMESTAMP"] == "499900"
    session._auth.assert_called_once_with("payload", 5000, 499900)
    clock[0] += 1
    assert session._prepare_headers(b"binary", 5000)["X-BAPI-TIMESTAMP"] == "500900"
    assert session.get_server_time.call_count == 1
    clock[0] += 60
    session._prepare_headers("payload", 5000)
    assert session.get_server_time.call_count == 2


def test_time_sync_failure_prevents_order_signing():
    session = SimpleNamespace(
        get_server_time=Mock(return_value={"retCode": 10000}), _auth=Mock()
    )
    synchronize_request_clock(session)
    with pytest.raises(RuntimeError, match="synchronize"):
        session._prepare_headers("payload", 5000)
    session._auth.assert_not_called()
