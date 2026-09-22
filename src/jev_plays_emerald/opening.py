"""Legal semantic choices for the Emerald opening."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from jev_plays_emerald.actions import PLAYER_NAME, Action, Outcome
from jev_plays_emerald.state import MoveState, Observation, PartyMember


def legal_actions(observation: Observation, *, suppress_futile: bool = True) -> tuple[Action, ...]:
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

        # Switching, items, catching and running are all top-level ("action")
        # choices; the move list is its own phase, so those extras only apply
        # before Fight has been selected.
        if observation.battle_phase != "action":
            return moves
        run = (Action("battle-run", "Attempt to escape this wild encounter", observation.context_id),)
        extras = (
            _battle_switch_actions(observation)
            + _battle_item_actions(observation)
            + _catch_actions(observation)
        )
        return moves + extras + (run if observation.can_run else ())

    return _opening_actions(observation, suppress_futile=suppress_futile)


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
    """Materially hurt, rather than a few PP short of full.

    Every spent PP used to count, so a starter at full health was told its
    party was hurt after one battle - and Jev went hunting for a nurse it
    cannot walk up to instead of heading for Route 103.
    """

    return any(
        member.hp < member.max_hp
        or member.status != "Healthy"
        or any(move.pp == 0 for move in member.moves)
        for member in party
    )


MISSION = (
    "You are playing Pokemon Emerald. Your mission, in order: find and rescue "
    "Professor Birch, choosing a starter when the rescue encounter prompts you, "
    "then travel north and beat your rival on Route 103."
)
_BASE_MISSION = MISSION + (
    " "
    "Choose exactly one legal action for the current state and treat the probabilities "
    "as your preference over the offered actions."
)


def authored_hints_enabled() -> bool:
    return os.environ.get("JEV_AUTHORED_HINTS", "1").strip().casefold() not in {
        "0", "false", "no", "off",
    }


def decision_instructions(
    observation: Observation,
    *,
    advice: str | None = None,
    authored_hints: bool = True,
) -> str:
    """Mission text plus a hint for the situation Jev is actually deciding in.

    The open-world action set deliberately carries no route opinion, so the
    guidance the model needs to make a good choice lives here instead: which
    direction the story wants next, and how to play the battle in front of it.
    This nudges without deciding - every listed action stays Jev's to pick.
    """

    if observation.game_state == "NAMING_SCREEN" and not observation.party:
        return f"Your requested player name is {PLAYER_NAME}. Confirm the offered naming action."
    hint = _situation_hint(observation) if advice is None and authored_hints else advice
    if advice:
        hint = (
            "Planner advice is a persistent objective, not a fixed action. Adapt it to your "
            "CURRENT map and recent results; skip steps already completed. You choose the "
            "action. Prefer progress over revisiting completed locations. Planner: " + advice
        )
    return f"{_BASE_MISSION} {hint}" if hint else _BASE_MISSION


def _situation_hint(observation: Observation) -> str:
    from modules.map_data import MapRSE

    if observation.game_state == "CHOOSE_STARTER" or observation.menu_phase == "starter":
        return (
            "This is your starter choice: Treecko (Grass), Torchic (Fire), or Mudkip (Water). "
            "Any of them can win the opening, so pick one and commit. Expect the rival to "
            "carry the starter that beats your type."
        )
    if observation.game_state == "BATTLE":
        if observation.trainer_id in RIVAL_TRAINER_IDS:
            return (
                "This is the rival battle that completes your mission. Their lone Pokemon is "
                "under-levelled, so attack with your highest-damage move every turn and never run."
            )
        if observation.trainer_id is not None:
            return "This is a trainer battle. Use your strongest damaging move and do not run."
        return (
            "This is a wild battle. End it fast with your highest-damage move; only run if your "
            "Pokemon is close to fainting and you need it healthy for the rival."
        )
    flags = observation.opening_flags
    here = observation.position.map_id if observation.position is not None else None
    if here == MapRSE.INSIDE_OF_TRUCK.value:
        return (
            "You are riding in the back of the moving truck taking your family to Littleroot "
            "Town, and it has arrived. The boxes around you are scenery. Go through the doorway "
            "to step outside; nothing else in here advances the game."
        )
    if not flags.set_wall_clock:
        return (
            "You just moved into your new house in Littleroot Town. The game will not let you "
            "leave yet: first go up to your bedroom and examine the wall clock to set the time, "
            "then head back downstairs. Do not try to leave the house until the clock is set."
        )
    if not observation.party:
        if observation.rival_house_state < 3:
            # The town's north exit tiles run NeedPokemonTrigger and push you
            # back until this is done, so "head north" is not yet advice. Name
            # the houses: both are on the menu, and one of them is your own.
            neighbour, own = (
                ("Mays House", "Brendans House")
                if observation.player_gender == "male"
                else ("Brendans House", "Mays House")
            )
            return (
                "You have no Pokemon yet, and Littleroot will not let you walk north out of town "
                f"until you have introduced yourself to the new neighbour. {own} is your own "
                f"home and has nothing left for you. Go into {neighbour}, up to the bedroom on "
                "the second floor, and talk to the child there."
            )
        return (
            "You have no Pokemon yet. Professor Birch is being attacked on Route 101, straight "
            "north of Littleroot Town - leave the house and head north to reach his bag."
        )
    if not flags.rescued_birch:
        return "Finish helping Professor Birch, then follow where he leads."
    if not flags.defeated_rival_route103:
        if here == MapRSE.ROUTE103.value:
            hint = (
                "You are already on Route 103. Your rival is north of you on this route - walk "
                "the whole way over to him and start the battle."
            )
        else:
            hint = (
                "Head north out of Littleroot: cross Route 101, pass through Oldale Town, then "
                "go up Route 103 to find and challenge your rival."
            )
        if party_needs_healing(observation.party):
            hint += (
                " Your party is hurt - healing for free at the Oldale Town Pokemon Center before "
                "the rival fight is usually worth it. Pick the Pokemon Center action itself: it "
                "walks in, heals and comes back out, while walking into the building yourself "
                "heals nothing."
            )
        return hint
    return ""


def _opening_actions(observation: Observation, *, suppress_futile: bool = True) -> tuple[Action, ...]:
    def action(identifier: str, label: str) -> tuple[Action, ...]:
        return (Action(identifier, label, observation.context_id),)

    if observation.game_state in {"TITLE_SCREEN", "MAIN_MENU"} and not observation.party:
        return action("setup:new-game", f"Configured New Game: boy, {PLAYER_NAME}, no nickname")
    if observation.game_state == "NAMING_SCREEN" and not observation.party:
        return action("setup:name", f"Confirm your requested player name: {PLAYER_NAME}")
    if "Task_SetClock_HandleInput" in observation.tasks:
        return action("setup:clock", "Set the opening clock to the default 10:00 AM")
    if observation.game_state != "OVERWORLD":
        return ()
    if observation.menu_phase == "script":
        return action("dialogue:advance", "Advance mandatory opening dialogue (configured nickname: none)")
    if not observation.controllable or observation.position is None:
        return ()
    if observation.opening_flags.defeated_rival_route103:
        return ()
    # Every overworld choice from here is Jev's: character creation and forced
    # cutscenes are handled above, and nothing below decides where to go.
    return open_world_actions(observation, suppress_futile=suppress_futile)


def dialogue_button(scripts: tuple[str, ...]) -> str:
    # Birch repeats this mandatory question until Yes. B elsewhere advances
    # ordinary text and preserves the configured default of no starter nickname.
    return "A" if "LittlerootTown_ProfessorBirchsLab_EventScript_DeclineSeeingRival" in scripts else "B"


MAX_OPEN_WORLD_ACTIONS = 24


def open_world_actions(observation: Observation, *, suppress_futile: bool = True) -> tuple[Action, ...]:
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
        if exit_.direction:
            label = f"Travel {exit_.direction} out of here into {exit_.destination_name}"
        elif exit_.destination_name:
            label = f"Go through the doorway at ({x}, {y}) into {exit_.destination_name}"
        else:
            # A dynamic warp. Say only what is certain: it leaves this map.
            label = f"Go through the doorway at ({x}, {y}) to leave this map"
        choices.append(act(f"walk:{group}:{number}:{x}:{y}", label))

    for npc in observation.objects:
        x, y = npc.coordinates
        who = _readable_symbol(npc.script_symbol) or "someone"
        if npc.trainer_type != "None":
            state = "already beaten" if npc.trainer_defeated else "not yet battled"
            label = f"Approach and talk to {who} at ({x}, {y}), a trainer you have {state}"
        else:
            label = f"Approach and talk to {who} at ({x}, {y})"
        if npc.loaded:
            choices.append(act(f"talk:{npc.local_id}", label))
        else:
            # Too far away for the game to track, so only the tile they stand
            # on is known. Walking there and pressing A reaches them anyway.
            choices.append(act(f"interact:{x}:{y}", f"Walk all the way over to {who} at ({x}, {y})"))

    for sign in observation.signs:
        x, y = sign.coordinates
        if sign.hidden_item:
            label = f"Search the ground at ({x}, {y}), where a {sign.hidden_item} is hidden"
        else:
            what = _readable_symbol(sign.script_symbol) or "object"
            label = f"Walk up to and examine the {what} at ({x}, {y})"
        choices.append(act(f"interact:{x}:{y}", label))

    choices.extend(_reachable_heal_actions(observation))

    futile = recently_futile(observation) if suppress_futile else set()
    usable = [choice for choice in choices if choice.id not in futile]
    # Never empty the menu: a suppressed action beats no action at all.
    choices = usable or choices

    here = observation.position.coordinates if observation.position is not None else (0, 0)
    ordered = sorted(dict.fromkeys(choices), key=lambda choice: _menu_order(choice, here))
    return tuple(ordered[:MAX_OPEN_WORLD_ACTIONS])


# Ways off this map come first, then people, then whatever is nearest. A
# crowded route enumerates more landmarks than the menu holds, and ordering by
# ID alone would drop `walk:` before `interact:` - cutting the exits and
# leaving Jev nowhere to go.
MENU_ORDER = {"walk": 0, "heal": 1, "talk": 2, "interact": 3}


def _menu_order(choice: Action, here: tuple[int, int]) -> tuple[int, int, str]:
    kind, _, rest = choice.id.partition(":")
    distance = 0
    if kind == "interact":
        x, y = (int(part) for part in rest.split(":"))
        distance = abs(x - here[0]) + abs(y - here[1])
    return MENU_ORDER.get(kind, len(MENU_ORDER)), distance, choice.id


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
