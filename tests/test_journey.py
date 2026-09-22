from dataclasses import replace

import pytest

from jev_plays_emerald.state import MapPosition, Observation, OpeningFlags, PartyMember


def observation(**changes):
    return replace(Observation(
        "gym", "OVERWORLD", MapPosition((11, 3), (5, 10), "Up", "Rustboro City Gym"),
        True, "none", "none", (PartyMember("Treecko", 15, 35, 35, "Healthy", ()),),
        (), OpeningFlags(True, True, True), None, (),
    ), **changes)


def test_default_target_is_first_gym_and_invalid_configuration_fails(monkeypatch):
    from jev_plays_emerald.journey import configured_target

    monkeypatch.delenv("JEV_TARGET", raising=False)
    assert configured_target() == "first-gym"
    monkeypatch.setenv("JEV_TARGET", "rival")
    assert configured_target() == "rival"
    monkeypatch.setenv("JEV_TARGET", "champion")
    with pytest.raises(ValueError, match="JEV_TARGET"):
        configured_target()


@pytest.mark.parametrize("trainer,outcome,expected", [(265, "Won", True), (265, "Lost", False), (100, "Won", False), (None, "Won", False)])
def test_badge_requires_new_roxanne_win(trainer, outcome, expected):
    from jev_plays_emerald.journey import GymProgress

    progress = GymProgress()
    before = observation(game_state="BATTLE", trainer_id=trainer)
    progress.observe(before)
    progress.battle_ended(outcome)
    progress.observe(observation())
    assert not progress.completed
    progress.observe(observation(opening_flags=replace(before.opening_flags, stone_badge=True)))
    assert progress.completed is expected


def test_loaded_badge_or_wrong_map_cannot_count_as_a_new_win():
    from jev_plays_emerald.journey import GymProgress

    for obs in (
        observation(opening_flags=replace(observation().opening_flags, stone_badge=True)),
        observation(position=MapPosition((0, 3), (5, 10), "Up")),
    ):
        progress = GymProgress()
        progress.observe(replace(obs, game_state="BATTLE", trainer_id=265))
        progress.battle_ended("Won")
        progress.observe(replace(obs, opening_flags=replace(obs.opening_flags, stone_badge=True)))
        assert not progress.completed


def test_lost_battle_animation_cannot_rearm_completion():
    from jev_plays_emerald.journey import GymProgress

    progress = GymProgress()
    battle = observation(game_state="BATTLE", trainer_id=265)
    progress.observe(battle)
    progress.battle_ended("Lost")
    progress.observe(battle)
    progress.battle_ended("Won")
    progress.observe(observation(opening_flags=replace(battle.opening_flags, stone_badge=True)))
    assert not progress.completed


def test_first_gym_retains_actions_after_rival(monkeypatch):
    from jev_plays_emerald.opening import legal_actions
    from jev_plays_emerald.state import MapObject

    obs = observation(objects=(MapObject(1, (5, 2), "Roxanne", True),))
    monkeypatch.setenv("JEV_TARGET", "first-gym")
    assert legal_actions(obs)
    monkeypatch.setenv("JEV_TARGET", "rival")
    assert not legal_actions(obs)


def test_post_rival_milestones_do_not_call_luna_without_a_stall(monkeypatch, tmp_path):
    from jev_plays_emerald.planner import PlannerMemory
    from jev_plays_emerald.planner_memory import EvidenceLedger
    from jev_plays_emerald.planner_knowledge import stage_key

    monkeypatch.setenv("JEV_TARGET", "first-gym")
    obs = observation(opening_flags=OpeningFlags(True, False, True))
    memory = PlannerMemory(ledger=EvidenceLedger(tmp_path / "memory.json"))
    memory.sync_progress(obs)
    assert stage_key(obs) == "receive_pokedex"
    assert memory.reason(obs) is None
    obs = replace(obs, opening_flags=replace(obs.opening_flags, received_pokedex=True))
    memory.sync_progress(obs)
    assert stage_key(obs) == "visit_petalburg"
    assert memory.reason(obs) is None
    assert memory.reason(replace(obs, game_state="BATTLE")) is None


def test_entering_the_gym_does_not_call_luna_without_a_stall(tmp_path, monkeypatch):
    from jev_plays_emerald.planner import PlannerMemory
    from jev_plays_emerald.planner_memory import EvidenceLedger
    from jev_plays_emerald.planner_knowledge import stage_key

    monkeypatch.setenv("JEV_TARGET", "first-gym")
    obs = observation(opening_flags=OpeningFlags(True, True, True, stone_badge=False, petalburg_tutorial=True, devon_goods_saved=True))
    memory = PlannerMemory(ledger=EvidenceLedger(tmp_path / "memory.json"))
    assert stage_key(obs) == "first_gym"
    assert memory.reason(obs) is None


def test_distinct_woods_exits_to_same_route_are_both_offered(monkeypatch):
    from jev_plays_emerald.opening import legal_actions
    from jev_plays_emerald.state import MapExit

    monkeypatch.setenv("JEV_TARGET", "first-gym")
    woods = observation(position=MapPosition((24, 11), (1, 2), "Up"), exits=(
        MapExit((24, 11), (1, 1), (0, 19), "Route 104"),
        MapExit((24, 11), (10, 49), (0, 19), "Route 104"),
    ))
    actions = legal_actions(woods)
    assert {a.id for a in actions} >= {"walk:24:11:1:1", "walk:24:11:10:49"}


def test_observed_route_survives_restart_without_replaying_prior_instructions(tmp_path, monkeypatch):
    from jev_plays_emerald.actions import Action, Outcome
    from jev_plays_emerald.jev import TokenUsage
    from jev_plays_emerald.planner import PlannerAdvice, PlannerMemory
    from jev_plays_emerald.planner_memory import EvidenceLedger

    monkeypatch.setenv("JEV_TARGET", "first-gym")
    path = tmp_path / "memory.json"
    before = observation(opening_flags=OpeningFlags(True, False, True))
    after = replace(before, position=MapPosition((0, 3), (1, 2), "Up"))
    action = Action("walk:11:3:4:15", "Leave gym", before.context_id)
    advice = PlannerAdvice("Return to Birch", action.id, "Gym exit", "Do not challenge trainers", "Pokedex received", "test", TokenUsage(), 0)
    memory = PlannerMemory(ledger=EvidenceLedger(path))
    memory.accept(before, advice)
    memory.record(before, after, action, Outcome.INTERRUPTED, "map changed")
    restored = PlannerMemory(ledger=EvidenceLedger(path)).decision_brief(after, (), None)
    assert "remembered_plan" not in restored
    assert "observed_routes" not in restored


def test_exhausted_trainer_battle_can_select_struggle():
    from jev_plays_emerald.opening import legal_actions
    from jev_plays_emerald.state import ActiveBattler, MoveState

    obs = observation(game_state="BATTLE", trainer_id=265, battle_phase="action", menu_phase="battle",
                      active_battler=ActiveBattler(0, (MoveState("Pound", 0, 35),)))
    actions = legal_actions(obs)
    assert len(actions) == 1
    assert "Struggle" in actions[0].label


def test_completed_coaching_step_does_not_remain_a_destination(tmp_path):
    from jev_plays_emerald.jev import TokenUsage
    from jev_plays_emerald.planner import PlannerAdvice, PlannerMemory
    from jev_plays_emerald.planner_memory import EvidenceLedger

    memory = PlannerMemory(ledger=EvidenceLedger(tmp_path / "memory.json"))
    obs = observation()
    advice = PlannerAdvice("Return through Oldale then south to Birch", "walk:0:10:1:1", "Oldale", "Avoid detours", "Arrive Oldale", "test", TokenUsage(), 0)
    memory.accept(obs, advice)
    brief = memory.decision_brief(obs, (), None, advice)
    assert "destination_action_id" not in brief["planner_follow_up"]
    assert "location" not in brief["planner_follow_up"]
    assert brief["planner_follow_up"]["success_signal"] == "Arrive Oldale"
    assert brief["planner_follow_up"]["avoid"] == "Avoid detours"
    assert "remembered_plan" not in brief


def test_transient_regression_cannot_verify_advice(tmp_path):
    from jev_plays_emerald.jev import TokenUsage
    from jev_plays_emerald.planner import PlannerAdvice, PlannerMemory
    from jev_plays_emerald.planner_memory import EvidenceLedger

    ledger = EvidenceLedger(tmp_path / "memory.json")
    memory = PlannerMemory(ledger=ledger)
    obs = observation()
    advice = PlannerAdvice("Go west", "walk:0:17:49:10", "Route 102", "Avoid loops", "Meet Norman", "test", TokenUsage(), 0)
    memory.accept(obs, advice)
    memory.mark_advice_followed(advice.destination_action_id)
    invalid = replace(obs, game_state="UNKNOWN", opening_flags=OpeningFlags(False, False, False))
    assert memory.sync_progress(invalid) is False
    assert memory.sync_progress(obs) is False
    assert ledger.summary("completed", obs.position.map_id)["verified"] == []
