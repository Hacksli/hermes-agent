"""
YouSelf Gateway Transport — adapter for the youself.io platform.

Supports three polling modes:
  - long_poll  (default): GET /updates?offset=N&timeout=30
  - websocket:            ws://{host}/vm-connect?token={token}
  - sse:                  GET /stream  (text/event-stream)

Auto-detection:
  - Uses long_poll by default.
  - On HTTP 409 from /updates, switches to SSE mode.

Environment:
  YOUSELF_GATEWAY_URL    Base URL of the youself.io gateway (no trailing slash)
  YOUSELF_GATEWAY_TOKEN  Bearer token for authentication

The helper ``load_youself_env()`` reads /etc/openclaw/env and injects its
key=value pairs into os.environ before any transport instance is created.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Callable, Optional
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Env loader
# ---------------------------------------------------------------------------

def load_youself_env(path: str = "/etc/openclaw/env") -> None:
    """Read *path* (key=value, lines starting with # ignored) and inject
    the entries into ``os.environ``.  Missing file is silently ignored."""
    if not os.path.isfile(path):
        return
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Strip optional surrounding quotes
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                os.environ.setdefault(key, value)


# ---------------------------------------------------------------------------
# Transport class
# ---------------------------------------------------------------------------

class YouSelfGatewayTransport:
    """Gateway transport for the youself.io AI-VM platform.

    Parameters
    ----------
    handler:
        Async-or-sync callable that receives a single update dict and returns
        an optional response string (or None).  The transport posts the reply
        via ``POST {gateway_url}/messages``.
    mode:
        ``"long_poll"`` (default), ``"websocket"``, or ``"sse"``.
    gateway_url:
        Overrides ``YOUSELF_GATEWAY_URL`` env var.
    token:
        Overrides ``YOUSELF_GATEWAY_TOKEN`` env var.
    """

    #: seconds between retry attempts (doubles on each failure up to max)
    _BACKOFF_BASE = 1.0
    _BACKOFF_MAX = 60.0

    def __init__(
        self,
        handler: Callable[[dict], Optional[str]],
        *,
        mode: str = "long_poll",
        gateway_url: Optional[str] = None,
        token: Optional[str] = None,
    ) -> None:
        load_youself_env()

        self.gateway_url = (
            gateway_url
            or os.environ.get("YOUSELF_GATEWAY_URL", "").rstrip("/")
        )
        self.token = token or os.environ.get("YOUSELF_GATEWAY_TOKEN", "")
        self.handler = handler
        self.mode = mode
        self._offset: int = 0
        self._running = False
        # Persist last stream_id across restarts to avoid replaying old messages
        self._state_file = os.path.join(
            os.path.expanduser("~"), ".hermes", "youself_offset.txt"
        )
        self._last_stream_id = self._load_offset()

        if not self.gateway_url:
            raise ValueError(
                "YOUSELF_GATEWAY_URL must be set (env or gateway_url kwarg)"
            )
        if not self.token:
            raise ValueError(
                "YOUSELF_GATEWAY_TOKEN must be set (env or token kwarg)"
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _load_offset(self) -> str:
        """Load last stream_id from disk, default to 0 (fetch all)."""
        try:
            if os.path.isfile(self._state_file):
                val = open(self._state_file).read().strip()
                if val:
                    return val
        except Exception:
            pass
        return "0"

    def _save_offset(self, stream_id: str) -> None:
        """Persist last stream_id to disk."""
        try:
            os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
            with open(self._state_file, "w") as f:
                f.write(stream_id)
        except Exception:
            pass

    def run(self) -> None:
        """Block and serve updates.  Runs until stopped or a fatal error."""
        self._running = True
        logger.info("YouSelf gateway transport starting in %s mode", self.mode)
        try:
            if self.mode == "long_poll":
                self._run_long_poll()
            elif self.mode == "websocket":
                self._run_websocket()
            elif self.mode == "sse":
                self._run_sse()
            else:
                raise ValueError(f"Unknown mode: {self.mode!r}")
        finally:
            self._running = False
            logger.info("YouSelf gateway transport stopped")

    def stop(self) -> None:
        """Signal the transport loop to exit."""
        self._running = False

    # ------------------------------------------------------------------
    # Long-poll mode
    # ------------------------------------------------------------------

    def _run_long_poll(self) -> None:
        import urllib.request
        import urllib.error

        backoff = self._BACKOFF_BASE
        while self._running:
            url = (
                f"{self.gateway_url}/updates"
                f"?offset={self._last_stream_id}&timeout=30"
            )
            req = urllib.request.Request(
                url,
                headers={"Authorization": f"Bearer {self.token}"},
            )
            try:
                with urllib.request.urlopen(req, timeout=40) as resp:
                    status = resp.status
                    body = resp.read().decode("utf-8")

                if status == 200:
                    backoff = self._BACKOFF_BASE  # reset on success
                    updates = json.loads(body) if body else []
                    if isinstance(updates, dict):
                        # Some gateways wrap in {"updates": [...], "offset": N}
                        updates_list = updates.get("updates", [updates])
                        if "offset" in updates:
                            self._offset = updates["offset"]
                    else:
                        updates_list = updates

                    for item in updates_list:
                        # Unwrap {"stream_id": "...", "update": {...}} envelope
                        sid = item.get("stream_id")
                        update = item.get("update", item)  # unwrap or use as-is
                        self._handle_and_reply(update)
                        if sid:
                            last_stream_id = sid
                    if last_stream_id:
                        self._last_stream_id = last_stream_id
                        self._save_offset(last_stream_id)

                elif status == 409:
                    logger.warning(
                        "409 Conflict from /updates — switching to SSE mode"
                    )
                    self.mode = "sse"
                    self._run_sse()
                    return

                else:
                    logger.warning("Unexpected status %s from /updates", status)
                    self._sleep_backoff(backoff)
                    backoff = min(backoff * 2, self._BACKOFF_MAX)

            except urllib.error.HTTPError as exc:
                if exc.code == 409:
                    logger.warning(
                        "409 Conflict from /updates — switching to SSE mode"
                    )
                    self.mode = "sse"
                    self._run_sse()
                    return
                logger.error("HTTP error polling /updates: %s", exc)
                self._sleep_backoff(backoff)
                backoff = min(backoff * 2, self._BACKOFF_MAX)

            except Exception as exc:  # noqa: BLE001
                logger.error("Error polling /updates: %s", exc)
                self._sleep_backoff(backoff)
                backoff = min(backoff * 2, self._BACKOFF_MAX)

    # ------------------------------------------------------------------
    # WebSocket mode
    # ------------------------------------------------------------------

    def _run_websocket(self) -> None:
        try:
            import websocket  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "websocket-client is required for WebSocket mode: "
                "pip install websocket-client"
            ) from exc

        parsed = urlparse(self.gateway_url)
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        ws_host = parsed.netloc
        ws_url = f"{ws_scheme}://{ws_host}/vm-connect?token={self.token}"

        backoff = self._BACKOFF_BASE

        while self._running:
            try:
                logger.info("Connecting WebSocket: %s", ws_url)
                ws = websocket.create_connection(ws_url, timeout=30)
                backoff = self._BACKOFF_BASE
                try:
                    while self._running:
                        raw = ws.recv()
                        if not raw:
                            continue
                        try:
                            update = json.loads(raw)
                        except json.JSONDecodeError:
                            logger.warning("WS non-JSON frame: %r", raw[:200])
                            continue
                        self._handle_and_reply(update)
                finally:
                    ws.close()
            except Exception as exc:  # noqa: BLE001
                logger.error("WebSocket error: %s", exc)
                self._sleep_backoff(backoff)
                backoff = min(backoff * 2, self._BACKOFF_MAX)

    # ------------------------------------------------------------------
    # SSE mode
    # ------------------------------------------------------------------

    def _run_sse(self) -> None:
        import urllib.request
        import urllib.error

        url = f"{self.gateway_url}/stream"
        backoff = self._BACKOFF_BASE

        while self._running:
            req = urllib.request.Request(
                url,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": "text/event-stream",
                    "Cache-Control": "no-cache",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=None) as resp:
                    backoff = self._BACKOFF_BASE
                    event_data: list[str] = []
                    for raw_line in resp:
                        if not self._running:
                            break
                        line = raw_line.decode("utf-8", errors="replace").rstrip("\n\r")
                        if line.startswith("data:"):
                            event_data.append(line[5:].lstrip(" "))
                        elif line == "" and event_data:
                            payload = "\n".join(event_data)
                            event_data = []
                            if payload == "[DONE]":
                                continue
                            try:
                                update = json.loads(payload)
                            except json.JSONDecodeError:
                                logger.warning(
                                    "SSE non-JSON payload: %r", payload[:200]
                                )
                                continue
                            self._handle_and_reply(update)

            except Exception as exc:  # noqa: BLE001
                logger.error("SSE stream error: %s", exc)
                self._sleep_backoff(backoff)
                backoff = min(backoff * 2, self._BACKOFF_MAX)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _handle_and_reply(self, update: dict) -> None:
        """Call handler with *update*, then POST the reply if one is returned."""
        try:
            reply = self.handler(update)
        except Exception as exc:  # noqa: BLE001
            logger.error("Handler raised an exception: %s", exc)
            return

        if reply is None:
            return

        chat_id = (
            update.get("chat_id")
            or update.get("channel_id")
            or (update.get("message") or {}).get("chat", {}).get("id")
        )
        self._post_message(reply, chat_id=chat_id, update=update)

    def _post_message(
        self,
        text: str,
        *,
        chat_id=None,
        update: Optional[dict] = None,
    ) -> None:
        import urllib.request
        import urllib.error

        payload: dict = {"text": text}
        if chat_id is not None:
            payload["chat_id"] = chat_id
        if update is not None:
            update_id = update.get("update_id") or update.get("id")
            if update_id is not None:
                payload["reply_to_update_id"] = update_id

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.gateway_url}/messages/send",
            data=data,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status not in (200, 201, 202, 204):
                    logger.warning(
                        "POST /messages returned %s", resp.status
                    )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to POST reply to /messages: %s", exc)

    def _sleep_backoff(self, seconds: float) -> None:
        """Sleep up to *seconds*, honouring stop requests early."""
        deadline = time.monotonic() + seconds
        while self._running and time.monotonic() < deadline:
            time.sleep(0.25)
