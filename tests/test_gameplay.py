from dataclasses import replace

import pytest

from jev_plays_emerald.actions import Action, ACTION_EXECUTORS
from jev_plays_emerald.state import Observation, OpeningFlags, MapPosition, PartyMember, MoveState, InventoryItem


def observation(**changes):
    member = PartyMember('Mudkip', 10, 10, 30, 'Healthy', (MoveState('Tackle', 20, 35),))
    base = Observation('test', 'OVERWORLD', MapPosition((0, 0), (10, 4), 'Up'), True,
                       'none', 'none', (member,), (), OpeningFlags(True, True, True), None, ())
    return replace(base, **changes)


@pytest.mark.parametrize('map_id, action', [((0, 0), 'heal:petalburg'), ((8, 4), 'heal:petalburg'),
                                          ((0, 3), 'heal:rustboro'), ((11, 5), 'heal:rustboro'),
                                          ((2, 2), 'heal:oldale')])
def test_injured_party_can_heal_in_city_or_inside_center(map_id, action):
    from jev_plays_emerald.gameplay import healing_actions
    state = observation(position=MapPosition(map_id, (7, 8), 'Up'))
    assert [a.id for a in healing_actions(state)] == [action]
    assert ACTION_EXECUTORS['heal'](Action(action, '', 'test'))


def test_yes_no_is_a_player_choice_and_not_dialogue_spam():
    from jev_plays_emerald.gameplay import extra_actions
    state = observation(menu_phase='yes_no', controllable=False)
    assert [a.id for a in extra_actions(state)] == ['answer:yes', 'answer:no']
    assert 'yes_no' not in ACTION_EXECUTORS['dialogue'](Action('dialogue', '', 'test')).allowed_menu_phases


def test_replacement_excludes_fainted_party_slots():
    from jev_plays_emerald.gameplay import extra_actions
    healthy = observation().party[0]
    state = observation(game_state='PARTY_MENU', menu_phase='forced_switch',
                        party=(replace(healthy, hp=0), healthy))
    assert [a.id for a in extra_actions(state)] == ['forced-switch:1']


def test_shop_only_offers_affordable_stock_and_exit():
    from jev_plays_emerald.gameplay import extra_actions
    from jev_plays_emerald.state import ShopItem
    state = observation(menu_phase='shop', money=300,
                        shop_items=(ShopItem('Potion', 300), ShopItem('Super Potion', 700)))
    assert [a.id for a in extra_actions(state)] == ['shop-buy:Potion:1', 'shop-exit']


def test_move_learning_labels_target_moves_and_preserves_decline():
    from jev_plays_emerald.gameplay import extra_actions
    state = observation(menu_phase='learn_move', learning_party_index=0,
                        learning_move=MoveState('Water Gun', 25, 25))
    actions = extra_actions(state)
    assert [a.id for a in actions] == ['learn-move:0', 'learn-move:skip']
    assert 'Tackle' in actions[0].label and 'Water Gun' in actions[0].label


def test_field_potion_only_targets_living_injured_members():
    from jev_plays_emerald.gameplay import item_actions
    injured = observation().party[0]
    state = observation(party=(replace(injured, hp=0), injured, replace(injured, hp=30)),
                        inventory=(InventoryItem('Potion', 1, 'healing'),))
    assert [a.id for a in item_actions(state)] == ['field-item:Potion:1']


def test_evolution_has_explicit_allow_and_cancel_choices():
    from jev_plays_emerald.gameplay import extra_actions
    assert [a.id for a in extra_actions(observation(menu_phase='evolution'))] == ['evolve:yes', 'evolve:no']


def test_tutorial_battle_offers_only_forced_progress():
    from jev_plays_emerald.gameplay import extra_actions
    assert [a.id for a in extra_actions(observation(tutorial_battle=True, game_state='BATTLE', menu_phase='battle'))] == ['tutorial:advance']


# ROM-backed input evidence: these checkpoints are labeled development fixtures,
# not a claim that an autonomous player reached them.
from pathlib import Path
import modules.gui.multi_select_window
from tests.utility import BotTestCase, with_frame_timeout, with_save_state

_INJURED = Path(__file__).parents[1] / '.cache/task4-development3/final.ss1'


@pytest.mark.skipif(not _INJURED.is_file(), reason='requires naturally injured development checkpoint')
class TestGameplayInputs(BotTestCase):
    def execute(self, identifier, expected="success", allow_timeout=False):
        from modules.context import context
        from jev_plays_emerald.actions import ActionExecutor, FrameState, Outcome
        from jev_plays_emerald.state import ObservationReader
        reader = ObservationReader()
        class Boundary:
            def read_frame_state(self):
                state = reader.read()
                return FrameState(state.game_state, state.menu_phase)
            def read_context_id(self):
                return 'rom'
            def reset_held_buttons(self):
                context.emulator.reset_held_buttons()
        executor = ActionExecutor(Boundary(), frame_limit=3600)
        for outcome in executor.execute(Action(identifier, identifier, 'rom')):
            if outcome is not None:
                if allow_timeout and outcome is Outcome.FAILED and executor.last_reason == 'action exceeded 3600 frames':
                    return
                if expected is None:
                    self.assertNotEqual(outcome, Outcome.FAILED, executor.last_reason)
                else:
                    self.assertEqual(outcome.value, expected, executor.last_reason)
                return
            yield

    @with_save_state(str(_INJURED))
    @with_frame_timeout(7200)
    def test_buy_and_apply_potion_then_heal_inside_oldale(self):
        from modules.context import context
        from modules.items import get_item_by_name, get_item_bag
        from modules.map_data import MapRSE
        from modules.modes import BattleAction
        from modules.modes.util.walking import navigate_to, wait_for_player_avatar_to_be_controllable
        from modules.modes.util.higher_level_actions import talk_to_npc
        from modules.pokemon_party import get_party
        from jev_plays_emerald.state import ObservationReader
        self.bot_mode.set_on_battle_started(lambda _: BattleAction.CustomAction)
        yield from wait_for_player_avatar_to_be_controllable()
        from modules.map import get_map_data_for_current_position
        yield from navigate_to(MapRSE.OLDALE_TOWN, (6, 17))
        door = next(w for w in get_map_data_for_current_position().warps
                    if tuple(w.destination_location.map_group_and_number) == MapRSE.OLDALE_TOWN_MART.value)
        yield from navigate_to(MapRSE.OLDALE_TOWN, door.local_coordinates)
        yield from self.execute("talk:1")
        reader = ObservationReader()
        yield from self.execute('dialogue:advance', expected='interrupted')
        self.assertEqual('shop', reader.read().menu_phase)
        before = get_item_bag().quantity_of(get_item_by_name('Potion'))
        yield from self.execute('shop-buy:Potion:1')
        self.assertEqual(before + 1, get_item_bag().quantity_of(get_item_by_name('Potion')))
        yield from self.execute('shop-exit')
        yield from wait_for_player_avatar_to_be_controllable('B')
        injured = next(p.index for p in get_party() if 0 < p.current_hp < p.total_hp)
        before_hp = get_party()[injured].current_hp
        yield from self.execute(f'field-item:Potion:{injured}')
        self.assertGreater(get_party()[injured].current_hp, before_hp)
        exit_tile = get_map_data_for_current_position().warps[0].local_coordinates
        yield from navigate_to(MapRSE.OLDALE_TOWN_MART, exit_tile)
        yield from navigate_to(MapRSE.OLDALE_TOWN, (6, 16))
        yield from self.execute('heal:oldale')
        for p in get_party():
            self.assertEqual(p.total_hp, p.current_hp)
            self.assertTrue(all(m.pp == m.total_pp for m in p.moves if m))


    @with_save_state(str(Path(__file__).parents[1] / '.cache/first-gym-hybrid-20260922-2/final.ss1'))
    @with_frame_timeout(2000)
    def test_route102_petalburg_exit_has_a_real_walkable_path(self):
        from modules.map_data import MapRSE
        from modules.modes.util.walking import navigate_to, wait_for_player_avatar_to_be_controllable
        from modules.player import get_player_location
        from modules.map_path import calculate_path, PathFindingError
        from jev_plays_emerald.state import ObservationReader
        yield from wait_for_player_avatar_to_be_controllable()
        if tuple(get_player_location()[0]) != MapRSE.ROUTE102.value:
            yield from navigate_to(MapRSE.ROUTE102, (49, 10))
        reader = ObservationReader(landmarks=True)
        state = reader.read()
        exit_ = next(e for e in state.exits if e.destination_id == MapRSE.PETALBURG_CITY.value)
        self.assertEqual('West', exit_.direction)
        self.assertNotEqual((29, 20), exit_.target_coordinates)
        path = calculate_path(get_player_location(), (exit_.target_map, exit_.target_coordinates), no_surfing=True)
        self.assertTrue(path)
        self.assertEqual(MapRSE.PETALBURG_CITY.value, path[-1].map)

    @with_save_state(str(Path(__file__).parents[1] / '.cache/first-gym-hybrid-20260922-2/final.ss1'))
    @with_frame_timeout(3000)
    def test_natural_map_transitions_preserve_story_flags(self):
        from modules.map_data import MapRSE
        from modules.modes.util.walking import navigate_to, wait_for_player_avatar_to_be_controllable
        from jev_plays_emerald.state import ObservationReader
        yield from wait_for_player_avatar_to_be_controllable()
        reader = ObservationReader()
        before = reader.read()
        self.assertTrue(before.opening_flags.received_pokedex)
        for target, tile in ((MapRSE.OLDALE_TOWN, (6, 16)), (MapRSE.OLDALE_TOWN_POKEMON_CENTER_1F, (7, 8))):
            for _ in navigate_to(target, tile):
                state = reader.read()
                self.assertEqual(before.opening_flags, state.opening_flags)
                self.assertEqual(before.lab_state, state.lab_state)
                self.assertEqual(before.rival_house_state, state.rival_house_state)
                yield

    @with_save_state(str(Path(__file__).parents[1] / '.cache/first-gym-hybrid-20260922-4/final.ss1'))
    @with_frame_timeout(2000)
    def test_accidental_nickname_yes_recovers_with_default_species_name(self):
        from modules.memory import get_game_state, GameState
        from modules.pokemon_party import get_party
        self.assertEqual(GameState.NAMING_SCREEN, get_game_state())
        before = get_party()[0].name
        yield from self.execute('nickname:keep')
        self.assertNotEqual(GameState.NAMING_SCREEN, get_game_state())
        self.assertEqual(before, get_party()[0].name)
        self.assertEqual(get_party()[0].species.name.upper(), get_party()[0].name.upper())
        from jev_plays_emerald.state import ObservationReader
        from jev_plays_emerald.gameplay import extra_actions
        from modules.memory import get_event_var
        while get_game_state() is not GameState.OVERWORLD:
            yield
        yield from self.execute('dialogue:advance', expected='interrupted')
        prompt = ObservationReader().read()
        self.assertIn('LittlerootTown_ProfessorBirchsLab_EventScript_GoSeeRival', prompt.scripts)
        self.assertIn('answer:yes', [a.id for a in extra_actions(prompt)])
        yield from self.execute('answer:yes')
        yield from self.execute('dialogue:advance')
        self.assertEqual(3, get_event_var('BIRCH_LAB_STATE'))

    @with_save_state(str(Path(__file__).parents[1] / '.cache/first-gym-hybrid-20260922-5/final.ss1'))
    @with_frame_timeout(24000)
    def test_relocated_norman_is_offered_and_wally_tutorial_completes(self):
        from jev_plays_emerald.state import ObservationReader
        from jev_plays_emerald.opening import open_world_actions
        reader = ObservationReader(landmarks=True)
        state = reader.read()
        self.assertEqual((8, 1), state.position.map_id)
        self.assertIn('talk:1', [a.id for a in open_world_actions(state)])
        self.assertEqual((4, 107), next(npc.coordinates for npc in state.objects if npc.local_id == 1))
        yield from self.execute('talk:1')
        saw_tutorial = False
        while True:
            state = reader.read()
            if state.opening_flags.petalburg_tutorial:
                break
            if state.tutorial_battle:
                saw_tutorial = True
                yield from self.execute('tutorial:advance', expected=None)
            elif state.menu_phase == 'script':
                yield from self.execute('dialogue:advance', expected=None, allow_timeout=True)
            elif state.menu_phase == 'yes_no':
                yield from self.execute('answer:yes')
            else:
                yield
        self.assertTrue(saw_tutorial)
        self.assertEqual((8, 1), state.position.map_id)

    def reach_level_up_menu(self, target_phase):
        from modules.context import context
        from modules.modes import BattleAction
        from modules.modes.util import spin
        from modules.memory import get_game_state, GameState
        from jev_plays_emerald.state import ObservationReader
        self.stats.has_encounter_with_personality_value = lambda _: False
        self.bot_mode.set_on_battle_started(lambda _: BattleAction.CustomAction)
        while get_game_state() not in {GameState.BATTLE, GameState.BATTLE_STARTING}:
            context.emulator.press_button('Left' if context.emulator.get_frame_count() % 16 < 8 else 'Right')
            yield
        reader = ObservationReader()
        while True:
            state = reader.read()
            if state.menu_phase == target_phase:
                return state
            if state.game_state == 'BATTLE' and state.battle_phase == 'action':
                slot = next(i for i, move in enumerate(state.active_battler.moves) if move.usable and move.pp > 0 and move.power > 0)
                yield from self.execute(f'battle-move:{slot}')
            else:
                context.emulator.press_button('B')
                yield

    @with_save_state('emerald/in_tall_grass_before_levelling_up_and_learning_move_with_no_empty_slot.ss1')
    @with_frame_timeout(4000)
    def test_level_up_replaces_the_move_chosen_by_player(self):
        from modules.pokemon_party import get_party
        state = yield from self.reach_level_up_menu('learn_move')
        move_name = state.learning_move.name
        index = state.learning_party_index
        yield from self.execute('learn-move:2')
        self.assertEqual(get_party()[index].moves[2].move.name, move_name)

    @with_save_state('emerald/in_tall_grass_before_levelling_up_and_evolving.ss1')
    @with_frame_timeout(4000)
    def test_evolution_uses_player_allow_choice(self):
        from modules.pokemon_party import get_party
        before = get_party()[0].species.name
        yield from self.reach_level_up_menu('evolution')
        yield from self.execute('evolve:yes')
        self.assertNotEqual(get_party()[0].species.name, before)


@pytest.mark.parametrize("script", ["LittlerootTown_ProfessorBirchsLab_EventScript_GiveStarter", "LittlerootTown_ProfessorBirchsLab_EventScript_GiveStarterEvent"])
def test_starter_nickname_remains_configured_no(script):
    from jev_plays_emerald.gameplay import extra_actions
    state = observation(menu_phase='yes_no', scripts=(script,))
    assert [a.id for a in extra_actions(state)] == ['answer:no']


def test_resumed_move_selection_honors_selected_slot(monkeypatch):
    from types import SimpleNamespace
    from modules import battle_move_replacing
    from modules.context import context
    phase = battle_move_replacing.LearnMoveState.SelectMoveToReplace
    cursor = 0
    confirmed = []
    def press(button):
        nonlocal cursor, phase
        if button == 'Down':
            cursor += 1
        elif button == 'Up':
            cursor -= 1
        elif button == 'A':
            confirmed.append(cursor)
            phase = battle_move_replacing.LearnMoveState.DialogueNotActive
    monkeypatch.setattr(context, 'emulator', SimpleNamespace(press_button=press))
    monkeypatch.setattr(battle_move_replacing, 'get_task', lambda _: None)
    monkeypatch.setattr(battle_move_replacing, 'get_learn_move_state', lambda: phase)
    monkeypatch.setattr(battle_move_replacing, '_get_move_selection_cursor', lambda: cursor)
    list(ACTION_EXECUTORS['learn-move'](Action('learn-move:2', '', 'test')).start())
    assert confirmed == [2]


def test_caught_pokedex_page_has_forced_continue_action():
    from jev_plays_emerald.gameplay import extra_actions
    state = observation(game_state='UNKNOWN', menu_phase='none', tasks=('Task_HandleCaughtMonPageInput',))
    assert [a.id for a in extra_actions(state)] == ['caught-dex:continue']


def test_border_exit_uses_reachable_tile_when_midpoint_is_blocked(monkeypatch):
    from modules import map_path
    from jev_plays_emerald.state import _reachable_border_tile
    monkeypatch.setattr(map_path, 'PathFindingError', ValueError)
    def path(source, destination, *, no_surfing):
        assert no_surfing
        if destination[1] != (9, 3):
            raise ValueError('blocked')
        return []
    monkeypatch.setattr(map_path, 'calculate_path', path)
    _reachable_border_tile.cache_clear()
    assert _reachable_border_tile(MapPosition((0, 17), (3, 3), 'Left'), 'West', 0,
                                  (10, 10), (10, 10), (0, 0)) == (9, 3)


@pytest.mark.parametrize("game_state,pointer", [("CHANGE_MAP", bytes(4)), ("CHANGE_MAP", bytes([1,0,0,2])), ("UNKNOWN", bytes([1,0,0,2]))])
def test_transient_unavailable_save_preserves_coherent_story_without_choices(monkeypatch, game_state, pointer):
    from modules import memory, tasks
    from jev_plays_emerald.state import ObservationReader
    reader = ObservationReader()
    reader._last_observation = observation(opening_flags=OpeningFlags(True, True, True), lab_state=5, rival_house_state=4)
    monkeypatch.setattr(memory, 'get_game_state', lambda: getattr(memory.GameState, game_state))
    monkeypatch.setattr(memory, 'read_symbol', lambda *args, **kwargs: pointer)
    monkeypatch.setattr(tasks, 'get_tasks', lambda: ())
    def no_save_read(*args, **kwargs):
        pytest.fail('uninitialized save block must not be read')
    monkeypatch.setattr(memory, 'get_event_flag', no_save_read)
    state = reader.read()
    assert state.opening_flags.received_pokedex and state.lab_state == 5 and state.rival_house_state == 4
    assert not state.controllable and not state.exits


def test_loaded_pokemon_naming_screen_can_keep_species_name():
    from jev_plays_emerald.gameplay import extra_actions
    state = observation(game_state='NAMING_SCREEN', controllable=False)
    assert [a.id for a in extra_actions(state)] == ['nickname:keep']
    assert extra_actions(replace(state, party=())) == ()
