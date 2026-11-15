"""Owner action summaries must fully describe the pending change."""
from __future__ import annotations

import inspect
import json
from pathlib import Path

from risarms_ui import owner_actions
from risarms_ui.owner_actions import format_bytes, summarize_action


def payload_from(summary):
    return json.loads(summary.split("Full payload JSON:\n", 1)[1])


def test_service_grant_summary_includes_target_service_and_full_payload():
    payload = {"profile_id": "profile-123", "service": "ascs"}
    summary = summarize_action({
        "id": "request-grant",
        "requested_by": "requester-456",
        "action": "service_grant",
        "payload": payload,
        "status": "pending",
    })

    assert "Grant service" in summary
    assert "service_grant" in summary
    assert "requester-456" in summary
    assert "profile-123" in summary
    assert "ascs" in summary
    assert payload_from(summary) == payload


def test_service_revoke_summary_includes_target_service_and_full_payload():
    payload = {"profile_id": "profile-789", "service": "rescs"}
    summary = summarize_action({
        "id": "request-revoke",
        "requested_by": "requester-456",
        "action": "service_revoke",
        "payload": payload,
        "status": "pending",
    })

    assert "Revoke service" in summary
    assert "service_revoke" in summary
    assert "profile-789" in summary
    assert "rescs" in summary
    assert payload_from(summary) == payload


def test_quota_summary_includes_account_and_exact_mib_and_gib_values():
    mib_payload = {"username": "member-one", "bytes": 16 * 1024 * 1024}
    mib_summary = summarize_action({
        "id": "request-quota",
        "requested_by": "requester-456",
        "action": "quota_set",
        "payload": mib_payload,
        "status": "pending",
    })

    assert "Set storage quota" in mib_summary
    assert "member-one" in mib_summary
    assert "16 MiB (16777216 bytes)" in mib_summary
    assert payload_from(mib_summary) == mib_payload

    gib_payload = {"username": "member-two", "bytes": 3 * 1024 * 1024 * 1024}
    gib_summary = summarize_action({
        "id": "request-quota-gib",
        "requested_by": "requester-456",
        "action": "quota_set",
        "payload": gib_payload,
        "status": "pending",
    })

    assert "member-two" in gib_summary
    assert "3 GiB (3221225472 bytes)" in gib_summary
    assert payload_from(gib_summary) == gib_payload


def test_malformed_payloads_still_render_the_full_payload_without_crashing():
    non_object = summarize_action({
        "id": "request-malformed",
        "requested_by": "requester-456",
        "action": "quota_set",
        "payload": ["not", "an", "object"],
        "status": "pending",
    })
    assert "Malformed payload" in non_object
    assert payload_from(non_object) == ["not", "an", "object"]

    missing_field = summarize_action({
        "id": "request-missing",
        "requested_by": "requester-456",
        "action": "service_grant",
        "payload": {"profile_id": "profile-123"},
        "status": "pending",
    })
    assert "missing service" in missing_field
    assert payload_from(missing_field) == {"profile_id": "profile-123"}

    invalid_size = summarize_action({
        "id": "request-invalid-size",
        "requested_by": "requester-456",
        "action": "quota_set",
        "payload": {"username": "member-one", "bytes": "lots"},
        "status": "pending",
    })
    assert "invalid size" in invalid_size
    assert payload_from(invalid_size) == {"username": "member-one", "bytes": "lots"}

    assert "Malformed payload" in summarize_action(None)


def test_format_bytes_keeps_exact_value_for_fractions_and_zero():
    assert format_bytes(0) == "0 MiB (0 bytes)"
    assert format_bytes(1536 * 1024) == "1.5 MiB (1572864 bytes)"
    assert format_bytes(1024 ** 3) == "1 GiB (1073741824 bytes)"


def test_helper_module_does_not_import_tkinter():
    source = Path(inspect.getfile(owner_actions)).read_text(encoding="utf-8")
    assert "tkinter" not in source
