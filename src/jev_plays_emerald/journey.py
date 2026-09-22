"""Run targets and input-only proof of the first Stone Badge."""

from dataclasses import dataclass
import os
from typing import Literal

from jev_plays_emerald.state import Observation

Target = Literal["rival", "first-gym"]
# pret/pokeemerald: include/constants/opponents.h and RustboroCity_Gym/scripts.inc.
ROXANNE_TRAINER_ID = 265
RUSTBORO_GYM = (11, 3)


def configured_target() -> Target:
    value = os.environ.get("JEV_TARGET", "first-gym").strip()
    if value not in {"rival", "first-gym"}:
        raise ValueError("JEV_TARGET must be rival or first-gym")
    return value


def target_flag(observation: Observation, target: Target) -> bool:
    flags = observation.opening_flags
    return flags.stone_badge if target == "first-gym" else flags.defeated_rival_route103


@dataclass
class GymProgress:
    """A loaded badge, wild win, or post-loss animation cannot prove a gym win."""

    starter_acquired: bool = False
    completed: bool = False
    _candidate: bool = False
    _saw_win: bool = False
    _ended_current_battle: bool = False

    def observe(self, observation: Observation) -> None:
        badge = observation.opening_flags.stone_badge
        if observation.game_state not in {"BATTLE", "BATTLE_ENDING"}:
            self._ended_current_battle = False
        if observation.game_state == "BATTLE" and not self._candidate and not self._ended_current_battle:
            self._candidate = (
                not badge and bool(observation.party)
                and observation.position is not None
                and observation.position.map_id == RUSTBORO_GYM
                and observation.trainer_id == ROXANNE_TRAINER_ID
            )
        if self._saw_win and badge:
            self.completed = True

    def battle_ended(self, outcome: str) -> None:
        self._ended_current_battle = True
        if self._candidate:
            self._saw_win = outcome == "Won"
        self._candidate = False
