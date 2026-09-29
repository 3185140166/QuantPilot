"""Binance USD-M Futures REST adapter with Testnet-first defaults."""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from typing import Any
from urllib.parse import urlencode

import requests

from ..config import Settings


class BinanceFuturesClient:
    def __init__(self, settings: Settings, require_private: bool = False):
        if require_private:
            settings.require_binance_private_api()
        self.settings = settings
        self.base_url = (
            "https://testnet.binancefuture.com"
            if settings.binance_testnet
            else "https://fapi.binance.com"
        )
        self.session = requests.Session()
        self.timeout = 15

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> Any:
        request_params = dict(params or {})
        headers: dict[str, str] = {}
        if signed:
            self.settings.require_binance_private_api()
            request_params.setdefault("timestamp", int(time.time() * 1000))
            request_params.setdefault("recvWindow", 5000)
            query = urlencode(request_params)
            signature = hmac.new(
                self.settings.binance_api_secret.encode(),
                query.encode(),
                hashlib.sha256,
            ).hexdigest()
            request_params["signature"] = signature
            headers["X-MBX-APIKEY"] = self.settings.binance_api_key
        response = self.session.request(
            method,
            self.base_url + path,
            params=request_params,
            headers=headers,
            timeout=self.timeout,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"Binance returned non-JSON HTTP {response.status_code}"
            ) from exc
        if not response.ok or (
            isinstance(payload, dict) and payload.get("code", 0) < 0
        ):
            if isinstance(payload, dict):
                raise RuntimeError(
                    f"Binance request failed: code={payload.get('code')} "
                    f"msg={payload.get('msg')}"
                )
            raise RuntimeError(f"Binance request failed: HTTP {response.status_code}")
        return payload

    def get_server_time(self) -> dict[str, Any]:
        return self._request("GET", "/fapi/v1/time")

    def get_klines(self, limit: int = 200) -> list[list[Any]]:
        interval = self.settings.interval
        if interval.isdigit():
            interval += "m"
        return self._request(
            "GET",
            "/fapi/v1/klines",
            {"symbol": self.settings.symbol, "interval": interval, "limit": limit},
        )

    def get_instrument(self) -> dict[str, Any]:
        payload = self._request("GET", "/fapi/v1/exchangeInfo")
        symbols = [
            item for item in payload["symbols"] if item["symbol"] == self.settings.symbol
        ]
        if not symbols:
            raise RuntimeError(f"Binance symbol not found: {self.settings.symbol}")
        return symbols[0]

    def get_account(self) -> dict[str, Any]:
        return self._request("GET", "/fapi/v2/account", signed=True)

    def place_market(
        self, side: str, qty: str, reduce_only: bool = False
    ) -> dict[str, Any]:
        self.settings.require_binance_private_api()
        if side not in {"BUY", "SELL"}:
            raise ValueError("Binance side must be BUY or SELL")
        params: dict[str, Any] = {
            "symbol": self.settings.symbol,
            "side": side,
            "type": "MARKET",
            "quantity": qty,
            "newClientOrderId": "quantpilot-" + uuid.uuid4().hex[:20],
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        return self._request("POST", "/fapi/v1/order", params, signed=True)
