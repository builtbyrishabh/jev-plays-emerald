"""The battle turn menu: moves, switching, items, catching and running.

End-to-end input flows need the emulator; here we pin the menu each kind
produces and that every offered ID has an executor that parses it.
"""

from dataclasses import replace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / ".cache/pokebot-gen3"))
import pytest

from jev_plays_emerald.actions import ACTION_EXECUTORS
from jev_plays_emerald.opening import legal_actions
from jev_plays_emerald.state import (
    ActiveBattler,
    InventoryItem,
    MapPosition,
    MoveState,
    Observation,
    OpeningFlags,
    OpponentBattler,
    PartyMember,
)


def member(species="Treecko", hp=20, max_hp=20, level=5):
    return PartyMember(species, level, hp, max_hp, "Healthy", (MoveState("Pound", 35, 35),))


def battle(**changes):
    """A wild action-phase battle with a single healthy lead and empty bag."""

    base = Observation(
        "ctx", "BATTLE", MapPosition((0, 18), (10, 4), "Up"), False, "battle", "action",
        (member(),), (), OpeningFlags(True, False, False),
        ActiveBattler(0, (MoveState("Pound", 35, 35),)), (),
        opponent=OpponentBattler("Poochyena", 3, 12, 12, "Healthy"),
    )
    return replace(base, **changes)


def ids(observation):
    return {action.id for action in legal_actions(observation)}


def test_the_action_phase_offers_the_whole_turn_menu():
    obs = battle(
        party=(member(), member("Poochyena")),
        inventory=(InventoryItem("Potion", 3, "healing"), InventoryItem("Poké Ball", 5, "catch")),
        can_run=True,
    )
    assert ids(obs) == {
        "battle-move:0", "battle-switch:1", "battle-item:Potion", "catch:Poké Ball", "battle-run",
    }


def test_move_phase_still_offers_only_moves():
    obs = battle(
        battle_phase="move",
        party=(member(), member("Poochyena")),
        inventory=(InventoryItem("Potion", 3, "healing"),),
    )
    assert ids(obs) == {"battle-move:0"}


def test_switch_skips_the_active_and_any_fainted_member():
    obs = battle(party=(member(), member("Zigzagoon", hp=0), member("Wingull")))
    assert ids(obs) & {"battle-switch:0", "battle-switch:1", "battle-switch:2"} == {"battle-switch:2"}


def test_switch_is_absent_with_a_single_pokemon():
    assert not any(a.startswith("battle-switch") for a in ids(battle()))


def test_battle_item_offers_only_usable_kinds_in_stock():
    obs = battle(inventory=(
        InventoryItem("Potion", 2, "healing"),
        InventoryItem("Ether", 1, "pp_recovery"),
        InventoryItem("X Attack", 1, "stat_increase"),
        InventoryItem("Repel", 1, "not_usable"),
        InventoryItem("Super Potion", 0, "healing"),
        InventoryItem("Escape Rope", 1, "escape"),
    ))
    items = {a for a in ids(obs) if a.startswith("battle-item:")}
    assert items == {"battle-item:Potion", "battle-item:Ether", "battle-item:X Attack"}


def test_catch_only_against_a_wild_pokemon():
    with_balls = battle(inventory=(InventoryItem("Poké Ball", 5, "catch"),))
    assert "catch:Poké Ball" in ids(with_balls)
    # A trainer's Pokémon cannot be caught.
    assert not any(a.startswith("catch:") for a in ids(replace(with_balls, trainer_id=535)))
    # Nor with an empty bag or no opponent read.
    assert not any(a.startswith("catch:") for a in ids(battle()))


def test_every_offered_battle_action_has_an_executor():
    obs = battle(
        party=(member(), member("Poochyena")),
        inventory=(InventoryItem("Potion", 3, "healing"), InventoryItem("Poké Ball", 5, "catch")),
        can_run=True,
    )
    for action in legal_actions(obs):
        assert action.id.partition(":")[0] in ACTION_EXECUTORS, action.id


@pytest.mark.parametrize("bad", ["battle-switch:x", "battle-item:", "catch:"])
def test_malformed_battle_action_ids_are_rejected(bad):
    from jev_plays_emerald.actions import Action

    kind = bad.partition(":")[0]
    with pytest.raises(ValueError):
        ACTION_EXECUTORS[kind](Action(bad, "bad", "ctx"))
