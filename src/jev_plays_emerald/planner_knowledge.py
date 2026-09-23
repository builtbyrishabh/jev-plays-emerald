"""Small, sourced walkthrough slice for the current mission milestone."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jev_plays_emerald.state import Observation
from jev_plays_emerald.journey import configured_target


KNOWLEDGE_PATH = Path(__file__).resolve().parents[2] / "knowledge" / "emerald-opening.json"


@lru_cache(maxsize=4)
def _knowledge_document(path: Path) -> dict:
    document = json.loads(path.read_text())
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("unsupported planner knowledge schema")
    return document


def stage_key(observation: Observation) -> str:
    """Map trusted opening progress to the one relevant knowledge stage."""

    if not observation.party:
        return "meet_neighbor" if observation.rival_house_state < 3 else "rescue_birch"
    if not observation.opening_flags.defeated_rival_route103:
        return "reach_rival"
    if configured_target() == "rival" or observation.opening_flags.stone_badge:
        return "completed"
    if not observation.opening_flags.received_pokedex:
        return "receive_pokedex"
    if not observation.opening_flags.petalburg_tutorial:
        return "visit_petalburg"
    if not observation.opening_flags.devon_goods_saved:
        return "cross_woods"
    return "first_gym"


def knowledge_for(observation: Observation) -> tuple[str, ...]:
    """Return validated facts for the current stage and player identity."""

    document = _knowledge_document(KNOWLEDGE_PATH)

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
