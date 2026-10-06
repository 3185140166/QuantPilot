"""Private testnet events wake REST reconciliation; payloads never become fills."""

import hashlib
import hmac
import json
import threading
import time
from urllib.parse import unquote, urlsplit

import requests
import websocket


class TestnetStream:
    topics = ("order", "execution", "position", "wallet")

    def __init__(self, settings):
        if settings.exchange != "bybit":
            raise ValueError("Private stream requires Bybit")
        settings.require_private_api()
        if (
            not settings.testnet
            and settings.live_trading_confirm != "I_UNDERSTAND_LIVE_TRADING"
        ):
            raise ValueError("Mainnet connection requires explicit confirmation")
        self.rest_url = (
            "https://api-testnet.bybit.com"
            if settings.testnet
            else "https://api.bybit.com"
        )
        self.stream_url = (
            "wss://stream-testnet.bybit.com/v5/private"
            if settings.testnet
            else "wss://stream.bybit.com/v5/private"
        )
        self._settings = settings
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._socket = None
        self._generation = self._revision = self._events = 0
        self._authenticated = self._subscribed = False
        self._received = 0.0
        self._reconciled = None
        self._failure_stage = None
        self._transport_error = None

    def checkpoint(self):
        with self._lock:
            return self._generation, self._revision

    def valid(self, token):
        with self._lock:
            return (
                self._authenticated
                and self._subscribed
                and time.monotonic() - self._received < 60
                and token == self.checkpoint()
            )

    def reconciled(self, token):
        with self._lock:
            if self.valid(token):
                self._reconciled = token
                return True
            return False

    def forget_reconciliation(self):
        with self._lock:
            self._reconciled = None

    def health(self):
        with self._lock:
            token = self.checkpoint()
            ready = self.valid(token)
            return {
                "ready": ready,
                "reconciled": ready and self._reconciled == token,
                "generation": self._generation,
                "events": self._events,
                "failure_stage": self._failure_stage,
                "transport_error": self._transport_error,
            }

    def _invalidate(self, *_args):
        with self._lock:
            self._generation += 1
            self._authenticated = self._subscribed = False
            self._reconciled = None
            self._wake.set()

    def _message(self, ws, raw):
        stage = "message"
        try:
            if len(raw) > 1_000_000:
                raise ValueError("Oversized stream message")
            payload = json.loads(raw)
            if isinstance(payload, dict) and payload.get("op") in {"auth", "subscribe"}:
                stage = payload["op"]
            with self._lock:
                self._received = time.monotonic()
                if payload.get("op") == "auth":
                    if payload.get("success") is not True:
                        raise ValueError("Authentication unavailable")
                    self._authenticated = True
                    ws.send(json.dumps({"op": "subscribe", "args": self.topics}))
                elif payload.get("op") == "subscribe":
                    if not self._authenticated or payload.get("success") is not True:
                        raise ValueError("Subscription unavailable")
                    self._subscribed = True
                    self._failure_stage = None
                    self._transport_error = None
                    self._wake.set()
                elif payload.get("topic", "").split(".")[0] in self.topics:
                    if not self._authenticated or not self._subscribed:
                        raise ValueError("Event before subscription acknowledgement")
                    # Duplicated/out-of-order events only wake an idempotent REST read.
                    self._revision += 1
                    self._events += 1
                    self._wake.set()
        except Exception:  # noqa: BLE001 - do not log credentials or private payloads
            with self._lock:
                self._failure_stage = stage
            self._invalidate()
            ws.close()

    def _open(self, ws):
        self._invalidate()
        self._opened = time.monotonic()
        try:
            started = time.monotonic()
            response = requests.get(self.rest_url + "/v5/market/time", timeout=4)
            response.raise_for_status()
            result = response.json()
            if result.get("retCode") != 0 or time.monotonic() - started > 4:
                raise ValueError("Server clock unavailable")
            expires = int(result["time"]) + 10_000
            signature = hmac.new(
                self._settings.api_secret.encode(),
                f"GET/realtime{expires}".encode(),
                hashlib.sha256,
            ).hexdigest()
            ws.send(
                json.dumps(
                    {"op": "auth", "args": [self._settings.api_key, expires, signature]}
                )
            )
        except Exception:  # noqa: BLE001 - reconnect with no private SDK diagnostics
            with self._lock:
                self._failure_stage = "clock_or_auth_send"
            self._invalidate()
            ws.close()

    def _error(self, _ws, error):
        with self._lock:
            self._failure_stage = "transport"
            self._transport_error = type(error).__name__
        self._invalidate()

    @staticmethod
    def _proxy_options(url="https://stream-testnet.bybit.com"):
        # requests also discovers Windows system proxies; websocket-client only
        # reads environment variables. Use the same configured route as REST.
        proxies = requests.utils.get_environ_proxies(url)
        value = proxies.get("https") or proxies.get("all")
        if not value:
            return {}
        proxy = urlsplit(value)
        if (
            proxy.scheme not in {"http", "socks4", "socks4a", "socks5", "socks5h"}
            or not proxy.hostname
        ):
            raise ValueError("Unsupported private stream proxy")
        return {
            "http_proxy_host": proxy.hostname,
            "http_proxy_port": proxy.port or 80,
            "proxy_type": proxy.scheme,
            "http_proxy_auth": (unquote(proxy.username), unquote(proxy.password or ""))
            if proxy.username
            else None,
        }

    def _run(self):
        delay = 1
        while not self._stop.is_set():
            try:
                self._socket = websocket.WebSocketApp(
                    self.stream_url,
                    on_open=self._open,
                    on_message=self._message,
                    on_error=self._error,
                    on_close=self._invalidate,
                    on_pong=self._pong,
                )
                self._socket.run_forever(
                    ping_interval=20,
                    ping_timeout=10,
                    http_proxy_timeout=10,
                    **self._proxy_options(
                        self.stream_url.replace("wss://", "https://")
                    ),
                )
            except Exception:  # noqa: BLE001 - failures are represented by health only
                self._invalidate()
            self._invalidate()
            if self._stop.wait(delay):
                break
            delay = min(30, delay * 2)

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _pong(self, ws, *_args):
        with self._lock:
            self._received = time.monotonic()
        try:
            ws.send(json.dumps({"op": "ping"}))
        except Exception:  # noqa: BLE001 - no private transport diagnostics
            self._invalidate()
            ws.close()

    def wait(self, timeout):
        self._wake.wait(timeout)
        self._wake.clear()
        with self._lock:
            stale = (self._subscribed and time.monotonic() - self._received >= 60) or (
                not self._subscribed
                and time.monotonic() - getattr(self, "_opened", time.monotonic()) > 15
            )
        if stale and self._socket:
            self._invalidate()
            self._socket.close()

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._socket:
            self._socket.close()
        if self._thread:
            self._thread.join(timeout=2)
