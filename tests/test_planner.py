from dataclasses import replace

from jev_plays_emerald.actions import Action, Outcome
from jev_plays_emerald.planner import PlannerMemory
from jev_plays_emerald.state import MapPosition, Observation, OpeningFlags


def observation(map_id=(1, 3), **changes):
    return replace(Observation(
        "ctx", "OVERWORLD", MapPosition(map_id, (1, 2), "Up"), True,
        "none", "none", (), (), OpeningFlags(False, False, False), None, (),
    ), **changes)


def record(memory, before, after=None, action_id="walk:1:3:1:1", outcome=Outcome.SUCCESS):
    memory.record(before, after or before, Action(action_id, "stairs", "ctx"), outcome, "blocked")


def test_three_repeated_attempts_request_one_correction_and_reset():
    obs = observation()
    memory = PlannerMemory()
    assert memory.sync_progress(obs) is False
    assert memory.reason(obs) is None
    for _ in range(2):
        record(memory, obs, outcome=Outcome.INTERRUPTED)
        assert memory.reason(obs) is None
    record(memory, obs, outcome=Outcome.INTERRUPTED)
    assert memory.reason(obs) == "three repeated attempts without story progress"
    memory.accept(obs)
    assert memory.reason(obs) is None
    assert len(memory.history) == 3  # evidence survives an intervention


def test_successful_stairs_can_loop_but_ids_on_other_maps_do_not_collide():
    memory = PlannerMemory()
    upstairs, downstairs = observation(), observation((1, 2))
    memory.sync_progress(upstairs)
    for _ in range(2):
        record(memory, upstairs, downstairs, "talk:1")
        record(memory, downstairs, upstairs, "talk:1")
    assert memory.reason(upstairs) is None
    record(memory, upstairs, downstairs, "talk:1")
    assert memory.reason(downstairs) is not None


def test_story_progress_silently_forgets_old_repetition():
    memory = PlannerMemory()
    before = observation()
    memory.sync_progress(before)
    for _ in range(3):
        record(memory, before)
    after = observation(rival_house_state=3)
    assert memory.sync_progress(after) is True
    assert memory.reason(after) is None


def test_battle_and_forced_dialogue_never_trigger_repetition():
    memory = PlannerMemory()
    obs = observation()
    memory.sync_progress(obs)
    for _ in range(5):
        record(memory, replace(obs, game_state="BATTLE"), action_id="battle-move:0")
        record(memory, obs, action_id="dialogue:advance")
    assert memory.reason(obs) is None
