"""``risarms-ui --doctor`` dependency reporting."""
import json

from risarms_ui import entrypoint, paths


def test_doctor_reports_desktop_dependencies_without_core_client(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(paths, "config_directory", lambda: tmp_path / "config")
    monkeypatch.setattr(paths, "data_directory", lambda: tmp_path / "data")

    exit_code = entrypoint._doctor()
    payload = json.loads(capsys.readouterr().out)

    assert "core-client" not in payload
    assert payload["application"] == "RISARMS Portal"
    assert exit_code in (0, 1)
    assert payload["result"] in ("ready",) or payload["result"].startswith("not ready:")
