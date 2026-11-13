from risarms_ui.connection.api import APIError
from risarms_ui.connection.manager import ConnectionManager
from risarms_ui.connection.states import ConnectionState
from risarms_ui.security.credentials import CredentialStore


class FailingBackend:
    def get_password(self, *args):
        return None

    def set_password(self, *args):
        return None

    def delete_password(self, *args):
        return None


class FakeAPI:
    def __init__(self, url, profile=None, fail_login=None, fail_health=None):
        self.url = url
        self.profile = profile or {"id": "u1", "role": "owner", "name": "Owner"}
        self.fail_login = fail_login
        self.fail_health = fail_health
        self.csrf = "csrf"
        self.ticket_requests = 0
        self.closed = False

    def login(self, username, password):
        if self.fail_login:
            raise self.fail_login
        return {"profile": self.profile, "csrf": "csrf", "device_credential": "device-token"}

    def login_device(self, credential):
        if self.fail_login:
            raise self.fail_login
        return {"profile": self.profile, "csrf": "csrf"}

    def ws_ticket(self):
        self.ticket_requests += 1
        return {"ticket": "single-use"}

    def health(self):
        if self.fail_health:
            raise self.fail_health
        return {"service": "CORE"}

    def renew(self):
        return {"csrf": "renewed", "profile": self.profile}

    def logout(self):
        pass

    def close(self):
        self.closed = True


def make_manager(api):
    states = []
    manager = ConnectionManager(
        api_factory=lambda url: api,
        credentials=CredentialStore(backend=FailingBackend()),
        on_state=states.append,
        heartbeat_interval=0.05,
        sleep=lambda seconds: None,
        enable_events=False,
    )
    return manager, states


def test_login_success_stores_device_credential_and_reports_online():
    api = FakeAPI("https://core.example")
    manager, states = make_manager(api)
    profile = manager.connect("https://core.example", "owner", "password")
    assert profile["role"] == "owner"
    assert manager.state is ConnectionState.ONLINE
    assert ConnectionState.CONNECTING in states
    assert states[-1] is ConnectionState.ONLINE
    assert manager.credentials.get("https://core.example", "u1") == "device-token"
    manager.close()


def test_rejected_login_marks_authentication_rejected():
    api = FakeAPI("https://core.example", fail_login=APIError("bad", 401))
    manager, states = make_manager(api)
    try:
        manager.connect("https://core.example", "owner", "wrong")
    except APIError:
        pass
    assert manager.state is ConnectionState.AUTH_REJECTED
    manager.close()


def test_pending_login_marks_pending_approval():
    api = FakeAPI("https://core.example", profile={"id": "u1", "role": "pending", "name": "New"})
    manager, _ = make_manager(api)
    manager.connect("https://core.example", "new", "password")
    assert manager.state is ConnectionState.PENDING_APPROVAL
    manager.close()


def test_unavailable_server_marks_server_unavailable():
    api = FakeAPI("https://core.example", fail_login=APIError("down", 0))
    manager, _ = make_manager(api)
    try:
        manager.connect("https://core.example", "owner", "password")
    except APIError:
        pass
    assert manager.state is ConnectionState.SERVER_UNAVAILABLE
    manager.close()


def test_disconnect_is_clean_and_idempotent():
    api = FakeAPI("https://core.example")
    manager, _ = make_manager(api)
    manager.connect("https://core.example", "owner", "password")
    manager.disconnect()
    manager.disconnect()
    assert manager.state is ConnectionState.OFFLINE
    assert api.closed
