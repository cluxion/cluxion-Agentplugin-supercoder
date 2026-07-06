from __future__ import annotations

import json

from cluxion_agentplugin_supercoder import cli


def test_plan_cli_reads_json_stdin(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "sys.stdin",
        type("Stdin", (), {"read": lambda self: json.dumps({"prompt": "fix tests", "cwd": str(tmp_path)})})(),
    )

    assert cli.main(["plan", "--json-stdin"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["mode"] == "coding_queue"


def test_cli_reports_bad_json_stdin(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin", type("Stdin", (), {"read": lambda self: "not json"})())

    assert cli.main(["plan", "--json-stdin"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "invalid_json"
    assert "invalid JSON" in payload["message"]
    assert payload["hint"]


def test_cli_reports_deeply_nested_json_stdin(monkeypatch, capsys) -> None:
    # adversarial: ~10k nesting must give a clean error, not a raw RecursionError traceback
    deep = "{\"x\":" + "[" * 10000 + "]" * 10000 + "}"
    monkeypatch.setattr("sys.stdin", type("Stdin", (), {"read": lambda self: deep})())

    assert cli.main(["plan", "--json-stdin"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "invalid_json"
    assert "nesting too deep" in payload["message"]
