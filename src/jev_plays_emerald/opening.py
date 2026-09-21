"""Legal semantic choices for the Emerald opening."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from jev_plays_emerald.actions import Action, Outcome
from jev_plays_emerald.state import MoveState, Observation, PartyMember


def legal_actions(observation: Observation) -> tuple[Action, ...]:
    if observation.game_state == "CHOOSE_STARTER" or observation.menu_phase == "starter":
        return tuple(
            Action(f"starter:{starter.casefold()}", f"Choose {starter}", observation.context_id)
            for starter in ("Treecko", "Torchic", "Mudkip")
        )

    if observation.game_state == "BATTLE" and observation.battle_phase in {"action", "move"}:
        if observation.active_battler is None:
            return ()
        moves = tuple(
            Action(
                f"battle-move:{index}",
                _move_label(move),
                observation.context_id,
            )
            for index, move in enumerate(observation.active_battler.moves)
            if move.pp > 0 and move.usable
        )

        run = (Action("battle-run", "Attempt to escape this wild encounter", observation.context_id),)
        if not open_world_spike_enabled():
            if observation.can_run and observation.battle_phase == "action":
                return moves + run
            return moves
        # Spike: the whole turn menu, not just moves. Switching, items, catching
        # and running are all top-level ("action") choices; the move list is its
        # own phase, so those extras only apply before Fight is selected.
        if observation.battle_phase != "action":
            return moves
        extras = (
            _battle_switch_actions(observation)
            + _battle_item_actions(observation)
            + _catch_actions(observation)
        )
        return moves + extras + (run if observation.can_run else ())

    return _opening_actions(observation)


def _move_label(move: MoveState) -> str:
    accuracy = f"{move.accuracy * 100:g}%" if move.accuracy <= 1 else f"{move.accuracy:g}%"
    mechanics = f"{move.type}, {move.pp}/{move.max_pp} PP, {accuracy} accuracy"
    if move.power > 0:
        mechanics += f", {move.power} power"
    description = f" {move.description}" if move.description else ""
    return f"Use {move.name} ({mechanics}).{description}"


# ItemBattleUse values that a party can act on during the action menu. Escape
# items ("escape") are deliberately left out - running is its own battle-run
# choice, and catch is offered separately so the wild target reads in the label.
BATTLE_ITEM_USES = frozenset({"healing", "pp_recovery", "stat_increase"})


def _battle_switch_actions(observation: Observation) -> tuple[Action, ...]:
    """Offer each benched Pokémon that can still be sent out.

    The active battler is never offered against itself, and a fainted member
    cannot switch in, so both are skipped rather than left for the game to
    reject frame by frame.
    """

    battler = observation.active_battler
    if battler is None or len(observation.party) < 2:
        return ()
    return tuple(
        Action(
            f"battle-switch:{index}",
            f"Switch to {member.species} (Lv {member.level}, {member.hp}/{member.max_hp} HP)",
            observation.context_id,
        )
        for index, member in enumerate(observation.party)
        if index != battler.party_index and member.hp > 0
    )


def _battle_item_actions(observation: Observation) -> tuple[Action, ...]:
    """Offer bag items the party can use on this turn."""

    return tuple(
        Action(
            f"battle-item:{item.name}",
            f"Use {item.name} on your Pokémon ({item.quantity} in the bag)",
            observation.context_id,
        )
        for item in observation.inventory
        if item.quantity > 0 and item.battle_use in BATTLE_ITEM_USES
    )


def _catch_actions(observation: Observation) -> tuple[Action, ...]:
    """Offer a throw for each Poké Ball, but only against a wild Pokémon.

    Trainers cannot be caught, so a live `trainer_id` removes the option
    entirely rather than letting the game refuse the throw.
    """

    opponent = observation.opponent
    if opponent is None or observation.trainer_id is not None:
        return ()
    return tuple(
        Action(
            f"catch:{item.name}",
            f"Throw a {item.name} at the wild {opponent.species} ({item.quantity} in the bag)",
            observation.context_id,
        )
        for item in observation.inventory
        if item.quantity > 0 and item.battle_use == "catch"
    )


# Pinned pret Route103 scripts select these six trainer constants, depending on
# the configured gender and normally chosen starter. Other Route103 trainers do
# not satisfy this identity, even if the save has not beaten the rival yet.
RIVAL_TRAINER_IDS = frozenset({520, 523, 526, 529, 532, 535})


def rival_completed(*, starter_acquired: bool, saw_rival_win: bool,
                    flag_before: bool, flag_after: bool) -> bool:
    return starter_acquired and saw_rival_win and not flag_before and flag_after


@dataclass
class RivalProgress:
    starter_acquired: bool = False
    saw_rival_win: bool = False
    completed: bool = False
    _candidate: bool = False
    _flag_before: bool = True
    _ended_current_battle: bool = False

    def observe(self, observation: Observation) -> None:
        from modules.map_data import MapRSE

        flag = observation.opening_flags.defeated_rival_route103
        if observation.game_state not in {"BATTLE", "BATTLE_ENDING"}:
            self._ended_current_battle = False
        if observation.game_state == "BATTLE" and not self._candidate and not self._ended_current_battle:
            self._candidate = (
                self.starter_acquired and not flag
                and observation.position is not None
                and observation.position.map_id == MapRSE.ROUTE103.value
                and observation.trainer_id in RIVAL_TRAINER_IDS
            )
            if self._candidate:
                self._flag_before = flag
        self.completed = self.completed or rival_completed(
            starter_acquired=self.starter_acquired, saw_rival_win=self.saw_rival_win,
            flag_before=self._flag_before, flag_after=flag,
        )

    def battle_ended(self, outcome: str) -> None:
        self._ended_current_battle = True
        if self._candidate:
            self.saw_rival_win = outcome == "Won"
        self._candidate = False


def party_needs_healing(party: tuple[PartyMember, ...]) -> bool:
    return any(member.hp < member.max_hp or member.status != "Healthy"
               or any(move.pp < move.max_pp for move in member.moves) for member in party)


def _opening_actions(observation: Observation) -> tuple[Action, ...]:
    from modules.map_data import MapRSE

    def action(identifier: str, label: str) -> tuple[Action, ...]:
        return (Action(identifier, label, observation.context_id),)

    if observation.game_state in {"TITLE_SCREEN", "MAIN_MENU"} and not observation.party:
        return action("setup:new-game", "Configured New Game: boy, JEV, no nickname")
    if observation.game_state == "NAMING_SCREEN" and not observation.party:
        return action("setup:name", "Enter configured player name JEV")
    if "Task_SetClock_HandleInput" in observation.tasks:
        return action("setup:clock", "Set the opening clock to the default 10:00 AM")
    if observation.game_state != "OVERWORLD":
        return ()
    if observation.menu_phase == "script":
        return action("dialogue:advance", "Advance mandatory opening dialogue (configured nickname: none)")
    if not observation.controllable or observation.position is None:
        return ()
    here = observation.position.map_id
    flags = observation.opening_flags
    if flags.defeated_rival_route103:
        return ()
    if open_world_spike_enabled():
        # Every overworld choice becomes Jev's. Only character creation and
        # forced cutscenes above this line stay scripted.
        return open_world_actions(observation)
    male = observation.player_gender == "male"
    own1 = MapRSE.LITTLEROOT_TOWN_BRENDANS_HOUSE_1F if male else MapRSE.LITTLEROOT_TOWN_MAYS_HOUSE_1F
    own2 = MapRSE.LITTLEROOT_TOWN_BRENDANS_HOUSE_2F if male else MapRSE.LITTLEROOT_TOWN_MAYS_HOUSE_2F
    rival1 = MapRSE.LITTLEROOT_TOWN_MAYS_HOUSE_1F if male else MapRSE.LITTLEROOT_TOWN_BRENDANS_HOUSE_1F
    rival2 = MapRSE.LITTLEROOT_TOWN_MAYS_HOUSE_2F if male else MapRSE.LITTLEROOT_TOWN_BRENDANS_HOUSE_2F
    if here == MapRSE.INSIDE_OF_TRUCK.value:
        return action("goal:leave-truck", "Step out of the moving truck")
    if here == own1.value:
        return action("goal:upstairs" if not flags.set_wall_clock else "goal:leave-house",
                      "Set the bedroom clock" if not flags.set_wall_clock else "Leave home after the moving-in cutscene")
    if here == own2.value:
        return action("goal:clock" if not flags.set_wall_clock else "goal:downstairs",
                      "Interact with the bedroom clock" if not flags.set_wall_clock else "Go downstairs to Mom")
    if here == rival1.value:
        return action("goal:rival-upstairs" if observation.rival_house_state < 3 else "goal:leave-house",
                      "Meet the new neighbor upstairs" if observation.rival_house_state < 3 else "Leave the neighbor's house")
    if here == rival2.value:
        return action("goal:meet-rival" if observation.rival_house_state < 3 else "goal:downstairs",
                      "Inspect the neighbor's Poké Ball and introduce yourself" if observation.rival_house_state < 3 else "Go downstairs")
    if here == MapRSE.LITTLEROOT_TOWN_PROFESSOR_BIRCHS_LAB.value:
        return action("goal:leave-lab", "Travel to Oldale and find the rival on Route 103")
    if here == MapRSE.LITTLEROOT_TOWN.value and observation.rival_house_state < 3:
        return action("goal:rival-house", "Introduce yourself to the neighbor")
    if not observation.party:
        return action("goal:birch-bag", "Investigate Birch's call for help and choose a Pokémon from his bag")
    if here in {MapRSE.LITTLEROOT_TOWN.value, MapRSE.ROUTE101.value}:
        return action("goal:oldale", "Travel to Oldale Town, where the Pokémon Center can restore HP, status, and PP")
    if here in {MapRSE.OLDALE_TOWN.value, MapRSE.ROUTE103.value}:
        choices = action("goal:rival", "Approach and challenge the rival on Route 103")
        if party_needs_healing(observation.party):
            choices += action("heal:oldale", "Restore the party's HP, status and PP at Oldale Pokémon Center before continuing")
        return choices
    return ()


def dialogue_button(scripts: tuple[str, ...]) -> str:
    # Birch repeats this mandatory question until Yes. B elsewhere advances
    # ordinary text and preserves the configured default of no starter nickname.
    return "A" if "LittlerootTown_ProfessorBirchsLab_EventScript_DeclineSeeingRival" in scripts else "B"


SPIKE_FLAG = "JEV_OPEN_WORLD_SPIKE"
MAX_OPEN_WORLD_ACTIONS = 24


def open_world_spike_enabled() -> bool:
    """Gate the wide-menu spike so the scripted route stays the default."""

    return os.environ.get(SPIKE_FLAG) == "1"


def open_world_actions(observation: Observation) -> tuple[Action, ...]:
    """Offer the primitives available here, with no opinion about the goal.

    Deliberately contains no destination the story needs: no "go fight the
    rival", no "walk to Oldale". Only exits, people, objects and neighbouring
    maps - Jev works out the route from those.

    Pure logic over Observation fields. `state.py` already read this map's
    landmarks on the emulator thread, so nothing here touches PokeBot - which
    is also what keeps this testable without an emulator.

    IDs and labels come from static map definitions rather than live object
    positions, so the offered tuple is byte-identical between frames. A set
    that churned would fail `_pending_is_stale` on every in-flight request and
    the run would never commit to an action.
    """

    def act(identifier: str, label: str) -> Action:
        return Action(identifier, label, observation.context_id)

    choices: list[Action] = []

    # Two exits that land on the same map are the same choice to a player, and
    # offering both splits Jev's probability mass between identical options.
    seen_destinations: set[tuple[int, int]] = set()
    for exit_ in observation.exits:
        if exit_.destination_id in seen_destinations:
            continue
        seen_destinations.add(exit_.destination_id)
        group, number = exit_.target_map
        x, y = exit_.target_coordinates
        label = (
            f"Travel {exit_.direction} out of here into {exit_.destination_name}"
            if exit_.direction
            else f"Go through the doorway at ({x}, {y}) into {exit_.destination_name}"
        )
        choices.append(act(f"walk:{group}:{number}:{x}:{y}", label))

    for npc in observation.objects:
        x, y = npc.coordinates
        who = _readable_symbol(npc.script_symbol) or "someone"
        if npc.trainer_type != "None":
            state = "already beaten" if npc.trainer_defeated else "not yet battled"
            label = f"Approach and talk to {who} at ({x}, {y}), a trainer you have {state}"
        else:
            label = f"Approach and talk to {who} at ({x}, {y})"
        choices.append(act(f"talk:{npc.local_id}", label))

    for sign in observation.signs:
        x, y = sign.coordinates
        if sign.hidden_item:
            label = f"Search the ground at ({x}, {y}), where a {sign.hidden_item} is hidden"
        else:
            what = _readable_symbol(sign.script_symbol) or "object"
            label = f"Walk up to and examine the {what} at ({x}, {y})"
        choices.append(act(f"interact:{x}:{y}", label))

    choices.extend(_reachable_heal_actions(observation))

    futile = recently_futile(observation)
    usable = [choice for choice in choices if choice.id not in futile]
    # Never empty the menu: a suppressed action beats no action at all.
    choices = usable or choices

    ordered = sorted(dict.fromkeys(choices), key=lambda choice: choice.id)
    return tuple(ordered[:MAX_OPEN_WORLD_ACTIONS])


FUTILE_ATTEMPTS = 3
REPEAT_LIMIT = 4


def recently_futile(observation: Observation, threshold: int = FUTILE_ATTEMPTS) -> set[str]:
    """Action IDs that keep being interrupted here and never complete.

    Without this an action whose label promises something the game refuses is
    picked forever: it is interrupted, the situation looks unchanged, and it
    becomes the best-looking option again. Actions that succeeded recently are
    never suppressed - ordinary cutscene interruptions must stay retryable.
    """

    interruptions: dict[str, int] = {}
    completions: dict[str, int] = {}
    impossible: set[str] = set()
    succeeded: set[str] = set()
    for record in observation.recent_outcomes:
        if record.outcome == Outcome.SUCCESS:
            succeeded.add(record.action_id)
            completions[record.action_id] = completions.get(record.action_id, 0) + 1
        elif record.outcome == Outcome.FAILED:
            # "no empty tile around this object" will not become true by
            # retrying, so one refusal is enough to take it off the menu.
            impossible.add(record.action_id)
        else:
            interruptions[record.action_id] = interruptions.get(record.action_id, 0) + 1
    futile = {action for action, count in interruptions.items() if count >= threshold}
    # Oscillation is built from successes: walking up and down a staircase
    # works every time and gets nowhere. Counting completions rather than
    # attempts keeps actions that merely get interrupted a lot - the first
    # rival run needed nine tries at Birch's bag before one landed.
    repeated = {action for action, count in completions.items() if count >= REPEAT_LIMIT}
    return ((futile | impossible) - succeeded) | repeated


# MapRSE.OLDALE_TOWN and MapRSE.ROUTE103, written out so the enumerator stays
# pure data. `test_heal_map_ids_match_pokebot` pins them to PokeBot's table.
OLDALE_HEAL_MAPS = frozenset({(0, 10), (0, 18)})


def _reachable_heal_actions(observation: Observation) -> tuple[Action, ...]:
    """Offer a Pokémon Center run when the party needs it and one is walkable.

    The open-world menu otherwise has no way to heal, so a fainted or worn-down
    party would be stuck. Only Oldale's Center is reachable in the opening; the
    heal executor walks there and back, so it is offered from the town and the
    route it borders. Extending this to every visited Center is later work.
    """

    here = observation.position.map_id if observation.position is not None else None
    if here not in OLDALE_HEAL_MAPS:
        return ()
    if not party_needs_healing(observation.party):
        return ()
    return (
        Action(
            "heal:oldale",
            "Restore the party's HP, status and PP at Oldale Pokémon Center",
            observation.context_id,
        ),
    )


def _readable_symbol(symbol: str) -> str:
    """Turn Emerald's own script name into the thing a player would see.

    `LittlerootTown_BrendansHouse_2F_EventScript_Clock` becomes "Clock". The
    game already knows it is a clock; withholding that only blinds the model to
    what a player reads off the screen.
    """

    if not symbol or symbol.startswith("0x"):
        return ""
    tail = symbol.rsplit("EventScript_", 1)[-1].rsplit("_", 1)[-1]
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", tail).strip()
    return spaced if spaced and not spaced.isdigit() else ""
