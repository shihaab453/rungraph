"""Machine-readable adapter inventory."""

from __future__ import annotations

import json

from runevidence.cli import main


def test_adapters_json_lists_every_installed_adapter(capsys):
    assert main(["adapters", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["format"] == "runevidence-adapters-v1"
    names = [item["name"] for item in data["adapters"]]
    assert names == sorted(names)
    assert "generic" in names
    generic = next(item for item in data["adapters"] if item["name"] == "generic")
    assert generic["module"] == "runevidence.adapters.generic"
    assert generic["description"]
