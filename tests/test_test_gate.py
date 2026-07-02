from __future__ import annotations

from pathlib import Path

from cluxion_agentplugin_supercoder.core.test_gate import suggest_test_commands


def test_maps_src_file_to_matching_test(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "pkg" / "store.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_store.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    payload = suggest_test_commands(["src/pkg/store.py"], cwd=tmp_path)
    assert payload["targets"] == ["tests/test_store.py"]
    assert payload["command"] == "pytest -q tests/test_store.py"


def test_uses_changed_test_file_directly(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_plugin.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    payload = suggest_test_commands(["tests/test_plugin.py"], cwd=tmp_path)
    assert payload["targets"] == ["tests/test_plugin.py"]
    assert "tests/test_plugin.py" in str(payload["command"])


def test_maps_flat_layout_without_src(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "pkg" / "engine.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_engine.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    payload = suggest_test_commands(["pkg/engine.py"], cwd=tmp_path)
    assert payload["targets"] == ["tests/test_engine.py"]


def test_maps_tests_beside_code(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "engine.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "test_engine.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    payload = suggest_test_commands(["pkg/engine.py"], cwd=tmp_path)
    assert payload["targets"] == [str(Path("pkg") / "test_engine.py")]


def test_proximity_prefers_nearest_test(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b" / "tests").mkdir(parents=True)
    (tmp_path / "a" / "engine.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "a" / "test_engine.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    (tmp_path / "b" / "tests" / "test_engine.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    payload = suggest_test_commands(["a/engine.py"], cwd=tmp_path)
    assert payload["targets"] == [str(Path("a") / "test_engine.py")]


def test_suffix_test_convention_is_recognized(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "engine.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "engine_test.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    payload = suggest_test_commands(["pkg/engine.py"], cwd=tmp_path)
    assert payload["targets"] == [str(Path("pkg") / "engine_test.py")]


def test_conftest_change_targets_its_directory(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text("import pytest\n", encoding="utf-8")
    payload = suggest_test_commands(["tests/conftest.py"], cwd=tmp_path)
    assert payload["targets"] == ["tests"]


def test_rust_change_routes_to_cargo(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\n', encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("fn main() {}\n", encoding="utf-8")
    payload = suggest_test_commands(["src/lib.rs"], cwd=tmp_path)
    assert payload["command"] == "cargo test -q"
    assert payload["source"] == "project_runner"
    assert {"language": "rust", "command": "cargo test -q"} in payload["runners"]


def test_node_runner_requires_test_script(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"scripts": {"build": "tsc"}}', encoding="utf-8")
    (tmp_path / "app.ts").write_text("const x = 1;\n", encoding="utf-8")
    payload = suggest_test_commands(["app.ts"], cwd=tmp_path)
    assert payload["runners"] == []
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest run"}}', encoding="utf-8")
    payload = suggest_test_commands(["app.ts"], cwd=tmp_path)
    assert payload["command"] == "npm test --silent"


def test_mixed_change_keeps_pytest_primary_with_runner_alternative(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\n', encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "core.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_core.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    payload = suggest_test_commands(["core.py", "src/lib.rs"], cwd=tmp_path)
    assert payload["command"] == "pytest -q " + str(Path("tests") / "test_core.py")
    assert "cargo test -q" in payload["alternatives"]


def test_expanding_fuzzy_maps_module_to_extended_test_names(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "pkg" / "pruner.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_pruner_archive.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    (tmp_path / "tests" / "test_pruner_db.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    payload = suggest_test_commands(["src/pkg/pruner.py"], cwd=tmp_path)
    assert set(payload["targets"]) == {
        str(Path("tests") / "test_pruner_archive.py"),
        str(Path("tests") / "test_pruner_db.py"),
    }


def test_shrinking_fuzzy_drops_trailing_tokens(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "pkg" / "guard_bridge.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_guard.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    payload = suggest_test_commands(["src/pkg/guard_bridge.py"], cwd=tmp_path)
    assert payload["targets"] == [str(Path("tests") / "test_guard.py")]


def test_source_module_named_like_test_maps_to_its_real_test(tmp_path: Path) -> None:
    # Regression: core/test_gate.py is a source module, not a test; it must
    # never suggest itself and instead map to tests/test_test_gate.py.
    (tmp_path / "src" / "core").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "core" / "test_gate.py").write_text("def suggest(): pass\n", encoding="utf-8")
    (tmp_path / "tests" / "test_test_gate.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    payload = suggest_test_commands(["src/core/test_gate.py"], cwd=tmp_path)
    assert payload["targets"] == [str(Path("tests") / "test_test_gate.py")]


def test_decoy_source_named_test_is_not_picked(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "pkg" / "gate.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "test_gate.py").write_text("HELPER = 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_gate.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    payload = suggest_test_commands(["pkg/gate.py"], cwd=tmp_path)
    assert payload["targets"] == [str(Path("tests") / "test_gate.py")]


def test_default_detection_uses_project_marker(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\n', encoding="utf-8")
    payload = suggest_test_commands([], cwd=tmp_path)
    assert payload["command"] == "cargo test -q"
    assert payload["source"] == "project_default"


def test_test_gate_does_not_create_workspace_artifacts(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_core.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    payload = suggest_test_commands(["core.py"], cwd=tmp_path)
    assert payload["ok"] is True
    assert not (tmp_path / ".cluxion-test-dispatch").exists()
    assert not list(tmp_path.glob(".cluxion-test-dispatch*"))
