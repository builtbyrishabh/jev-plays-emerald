"""Bounded Gen III species facts from the pinned PokéBot data, without RAM reads."""

from dataclasses import asdict, dataclass
from functools import lru_cache
from math import prod

from jev_plays_emerald.state import Observation


@dataclass(frozen=True)
class LevelMove:
    level: int
    name: str
    type: str
    power: int


@dataclass(frozen=True)
class LevelEvolution:
    level: int
    species: str
    moves_at_evolution: tuple[LevelMove, ...]


@dataclass(frozen=True)
class SpeciesFacts:
    types: tuple[str, ...]
    moves: tuple[LevelMove, ...]
    evolutions: tuple[LevelEvolution, ...]
    damage_taken: tuple[tuple[str, float], ...]


@lru_cache(maxsize=512)
def _species_facts(name: str) -> SpeciesFacts | None:
    # These API calls only read upstream's JSON tables. In particular, never use
    # get_opponent(), a battler's moves, or any personality/RNG-derived state.
    from modules.pokemon import get_species_by_name, get_species_by_index, get_type_by_index

    try:
        species = get_species_by_name(name)
    except KeyError:
        return None
    moves = tuple(
        LevelMove(entry.level, entry.move.name, entry.move.type.name, entry.move.base_power)
        for entry in sorted(species.learnset.level_up, key=lambda entry: entry.level)
    )
    evolutions = []
    for evolution in species.evolutions:
        # Conditional evolutions (e.g. Wurmple's personality split) need more
        # than a level. Omit them rather than imply a guaranteed target.
        if evolution.method != "level" or evolution.method_param > 20:
            continue
        target = get_species_by_index(evolution.target_species_index)
        evolution_moves = tuple(
            LevelMove(entry.level, entry.move.name, entry.move.type.name, entry.move.base_power)
            for entry in target.learnset.level_up if entry.level == evolution.method_param
        )
        evolutions.append(LevelEvolution(evolution.method_param, target.name, evolution_moves))
    damage_taken = []
    for index in range(18):
        attack_type = get_type_by_index(index)
        if attack_type.name == "???":
            continue
        multiplier = prod(attack_type.get_effectiveness_against(t) for t in species.types)
        damage_taken.append((attack_type.name, multiplier))
    return SpeciesFacts(tuple(t.name for t in species.types), moves, tuple(evolutions), tuple(damage_taken))


def pokemon_reference(observation: Observation) -> dict:
    """Give player and coach species context for at most six party slots and one foe.

    Level-up suggestions stop at level 20 and three moves per species to keep
    first-gym prompts small. Opponent facts contain types only: public learnsets
    do not establish what the observed opponent actually knows.
    """
    if not observation.party and observation.opponent is None:
        return {}
    reference = {
        "scope": (
            "Gen III general species knowledge; not observed opponent moves. "
            "Multipliers are type-only, excluding abilities/status; omitted types are 1x. "
            "Next moves/evolutions cover levels through 20; power is base power, not predicted damage."
        ),
        "party": [],
    }
    opponent_facts = _species_facts(observation.opponent.species) if observation.opponent else None
    for member in observation.party[:6]:
        facts = _species_facts(member.species)
        if facts is None:
            reference["party"].append({"species": member.species, "reference_unavailable": True})
            continue
        entry = {
            "species": member.species,
            "types": list(facts.types),
            "next_moves": [asdict(move) for move in facts.moves if member.level < move.level <= 20][:3],
            "level_evolutions": [
                {
                    "level": evolution.level,
                    "species": evolution.species,
                    "moves_at_evolution": [asdict(move) for move in evolution.moves_at_evolution],
                }
                for evolution in facts.evolutions
            ],
        }
        if opponent_facts:
            entry["damage_taken_from_opponent_types"] = {
                kind: multiplier for kind, multiplier in facts.damage_taken
                if kind in opponent_facts.types
            }
        reference["party"].append(entry)
    if observation.opponent:
        entry = {"species": observation.opponent.species}
        if opponent_facts:
            entry.update({
                "types": list(opponent_facts.types),
                "damage_taken_by_type": {
                    kind: multiplier for kind, multiplier in opponent_facts.damage_taken if multiplier != 1
                },
            })
        else:
            entry["reference_unavailable"] = True
        reference["opponent"] = entry
    return reference
