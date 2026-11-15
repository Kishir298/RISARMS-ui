"""Cached service grants are scoped, expiring and offline-safe."""
from __future__ import annotations

import time

from risarms_ui.authorization import available_services, service_allowed
from risarms_ui.store import LocalStore

PASSWORD = "long-enough-passphrase"


def make_store(tmp_path):
    store = LocalStore(tmp_path / "data")
    profile = store.create("tester", PASSWORD)
    return store, profile


def test_cached_grant_allows_only_listed_services(tmp_path):
    store, profile = make_store(tmp_path)
    try:
        store.cache_service_grants(profile, ["asis", "ascs"])
        assert store.service_allowed(profile, "asis") is True
        assert store.service_allowed(profile, "ascs") is True
        assert store.service_allowed(profile, "tiviss") is False
        assert store.service_grants(profile) == {"asis": True, "ascs": True}
        assert available_services(owner=False, profile=profile, store=store) == (
            "workspace", "asis", "ascs",
        )
        assert available_services(owner=True, profile=profile, store=store) == (
            "workspace", "asis", "ascs", "tiviss", "rescs",
        )
    finally:
        store.close()


def test_cached_grant_expires(tmp_path):
    store, profile = make_store(tmp_path)
    try:
        store.cache_service_grants(profile, ["ascs"], ttl=1)
        assert store.service_allowed(profile, "ascs") is True
        assert store.service_allowed(profile, "ascs", now=time.time() + 2) is False
    finally:
        store.close()


def test_owner_is_always_allowed_and_member_needs_cache(tmp_path):
    store, profile = make_store(tmp_path)
    try:
        assert service_allowed("tiviss", owner=True, profile=profile, store=store) is True
        assert service_allowed("tiviss", owner=False, profile=profile, store=store) is False
        store.cache_service_grants(profile, ["tiviss"])
        assert service_allowed("tiviss", owner=False, profile=profile, store=store) is True
    finally:
        store.close()
