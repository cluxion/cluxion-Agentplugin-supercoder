from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from importlib.resources import files
from pathlib import Path

from cluxion_agentplugin_supercoder import __version__, runner
from cluxion_agentplugin_supercoder.doctor import render_json, render_text, run_doctor
from cluxion_agentplugin_supercoder.doctor.probes import PROBES
from cluxion_agentplugin_supercoder.rust_bridge import index_available, resolve_backend

# Max dict/list nesting accepted on JSON stdin after json.loads.
# Independent of CPython's json recursion limit (which varies by version; 3.14 accepts ~10k).
MAX_JSON_CONTAINER_DEPTH = 128

_JSON_COMMANDS = {
    "plan": runner.plan,
    "read-window": runner.read_window_tool,
    "patch": runner.patch_tool,
    "cursor-map": runner.cursor_map_tool,
    "syntax-gate": runner.syntax_gate_tool,
    "lint-gate": runner.lint_gate_tool,
    "repo-map": runner.repo_map_tool,
    "test-gate": runner.test_gate_tool,
    "brief": runner.brief_tool,
}


def _json_container_depth_exceeded(value: object, max_depth: int = MAX_JSON_CONTAINER_DEPTH) -> bool:
    """Return True if any dict/list nesting exceeds max_depth (iterative; no recursion)."""
    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        if not isinstance(node, (dict, list)):
            continue
        if depth > max_depth:
            return True
        children = node.values() if isinstance(node, dict) else node
        next_depth = depth + 1
        for child in children:
            if isinstance(child, (dict, list)):
                stack.append((child, next_depth))
    return False


def _json_error(error: str, message: str, hint: str) -> str:
    return json.dumps(
        {"ok": False, "error": error, "message": message, "hint": hint},
        ensure_ascii=False,
        sort_keys=True,
    )


class _JsonArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args: object, json_mode: bool = False, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._json_mode = json_mode

    def error(self, message: str) -> None:
        if self._json_mode:
            print(_json_error("usage_error", message, "Check the command and JSON flag placement."))
            raise SystemExit(2)
        super().error(message)


def _parser_class(json_mode: bool):
    class Parser(_JsonArgumentParser):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, json_mode=json_mode, **kwargs)

    return Parser


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in raw_argv or "--json-stdin" in raw_argv
    parser = _JsonArgumentParser(prog="cluxion-supercoder", json_mode=json_mode)
    parser.add_argument("--version", action="version", version=f"cluxion-agentplugin-supercoder {__version__}")
    sub = parser.add_subparsers(dest="command", parser_class=_parser_class(json_mode))
    sub.add_parser("check", help="Check plugin and Rust index availability")
    doctor_p = sub.add_parser("doctor", help="Run embedded doctor checks")
    doctor_p.add_argument("--json", action="store_true", help="Output JSON to stdout")
    doctor_p.add_argument("--verbose", action="store_true", help="Verbose text output")
    for name in _JSON_COMMANDS:
        command_p = sub.add_parser(name, help=f"Run {name} JSON contract")
        command_p.add_argument("--json-stdin", action="store_true", help="Read request payload as JSON from stdin")
    try:
        args = parser.parse_args(raw_argv)
    except SystemExit as exc:
        if json_mode:
            return int(exc.code)
        raise
    if args.command == "check":
        available = index_available()
        payload = {
            "plugin": "cluxion-agentplugin-supercoder",
            "version": __version__,
            "rust_index": available,
            "index_backend": resolve_backend(),
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0 if available else 1
    if args.command == "doctor":
        catalog_path = files("cluxion_agentplugin_supercoder.doctor") / "catalog.json"
        result = run_doctor(
            cwd=Path.cwd(),
            catalog_path=Path(str(catalog_path)),
            probes=PROBES,
            plugin="supercoder",
            version=__version__,
        )
        if args.json:
            print(render_json(result))
        else:
            text = render_text(result, load_catalog_for_text(catalog_path), verbose=args.verbose)
            print(text, file=sys.stderr)
        return 0 if result.ok else 1
    if args.command in _JSON_COMMANDS:
        return _run_json_command(args.command, bool(args.json_stdin))
    parser.print_help(sys.stderr)
    return 2


def _run_json_command(command: str, json_stdin: bool) -> int:
    if not json_stdin:
        print(_json_error("usage_error", "--json-stdin is required", "Pass --json-stdin and a JSON object on stdin."))
        return 2
    try:
        raw = sys.stdin.read()
        # Text stdin may use surrogateescape: read() succeeds on invalid bytes.
        # Reject any text that is not strictly UTF-8 encodable before json.loads.
        raw.encode("utf-8")
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        print(_json_error("invalid_json", f"invalid JSON stdin: {exc.msg}", "Pass a JSON object on stdin."))
        return 2
    except (UnicodeDecodeError, UnicodeEncodeError):
        print(_json_error("invalid_json", "invalid JSON stdin: not valid UTF-8", "Pass UTF-8 encoded JSON on stdin."))
        return 2
    except RecursionError:
        # Older interpreters may raise during json.loads on deep nesting; keep structured shape.
        print(_json_error("invalid_json", "invalid JSON stdin: nesting too deep", "Reduce JSON nesting depth."))
        return 2
    # Enforce a stable depth ceiling after loads (e.g. Python 3.14 accepts very deep JSON).
    if _json_container_depth_exceeded(payload):
        print(_json_error("invalid_json", "invalid JSON stdin: nesting too deep", "Reduce JSON nesting depth."))
        return 2
    if not isinstance(payload, dict):
        print(_json_error("usage_error", "JSON stdin must be an object", "Pass a JSON object on stdin."))
        return 2
    try:
        result = _JSON_COMMANDS[command](payload)
    except Exception as exc:
        print(_json_error("command_failed", str(exc), "Check the request payload and workspace state."))
        return 1
    print(result.to_json())
    return 0 if result.ok else 1


def load_catalog_for_text(catalog_path):
    # helper to avoid circular, but since framework has load_catalog
    from cluxion_agentplugin_supercoder.doctor.framework import load_catalog

    return load_catalog(Path(str(catalog_path)))


if __name__ == "__main__":
    raise SystemExit(main())
