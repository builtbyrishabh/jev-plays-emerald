from dataclasses import replace

import pytest

from jev_plays_emerald.actions import Action, Outcome
from jev_plays_emerald.jev import TokenUsage
from jev_plays_emerald.planner import PlannerAdvice, PlannerMemory
from jev_plays_emerald.state import (
    InventoryItem,
    MapPosition,
    MoveState,
    Observation,
    OpeningFlags,
    PartyMember,
)


def observation(map_id=(1, 3), **changes):
    return replace(Observation(
        "ctx", "OVERWORLD", MapPosition(map_id, (1, 2), "Up"), True,
        "none", "none", (), (), OpeningFlags(False, False, False), None, (),
    ), **changes)


def record(memory, before, after=None, action_id="walk:1:3:1:1", outcome=Outcome.SUCCESS):
    memory.record(before, after or before, Action(action_id, "stairs", "ctx"), outcome, "blocked")


def advice(action_id="walk:1:3:1:1"):
    return PlannerAdvice(
        hint="Try the next objective.",
        destination_action_id=action_id,
        location="Observed destination",
        avoid="Do not repeat the blocked route.",
        success_signal="story progress changes",
        model="test",
        usage=TokenUsage(),
        latency_ms=1,
    )


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
    memory.accept(obs, advice())
    assert memory.reason(obs) is None


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


@pytest.mark.parametrize("changes", [
    {"game_state": "BATTLE", "battle_phase": "action"},
    {"game_state": "CHOOSE_STARTER", "menu_phase": "starter"},
    {"controllable": False},
    {"menu_phase": "script"},
    {"menu_phase": "start"},
    {"battle_phase": "action"},
])
def test_navigation_correction_waits_for_overworld_control(changes):
    obs = observation()
    memory = PlannerMemory()
    for _ in range(3):
        record(memory, obs, outcome=Outcome.INTERRUPTED)
    assert memory.reason(replace(obs, **changes)) is None
    assert memory.reason(obs) == "three repeated attempts without story progress"


def test_decision_brief_counts_failed_choices_and_keeps_other_maps_separate():
    obs = observation((0, 9), player_gender="male")
    memory = PlannerMemory()
    memory.sync_progress(obs)
    north = Action(
        "walk:0:16:10:19", "Travel North into Route101 at (10, 19)", "ctx"
    )
    for _ in range(3):
        memory.record(obs, obs, north, Outcome.INTERRUPTED, "NeedPokemonTrigger")
    other_map = observation((0, 8))

    brief = memory.decision_brief(obs, (north,), None)
    other_brief = memory.decision_brief(other_map, (north,), None)

    assert "confirmed_facts" not in brief
    assert brief["legal_actions"][0]["attempts_without_progress"] == 3
    assert brief["legal_actions"][0]["last_result"] == "NeedPokemonTrigger"
    assert other_brief["legal_actions"][0]["attempts_without_progress"] == 0


def test_successful_travel_can_trigger_help_without_becoming_a_dead_end():
    obs = observation((1, 4))
    memory = PlannerMemory()
    memory.sync_progress(obs)
    exit_lab = Action(
        "walk:1:4:6:12", "Go through the doorway into Littleroot Town", "ctx"
    )

    for _ in range(3):
        memory.record(obs, obs, exit_lab, Outcome.SUCCESS, None)

    assert memory.reason(obs) == "three repeated attempts without story progress"
    assert memory.decision_brief(obs, (exit_lab,), None)["legal_actions"][0] == {
        "action_id": exit_lab.id,
        "attempts_without_progress": 3,
        "last_result": "success",
    }


def test_brief_exposes_lunas_grounded_recovery_hint_to_jev():
    action = Action("walk:0:9:14:8", "Enter May's House at (14, 8)", "ctx")
    planner_advice = PlannerAdvice(
        hint="Meet May upstairs.",
        destination_action_id=action.id,
        location="May's House entrance at (14, 8)",
        avoid="Do not retry Route 101.",
        success_signal="rival_house_state changes",
        model="test",
        usage=TokenUsage(),
        latency_ms=1,
    )

    brief = PlannerMemory().decision_brief(observation((0, 9)), (action,), planner_advice)

    assert brief["planner_hint"] == planner_advice.guidance
    assert "planner_follow_up" not in brief
    assert "Exact location: May's House entrance at (14, 8)" in planner_advice.text
    assert "Avoid: Do not retry Route 101" in planner_advice.text
    assert "Success looks like: rival_house_state changes" in planner_advice.text


def test_general_stall_requests_help_without_repeating_one_action():
    memory = PlannerMemory()
    obs = observation((0, 10))
    memory.sync_progress(obs)

    for index in range(7):
        record(memory, obs, action_id=f"walk:{index}")
        assert memory.reason(obs) is None
    record(memory, obs, action_id="walk:7")

    assert memory.reason(obs) == "eight decisions without story progress"


@pytest.mark.parametrize("progress", ["map", "party", "inventory", "money"])
def test_useful_travel_and_preparation_reset_general_stall(progress):
    memory = PlannerMemory()
    obs = observation((0, 10))
    memory.sync_progress(obs)
    for index in range(7):
        record(memory, obs, action_id=f"walk:{index}")

    move = MoveState("Pound", 35, 35)
    member = PartyMember("Treecko", 6, 20, 20, "Healthy", (move,))
    changes = {
        "map": {"position": MapPosition((0, 11), (1, 2), "Up")},
        "party": {"party": (member,)},
        "inventory": {"inventory": (InventoryItem("Potion", 1),)},
        "money": {"money": 100},
    }[progress]
    advanced = replace(obs, **changes)

    record(memory, obs, advanced, action_id="prepare")
    assert memory.reason(advanced) is None

    record(memory, advanced, action_id="next")
    assert memory.reason(advanced) is None


def test_planner_context_contains_no_walkthrough_or_saved_route_instructions():
    memory = PlannerMemory()
    obs = observation()
    context = memory.planner_context(obs, (), None)
    brief = memory.decision_brief(obs, (), None)

    assert "walkthroughKnowledge" not in context
    assert "verifiedLessons" not in context
    assert "rememberedPlan" not in context
    assert "currentGoal" not in context
    assert context["currentObjective"] == "meet May, the rival"
    assert context["rivalName"] == "May"
    assert "observedRoutes" not in context
    assert "deadEnds" not in context
    assert "current_goal" not in brief
    assert "observed_routes" not in brief
    assert "avoid_repeating" not in brief


def test_luna_context_tracks_the_other_rival_identity():
    memory = PlannerMemory()

    context = memory.planner_context(
        observation(player_gender="female"), (), None
    )

    assert context["currentObjective"] == "meet Brendan, the rival"
    assert context["rivalName"] == "Brendan"


def test_luna_context_names_the_route_103_rival_after_the_rescue():
    member = PartyMember("Treecko", 5, 20, 20, "Healthy", ())
    context = PlannerMemory().planner_context(observation(party=(member,)), (), None)

    assert context["currentObjective"] == "find and defeat May, the rival, on Route 103"
    assert context["rivalName"] == "May"
