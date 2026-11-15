"""Central service authorization shared by desktop pages and tests."""
from __future__ import annotations


def service_allowed(service: str, *, owner: bool, profile: str, store) -> bool:
    if owner:
        return True
    if not profile:
        return False
    return bool(store.service_allowed(profile, service))
