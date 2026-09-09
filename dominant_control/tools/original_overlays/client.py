"""Small HTTP client used by original-overlay renderer processes."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


class OverlayBridgeClient:
    def __init__(self, tool_id: str | None = None) -> None:
        self.host = "127.0.0.1"
        self.port = int(os.environ["DOMINANT_CONTROL_OVERLAY_BRIDGE_PORT"])
        self.token = os.environ["DOMINANT_CONTROL_OVERLAY_BRIDGE_TOKEN"]
        self.tool_id = tool_id or os.environ["DOMINANT_CONTROL_OVERLAY_TOOL"]
        # All requests are loopback-only.  A short timeout keeps an orphaned
        # renderer responsive if the main Dominant Control process is closed.
        self.timeout = 0.15

    def _request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        body = None
        headers = {"X-Dominant-Control-Token": self.token}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = urllib.request.Request(
            f"http://{self.host}:{self.port}{path}",
            data=body,
            headers=headers,
            method="POST" if body is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                value = json.loads(response.read().decode("utf-8"))
                return value if isinstance(value, dict) else None
        except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError):
            return None

    def state(self) -> dict[str, Any] | None:
        response = self._request(f"/state/{self.tool_id}")
        if not response or not response.get("ok"):
            return None
        return response

    def command(self, action: str, **values: Any) -> bool:
        response = self._request(
            f"/command/{self.tool_id}",
            {"action": action, **values},
        )
        return bool(response and response.get("ok"))


__all__ = ["OverlayBridgeClient"]
