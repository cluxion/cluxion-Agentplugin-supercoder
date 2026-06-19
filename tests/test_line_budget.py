from __future__ import annotations

from cluxion_agentplugin_supercoder.core.line_budget import budget_for, is_coding_task


def test_mode_cap_enforced() -> None:
    decision = budget_for("inspect", requested_lines=121)
    assert decision.allowed is False
    assert decision.reason == "line_budget_exceeded:inspect"
    assert decision.max_lines == 120


def test_unknown_mode_falls_back_to_default_cap() -> None:
    decision = budget_for("totally_new_mode", requested_lines=121)
    assert decision.allowed is False
    assert decision.max_lines == 120


def test_session_budget_exhaustion() -> None:
    decision = budget_for("inspect", requested_lines=100, remaining=50)
    assert decision.allowed is False
    assert decision.reason == "session_line_budget_exhausted"


def test_within_budget_decrements_remaining() -> None:
    decision = budget_for("refactor_unit", requested_lines=200, remaining=1000)
    assert decision.allowed is True
    assert decision.remaining_lines == 800


def test_is_coding_task_korean_and_english() -> None:
    assert is_coding_task("fix the login bug")
    assert is_coding_task("이 버그 수정해줘")
    assert not is_coding_task("오늘 날씨 어때?")


def test_is_coding_task_word_boundaries_avoid_false_positives() -> None:
    assert not is_coding_task("what is the latest news")
    assert not is_coding_task("add a prefix to each line")
    assert not is_coding_task("who won the contest yesterday")


def test_is_coding_task_true_positives_with_word_boundaries() -> None:
    assert is_coding_task("fix the bug in main.py")
    assert is_coding_task("add a unit test for login")
    assert is_coding_task("refactor the auth module")
    assert is_coding_task("debug the timeout issue")
