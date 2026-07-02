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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cluxion-supercoder")
    parser.add_argument("--version", action="version", version=f"cluxion-agentplugin-supercoder {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check", help="Check plugin and Rust index availability")
    doctor_p = sub.add_parser("doctor", help="Run embedded doctor checks")
    doctor_p.add_argument("--json", action="store_true", help="Output JSON to stdout")
    doctor_p.add_argument("--verbose", action="store_true", help="Verbose text output")
    for name in _JSON_COMMANDS:
        command_p = sub.add_parser(name, help=f"Run {name} JSON contract")
        command_p.add_argument("--json-stdin", action="store_true", help="Read request payload as JSON from stdin")
    args = parser.parse_args(argv)
    if args.command == "check":
        payload = {
            "plugin": "cluxion-agentplugin-supercoder",
            "version": __version__,
            "rust_index": index_available(),
            "index_backend": resolve_backend(),
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "doctor":
        catalog_path = files("cluxion_agentplugin_supercoder.doctor") / "catalog.json"
        result = run_doctor(
            cwd=Path.cwd(),
            catalog_path=Path(str(catalog_path)),
            probes=PROBES,
            plugin="supercoder",
            version=__version__,
        )
        text = render_text(result, load_catalog_for_text(catalog_path), verbose=args.verbose)
        print(text, file=sys.stderr)
        if args.json:
            print(render_json(result))
        return 0 if result.ok else 1
    if args.command in _JSON_COMMANDS:
        return _run_json_command(args.command, bool(args.json_stdin))
    parser.print_help(sys.stderr)
    return 2


def _run_json_command(command: str, json_stdin: bool) -> int:
    if not json_stdin:
        print(json.dumps({"ok": False, "error": "--json-stdin is required"}, ensure_ascii=False, sort_keys=True))
        return 2
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"invalid JSON stdin: {exc.msg}"}, ensure_ascii=False, sort_keys=True))
        return 2
    if not isinstance(payload, dict):
        print(json.dumps({"ok": False, "error": "JSON stdin must be an object"}, ensure_ascii=False, sort_keys=True))
        return 2
    try:
        result = _JSON_COMMANDS[command](payload)
    except (ValueError, TypeError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
    print(result.to_json())
    return 0 if result.ok else 1


def load_catalog_for_text(catalog_path):
    # helper to avoid circular, but since framework has load_catalog
    from cluxion_agentplugin_supercoder.doctor.framework import load_catalog

    return load_catalog(Path(str(catalog_path)))


if __name__ == "__main__":
    raise SystemExit(main())
