"""Legal semantic choices for the Emerald opening."""

from __future__ import annotations

from dataclasses import dataclass

from jev_plays_emerald.actions import Action
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

        if observation.can_run and observation.battle_phase == "action":
            return moves + (Action("battle-run", "Attempt to escape this wild encounter", observation.context_id),)
        return moves

    return _opening_actions(observation)


def _move_label(move: MoveState) -> str:
    accuracy = f"{move.accuracy * 100:g}%" if move.accuracy <= 1 else f"{move.accuracy:g}%"
    mechanics = f"{move.type}, {move.pp}/{move.max_pp} PP, {accuracy} accuracy"
    if move.power > 0:
        mechanics += f", {move.power} power"
    description = f" {move.description}" if move.description else ""
    return f"Use {move.name} ({mechanics}).{description}"


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
