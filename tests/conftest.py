from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_retry_state(monkeypatch, tmp_path_factory) -> None:
    """Keep retry-loop disk state per-test: never touch (or get poisoned by)
    the real temp-dir state of a live agent session on this machine."""
    monkeypatch.setenv("CLUXION_SUPERCODER_RETRY_DIR", str(tmp_path_factory.mktemp("retry-state")))
