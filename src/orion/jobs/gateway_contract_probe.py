"""
Gateway contract probe for live Data-Gateway validation.

Validates:
- HTTP health endpoint availability
- WebSocket auth handshake
- Subscription acknowledgement contract
- Unknown-action error mapping
- Data-event envelope/schema shape (best-effort)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from contextlib import suppress
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
import websockets

from orion.config import system_settings
from orion.connectors.gateway_stream_client import GatewayStreamClient


def _normalize_http_gateway_base_url(gateway_url: str) -> str:
    raw = (gateway_url or "").strip()
    if not raw:
        raise ValueError("gateway_url is required")

    if "://" not in raw:
        raw = f"http://{raw}"

    parsed = urlparse(raw)
    scheme_map = {
        "http": "http",
        "https": "https",
        "ws": "http",
        "wss": "https",
    }
    http_scheme = scheme_map.get(parsed.scheme, "http")

    path = (parsed.path or "").rstrip("/")
    if path.endswith("/api/v1"):
        path = path[: -len("/api/v1")]

    return urlunparse((http_scheme, parsed.netloc, path, "", "", ""))


async def _receive_json_message(websocket: Any, timeout_seconds: float) -> dict[str, Any]:
    raw = await asyncio.wait_for(websocket.recv(), timeout=timeout_seconds)

    if isinstance(raw, bytes):
        decoded = raw.decode("utf-8")
    elif isinstance(raw, str):
        decoded = raw
    else:
        raise ValueError(f"Unsupported websocket frame type: {type(raw)}")

    parsed = json.loads(decoded)
    if not isinstance(parsed, dict):
        raise ValueError("Expected websocket message object")
    return parsed


async def _wait_for_message_type(
    websocket: Any,
    *,
    message_types: set[str],
    timeout_seconds: float,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + max(0.0, timeout_seconds)

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None

        try:
            message = await _receive_json_message(websocket, remaining)
        except TimeoutError:
            return None
        msg_type = str(message.get("type") or "").lower()

        if msg_type in message_types:
            return message

        if msg_type == "heartbeat":
            await websocket.send(json.dumps({"action": "heartbeat"}))


def _is_valid_data_event_schema(message: dict[str, Any]) -> bool:
    if str(message.get("type") or "").lower() != "data":
        return False

    required_keys = {"feed", "event_id", "envelope", "data"}
    if not required_keys.issubset(set(message.keys())):
        return False

    return isinstance(message.get("envelope"), dict) and isinstance(message.get("data"), dict)


async def _run_health_probe(
    *,
    health_url: str,
    retries: int,
    retry_delay_seconds: float,
    timeout_seconds: float,
) -> tuple[bool, int, str | None]:
    attempts = 0
    last_error: str | None = None

    for attempt in range(1, max(1, retries) + 1):
        attempts = attempt
        try:
            response = await asyncio.to_thread(httpx.get, health_url, timeout=timeout_seconds)
            payload: dict[str, Any] = {}
            with suppress(Exception):
                parsed = response.json()
                if isinstance(parsed, dict):
                    payload = parsed

            if response.status_code == 200 and str(payload.get("status", "")).lower() == "ok":
                return True, attempts, None

            last_error = (
                f"health_status_mismatch(status_code={response.status_code}, payload_status={payload.get('status')})"
            )
        except Exception as exc:  # pragma: no cover - exercised in live probe
            last_error = f"health_request_error({exc})"

        if attempt < max(1, retries):
            await asyncio.sleep(max(0.0, retry_delay_seconds))

    return False, attempts, last_error


async def run_gateway_contract_probe(
    *,
    gateway_url: str,
    api_key: str,
    symbol: str,
    health_retries: int = 3,
    health_retry_delay_seconds: float = 1.0,
    health_timeout_seconds: float = 5.0,
    receive_timeout_seconds: float = 5.0,
    data_wait_seconds: float = 10.0,
) -> dict[str, Any]:
    if not api_key:
        raise ValueError("api_key is required")

    http_base = _normalize_http_gateway_base_url(gateway_url)
    health_url = f"{http_base}/health"
    ws_url = GatewayStreamClient._normalize_ws_url(gateway_url)

    summary: dict[str, Any] = {
        "gateway_url": gateway_url,
        "health_url": health_url,
        "ws_url": ws_url,
        "symbol": symbol,
        "health_ok": False,
        "health_attempts": 0,
        "auth_ok": False,
        "auth_error_code": None,
        "subscription_ok": False,
        "subscription_error_code": None,
        "unknown_action_error_code": None,
        "data_event_seen": False,
        "data_event_schema_ok": False,
        "errors": [],
    }

    health_ok, attempts, health_error = await _run_health_probe(
        health_url=health_url,
        retries=health_retries,
        retry_delay_seconds=health_retry_delay_seconds,
        timeout_seconds=health_timeout_seconds,
    )
    summary["health_ok"] = health_ok
    summary["health_attempts"] = attempts

    if not health_ok:
        if health_error:
            summary["errors"].append(health_error)
        return summary

    websocket = await websockets.connect(ws_url, ping_interval=20, ping_timeout=10)
    try:
        await websocket.send(json.dumps({"action": "auth", "key": api_key}))
        auth_message = await _wait_for_message_type(
            websocket,
            message_types={"auth_result"},
            timeout_seconds=receive_timeout_seconds,
        )

        if auth_message is None:
            summary["errors"].append("auth_timeout")
            return summary

        if str(auth_message.get("status") or "").lower() != "ok":
            summary["auth_error_code"] = auth_message.get("error_code")
            summary["errors"].append(str(auth_message.get("message") or "auth_failed"))
            return summary

        summary["auth_ok"] = True

        await websocket.send(
            json.dumps(
                {
                    "action": "subscribe",
                    "provider": "alpaca",
                    "feed": "bars",
                    "symbols": [symbol],
                }
            )
        )
        subscribe_message = await _wait_for_message_type(
            websocket,
            message_types={"subscription_ack", "error"},
            timeout_seconds=receive_timeout_seconds,
        )

        if subscribe_message is None:
            summary["errors"].append("subscription_timeout")
            return summary

        subscribe_type = str(subscribe_message.get("type") or "").lower()
        if subscribe_type == "error":
            summary["subscription_error_code"] = subscribe_message.get("error_code")
            summary["errors"].append(str(subscribe_message.get("message") or "subscription_error"))
            return summary

        if str(subscribe_message.get("status") or "").lower() != "ok":
            summary["subscription_error_code"] = subscribe_message.get("error_code")
            summary["errors"].append(str(subscribe_message.get("message") or "subscription_failed"))
            return summary

        summary["subscription_ok"] = True

        await websocket.send(json.dumps({"action": "__probe_unknown_action__"}))
        unknown_action_message = await _wait_for_message_type(
            websocket,
            message_types={"error"},
            timeout_seconds=receive_timeout_seconds,
        )
        if unknown_action_message:
            summary["unknown_action_error_code"] = unknown_action_message.get("error_code")

        if data_wait_seconds > 0:
            data_message = await _wait_for_message_type(
                websocket,
                message_types={"data"},
                timeout_seconds=data_wait_seconds,
            )
            if data_message is not None:
                summary["data_event_seen"] = True
                summary["data_event_schema_ok"] = _is_valid_data_event_schema(data_message)

        with suppress(Exception):
            await websocket.send(
                json.dumps(
                    {
                        "action": "unsubscribe",
                        "provider": "alpaca",
                        "feed": "bars",
                        "symbols": [symbol],
                    }
                )
            )

        return summary
    finally:
        with suppress(Exception):
            await websocket.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run live Gateway contract probe.")
    parser.add_argument("--gateway-url", default=system_settings.data_gateway_url or "http://localhost:8080")
    parser.add_argument("--api-key", default=system_settings.data_gateway_api_key or "")
    parser.add_argument("--symbol", default="AAPL")
    parser.add_argument("--health-retries", type=int, default=3)
    parser.add_argument("--health-retry-delay-seconds", type=float, default=1.0)
    parser.add_argument("--health-timeout-seconds", type=float, default=5.0)
    parser.add_argument("--receive-timeout-seconds", type=float, default=5.0)
    parser.add_argument("--data-wait-seconds", type=float, default=10.0)
    return parser


async def _run_cli() -> None:
    args = _build_parser().parse_args()
    summary = await run_gateway_contract_probe(
        gateway_url=args.gateway_url,
        api_key=args.api_key,
        symbol=args.symbol,
        health_retries=args.health_retries,
        health_retry_delay_seconds=args.health_retry_delay_seconds,
        health_timeout_seconds=args.health_timeout_seconds,
        receive_timeout_seconds=args.receive_timeout_seconds,
        data_wait_seconds=args.data_wait_seconds,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_run_cli())
