from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import sys
sys.path.insert(0, str(Path(__file__).parents[1] / ".cache/pokebot-gen3"))
import pytest
from jev_plays_emerald.opening import RivalProgress, legal_actions, rival_completed
from jev_plays_emerald.state import Observation, OpeningFlags, MapPosition, PartyMember, MoveState

@pytest.mark.parametrize('starter,win,before,after,expected', [(True,True,False,True,True),(True,False,True,True,False),(True,False,False,True,False),(True,False,False,False,False),(False,True,False,True,False)])
def test_completion_requires_new_observed_win(starter,win,before,after,expected):
    assert rival_completed(starter_acquired=starter,saw_rival_win=win,flag_before=before,flag_after=after) is expected

def observation(**changes):
    base=Observation('test','OVERWORLD',MapPosition((0,18),(10,4),'Up'),True,'none','none',(),(),OpeningFlags(True,False,False),None,())
    return replace(base,**changes)


@pytest.mark.parametrize("task,expected", [
    ("Task_SetClock_HandleInput", "setup:clock"),
    ("Task_ViewClock_HandleInput", "setup:close-clock"),
])
def test_clock_screens_always_offer_the_matching_normal_input(task, expected):
    state = observation(game_state="UNKNOWN", position=None, controllable=False, tasks=(task,))
    assert [action.id for action in legal_actions(state)] == [expected]


def test_view_clock_executor_cancels_with_b_and_never_sets_time(monkeypatch):
    from modules.context import context
    from modules import tasks
    from jev_plays_emerald.actions import Action, _setup_plan

    pressed = []
    monkeypatch.setattr(context, "emulator", SimpleNamespace(press_button=pressed.append))
    monkeypatch.setattr(tasks, "task_is_active", lambda name: not pressed)
    list(_setup_plan(Action("setup:close-clock", "Close wall clock", "clock")).start())
    assert pressed == ["B"]

def test_wild_win_and_other_route103_trainer_never_complete():
    for trainer in (None, 100):
        progress=RivalProgress(starter_acquired=True)
        progress.observe(observation(game_state='BATTLE',trainer_id=trainer))
        progress.battle_ended('Won')
        progress.observe(observation(opening_flags=OpeningFlags(True,False,True)))
        assert not progress.completed

def test_rival_loss_does_not_complete_but_new_win_and_flag_do():
    progress=RivalProgress(starter_acquired=True)
    progress.observe(observation(game_state='BATTLE',trainer_id=535))
    progress.battle_ended('Lost')
    assert not progress.completed
    progress.observe(observation())
    progress.observe(observation(game_state='BATTLE',trainer_id=535))
    progress.battle_ended('Won')
    assert not progress.completed
    progress.observe(observation(opening_flags=OpeningFlags(True,False,True)))
    assert progress.completed

def test_loaded_completed_save_cannot_be_a_new_win():
    progress=RivalProgress(starter_acquired=True)
    progress.observe(observation(game_state='BATTLE',trainer_id=535,opening_flags=OpeningFlags(True,False,True)))
    progress.battle_ended('Won')
    progress.observe(observation(opening_flags=OpeningFlags(True,False,True)))
    assert not progress.completed

def test_an_injured_party_near_oldale_is_offered_the_centre():
    """The only way to heal: the open-world menu has no nurse to walk up to."""
    member=PartyMember('Treecko',5,10,20,'Healthy',(MoveState('Pound',30,35),))
    assert 'heal:oldale' in {a.id for a in legal_actions(observation(party=(member,)))}


def test_spent_pp_alone_is_not_an_injury():
    """A full-health starter after one battle does not need a Pokemon Center."""
    from jev_plays_emerald.opening import party_needs_healing
    used=PartyMember('Torchic',5,20,20,'Healthy',(MoveState('Scratch',32,35),))
    empty=PartyMember('Torchic',5,20,20,'Healthy',(MoveState('Scratch',0,35),))
    assert not party_needs_healing((used,))
    assert party_needs_healing((empty,))


def test_dialogue_declines_nickname_but_accepts_mandatory_rival_visit():
    from jev_plays_emerald.opening import dialogue_button
    assert dialogue_button(('LittlerootTown_ProfessorBirchsLab_EventScript_DeclineSeeingRival', 'Std_MsgboxYesNo')) == 'A'
    assert dialogue_button(('LittlerootTown_ProfessorBirchsLab_EventScript_GiveStarter',)) == 'B'


def test_run_is_offered_only_when_the_observer_confirms_it_is_legal():
    from jev_plays_emerald.state import ActiveBattler
    battle=observation(game_state='BATTLE', menu_phase='battle', battle_phase='action',
                       active_battler=ActiveBattler(0,(MoveState('Pound',35,35),)))
    assert [a.id for a in legal_actions(battle)] == ['battle-move:0']
    assert [a.id for a in legal_actions(replace(battle,can_run=True))] == ['battle-move:0','battle-run']
    assert all('switch' not in a.id for a in legal_actions(battle))


def test_fully_healed_party_has_no_redundant_center_choice():
    member=PartyMember('Treecko',5,20,20,'Healthy',(MoveState('Pound',35,35),))
    assert [a.id for a in legal_actions(observation(party=(member,)))] == []


def test_door_animation_is_not_counted_as_navigation_stall(monkeypatch):
    from modules import memory, player
    from modules.modes.util import walking
    from jev_plays_emerald.actions import Action, _walk_plan

    location = ((0,9),(14,8))

    def door_animation(*_):
        nonlocal location
        yield from (None for _ in range(70))
        location = ((0,10),(2,2))

    monkeypatch.setattr(walking, 'navigate_to', door_animation)
    monkeypatch.setattr(memory, 'get_game_state', lambda: memory.GameState.OVERWORLD)
    monkeypatch.setattr(player, 'get_player_location', lambda: location)
    monkeypatch.setattr(player, 'player_avatar_is_controllable', lambda: False)
    assert len(list(_walk_plan(Action('walk:0:9:14:8','Enter neighbor house','door')).start())) == 70


def test_walk_activates_a_doorway_when_already_standing_on_its_warp(monkeypatch):
    from modules import memory, player
    from modules.context import context
    from modules.modes.util import walking
    from jev_plays_emerald.actions import Action, _walk_plan

    lab_door = ((1, 4), (6, 12))
    town = ((0, 9), (7, 16))
    location = lab_door
    held_buttons = []

    def already_there(*_):
        if False:
            yield

    class Emulator:
        def hold_button(self, button):
            nonlocal location
            held_buttons.append(button)
            location = town

    previous_emulator = context.emulator
    context.emulator = Emulator()
    monkeypatch.setattr(walking, 'navigate_to', already_there)
    monkeypatch.setattr(memory, 'get_game_state', lambda: memory.GameState.OVERWORLD)
    monkeypatch.setattr(player, 'get_player_location', lambda: location)
    monkeypatch.setattr(player, 'get_player_avatar', lambda: SimpleNamespace(facing_direction='Up'))
    monkeypatch.setattr(player, 'player_avatar_is_controllable', lambda: True)
    try:
        frames = list(_walk_plan(Action('walk:1:4:6:12', 'Leave Birch\'s Lab', 'door')).start())
    finally:
        context.emulator = previous_emulator

    assert held_buttons == ['Down']
    assert len(frames) == 1


def test_post_loss_battle_animation_cannot_rearm_rival_for_later_wild_win():
    progress=RivalProgress(starter_acquired=True)
    rival=observation(game_state='BATTLE',trainer_id=532)
    progress.observe(rival)
    progress.battle_ended('Lost')
    progress.observe(rival)  # Emerald can still report BATTLE after its outcome callback.
    progress.observe(observation())
    progress.observe(observation(game_state='BATTLE',trainer_id=None))
    progress.battle_ended('Won')
    assert not progress.saw_rival_win
