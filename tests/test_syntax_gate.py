"""L1 syntax gate: cross-backend agreement on valid/invalid verdicts plus
the patch auto-revert loop. Error messages differ between tree-sitter and
the stdlib parsers, so assertions are on verdicts and locations, not text."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

from cluxion_agentplugin_supercoder import runner, rust_bridge
from cluxion_agentplugin_supercoder.core import syntax_gate
from cluxion_agentplugin_supercoder.core.hash_patch import file_hash

_LOCAL_BIN = (
    Path(__file__).resolve().parents[1] / "rust" / "supercoder_index" / "target" / "release" / "supercoder-index"
)

BACKENDS = ["python"]
if importlib.util.find_spec("supercoder_index_native") is not None:
    BACKENDS.append("native")
if _LOCAL_BIN.exists() or shutil.which("supercoder-index"):
    BACKENDS.append("subprocess")


@pytest.fixture(params=BACKENDS)
def backend(request, monkeypatch):
    monkeypatch.setenv(rust_bridge.INDEX_BACKEND_ENV, request.param)
    if request.param == "subprocess" and _LOCAL_BIN.exists():
        monkeypatch.setenv(rust_bridge.INDEX_BIN_ENV, str(_LOCAL_BIN))
    return request.param


_RESULT_KEYS = {"ok", "checked", "language", "valid", "errors", "error_count"}


def test_valid_python_passes(backend: str) -> None:
    result = syntax_gate.check_source(content="def add(a, b):\n    return a + b\n", language="python")
    assert set(result) >= _RESULT_KEYS
    assert result["checked"] is True
    assert result["valid"] is True
    assert result["errors"] == []


def test_broken_python_reports_location(backend: str) -> None:
    result = syntax_gate.check_source(content="def add(a, b:\n    return a + b\n", language="python")
    assert result["valid"] is False
    assert result["error_count"] >= 1
    first = result["errors"][0]
    assert first["line"] >= 1
    assert first["kind"] in {"error", "missing"}


def test_json_verdicts(backend: str) -> None:
    assert syntax_gate.check_source(content='{"a": 1}', language="json")["valid"] is True
    assert syntax_gate.check_source(content='{"a": 1,}', language="json")["valid"] is False


def test_toml_routes_to_python_everywhere(backend: str) -> None:
    assert syntax_gate.check_source(content="a = 1\n", language="toml")["valid"] is True
    broken = syntax_gate.check_source(content="a = = 1\n", language="toml")
    assert broken["checked"] is True
    assert broken["valid"] is False


def test_broken_toml_reports_version_safe_location(backend: str) -> None:
    result = syntax_gate.check_source(content="ok = 1\nbad = ]\n", language="toml")
    assert result["valid"] is False
    assert result["errors"][0]["line"] == (2 if sys.version_info >= (3, 14) else 1)


def test_unknown_language_is_fail_open(backend: str) -> None:
    result = syntax_gate.check_source(content="anything at all", language="")
    assert result["checked"] is False
    assert result["valid"] is True
    assert result["reason"] == "no_parser"


def test_rust_checked_only_with_treesitter(backend: str) -> None:
    result = syntax_gate.check_source(content="fn main( {", language="rust")
    if backend == "python":
        assert result["checked"] is False
    else:
        assert result["checked"] is True
        assert result["valid"] is False


def test_rust_identifier_named_raw_can_be_borrowed(backend: str) -> None:
    result = syntax_gate.check_source(
        content="fn f(raw: String) { let _ = foo(&raw); }",
        language="rust",
    )
    if backend == "python":
        assert result["checked"] is False
    else:
        assert result["checked"] is True
        assert result["valid"] is True


def test_backend_failure_falls_back_to_python_tier(monkeypatch) -> None:
    def boom(command: str, payload: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("backend down")

    monkeypatch.setattr(rust_bridge, "resolve_backend", lambda: "subprocess")
    monkeypatch.setattr(rust_bridge, "_invoke_subprocess", boom)
    result = syntax_gate.check_source(content="def add(a, b:\n    return a + b\n", language="python")
    assert result["checked"] is True
    assert result["valid"] is False
    unchecked = syntax_gate.check_source(content="fn main( {", language="rust")
    assert unchecked["checked"] is False
    assert unchecked["valid"] is True


def test_language_detection() -> None:
    assert syntax_gate.language_for_path("src/app.py") == "python"
    assert syntax_gate.language_for_path("ui/View.tsx") == "tsx"
    assert syntax_gate.language_for_path("Cargo.toml") == "toml"
    assert syntax_gate.language_for_path("notes.txt") is None


def _write(path: Path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    return file_hash(text)


def test_patch_reverts_on_broken_syntax(tmp_path: Path, backend: str) -> None:
    original = "def add(a, b):\n    return a + b\n"
    digest = _write(tmp_path / "mod.py", original)
    result = runner.patch_tool(
        {
            "cwd": str(tmp_path),
            "path": "mod.py",
            "old_text": "    return a + b\n",
            "new_text": "    return a +\n",
            "expected_file_hash": digest,
        }
    )
    assert result.ok is False
    assert result.payload["strategy"] == "syntax_reverted"
    assert result.payload["syntax_errors"]
    assert (tmp_path / "mod.py").read_text(encoding="utf-8") == original


def test_syntax_revert_preserves_crlf_bytes(tmp_path: Path, backend: str) -> None:
    path = tmp_path / "mod.py"
    original = b"def add(a, b):\r\n    return a + b\r\n"
    path.write_bytes(original)

    result = runner.patch_tool(
        {
            "cwd": str(tmp_path),
            "path": "mod.py",
            "old_text": "    return a + b\n",
            "new_text": "    return a +\n",
            "expected_file_hash": file_hash(original.decode()),
        }
    )

    assert result.ok is False
    assert result.payload["strategy"] == "syntax_reverted"
    assert path.read_bytes() == original


def test_syntax_revert_refuses_concurrent_change(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "mod.py"
    original = "def add(a, b):\n    return a + b\n"
    concurrent = b"def concurrent():\n    return 2\n"
    path.write_text(original, encoding="utf-8")
    check_source = syntax_gate.check_source

    def write_concurrently(*args, **kwargs):
        check = check_source(*args, **kwargs)
        path.write_bytes(concurrent)
        return check

    monkeypatch.setattr(syntax_gate, "check_source", write_concurrently)
    result = runner.patch_tool(
        {
            "cwd": str(tmp_path),
            "path": "mod.py",
            "old_text": "    return a + b\n",
            "new_text": "    return a +\n",
            "expected_file_hash": file_hash(original),
        }
    )

    assert result.ok is False
    assert result.payload["strategy"] == "revert_failed"
    assert path.read_bytes() == concurrent


def test_patch_passes_gate_when_valid(tmp_path: Path, backend: str) -> None:
    digest = _write(tmp_path / "mod.py", "def add(a, b):\n    return a + b\n")
    result = runner.patch_tool(
        {
            "cwd": str(tmp_path),
            "path": "mod.py",
            "old_text": "return a + b",
            "new_text": "return a * b",
            "expected_file_hash": digest,
        }
    )
    assert result.ok is True
    assert result.payload["syntax"] == {"checked": True, "language": "python", "valid": True, "error_count": 0}


def test_patch_gate_can_be_disabled(tmp_path: Path, backend: str) -> None:
    digest = _write(tmp_path / "mod.py", "def add(a, b):\n    return a + b\n")
    result = runner.patch_tool(
        {
            "cwd": str(tmp_path),
            "path": "mod.py",
            "old_text": "return a + b",
            "new_text": "return a +",
            "expected_file_hash": digest,
            "syntax_gate": False,
        }
    )
    assert result.ok is True
    assert "syntax" not in result.payload


def test_syntax_gate_tool_with_file(tmp_path: Path, backend: str) -> None:
    _write(tmp_path / "broken.json", '{"a": 1,}')
    result = runner.syntax_gate_tool({"cwd": str(tmp_path), "path": "broken.json"})
    assert result.ok is True
    assert result.payload["valid"] is False
    assert result.payload["language"] == "json"


def test_syntax_gate_tool_accepts_files_changed(tmp_path: Path, backend: str) -> None:
    _write(tmp_path / "ok.py", "def f():\n    return 1\n")
    _write(tmp_path / "broken.json", '{"a": 1,}')
    result = runner.syntax_gate_tool({"cwd": str(tmp_path), "files_changed": ["ok.py", "broken.json"]})
    assert result.ok is True
    assert [item["path"] for item in result.payload["files"]] == ["ok.py", "broken.json"]
    assert [item["valid"] for item in result.payload["files"]] == [True, False]


def test_malformed_native_syntax_schema_falls_back_without_keyerror(monkeypatch) -> None:
    monkeypatch.setattr(rust_bridge, "resolve_backend", lambda: "native")

    def malformed(_command: str, _payload: dict[str, object]) -> dict[str, object]:
        return {"ok": True}  # missing checked/valid/errors/error_count/language

    monkeypatch.setattr(rust_bridge, "_invoke_native", malformed)
    result = syntax_gate.check_source(content="def add(a, b:\n    return a + b\n", language="python")
    assert result["checked"] is True
    assert result["valid"] is False
    assert isinstance(result["errors"], list)
    assert result["error_count"] == len(result["errors"])


def test_malformed_native_schema_still_reverts_invalid_patch(tmp_path: Path, monkeypatch) -> None:
    original = "def add(a, b):\n    return a + b\n"
    digest = _write(tmp_path / "mod.py", original)
    monkeypatch.setattr(rust_bridge, "resolve_backend", lambda: "native")

    def malformed(_command: str, _payload: dict[str, object]) -> dict[str, object]:
        return {"ok": True}

    monkeypatch.setattr(rust_bridge, "_invoke_native", malformed)
    result = runner.patch_tool(
        {
            "cwd": str(tmp_path),
            "path": "mod.py",
            "old_text": "    return a + b\n",
            "new_text": "    return a +\n",
            "expected_file_hash": digest,
        }
    )
    assert result.ok is False
    assert result.payload["strategy"] == "syntax_reverted"
    assert (tmp_path / "mod.py").read_text(encoding="utf-8") == original


def test_syntax_verdict_uses_post_image_not_later_writer(tmp_path: Path, monkeypatch) -> None:
    """Concurrent writer must not rewrite this patch's syntax verdict."""
    path = tmp_path / "mod.py"
    original = "def add(a, b):\n    return a + b\n"
    later_writer = "def later():\n    return 99\n"
    path.write_text(original, encoding="utf-8")
    real_check = syntax_gate.check_source
    seen: dict[str, object] = {}

    def interleave(*args, **kwargs):
        path.write_text(later_writer, encoding="utf-8")
        seen["kwargs"] = dict(kwargs)
        if args:
            seen["args"] = args
        return real_check(*args, **kwargs)

    monkeypatch.setattr(syntax_gate, "check_source", interleave)
    result = runner.patch_tool(
        {
            "cwd": str(tmp_path),
            "path": "mod.py",
            "old_text": "    return a + b\n",
            "new_text": "    return a +\n",
            "expected_file_hash": file_hash(original),
        }
    )
    assert result.ok is False
    assert result.payload["strategy"] == "revert_failed"
    assert path.read_text(encoding="utf-8") == later_writer
    assert result.payload["syntax"]["valid"] is False
    assert result.payload["syntax_errors"]
    # Path may select language, but content must be the committed post-image.
    kwargs = seen.get("kwargs") or {}
    assert "content" in kwargs
    assert kwargs["content"] == "def add(a, b):\n    return a +\n"


def test_python_module_return_and_duplicate_args_rejected(backend: str) -> None:
    for content in ("return 1\n", "def f(a, a):\n    pass\n"):
        result = syntax_gate.check_source(content=content, language="python")
        assert result["checked"] is True
        assert result["valid"] is False
        assert result["error_count"] >= 1


def test_json_rejects_empty_multi_root_and_non_rfc_constants(backend: str) -> None:
    for content in ("", "{}{}", "NaN", "Infinity", "-Infinity", '{"x": NaN}'):
        result = syntax_gate.check_source(content=content, language="json")
        assert result["checked"] is True
        assert result["valid"] is False, content
        assert result["error_count"] >= 1
        assert result["errors"][0]["kind"] == "error"
        assert isinstance(result["errors"][0]["message"], str)
        assert result["errors"][0]["message"]


def test_json_non_rfc_constant_points_past_quoted_and_escaped_tokens() -> None:
    """Diagnostics must locate the unquoted constant, not an earlier string match."""
    # Lines 1-3: quoted/escaped decoys; line 4: real invalid NaN.
    content = '{\n  "note": "contains NaN text",\n  "escaped": "has \\"NaN\\" inside",\n  "value": NaN\n}\n'
    result = syntax_gate.check_source(content=content, language="json")
    assert result["valid"] is False
    err = result["errors"][0]
    assert "NaN" in err["message"]
    assert err["line"] == 4
    assert err["column"] == content.split("\n")[3].index("NaN") + 1
    assert "value" in err["snippet"]

    # Quoted Infinity before real -Infinity on a later line.
    content2 = '{\n  "a": "Infinity",\n  "b": -Infinity\n}\n'
    result2 = syntax_gate.check_source(content=content2, language="json")
    assert result2["valid"] is False
    err2 = result2["errors"][0]
    assert "Infinity" in err2["message"]
    assert err2["line"] == 3
    assert err2["column"] == content2.split("\n")[2].index("-Infinity") + 1


def test_python_json_stdlib_tier_even_when_native_forced(monkeypatch) -> None:
    """Public python/json truth is backend-independent (stdlib tier)."""
    calls: list[str] = []

    def boom(command: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append(command)
        raise AssertionError(f"native must not run for python/json: {command}")

    monkeypatch.setattr(rust_bridge, "resolve_backend", lambda: "native")
    monkeypatch.setattr(rust_bridge, "_invoke_native", boom)
    assert syntax_gate.check_source(content="def f():\n    return 1\n", language="python")["valid"] is True
    assert syntax_gate.check_source(content='{"a": 1}', language="json")["valid"] is True
    assert syntax_gate.check_source(content="return 1\n", language="python")["valid"] is False
    assert syntax_gate.check_source(content="NaN", language="json")["valid"] is False
    assert calls == []


def test_syntax_finding_snippet_uses_lf_only_line_semantics() -> None:
    # U+2028 must stay inside the line; splitlines() would invent a break.
    content = "x = 1\ny = 'a\u2028b'\nz = (\n"
    result = syntax_gate.check_source(content=content, language="python")
    assert result["valid"] is False
    snippet = result["errors"][0]["snippet"]
    assert "\u2028" in snippet or result["errors"][0]["line"] >= 1
    # Snippet extraction must not rewrite separators into LF-only multi-lines.
    lines = content.split("\n")
    line_no = result["errors"][0]["line"]
    if 0 < line_no <= len(lines):
        assert result["errors"][0]["snippet"] == lines[line_no - 1][:120]


def test_public_syntax_description_matches_runtime_routing() -> None:
    """Schema/docs/catalog must mirror runtime: stdlib python/json/toml; tree-sitter rust/js/ts/tsx."""
    import json
    import re
    from pathlib import Path

    from cluxion_agentplugin_supercoder.schemas import SYNTAX_GATE_SCHEMA

    stdlib = set(syntax_gate.PYTHON_TIER_LANGUAGES)
    tree_sitter = {"rust", "javascript", "typescript", "tsx"}
    assert stdlib == {"python", "json", "toml"}

    # Identifier list after "tree-sitter:" / "tree-sitter handles" (punctuation-tolerant, not prose-open).
    _ts_list = re.compile(
        r"tree-sitter(?:\s*:\s*|\s+handles\s+)((?:[a-z0-9_]+)(?:\s*[/,]\s*[a-z0-9_]+)*)",
        re.I,
    )
    _aliases = {"js": "javascript", "ts": "typescript"}

    def tree_sitter_langs(text: str) -> set[str]:
        match = _ts_list.search(text)
        assert match is not None, f"missing tree-sitter language clause: {text[:160]!r}"
        raw = {part.strip().lower() for part in re.split(r"[/,]", match.group(1)) if part.strip()}
        return {_aliases.get(lang, lang) for lang in raw}

    catalog = json.loads(Path("src/cluxion_agentplugin_supercoder/doctor/catalog.json").read_text(encoding="utf-8"))
    what = next(item["what_it_checks"] for item in catalog if item["check_id"] == "syntax_gate_parser_available")
    sources = {
        "schema": SYNTAX_GATE_SCHEMA["description"],
        "catalog": what,
        "tools.md": Path("Docs/tools.md").read_text(encoding="utf-8"),
        "design.md": Path("Docs/design.md").read_text(encoding="utf-8"),
    }
    for name, source in sources.items():
        langs = tree_sitter_langs(source.lower())
        assert langs == tree_sitter, name
        assert stdlib.isdisjoint(langs), f"{name}: stdlib langs claimed under tree-sitter: {langs & stdlib}"

    desc = SYNTAX_GATE_SCHEMA["description"].lower()
    std_match = re.search(
        r"stdlib(?:\s*:\s*|\s+always\s+handles\s+)((?:[a-z0-9_]+)(?:\s*[/,]\s*[a-z0-9_]+)*)",
        desc,
    )
    assert std_match is not None
    std_langs = {part.strip() for part in re.split(r"[/,]", std_match.group(1)) if part.strip()}
    assert std_langs == stdlib
