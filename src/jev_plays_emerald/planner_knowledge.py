"""Small, sourced walkthrough slice supplied only when Jev is stuck."""

from __future__ import annotations

import json
from pathlib import Path

from jev_plays_emerald.state import Observation


KNOWLEDGE_PATH = Path(__file__).resolve().parents[2] / "knowledge" / "emerald-opening.json"


def stage_key(observation: Observation) -> str:
    """Map trusted opening progress to the one relevant knowledge stage."""

    if not observation.party:
        return "meet_neighbor" if observation.rival_house_state < 3 else "rescue_birch"
    if not observation.opening_flags.defeated_rival_route103:
        return "reach_rival"
    return "completed"


def knowledge_for(observation: Observation) -> tuple[str, ...]:
    """Return validated facts for the current stage and player identity."""

    document = json.loads(KNOWLEDGE_PATH.read_text())
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("unsupported planner knowledge schema")

    stages = document.get("stages")
    if not isinstance(stages, dict):
        raise ValueError("invalid planner knowledge stages")
    facts = stages.get(stage_key(observation), [])
    if not isinstance(facts, list) or not all(isinstance(fact, str) for fact in facts):
        raise ValueError("planner knowledge facts must be strings")

    selected = list(facts)
    if stage_key(observation) == "meet_neighbor":
        if observation.player_gender == "male":
            selected += [
                "Brendan is the player; May is the rival.",
                "May's House is the neighbor's house.",
            ]
        else:
            selected += [
                "May is the player; Brendan is the rival.",
                "Brendan's House is the neighbor's house.",
            ]
    return tuple(selected)
