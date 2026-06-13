from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from cluxion_agentplugin_supercoder import __version__
from cluxion_agentplugin_supercoder.rust_bridge import index_available, resolve_backend


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cluxion-supercoder")
    parser.add_argument("--version", action="version", version=f"cluxion-agentplugin-supercoder {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check", help="Check plugin and Rust index availability")
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
    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
