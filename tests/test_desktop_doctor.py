"""``risarms-ui --doctor`` dependency reporting.

The repository also contains a legacy ``client`` package, so a doctor that
simply imported ``client`` could never notice that CORE-CLIENT is missing.
"""
import json
from importlib import metadata

from risarms_ui import entrypoint, paths


def test_doctor_reports_the_installed_core_client_distribution(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(paths, "config_directory", lambda: tmp_path / "config")
    monkeypatch.setattr(paths, "data_directory", lambda: tmp_path / "data")

    exit_code = entrypoint._doctor()
    payload = json.loads(capsys.readouterr().out)

    # The reported value comes from distribution metadata, not from the local
    # lookalike package that would always import successfully.
    assert payload["core-client"] == metadata.version("core-client")
    assert payload["core-client"] != "installed"
    assert exit_code in (0, 1)
    assert payload["result"] in ("ready",) or payload["result"].startswith("not ready:")
