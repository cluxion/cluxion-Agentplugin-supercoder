from __future__ import annotations

import json
import sys

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
    # regression: Python 3.14+ json.loads accepts deep nesting; SC still enforces max depth
    deep = '{"x":' + "[" * 10000 + "]" * 10000 + "}"
    monkeypatch.setattr("sys.stdin", type("Stdin", (), {"read": lambda self: deep})())

    assert cli.main(["plan", "--json-stdin"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "invalid_json"
    assert "nesting too deep" in payload["message"]


def test_cli_accepts_json_stdin_at_max_container_depth(tmp_path, monkeypatch, capsys) -> None:
    # boundary: root (depth 1) + (MAX-1) nested lists under "pad" => depth MAX accepted
    pad: object = 0
    for _ in range(cli.MAX_JSON_CONTAINER_DEPTH - 1):
        pad = [pad]
    body = json.dumps({"prompt": "fix tests", "cwd": str(tmp_path), "pad": pad})
    monkeypatch.setattr("sys.stdin", type("Stdin", (), {"read": lambda self, b=body: b})())

    assert cli.main(["plan", "--json-stdin"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True


def test_cli_rejects_json_stdin_beyond_max_container_depth(monkeypatch, capsys) -> None:
    # boundary: 129 nested dict containers must be rejected with the structured nesting error
    nested = "{}"
    for _ in range(cli.MAX_JSON_CONTAINER_DEPTH):
        nested = '{"x":' + nested + "}"
    monkeypatch.setattr("sys.stdin", type("Stdin", (), {"read": lambda self, n=nested: n})())

    assert cli.main(["plan", "--json-stdin"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "invalid_json"
    assert "nesting too deep" in payload["message"]
    assert payload["hint"]


def test_cli_rejects_mixed_dict_list_nesting_beyond_max_depth(monkeypatch, capsys) -> None:
    # mixed dict/list containers still count toward the depth limit (129 levels)
    node: object = 0
    for i in range(cli.MAX_JSON_CONTAINER_DEPTH + 1):
        node = [node] if i % 2 == 0 else {"x": node}
    monkeypatch.setattr(
        "sys.stdin",
        type("Stdin", (), {"read": lambda self, n=node: json.dumps(n)})(),
    )

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


def test_check_fails_for_forced_missing_subprocess_backend(monkeypatch, capsys) -> None:
    """Forced subprocess with a missing binary must report rust_index:false and rc 1."""
    from cluxion_agentplugin_supercoder import rust_bridge

    monkeypatch.setenv(rust_bridge.INDEX_BACKEND_ENV, "subprocess")
    monkeypatch.setenv(rust_bridge.INDEX_BIN_ENV, "/nonexistent/cluxion-missing-supercoder-index")

    rc = cli.main(["check"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["rust_index"] is False
    assert payload["index_backend"] == "subprocess"


def test_check_fails_for_forced_nonfunctional_subprocess_backend(monkeypatch, capsys) -> None:
    """Forced subprocess path that exists but is nonfunctional must not claim rust_index:true.

    Presence-only health (shutil.which / file exists) is insufficient: the Python
    executable exists on every supported host but cannot serve the index protocol.
    """
    from cluxion_agentplugin_supercoder import rust_bridge

    monkeypatch.setenv(rust_bridge.INDEX_BACKEND_ENV, "subprocess")
    monkeypatch.setenv(rust_bridge.INDEX_BIN_ENV, sys.executable)

    rc = cli.main(["check"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["rust_index"] is False
    assert payload["index_backend"] == "subprocess"


def test_check_fails_for_forced_nonfunctional_native_backend(monkeypatch, capsys) -> None:
    """Forced native object that exists but cannot process_json must not claim rust_index:true.

    Import/object presence alone is insufficient: the selected backend must be
    operationally able to process JSON index commands.
    """
    from cluxion_agentplugin_supercoder import rust_bridge

    class NonfunctionalNative:
        """Importable stand-in whose actual run entry point is broken."""

        def run(self, *_args, **_kwargs):
            raise RuntimeError("native run is nonfunctional")

    monkeypatch.setenv(rust_bridge.INDEX_BACKEND_ENV, "native")
    monkeypatch.setattr(rust_bridge, "_load_native", lambda: NonfunctionalNative())

    rc = cli.main(["check"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["rust_index"] is False
    assert payload["index_backend"] == "native"


def test_check_fails_for_native_success_without_scan_entries(monkeypatch, capsys) -> None:
    """A protocol shell returning only ok=true is not an operational index."""
    from cluxion_agentplugin_supercoder import rust_bridge

    class IncompleteNative:
        @staticmethod
        def run(_command: str, _payload: str) -> str:
            return json.dumps({"ok": True})

    monkeypatch.setenv(rust_bridge.INDEX_BACKEND_ENV, "native")
    monkeypatch.setattr(rust_bridge, "_load_native", lambda: IncompleteNative())

    rc = cli.main(["check"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 1
    assert payload["rust_index"] is False
    assert payload["index_backend"] == "native"
