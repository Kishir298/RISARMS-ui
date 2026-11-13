import pytest

from risarms_ui.security.credentials import CredentialStore


class FakeBackend:
    def __init__(self):
        self.values = {}

    def get_password(self, service, key):
        return self.values.get((service, key))

    def set_password(self, service, key, value):
        self.values[(service, key)] = value

    def delete_password(self, service, key):
        self.values.pop((service, key), None)


def test_store_roundtrip_and_delete():
    backend = FakeBackend()
    store = CredentialStore(backend=backend)
    assert store.set("https://core.example", "user-1", "secret-token") is True
    assert store.get("https://core.example", "user-1") == "secret-token"
    store.delete("https://core.example", "user-1")
    assert store.get("https://core.example", "user-1") is None


def test_store_requires_all_fields():
    store = CredentialStore(backend=FakeBackend())
    with pytest.raises(ValueError):
        store.set("", "user", "token")
    assert store.get("", "user") is None


def test_memory_fallback_never_persists():
    class Broken:
        def get_password(self, *args):
            raise RuntimeError("unavailable")

        def set_password(self, *args):
            raise RuntimeError("unavailable")

        def delete_password(self, *args):
            raise RuntimeError("unavailable")

    store = CredentialStore(backend=Broken())
    assert store.set("https://core.example", "user", "token") is False
    assert store.get("https://core.example", "user") == "token"
    assert store.persistent is False
