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
from modules.modes.util import ensure_facing_direction, wait_until_task_is_active
from modules.player import get_player_location
from modules.pokemon_party import get_party

from jev_plays_emerald.actions import Action, ActionExecutor, FrameState, Outcome


class RealFrameBoundary:
    def read_frame_state(self) -> FrameState:
        state = get_game_state().name
        menu = (
            "battle"
            if state == "BATTLE"
            else "starter"
            if state == "CHOOSE_STARTER"
            else "none"
        )
        return FrameState(state, menu)

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
    def choose_starter(self, starter: str):
        self.bot_mode.set_on_battle_started(lambda _: BattleAction.CustomAction)
        facing = "Left" if get_player_location()[1] == (8, 14) else "Up"
        yield from ensure_facing_direction(facing)
        yield from wait_until_task_is_active("Task_HandleStarterChooseInput", "A")
        self.assertEqual("CHOOSE_STARTER", get_game_state().name)

        action = Action(
            f"starter:{starter.casefold()}",
            f"Choose {starter}",
            "rom-checkpoint",
        )
        outcome, _ = yield from execute(action)

        self.assertEqual(Outcome.SUCCESS, outcome)
        self.assertTrue(get_party())
        self.assertEqual(starter, get_party()[0].species.name)

    @with_save_state("emerald/in_front_of_starter_pokemon_bag.ss1")
    @with_frame_timeout(1_500)
    def test_treecko_executor_acquires_treecko(self):
        yield from self.choose_starter("Treecko")

    @with_save_state("emerald/in_front_of_starter_pokemon_bag.ss1")
    @with_frame_timeout(1_500)
    def test_torchic_executor_acquires_torchic(self):
        yield from self.choose_starter("Torchic")

    @with_save_state("emerald/in_front_of_starter_pokemon_bag.ss1")
    @with_frame_timeout(1_500)
    def test_mudkip_executor_acquires_mudkip(self):
        yield from self.choose_starter("Mudkip")

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


INJURED_TASK4_STATE = PROJECT_ROOT / '.cache/task4-development3/final.ss1'


class TestOpeningRecoveryIntegration(BotTestCase):
    @pytest.mark.skipif(not INJURED_TASK4_STATE.is_file(), reason='requires labeled naturally injured Task 4 run checkpoint')
    @with_save_state(str(INJURED_TASK4_STATE))
    @with_frame_timeout(3_600)
    def test_oldale_center_restores_naturally_injured_party(self):
        from modules.modes.util import wait_for_player_avatar_to_be_controllable
        yield from wait_for_player_avatar_to_be_controllable()
        self.assertTrue(any(p.current_hp < p.total_hp for p in get_party()))
        self.assertTrue(any(move.pp < move.total_pp for p in get_party() for move in p.moves if move))
        self.bot_mode.set_on_battle_started(lambda _: BattleAction.CustomAction)
        action=Action('heal:oldale','Restore party at Oldale','rom-checkpoint')
        outcome, _ = yield from execute(action, frame_limit=3_600)
        self.assertEqual(Outcome.SUCCESS,outcome)
        self.assertEqual(MapRSE.OLDALE_TOWN,get_player_location()[0])
        for pokemon in get_party():
            self.assertEqual(pokemon.total_hp,pokemon.current_hp)
            self.assertEqual('Healthy',pokemon.status_condition.name)
            for move in pokemon.moves:
                if move is not None:
                    self.assertEqual(move.total_pp,move.pp)
