"""Human-readable presentation for queued owner actions.

The helpers in this module are pure presentation code and deliberately avoid
Tk so summaries can be reviewed and tested without a display server.
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

MIB = 1024 * 1024
GIB = 1024 * MIB

ACTION_LABELS = {
    "service_grant": "Grant service",
    "service_revoke": "Revoke service",
    "quota_set": "Set storage quota",
}


def format_bytes(value: Any) -> str:
    """Render a quota in MiB or GiB while keeping the exact byte count."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return f"invalid size ({value!r})"
    if not math.isfinite(value) or value < 0:
        return f"invalid size ({value!r})"
    size = int(value)
    if size >= GIB:
        amount, unit = size / GIB, "GiB"
    else:
        amount, unit = size / MIB, "MiB"
    text = f"{amount:.2f}".rstrip("0").rstrip(".")
    return f"{text} {unit} ({size} bytes)"


def payload_json(payload: Any) -> str:
    """Render the complete action payload as sorted, indented JSON."""
    try:
        return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(payload)


def _target(action_name: str, payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return "Malformed payload - no target can be determined."
    if action_name in ("service_grant", "service_revoke"):
        profile_id = payload.get("profile_id")
        service = payload.get("service")
        missing = [
            name for name, value in (("profile_id", profile_id), ("service", service))
            if value is None or value == ""
        ]
        if missing:
            return f"Malformed payload - missing {', '.join(missing)}."
        if action_name == "service_grant":
            return f"Grant service '{service}' to profile {profile_id}."
        return f"Revoke service '{service}' from profile {profile_id}."
    if action_name == "quota_set":
        username = payload.get("username")
        size = payload.get("bytes")
        missing = [name for name, value in (("username", username), ("bytes", size)) if value is None]
        if missing:
            return f"Malformed payload - missing {', '.join(missing)}."
        return f"Set storage quota for account '{username}' to {format_bytes(size)}."
    return "Unknown action - review the full payload before deciding."


def summarize_action(item: Mapping[str, Any] | Any) -> str:
    """Return the full multi-line review text shown before a decision."""
    if not isinstance(item, Mapping):
        item = {}
    action_name = str(item.get("action") or "unknown")
    label = ACTION_LABELS.get(action_name, "Unknown owner action")
    payload = item.get("payload")
    requested_by = item.get("requested_by") or "unknown requester"
    lines = [
        f"Action: {label} ({action_name})",
        f"Requested by: {requested_by}",
        f"Target: {_target(action_name, payload)}",
    ]
    request_id = item.get("id")
    if request_id:
        lines.append(f"Request ID: {request_id}")
    status = item.get("status")
    if status:
        lines.append(f"Status: {status}")
    error = item.get("error")
    if error:
        lines.append(f"Previous decision error: {error}")
    lines.extend(("", "Full payload JSON:", payload_json(payload)))
    return "\n".join(lines)
