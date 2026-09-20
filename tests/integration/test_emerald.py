"""ROM-backed checks using labeled save states from the pinned PokéBot checkout."""

from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).parents[2]
POKEBOT_ROOT = PROJECT_ROOT / ".cache" / "pokebot-gen3"
ROM = POKEBOT_ROOT / "roms" / "Pokemon - Emerald Version (USA, Europe).gba"
if not (POKEBOT_ROOT / "tests/states/emerald/new_game_inside_player_house.ss1").is_file() or not ROM.is_file():
    pytest.skip("run scripts/bootstrap.py and provide the supported Emerald ROM", allow_module_level=True)
sys.path.insert(0, str(POKEBOT_ROOT))

import modules.gui.multi_select_window
from tests.utility import BotTestCase, set_next_rng_seed, with_frame_timeout, with_save_state

from modules.context import context
from modules.map_data import MapRSE
from modules.memory import get_game_state
from modules.modes import BattleAction
from modules.player import get_player_location

from jev_plays_emerald.actions import Action, ActionExecutor, FrameState, Outcome


class RealFrameBoundary:
    def read_frame_state(self) -> FrameState:
        state = get_game_state().name
        return FrameState(state, "battle" if state == "BATTLE" else "none")

    def read_context_id(self) -> str:
        return "rom-checkpoint"

    def reset_held_buttons(self) -> None:
        context.emulator.reset_held_buttons()


def execute(action: Action, frame_limit: int = 1_500):
    executor = ActionExecutor(RealFrameBoundary(), frame_limit=frame_limit)
    run = executor.execute(action)
    while True:
        outcome = next(run)
        if outcome is not None:
            return outcome, executor
        yield


class TestEmeraldActionIntegration(BotTestCase):
    @with_save_state("emerald/new_game_inside_player_house.ss1")
    @with_frame_timeout(1_000)
    def test_littleroot_door_returns_the_destination_map_and_coordinates(self):
        action = Action("walk:1:2:2:2", "Go upstairs", "rom-checkpoint")

        outcome, _ = yield from execute(action)

        self.assertEqual(Outcome.SUCCESS, outcome)
        self.assertEqual((MapRSE.LITTLEROOT_TOWN_MAYS_HOUSE_2F, (1, 2)), get_player_location())

    @with_save_state("emerald/new_game_inside_player_house.ss1")
    @with_frame_timeout(200)
    def test_littleroot_blocked_tile_fails_without_moving_the_player(self):
        self.assertEqual((MapRSE.LITTLEROOT_TOWN_MAYS_HOUSE_1F, (5, 6)), get_player_location())
        action = Action("walk:1:2:6:6", "Walk into the table", "rom-checkpoint")

        outcome, executor = yield from execute(action, frame_limit=120)

        self.assertEqual(Outcome.FAILED, outcome)
        self.assertIn("Could not find a path", executor.failure_reason)
        self.assertEqual((MapRSE.LITTLEROOT_TOWN_MAYS_HOUSE_1F, (5, 6)), get_player_location())
        self.assertEqual(0, context.emulator.reset_held_buttons())

    @with_save_state("emerald/in_tall_grass_after_receiving_pokeballs.ss1")
    @with_frame_timeout(1_000)
    def test_route101_encounter_interrupts_without_stale_movement(self):
        self.bot_mode.set_on_battle_started(lambda _: BattleAction.CustomAction)
        # Controlled checkpoint evidence: this seed deterministically starts an
        # encounter on the first eastward grass step. It is not a continuous-run claim.
        set_next_rng_seed(0)
        action = Action("walk:0:16:15:15", "Cross the grass", "rom-checkpoint")

        outcome, executor = yield from execute(action)

        self.assertEqual(Outcome.INTERRUPTED, outcome)
        self.assertEqual(action, executor.interrupted_action)
        self.assertEqual((MapRSE.ROUTE101, (16, 11)), get_player_location())
        self.assertEqual("BATTLE_STARTING", get_game_state().name)
        self.assertEqual(0, context.emulator.reset_held_buttons())
