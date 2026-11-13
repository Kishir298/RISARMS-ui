import pytest

from risarms_ui.connection.api import APIError, RemoteAPI, validate_origin


@pytest.mark.parametrize(
    "value",
    ["", "core.example", "ftp://core.example", "https://user:pass@core.example",
     "https://core.example/path", "http://core.example"],
)
def test_invalid_origins(value):
    with pytest.raises(ValueError):
        validate_origin(value)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://core.example", "https://core.example"),
        ("https://core.example/", "https://core.example"),
        ("http://127.0.0.1:8780", "http://127.0.0.1:8780"),
        ("http://localhost:8780", "http://localhost:8780"),
    ],
)
def test_valid_origins(value, expected):
    assert validate_origin(value) == expected


def test_api_rejects_non_api_paths():
    api = RemoteAPI("https://core.example")
    with pytest.raises(ValueError):
        api.request("GET", "/etc/passwd")


def test_error_properties():
    error = APIError("no", 401)
    assert error.authentication_expired and error.forbidden and not error.conflict
    conflict = APIError("conflict", 409)
    assert conflict.conflict and not conflict.authentication_expired
