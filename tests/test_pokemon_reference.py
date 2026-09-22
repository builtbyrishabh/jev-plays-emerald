"""Reference output uses public species data, never an opponent's move slots."""

import json

from jev_plays_emerald.prompts import model_state
from jev_plays_emerald.state import (
    MapPosition, Observation, OpeningFlags, OpponentBattler, PartyMember,
)


def observation(*species, opponent=None):
    return Observation(
        "ctx", "BATTLE" if opponent else "OVERWORLD", MapPosition((0, 3), (1, 2), "Up"),
        True, "none", "action" if opponent else "none",
        tuple(PartyMember(name, level, 20, 20, "Healthy", ()) for name, level in species),
        (), OpeningFlags(True, True, True), None, (), opponent=opponent,
    )


def test_party_reference_exposes_nearby_training_and_evolution_opportunities():
    state = model_state(observation(("Torchic", 9)))
    assert "pokemon_reference" in state
    reference = state["pokemon_reference"]
    torchic = reference["party"][0]
    assert torchic["types"] == ["Fire"]
    assert torchic["next_moves"][0] == {"level": 10, "name": "Ember", "type": "Fire", "power": 40}
    evolution = torchic["level_evolutions"][0]
    assert evolution["level"] == 16
    assert evolution["species"] == "Combusken"
    assert evolution["moves_at_evolution"] == [{"level": 16, "name": "Double Kick", "type": "Fighting", "power": 30}]
    assert "general species knowledge" in reference["scope"]


def test_opponent_chart_multiplies_dual_types_and_keeps_immunity():
    state = model_state(observation(("Mudkip", 10), opponent=OpponentBattler("Geodude", 12, 25, 25, "Healthy")))
    opponent = state.get("pokemon_reference", {}).get("opponent", {})
    assert opponent.get("damage_taken_by_type", {}).get("Water") == 4
    assert opponent["damage_taken_by_type"]["Grass"] == 4
    assert opponent["damage_taken_by_type"]["Electric"] == 0
    assert opponent["damage_taken_by_type"]["Normal"] == 0.5
    assert "moves" not in opponent
    assert "next_moves" not in opponent
    assert set(opponent) == {"species", "types", "damage_taken_by_type"}


def test_party_defense_uses_species_types_without_claiming_opponent_moves():
    state = model_state(observation(("Torchic", 10), opponent=OpponentBattler("Geodude", 12, 25, 25, "Healthy")))
    party = state.get("pokemon_reference", {}).get("party", [])
    assert party and party[0]["damage_taken_from_opponent_types"] == {"Ground": 2, "Rock": 2}


def test_reference_is_bounded_and_omits_learned_levels():
    state = model_state(observation(*[(name, 10) for name in ("Torchic", "Mudkip", "Treecko", "Shroomish", "Ralts", "Wurmple")],
                                    opponent=OpponentBattler("Geodude", 12, 25, 25, "Healthy")))
    assert "pokemon_reference" in state
    reference = state["pokemon_reference"]
    for species in reference["party"]:
        assert len(species["next_moves"]) <= 3
        assert all(10 < move["level"] <= 20 for move in species["next_moves"])
    assert len(json.dumps(reference)) < 4500


def test_unknown_species_and_empty_party_do_not_block_decisions():
    assert "pokemon_reference" not in model_state(observation())
    state = model_state(observation(("MissingNo", 10)))
    assert "pokemon_reference" in state
    assert state["pokemon_reference"]["party"] == [{"species": "MissingNo", "reference_unavailable": True}]
