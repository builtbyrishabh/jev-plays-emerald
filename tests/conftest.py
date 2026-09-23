"""Keep the suite away from the real run log.

A mode built without explicit telemetry appends to `runs/decisions.jsonl`, the
same file the replay harness reads. Left alone the suite fills it with fixture
decisions, so every test gets a throwaway log instead.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / ".cache/pokebot-gen3"))

from jev_plays_emerald import telemetry


@pytest.fixture(autouse=True)
def isolated_run_log(tmp_path, monkeypatch):
    monkeypatch.setattr(telemetry, "RUN_LOG", tmp_path / "decisions.jsonl")
    # Legacy opening fixtures keep their original target. Journey tests select
    # first-gym explicitly, and the normal app defaults to first-gym.
    monkeypatch.setenv("JEV_TARGET", "rival")
