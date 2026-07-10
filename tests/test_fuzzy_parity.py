"""Parity gate: the rust fuzzy_span op must agree with the python tier.

A seeded property corpus (500+ generated cases: unicode incl. astral plane,
CRLF/CR/exotic line breaks, near-duplicate spans, ambiguous pairs, >200-char
references, no-match noise) is run through both implementations; they must
agree on (matched, start, end, score@12dp, ambiguous) for EVERY case.
The rust op must never be wired into the live path without this staying green.
"""

from __future__ import annotations

import importlib.util
import json
import random
import string
import subprocess
from pathlib import Path

import pytest

from cluxion_agentplugin_supercoder import rust_bridge
from cluxion_agentplugin_supercoder.core.hash_patch import _best_fuzzy_span, apply_patch

_LOCAL_BIN = (
    Path(__file__).resolve().parents[1] / "rust" / "supercoder_index" / "target" / "release" / "supercoder-index"
)
CORPUS_SIZE = 520
SEED = 20260706


def _native_has_fuzzy() -> bool:
    """True only when the importable native module already ships the op
    (an older installed wheel may predate fuzzy_span)."""
    if importlib.util.find_spec("supercoder_index_native") is None:
        return False
    import supercoder_index_native

    try:
        supercoder_index_native.run("fuzzy_span", '{"text": "a\\n", "reference": "a\\n"}')
    except RuntimeError:
        return False
    return True


_HAS_NATIVE = _native_has_fuzzy()


def _rust_fuzzy(text: str, reference: str) -> dict[str, object]:
    payload = json.dumps({"text": text, "reference": reference}, ensure_ascii=False)
    if _HAS_NATIVE:
        import supercoder_index_native

        return json.loads(supercoder_index_native.run("fuzzy_span", payload))
    completed = subprocess.run(
        [str(_LOCAL_BIN), "fuzzy_span"],
        input=payload,
        text=True,
        capture_output=True,
        check=True,
        timeout=30.0,
    )
    return json.loads(completed.stdout)


def _py_key(result: tuple[int, int, str, float, bool] | None) -> tuple | None:
    if result is None:
        return None
    start, end, _block, score, ambiguous = result
    return (start, end, round(score, 12), ambiguous)


def _rust_key(result: dict[str, object]) -> tuple | None:
    assert result.get("ok") is True
    if not result.get("matched"):
        return None
    return (int(result["start"]), int(result["end"]), round(float(result["score"]), 12), bool(result["ambiguous"]))


_WORDS = [
    "alpha",
    "beta",
    "gamma",
    "delta",
    "value",
    "compute",
    "handler",
    "request",
    "return",
    "total",
    "offset",
    "감자",
    "고구마",
    "데이터",
    "처리",
    "함수",
    "café",
    "naïve",
    "über",
    "żółw",
    "Ωμέγα",
    "λόγος",
    "🚀",
    "🧪",
    "𝒳𝒴",  # noqa: RUF001 - intentional astral Unicode corpus
    "世界",
    "你好",
    "привет",
]
_BREAKS = ["\n"] * 12 + ["\r\n"] * 3 + ["\r", " ", "\x0b", "\x0c", "\x85"]  # noqa: RUF001


def _rand_line(rng: random.Random, long: bool = False) -> str:
    n_words = rng.randint(8, 20) if long else rng.randint(1, 7)
    words = [rng.choice(_WORDS) for _ in range(n_words)]
    indent = " " * rng.choice([0, 0, 4, 8])
    return indent + " ".join(words)


def _rand_text(rng: random.Random, n_lines: int, long_lines: bool = False) -> list[str]:
    return [_rand_line(rng, long=long_lines) + rng.choice(_BREAKS) for _ in range(n_lines)]


def _mutate(rng: random.Random, block: str, edits: int) -> str:
    chars = list(block)
    for _ in range(edits):
        if not chars:
            break
        op = rng.choice(("replace", "insert", "delete"))
        pos = rng.randrange(len(chars))
        if op == "replace":
            chars[pos] = rng.choice("abcxyz 감🚀")
        elif op == "insert":
            chars.insert(pos, rng.choice("abcxyz #_"))
        else:
            del chars[pos]
    return "".join(chars)


def _generate_case(rng: random.Random, index: int) -> tuple[str, str, str]:
    scenario = ("drift", "dup", "nomatch", "long_ref", "tiny")[index % 5]
    if scenario == "tiny":
        lines = _rand_text(rng, rng.randint(1, 4))
    elif scenario == "long_ref":
        lines = _rand_text(rng, rng.randint(4, 20), long_lines=True)
    else:
        lines = _rand_text(rng, rng.randint(3, 90))
    if scenario == "nomatch":
        reference = " ".join(rng.choice(string.ascii_letters) for _ in range(rng.randint(3, 40)))
        return scenario, "".join(lines), reference
    width = min(len(lines), rng.randint(1, 6))
    at = rng.randrange(len(lines) - width + 1)
    block = "".join(lines[at : at + width])
    edits = rng.randint(0, max(1, len(block) // 12))
    reference = _mutate(rng, block, edits)
    if scenario == "dup":
        copy = _mutate(rng, block, rng.randint(0, 2))
        insert_at = rng.choice([0, len(lines)])
        lines = [copy, *lines] if insert_at == 0 else [*lines, copy]
    return scenario, "".join(lines), reference


def _handcrafted_cases() -> list[tuple[str, str, str]]:
    near_old = "def compute_total(values):\n    total = sum(values) + offset_marker_a\n    return total\n"
    near_better = near_old.replace("offset_marker_a", "offset_marker_b")
    near_worse = near_old.replace("offset_marker_a", "offset_marker_zz")
    filler = "# header padding line that keeps wider windows far below the margin\n"
    return [
        ("empty-text", "", "ref\n"),
        ("no-trailing-newline", "alpha\nbeta\ngamma", "beta"),
        ("crlf-file", "alpha\r\nbeta\r\ngamma\r\n", "beta\n"),
        ("exotic-breaks", "a b\x0bc\x0cd\x85e\rf\r\ng\n", "b\x0bc\n"),  # noqa: RUF001
        ("astral-offsets", "🚀🚀🚀\ndef f():\n    return 1\n🧪🧪\n", "def f():\n    return 2\n"),
        ("near-tie-better-first", filler + near_better + filler + near_worse, near_old),
        ("near-tie-better-last", filler + near_worse + filler + near_better, near_old),
        ("dup-exact", "def f():\n    return 1\n\ndef f():\n    return 1\n", "def f():\n    return 2\n"),
        ("ref-longer-than-text", "one\ntwo\n", "one\ntwo\nthree\nfour\nfive\nsix\nseven\n"),
        ("autojunk-length-b", " ".join(["spam"] * 80) + "\n", " ".join(["spam"] * 79) + " eggs\n"),
    ]


@pytest.mark.skipif(not (_HAS_NATIVE or _LOCAL_BIN.exists()), reason="no rust backend built")
def test_fuzzy_span_parity_corpus() -> None:
    rng = random.Random(SEED)
    cases = _handcrafted_cases()
    for index in range(CORPUS_SIZE - len(cases)):
        cases.append(_generate_case(rng, index))
    assert len(cases) >= 500
    mismatches = []
    for scenario, text, reference in cases:
        py = _py_key(_best_fuzzy_span(text, reference))
        rust = _rust_key(_rust_fuzzy(text, reference))
        if py != rust:
            mismatches.append((scenario, py, rust, reference[:80]))
    assert not mismatches, f"{len(mismatches)}/{len(cases)} disagree; first: {mismatches[:3]}"


@pytest.mark.skipif(not (_HAS_NATIVE or _LOCAL_BIN.exists()), reason="no rust backend built")
def test_live_fuzzy_path_uses_rust_backend(tmp_path: Path, monkeypatch) -> None:
    """apply_patch's fuzzy fallback must consult the rust bridge, not bypass it."""
    if not _HAS_NATIVE:
        monkeypatch.setenv(rust_bridge.INDEX_BACKEND_ENV, "subprocess")
        monkeypatch.setenv(rust_bridge.INDEX_BIN_ENV, str(_LOCAL_BIN))
    seen: dict[str, object] = {}
    original = rust_bridge.fuzzy_span_result

    def spy(text: str, reference: str):
        result = original(text, reference)
        seen["backend"] = None if result is None else result.get("backend")
        return result

    monkeypatch.setattr(rust_bridge, "fuzzy_span_result", spy)
    path = tmp_path / "a.py"
    path.write_text("def handler(request):\n    value = compute(request)\n    return value\n", encoding="utf-8")
    drifted = "def handler(request):\n    value = compute(request)  # cached\n    return value\n"
    result = apply_patch(path, old_text=drifted, new_text="def handler(request):\n    return compute(request)\n")
    assert result.success is True
    assert result.strategy == "fuzzy"
    assert seen["backend"] in ("native", "subprocess")


def test_fuzzy_span_python_fallback_when_backend_off(tmp_path: Path, monkeypatch) -> None:
    """Forcing the python backend must keep the fuzzy path fully functional."""
    monkeypatch.setenv(rust_bridge.INDEX_BACKEND_ENV, "python")
    path = tmp_path / "a.py"
    path.write_text("def handler(request):\n    value = compute(request)\n    return value\n", encoding="utf-8")
    drifted = "def handler(request):\n    value = compute(request)  # cached\n    return value\n"
    result = apply_patch(path, old_text=drifted, new_text="def handler(request):\n    return compute(request)\n")
    assert result.success is True
    assert result.strategy == "fuzzy"


def _force_fuzzy_backend(monkeypatch, payload: dict[str, object]):
    monkeypatch.setattr(rust_bridge, "resolve_backend", lambda: "native")
    monkeypatch.setattr(rust_bridge, "_invoke_native", lambda _cmd, _payload: payload)
    rust_bridge._fallback_warned = False


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": True},  # missing matched
        {"ok": True, "matched": "yes"},
        {"ok": True, "matched": True},  # missing span fields
        {"ok": True, "matched": True, "start": True, "end": 2, "score": 0.9, "ambiguous": False},
        {"ok": True, "matched": True, "start": "0", "end": 2, "score": 0.9, "ambiguous": False},
        {"ok": True, "matched": True, "start": 0, "end": 2, "score": float("nan"), "ambiguous": False},
        {"ok": True, "matched": True, "start": 0, "end": 2, "score": float("inf"), "ambiguous": False},
        {"ok": True, "matched": True, "start": 0, "end": 2, "score": 10**400, "ambiguous": False},
        {"ok": True, "matched": True, "start": 0, "end": 2, "score": 1.5, "ambiguous": False},
        {"ok": True, "matched": True, "start": -1, "end": 2, "score": 0.9, "ambiguous": False},
        {"ok": True, "matched": True, "start": 1, "end": 1, "score": 0.9, "ambiguous": False},
        {"ok": True, "matched": True, "start": 0, "end": 99, "score": 0.9, "ambiguous": False},
        {"ok": True, "matched": True, "start": 0, "end": 2, "score": 0.9, "ambiguous": 1},
    ],
)
def test_fuzzy_span_malformed_backend_falls_back(monkeypatch, payload: dict[str, object], capsys) -> None:
    text = "ab\ncd\n"
    _force_fuzzy_backend(monkeypatch, payload)
    assert rust_bridge.fuzzy_span_result(text, "ab\n") is None
    err = capsys.readouterr().err
    assert "malformed fuzzy_span backend result" in err or "falling back" in err


def test_fuzzy_span_matched_false_is_valid_no_match(monkeypatch) -> None:
    _force_fuzzy_backend(monkeypatch, {"ok": True, "matched": False})
    result = rust_bridge.fuzzy_span_result("alpha\n", "nope\n")
    assert result is not None
    assert result["matched"] is False
    assert "start" not in result


def test_fuzzy_span_malformed_still_applies_correct_file_content(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "a.py"
    body = "def handler(request):\n    value = compute(request)\n    return value\n"
    path.write_text(body, encoding="utf-8")
    drifted = "def handler(request):\n    value = compute(request)  # cached\n    return value\n"
    _force_fuzzy_backend(
        monkeypatch,
        {"ok": True, "matched": True, "start": 0, "end": 9999, "score": 0.99, "ambiguous": False},
    )
    result = apply_patch(path, old_text=drifted, new_text="def handler(request):\n    return compute(request)\n")
    assert result.success is True
    assert result.strategy == "fuzzy"
    assert path.read_text(encoding="utf-8") == "def handler(request):\n    return compute(request)\n"
