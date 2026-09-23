"""Legal semantic choices for the Emerald opening."""

from __future__ import annotations

import re
from dataclasses import dataclass

from jev_plays_emerald.actions import PLAYER_NAME, Action, Outcome
from jev_plays_emerald.state import MoveState, Observation, PartyMember
from jev_plays_emerald.journey import configured_target


def legal_actions(observation: Observation, *, suppress_futile: bool = True) -> tuple[Action, ...]:
    from jev_plays_emerald.gameplay import extra_actions

    menu_actions = extra_actions(observation)
    if menu_actions:
        return menu_actions
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
        if not moves:
            # Fight lets Emerald select Struggle when no move can be used.
            # Upstream's normal move executor already handles that message.
            moves = (Action("battle-move:0", "Choose Fight to use Struggle (no usable moves remain)", observation.context_id),)
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


MISSION = "Goal: defeat your rival on Route 103."
GYM_MISSION = (
    "Goal: earn the Stone Badge."
)


def current_mission() -> str:
    return GYM_MISSION if configured_target() == "first-gym" else MISSION


def decision_instructions(
    observation: Observation,
    *,
    advice: str | None = None,
) -> str:
    """Give Jev a goal and a compact method for choosing its next action.

    Recent dialogue and story state are already in the model state. The prompt
    tells Jev how to use that evidence instead of repeating the full route on
    every decision. Luna can add a general recovery hint after repeated stalls,
    while every listed action stays Jev's to pick.
    """

    if observation.game_state == "NAMING_SCREEN" and not observation.party:
        return f"Your requested player name is {PLAYER_NAME}. Confirm the offered naming action."
    hint = f"Luna recovery hint: {advice}" if advice else None
    base = (
        current_mission()
        + " Build the immediate objective from the latest relevant dialogue and observed story state. "
        "Treat explicit directions in that dialogue as the strongest evidence for the next action. Keep the "
        "objective across map changes until the game shows it is complete. Use current location, recent outcomes, "
        "and action labels to choose the action most likely to advance it. When dialogue locates the objective "
        "elsewhere in the current building or area, move there before leaving. At the requested location, "
        "interact with its relevant person or object before leaving. Check whether the "
        "expected progress occurred after each action; if it did not, revise your assumption instead of "
        "repeating the same kind of choice. Do not let the long-term badge goal replace the immediate objective. "
        "Choose exactly one legal action; probabilities express preferences, not success chances."
    )
    return f"{base} {hint}" if hint else base


def _opening_actions(observation: Observation, *, suppress_futile: bool = True) -> tuple[Action, ...]:
    def action(identifier: str, label: str) -> tuple[Action, ...]:
        return (Action(identifier, label, observation.context_id),)

    if observation.game_state in {"TITLE_SCREEN", "MAIN_MENU"} and not observation.party:
        return action("setup:new-game", f"Configured New Game: boy, {PLAYER_NAME}, no nickname")
    if observation.game_state == "NAMING_SCREEN" and not observation.party:
        return action("setup:name", f"Confirm your requested player name: {PLAYER_NAME}")
    if "Task_SetClock_HandleInput" in observation.tasks:
        return action("setup:clock", "Set the opening clock to the default 10:00 AM")
    if "Task_ViewClock_HandleInput" in observation.tasks:
        return action("setup:close-clock", "Close the wall clock after viewing the time")
    if observation.game_state != "OVERWORLD":
        return ()
    if observation.menu_phase == "script":
        return action("dialogue:advance", "Advance mandatory opening dialogue (configured nickname: none)")
    if not observation.controllable or observation.position is None:
        return ()
    if configured_target() == "rival" and observation.opening_flags.defeated_rival_route103:
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

    Doorway and object IDs remain stable. Loaded NPC labels and distance ranks
    use their live positions, including story relocations; a pending decision
    stays valid when the same semantic action IDs remain available.
    """

    def act(identifier: str, label: str) -> Action:
        return Action(identifier, label, observation.context_id)

    choices: list[Action] = []

    # Adjacent warp tiles can be one doorway, but distant exits onto the same
    # map are distinct routes (notably the two ends of Petalburg Woods).
    seen_exits = []
    for exit_ in observation.exits:
        duplicate = any(
            other.destination_id == exit_.destination_id
            and other.target_map == exit_.target_map
            and other.direction == exit_.direction
            and sum(abs(a - b) for a, b in zip(other.target_coordinates, exit_.target_coordinates)) <= 1
            for other in seen_exits
        )
        seen_exits.append(exit_)
        if duplicate:
            continue
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

    from jev_plays_emerald.gameplay import healing_actions, item_actions

    choices.extend(healing_actions(observation))
    choices.extend(item_actions(observation))
    if configured_target() == "first-gym":
        from jev_plays_emerald.training import training_actions
        choices.extend(training_actions(observation))

    futile = recently_futile(observation) if suppress_futile else set()
    usable = [choice for choice in choices if choice.id not in futile]
    # Never empty the menu: a suppressed action beats no action at all.
    choices = usable or choices

    ordered = sorted(dict.fromkeys(choices), key=lambda choice: _menu_order(choice, observation))
    return tuple(ordered[:MAX_OPEN_WORLD_ACTIONS])


# Keep exits and nearby people together: a gym's distant room doors must not
# crowd its entrance NPCs out of the bounded menu. Signs come afterward.
MENU_ORDER = {"walk": 0, "talk": 0, "heal": 0, "field-item": 0, "train": 1, "interact": 2}


def _menu_order(choice: Action, observation: Observation) -> tuple[int, int, str]:
    kind, _, rest = choice.id.partition(":")
    here = observation.position.coordinates if observation.position else (0, 0)
    distance = 0
    if kind == "interact":
        x, y = (int(part) for part in rest.split(":"))
        distance = abs(x - here[0]) + abs(y - here[1])
    elif kind == "walk":
        group, number, x, y = (int(part) for part in rest.split(":"))
        if observation.position and (group, number) == observation.position.map_id:
            distance = abs(x - here[0]) + abs(y - here[1])
    elif kind == "talk":
        npc = next(npc for npc in observation.objects if npc.local_id == int(rest))
        distance = abs(npc.coordinates[0] - here[0]) + abs(npc.coordinates[1] - here[1])
    return MENU_ORDER.get(kind, len(MENU_ORDER)), distance, choice.id


FUTILE_ATTEMPTS = 3
REPEAT_LIMIT = 4


def recently_futile(observation: Observation) -> set[str]:
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
    futile = {action for action, count in interruptions.items() if count >= FUTILE_ATTEMPTS}
    # Oscillation is built from successes: walking up and down a staircase
    # works every time and gets nowhere. Counting completions rather than
    # attempts keeps actions that merely get interrupted a lot - the first
    # rival run needed nine tries at Birch's bag before one landed.
    repeated = {action for action, count in completions.items() if count >= REPEAT_LIMIT}
    return ((futile | impossible) - succeeded) | repeated


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
