from __future__ import annotations

import json

import pytest

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
    deep = '{"x":' + "[" * 10000 + "]" * 10000 + "}"
    monkeypatch.setattr("sys.stdin", type("Stdin", (), {"read": lambda self: deep})())

    assert cli.main(["plan", "--json-stdin"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "invalid_json"
    assert "nesting too deep" in payload["message"]


@pytest.mark.parametrize("command", ["plan", "patch", "brief"])
def test_cli_reports_invalid_utf8_stdin(monkeypatch, capsys, command) -> None:
    # adversarial: invalid UTF-8 bytes make sys.stdin.read() itself raise UnicodeDecodeError
    def read(self):
        raise UnicodeDecodeError("utf-8", b"\xff\xfe{\x80", 0, 1, "invalid start byte")

    monkeypatch.setattr("sys.stdin", type("Stdin", (), {"read": read})())

    assert cli.main([command, "--json-stdin"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "invalid_json"
    assert "not valid UTF-8" in payload["message"]
    assert payload["hint"]
