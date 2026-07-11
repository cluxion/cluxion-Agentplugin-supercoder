"""L0 repo map: cross-backend symbol outlines plus the honest budget
contract (omitted files are counted, never silently dropped)."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

from cluxion_agentplugin_supercoder import runner, rust_bridge
from cluxion_agentplugin_supercoder.core import repo_map

_LOCAL_BIN = (
    Path(__file__).resolve().parents[1] / "rust" / "supercoder_index" / "target" / "release" / "supercoder-index"
)

BACKENDS = ["python"]
if importlib.util.find_spec("supercoder_index_native") is not None:
    BACKENDS.append("native")
if _LOCAL_BIN.exists() or shutil.which("supercoder-index"):
    BACKENDS.append("subprocess")

TREE_SITTER_BACKENDS = [name for name in BACKENDS if name != "python"]


@pytest.fixture(autouse=True)
def _clear_outline_cache() -> None:
    repo_map.clear_outline_cache()


@pytest.fixture(params=BACKENDS)
def backend(request, monkeypatch):
    monkeypatch.setenv(rust_bridge.INDEX_BACKEND_ENV, request.param)
    if request.param == "subprocess" and _LOCAL_BIN.exists():
        monkeypatch.setenv(rust_bridge.INDEX_BIN_ENV, str(_LOCAL_BIN))
    return request.param


@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "import os\n\n\nclass Service:\n    def start(self):\n        pass\n\n\ndef helper(x):\n    return x\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "engine.rs").write_text(
        "pub struct Engine;\n\nimpl Engine {\n    pub fn run(&self) {}\n}\n",
        encoding="utf-8",
    )
    (tmp_path / "notes.md").write_text("# notes\n", encoding="utf-8")
    return tmp_path


def test_python_symbols_agree_across_backends(backend: str, sample_repo: Path) -> None:
    symbols = repo_map.outline_file(sample_repo / "src" / "app.py")
    triples = {(s["kind"], s["name"], s["line"]) for s in symbols}
    assert ("class", "Service", 4) in triples
    assert ("method", "start", 5) in triples
    assert ("function", "helper", 9) in triples


def test_map_contains_files_and_symbols(backend: str, sample_repo: Path) -> None:
    result = repo_map.build_repo_map(sample_repo)
    assert result["ok"] is True
    assert result["backend"] == backend
    assert result["files_mapped"] == 3
    assert result["files_omitted"] == 0 and result["truncated"] is False
    assert "src/app.py (11L)" in result["map"]  # trailing newline: count("\n") + 1
    assert "class Service:4" in result["map"]
    assert "notes.md" in result["map"]  # non-code files keep their line-count row


def test_rust_outline_in_tree_sitter_tiers_fails_open_in_python(backend: str, sample_repo: Path) -> None:
    symbols = repo_map.outline_file(sample_repo / "src" / "engine.rs")
    if backend == "python":
        assert symbols == []  # stdlib tier cannot outline rust: fail-open
    else:
        pairs = {(s["kind"], s["name"]) for s in symbols}
        assert ("struct", "Engine") in pairs
        assert ("method", "run") in pairs


def test_max_files_cap_is_surfaced_honestly(backend: str, tmp_path: Path) -> None:
    for index in range(6):
        (tmp_path / f"mod_{index}.py").write_text(f"def fn_{index}():\n    pass\n", encoding="utf-8")
    result = repo_map.build_repo_map(tmp_path, max_files=3, budget_chars=8000)
    assert result["truncated"] is True
    assert result["files_omitted"] > 0
    assert result["files_mapped"] + result["files_omitted"] == result["files_scanned"] == 6
    assert result["files_omitted"] == 3
    assert result["files_mapped"] == 3


def test_budget_omits_files_honestly(backend: str, sample_repo: Path) -> None:
    # budget_chars clamps to a 200-char floor, so overflow it with real files.
    for index in range(8):
        body = "\n\n".join(f"def fn_{index}_{i}(value):\n    return value" for i in range(6))
        (sample_repo / f"mod_{index}.py").write_text(body + "\n", encoding="utf-8")
    result = repo_map.build_repo_map(sample_repo, budget_chars=200)
    assert result["truncated"] is True
    assert result["files_mapped"] + result["files_omitted"] == result["files_scanned"] == 11
    assert result["files_omitted"] >= 1


def test_symbol_cap_is_reported_not_silent(backend: str, tmp_path: Path) -> None:
    body = "\n\n".join(f"def f{i}():\n    pass" for i in range(30))
    (tmp_path / "many.py").write_text(body + "\n", encoding="utf-8")
    result = repo_map.build_repo_map(tmp_path, max_symbols_per_file=5)
    assert "... +25 more symbols" in result["map"]


def test_outline_symbol_cap_matches_across_backends(backend: str, tmp_path: Path) -> None:
    # MAX_SYMBOLS parity: the python ast tier caps at the same 200 symbols as
    # the native outline, so the "+N more symbols" hint agrees on huge files.
    body = "\n\n".join(f"def f{i}():\n    pass" for i in range(repo_map.MAX_SYMBOLS + 30))
    (tmp_path / "huge.py").write_text(body + "\n", encoding="utf-8")
    symbols = repo_map.outline_file(tmp_path / "huge.py")
    assert len(symbols) == repo_map.MAX_SYMBOLS == 200
    result = repo_map.build_repo_map(tmp_path, max_symbols_per_file=5)
    assert f"... +{repo_map.MAX_SYMBOLS - 5} more symbols" in result["map"]


def test_broken_python_fails_open_per_file(backend: str, tmp_path: Path) -> None:
    (tmp_path / "broken.py").write_text("def f(:\n", encoding="utf-8")
    result = repo_map.build_repo_map(tmp_path)
    assert result["ok"] is True
    assert "broken.py" in result["map"]  # file row survives even when outline fails


def test_code_ranks_before_docs_and_hidden_paths_are_dropped(backend: str, sample_repo: Path) -> None:
    (sample_repo / ".github").mkdir()
    (sample_repo / ".github" / "ci.yml").write_text("jobs: {}\n", encoding="utf-8")
    result = repo_map.build_repo_map(sample_repo)
    assert ".github" not in result["map"]
    rows = result["map"].splitlines()
    assert rows[0].startswith("src/app.py")  # code first, docs after
    assert rows.index("notes.md (2L)") > rows.index("src/engine.rs (6L)")


def test_src_leads_and_tests_trail_within_code(backend: str, sample_repo: Path) -> None:
    (sample_repo / "tests").mkdir()
    (sample_repo / "tests" / "test_app.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
    (sample_repo / "tool.py").write_text("def main():\n    pass\n", encoding="utf-8")
    result = repo_map.build_repo_map(sample_repo)
    rows = result["map"].splitlines()
    order = [rows.index(f"{name} ({n}L)") for name, n in (("src/app.py", 11), ("tool.py", 3), ("tests/test_app.py", 3))]
    assert order == sorted(order)  # src -> other code -> tests


def test_plan_carries_compact_repo_map(backend: str, sample_repo: Path) -> None:
    result = runner.plan({"prompt": "fix the bug in service start", "cwd": str(sample_repo)})
    assert result.ok is True and result.payload["mode"] == "coding_queue"
    carried = result.payload["repo_map"]
    assert "class Service:4" in carried["map"]
    assert carried["truncated"] is False


def test_plan_repo_map_opt_out_and_bypass(backend: str, sample_repo: Path) -> None:
    opted_out = runner.plan({"prompt": "fix the bug", "cwd": str(sample_repo), "repo_map": False})
    assert "repo_map" not in opted_out.payload
    bypass = runner.plan({"prompt": "what does this project do?", "cwd": str(sample_repo)})
    assert bypass.payload["mode"] == "bypass" and "repo_map" not in bypass.payload


def test_missing_root_is_an_error() -> None:
    result = repo_map.build_repo_map(Path("/nonexistent/cluxion-repo-map"))
    assert result["ok"] is False
    assert "root" in result["error"]


def test_forced_unavailable_backend_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setenv(rust_bridge.INDEX_BACKEND_ENV, "native")
    monkeypatch.setattr(rust_bridge, "_load_native", lambda: None)
    result = runner.repo_map_tool({"cwd": str(tmp_path)})
    assert result.ok is False
    assert result.payload["error"] == "backend_unavailable"
    assert "CLUXION_SUPERCODER_BACKEND=native" in result.payload["hint"]


def test_auto_backend_fallback_reports_honest_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")

    class BrokenNative:
        def run(self, _command: str, _payload: str) -> str:
            raise RuntimeError("boom")

    monkeypatch.delenv(rust_bridge.INDEX_BACKEND_ENV, raising=False)
    monkeypatch.setattr(rust_bridge, "_load_native", lambda: BrokenNative())
    result = runner.repo_map_tool({"cwd": str(tmp_path)})
    assert result.ok is True
    assert result.payload["backend"] == "python"
    assert result.payload["fallback_from"] == "native"


def test_runner_tool_wraps_result(backend: str, sample_repo: Path) -> None:
    result = runner.repo_map_tool({"cwd": str(sample_repo)})
    assert result.ok is True
    assert result.payload["files_mapped"] == 3
    assert "class Service:4" in result.payload["map"]


def _counting_outline_wrapper(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"count": 0}
    real = repo_map._outline_file_result

    def counting_outline(path: Path, *, language: str | None = None) -> dict:
        calls["count"] += 1
        return real(path, language=language)

    monkeypatch.setattr(repo_map, "_outline_file_result", counting_outline)
    return calls


def test_second_build_reuses_outline_cache(backend: str, sample_repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _counting_outline_wrapper(monkeypatch)
    first = repo_map.build_repo_map(sample_repo)
    assert calls["count"] > 0

    calls["count"] = 0
    second = repo_map.build_repo_map(sample_repo)
    if backend == "python":
        # checked=false rust outlines are never cached; only python symbols stick.
        assert calls["count"] == 1
    else:
        assert calls["count"] == 0
    assert first["map"] == second["map"]


def test_changed_file_invalidates_outline_cache(
    backend: str, sample_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _counting_outline_wrapper(monkeypatch)
    repo_map.build_repo_map(sample_repo)
    calls["count"] = 0

    (sample_repo / "src" / "app.py").write_text(
        "import os\n\n\nclass RenamedService:\n    def boot(self):\n        pass\n",
        encoding="utf-8",
    )
    result = repo_map.build_repo_map(sample_repo)
    if backend == "python":
        # app.py (hash miss) + engine.rs (never cached on python tier)
        assert calls["count"] == 2
    else:
        assert calls["count"] == 1
    assert "class RenamedService:4" in result["map"]
    assert "method boot:5" in result["map"]


def test_identical_rewrite_keeps_outline_cache(
    backend: str, sample_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _counting_outline_wrapper(monkeypatch)
    repo_map.build_repo_map(sample_repo)

    original = (sample_repo / "src" / "app.py").read_text(encoding="utf-8")
    (sample_repo / "src" / "app.py").write_text(original, encoding="utf-8")

    calls["count"] = 0
    result = repo_map.build_repo_map(sample_repo)
    if backend == "python":
        # checked=false rust outlines are never cached.
        assert calls["count"] == 1
    else:
        assert calls["count"] == 0
    assert "map" in result


def test_outline_runtimeerror_and_timeout_fallback_to_py_outline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    (tmp_path / "app.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (tmp_path / "lib.rs").write_text("fn main() {}\n", encoding="utf-8")
    monkeypatch.setattr(rust_bridge, "resolve_backend", lambda: "subprocess")

    def boom(_command: str, _payload: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("backend down")

    monkeypatch.setattr(rust_bridge, "_invoke_subprocess", boom)
    symbols = repo_map.outline_file(tmp_path / "app.py")
    assert any(s.get("name") == "keep" for s in symbols)
    assert repo_map.outline_file(tmp_path / "lib.rs") == []

    def timed_out(_command: str, _payload: dict[str, object]) -> dict[str, object]:
        raise subprocess.TimeoutExpired(cmd="supercoder-index", timeout=30)

    monkeypatch.setattr(rust_bridge, "_invoke_subprocess", timed_out)
    symbols = repo_map.outline_file(tmp_path / "app.py")
    assert any(s.get("name") == "keep" for s in symbols)
    assert repo_map.outline_file(tmp_path / "lib.rs") == []


def test_outline_cache_skips_hash_race(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cluxion_agentplugin_supercoder.core.hash_patch import file_hash

    path = tmp_path / "app.py"
    original = "def one():\n    return 1\n"
    path.write_text(original, encoding="utf-8")
    scan_hash = file_hash(original)
    real = repo_map._outline_file_result

    def raced(p: Path, *, language: str | None = None) -> dict:
        result = real(p, language=language)
        # Pretend the backend parsed different bytes than the scan snapshot.
        result = dict(result)
        result["content_hash"] = "0" * 64
        return result

    monkeypatch.setattr(repo_map, "_outline_file_result", raced)
    symbols, cached = repo_map._outline_for_map_entry(path, language="python", file_hash=scan_hash)
    assert symbols  # still returned for this call
    assert cached is False
    assert (str(path), scan_hash) not in repo_map._outline_cache


def test_python_to_native_outline_cache_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A python-tier [] for Rust must not hide native symbols after backend switch."""
    if "native" not in BACKENDS:
        pytest.skip("native backend unavailable")
    path = tmp_path / "engine.rs"
    path.write_text("pub struct Engine;\n\nimpl Engine {\n    pub fn run(&self) {}\n}\n", encoding="utf-8")
    from cluxion_agentplugin_supercoder.core.hash_patch import file_hash

    digest = file_hash(path.read_text(encoding="utf-8"))
    monkeypatch.setenv(rust_bridge.INDEX_BACKEND_ENV, "python")
    empty, _ = repo_map._outline_for_map_entry(path, language="rust", file_hash=digest)
    assert empty == []
    assert (str(path), digest) not in repo_map._outline_cache

    monkeypatch.setenv(rust_bridge.INDEX_BACKEND_ENV, "native")
    symbols, _ = repo_map._outline_for_map_entry(path, language="rust", file_hash=digest)
    pairs = {(s["kind"], s["name"]) for s in symbols}
    assert ("struct", "Engine") in pairs
    assert ("method", "run") in pairs


def test_python_scan_excludes_file_symlinks_and_external_symbol_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(rust_bridge.INDEX_BACKEND_ENV, "python")
    outside = tmp_path / "outside_pkg"
    outside.mkdir()
    external = outside / "leaked.py"
    external.write_text("def external_secret_symbol():\n    return 1\n", encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "app.py").write_text("def local_ok():\n    return 0\n", encoding="utf-8")
    link = workspace / "leaked.py"
    link.symlink_to(external)
    entries = rust_bridge.scan_repo(workspace)
    paths = [entry["path"] for entry in entries]
    assert paths == ["app.py"]
    assert "leaked.py" not in paths
    result = repo_map.build_repo_map(workspace)
    assert result["ok"] is True
    assert "external_secret_symbol" not in result["map"]
    assert "local_ok" in result["map"]
