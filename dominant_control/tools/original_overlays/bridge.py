"""Local, authenticated state bridge for separately rendered overlays."""

from __future__ import annotations

import json
import secrets
import threading
from collections import defaultdict, deque
from dataclasses import asdict, is_dataclass
from enum import Enum
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse


MAX_COMMAND_BYTES = 64 * 1024


def json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class _BridgeHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, handler, bridge: "OverlayStateBridge"):
        self.bridge = bridge
        super().__init__(address, handler)


class _BridgeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    @property
    def bridge(self) -> "OverlayStateBridge":
        return self.server.bridge  # type: ignore[attr-defined]

    def _authorized(self) -> bool:
        return secrets.compare_digest(
            self.headers.get("X-Dominant-Control-Token", ""),
            self.bridge.token,
        )

    def _tool_from_path(self, prefix: str) -> str:
        path = urlparse(self.path).path
        if not path.startswith(prefix):
            return ""
        return unquote(path[len(prefix):]).strip("/")

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802 - HTTP API
        if not self._authorized():
            self._write_json(403, {"ok": False, "error": "forbidden"})
            return
        tool_id = self._tool_from_path("/state/")
        payload = self.bridge.read(tool_id)
        if payload is None:
            self._write_json(404, {"ok": False, "error": "unknown tool"})
            return
        self._write_json(200, {"ok": True, **payload})

    def do_POST(self) -> None:  # noqa: N802 - HTTP API
        if not self._authorized():
            self._write_json(403, {"ok": False, "error": "forbidden"})
            return
        tool_id = self._tool_from_path("/command/")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_COMMAND_BYTES:
            self._write_json(400, {"ok": False, "error": "invalid length"})
            return
        try:
            command = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._write_json(400, {"ok": False, "error": "invalid json"})
            return
        if not isinstance(command, dict) or not self.bridge.enqueue(tool_id, command):
            self._write_json(404, {"ok": False, "error": "unknown tool"})
            return
        self._write_json(200, {"ok": True})


class OverlayStateBridge:
    """Keep tool state in memory and expose read-only snapshots to overlays."""

    def __init__(self) -> None:
        self.token = secrets.token_urlsafe(32)
        self._lock = threading.RLock()
        self._states: dict[str, dict[str, Any]] = {}
        self._commands: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
        self._show_generation: dict[str, int] = defaultdict(int)
        self._registrations: dict[str, int] = defaultdict(int)
        self._server: _BridgeHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        server = self._server
        return int(server.server_address[1]) if server is not None else 0

    def register(self, tool_id: str) -> None:
        tool_id = str(tool_id)
        with self._lock:
            self._registrations[tool_id] += 1
            self._states.setdefault(tool_id, {})
        self.start()

    def unregister(self, tool_id: str) -> None:
        with self._lock:
            remaining = self._registrations.get(tool_id, 0) - 1
            if remaining > 0:
                self._registrations[tool_id] = remaining
                return
            self._registrations.pop(tool_id, None)
            self._states.pop(tool_id, None)
            self._commands.pop(tool_id, None)
            self._show_generation.pop(tool_id, None)
            should_stop = not self._registrations
        if should_stop:
            self.stop()

    def start(self) -> None:
        with self._lock:
            if self._server is not None:
                return
            server = _BridgeHTTPServer(("127.0.0.1", 0), _BridgeHandler, self)
            thread = threading.Thread(
                target=server.serve_forever,
                name="DominantControl-OverlayBridge",
                daemon=True,
            )
            self._server = server
            self._thread = thread
            thread.start()

    def stop(self) -> None:
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.5)

    def publish(self, tool_id: str, state: dict[str, Any]) -> None:
        snapshot = json_safe(state)
        with self._lock:
            if tool_id in self._states:
                self._states[tool_id] = snapshot

    def read(self, tool_id: str) -> dict[str, Any] | None:
        with self._lock:
            if tool_id not in self._states:
                return None
            return {
                "state": self._states[tool_id],
                "show_generation": self._show_generation[tool_id],
            }

    def enqueue(self, tool_id: str, command: dict[str, Any]) -> bool:
        with self._lock:
            if tool_id not in self._states:
                return False
            queue = self._commands[tool_id]
            if len(queue) >= 100:
                queue.popleft()
            queue.append(json_safe(command))
            return True

    def take_commands(self, tool_id: str) -> list[dict[str, Any]]:
        with self._lock:
            queue = self._commands[tool_id]
            commands = list(queue)
            queue.clear()
            return commands

    def request_show(self, tool_id: str) -> None:
        with self._lock:
            self._show_generation[tool_id] += 1


def get_overlay_bridge(telemetry: Any) -> OverlayStateBridge:
    bridge = getattr(telemetry, "_original_overlay_bridge", None)
    if bridge is None:
        bridge = OverlayStateBridge()
        setattr(telemetry, "_original_overlay_bridge", bridge)
    return bridge


__all__ = ["OverlayStateBridge", "get_overlay_bridge", "json_safe"]

