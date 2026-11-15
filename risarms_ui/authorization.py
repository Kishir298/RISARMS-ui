"""Central service authorization shared by desktop pages and tests."""
from __future__ import annotations

SERVICES = ("workspace", "asis", "ascs", "tiviss", "rescs")


def service_allowed(service: str, *, owner: bool, profile: str, store) -> bool:
    if owner:
        return True
    if not profile:
        return False
    return bool(store.service_allowed(profile, service))


def available_services(*, owner: bool, profile: str, store) -> tuple:
    """Return the service choices visible to this account."""
    if owner:
        return SERVICES
    return tuple(
        service for service in SERVICES
        if service == "workspace" or service_allowed(
            service, owner=False, profile=profile, store=store
        )
    )
