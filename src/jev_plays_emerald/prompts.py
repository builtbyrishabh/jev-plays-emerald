"""Model observations omit executor data already represented by the legal menu."""

from dataclasses import asdict

from jev_plays_emerald.state import Observation
from jev_plays_emerald.pokemon_reference import pokemon_reference


def model_state(observation: Observation) -> dict:
    state = asdict(observation)
    # These map tables build action labels; repeating the raw tables adds tokens
    # without giving the player another executable choice. Keep live scripts,
    # dialogue, battle facts and recent outcomes for reasoning about failures.
    for key in ("context_id", "tasks", "exits", "objects", "signs"):
        state.pop(key)
    reference = pokemon_reference(observation)
    if reference:
        state["pokemon_reference"] = reference
    return state
