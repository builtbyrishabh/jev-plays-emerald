from dataclasses import replace

from jev_plays_emerald.actions import Action, Outcome
from jev_plays_emerald.jev import TokenUsage
from jev_plays_emerald.planner import PlannerAdvice, PlannerMemory
from jev_plays_emerald.planner_memory import EvidenceLedger
from jev_plays_emerald.state import MapPosition, Observation, OpeningFlags


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


def test_three_repeated_attempts_request_one_correction_and_reset(tmp_path):
    obs = observation()
    memory = PlannerMemory(ledger=EvidenceLedger(tmp_path / "memory.json"))
    assert memory.sync_progress(obs) is False
    assert memory.reason(obs) is None
    for _ in range(2):
        record(memory, obs, outcome=Outcome.INTERRUPTED)
        assert memory.reason(obs) is None
    record(memory, obs, outcome=Outcome.INTERRUPTED)
    assert memory.reason(obs) == "three repeated attempts without story progress"
    memory.accept(obs, advice())
    assert memory.reason(obs) is None
    assert len(memory.history) == 3  # evidence survives an intervention


def test_successful_stairs_can_loop_but_ids_on_other_maps_do_not_collide(tmp_path):
    memory = PlannerMemory(ledger=EvidenceLedger(tmp_path / "memory.json"))
    upstairs, downstairs = observation(), observation((1, 2))
    memory.sync_progress(upstairs)
    for _ in range(2):
        record(memory, upstairs, downstairs, "talk:1")
        record(memory, downstairs, upstairs, "talk:1")
    assert memory.reason(upstairs) is None
    record(memory, upstairs, downstairs, "talk:1")
    assert memory.reason(downstairs) is not None


def test_story_progress_silently_forgets_old_repetition(tmp_path):
    memory = PlannerMemory(ledger=EvidenceLedger(tmp_path / "memory.json"))
    before = observation()
    memory.sync_progress(before)
    for _ in range(3):
        record(memory, before)
    after = observation(rival_house_state=3)
    assert memory.sync_progress(after) is True
    assert memory.reason(after) is None


def test_battle_and_forced_dialogue_never_trigger_repetition(tmp_path):
    memory = PlannerMemory(ledger=EvidenceLedger(tmp_path / "memory.json"))
    obs = observation()
    memory.sync_progress(obs)
    for _ in range(5):
        record(memory, replace(obs, game_state="BATTLE"), action_id="battle-move:0")
        record(memory, obs, action_id="dialogue:advance")
    assert memory.reason(obs) is None


def test_decision_brief_counts_failed_choices_and_keeps_other_maps_separate(tmp_path):
    obs = observation((0, 9), player_gender="male")
    memory = PlannerMemory(ledger=EvidenceLedger(tmp_path / "memory.json"))
    memory.sync_progress(obs)
    north = Action(
        "walk:0:16:10:19", "Travel North into Route101 at (10, 19)", "ctx"
    )
    for _ in range(3):
        memory.record(obs, obs, north, Outcome.INTERRUPTED, "NeedPokemonTrigger")
    other_map = observation((0, 8))

    brief = memory.decision_brief(obs, (north,), None)
    other_brief = memory.decision_brief(other_map, (north,), None)

    assert "Brendan is the player; May is the rival." in brief["confirmed_facts"]
    assert "May's House is the neighbor's house." in brief["confirmed_facts"]
    assert brief["legal_actions"][0]["attempts_without_progress"] == 3
    assert brief["legal_actions"][0]["last_result"] == "NeedPokemonTrigger"
    assert other_brief["legal_actions"][0]["attempts_without_progress"] == 0


def test_successful_travel_can_trigger_help_without_becoming_a_dead_end(tmp_path):
    path = tmp_path / "memory.json"
    obs = observation((1, 4))
    memory = PlannerMemory(ledger=EvidenceLedger(path))
    memory.sync_progress(obs)
    exit_lab = Action(
        "walk:1:4:6:12", "Go through the doorway into Littleroot Town", "ctx"
    )

    for _ in range(3):
        memory.record(obs, obs, exit_lab, Outcome.SUCCESS, None)

    assert memory.reason(obs) == "three repeated attempts without story progress"
    assert EvidenceLedger(path).summary("meet_neighbor", (1, 4))["dead_ends"] == []
    assert memory.decision_brief(obs, (exit_lab,), None)["legal_actions"][0] == {
        "action_id": exit_lab.id,
        "label": exit_lab.label,
        "attempts_without_progress": 3,
        "last_result": "success",
    }


def test_brief_includes_exact_validated_planner_destination(tmp_path):
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

    brief = PlannerMemory(
        ledger=EvidenceLedger(tmp_path / "m.json")
    ).decision_brief(observation((0, 9)), (action,), planner_advice)

    assert brief["planner_hint"]["location"] == "May's House entrance at (14, 8)"
    assert planner_advice.text.startswith("Hint: Meet May upstairs.")


def test_brief_reuses_cross_run_dead_ends(tmp_path):
    path = tmp_path / "m.json"
    EvidenceLedger(path).record_dead_end(
        "meet_neighbor", (1, 4), "talk:1", "Talk to Aide", "no story progress", 3
    )
    memory = PlannerMemory(ledger=EvidenceLedger(path))

    brief = memory.decision_brief(observation((1, 4)), (), None)

    assert brief["avoid_repeating"][0]["action_id"] == "talk:1"


def test_brief_sends_verified_cross_run_lessons_to_both_models(tmp_path):
    path = tmp_path / "m.json"
    ledger = EvidenceLedger(path)
    ledger.record_hypothesis(
        "meet_neighbor", (1, 1), "Inspect the wall clock", "interact:5:1"
    )
    ledger.verify_hypothesis(
        "meet_neighbor", "interact:5:1", "story_progress:set_wall_clock"
    )
    memory = PlannerMemory(ledger=EvidenceLedger(path))
    obs = observation((1, 1))

    brief = memory.decision_brief(obs, (), None)
    planner_context = memory.planner_context(obs, (), None)

    assert brief["verified_lessons"][0]["action_id"] == "interact:5:1"
    assert planner_context["verifiedLessons"] == brief["verified_lessons"]


def test_only_a_followed_hint_is_verified_on_story_progress(tmp_path):
    path = tmp_path / "m.json"
    obs = observation((0, 9))
    memory = PlannerMemory(ledger=EvidenceLedger(path))
    memory.sync_progress(obs)
    memory.accept(obs, advice("walk:0:9:14:8"))

    memory.sync_progress(replace(obs, rival_house_state=3))

    assert EvidenceLedger(path).summary("meet_neighbor", (0, 9))["verified"] == []

    memory = PlannerMemory(ledger=EvidenceLedger(path))
    memory.sync_progress(obs)
    memory.accept(obs, advice("walk:0:9:14:8"))
    memory.mark_advice_followed("walk:0:9:14:8")
    memory.sync_progress(replace(obs, rival_house_state=3))

    assert EvidenceLedger(path).summary("meet_neighbor", (0, 9))["verified"]


def test_reset_after_failed_refinement_keeps_existing_hypothesis(tmp_path):
    path = tmp_path / "m.json"
    obs = observation((0, 9))
    memory = PlannerMemory(ledger=EvidenceLedger(path))
    memory.sync_progress(obs)
    memory.accept(obs, advice("walk:0:9:14:8"))
    memory.mark_advice_followed("walk:0:9:14:8")

    memory.accept(obs)
    memory.sync_progress(replace(obs, rival_house_state=3))

    assert EvidenceLedger(path).summary("meet_neighbor", (0, 9))["verified"]


def test_replacing_unverified_advice_does_not_call_it_rejected(tmp_path):
    path = tmp_path / "m.json"
    obs = observation((1, 4))
    memory = PlannerMemory(ledger=EvidenceLedger(path))
    memory.sync_progress(obs)
    memory.accept(obs, advice("walk:1:4:6:12"))

    memory.accept(obs, advice("talk:1"))

    assert EvidenceLedger(path).summary("meet_neighbor", (1, 4))["rejected"] == []


def test_expiring_an_immediate_hint_leaves_it_unverified(tmp_path):
    path = tmp_path / "m.json"
    obs = observation((1, 4))
    memory = PlannerMemory(ledger=EvidenceLedger(path))
    memory.sync_progress(obs)
    memory.accept(obs, advice("walk:1:4:6:12"))

    memory.expire_advice()
    memory.sync_progress(replace(obs, rival_house_state=3))

    summary = EvidenceLedger(path).summary("meet_neighbor", (1, 4))
    assert summary["verified"] == []
    assert summary["rejected"] == []
