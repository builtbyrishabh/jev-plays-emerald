from dataclasses import replace

import pytest

from jev_plays_emerald.actions import Outcome
from jev_plays_emerald.jev import TokenUsage
from jev_plays_emerald.planner import PlannerAdvice
from jev_plays_emerald.state import MapPosition, Observation, OpeningFlags
from jev_plays_emerald.telemetry import DecisionTelemetry


def observation():
    return Observation("ctx", "OVERWORLD", MapPosition((1, 1), (7, 2), "Down"),
                       True, "none", "none", (), (), OpeningFlags(False, False, False), None, ())


def advice(usage=TokenUsage(100, 20)):
    return PlannerAdvice("Set the clock", "interact:5:1", "Bedroom clock", "Do not leave yet",
                         "Clock flag changes", "luna", usage, 10)


def session(tmp_path, monkeypatch, *, calls="2", tokens="200"):
    from jev_plays_emerald.coaching import CoachingSession
    monkeypatch.setenv("JEV_PLANNER_MAX_CALLS", calls)
    monkeypatch.setenv("JEV_PLANNER_MAX_TOKENS", tokens)
    return CoachingSession(DecisionTelemetry(tmp_path / "events.jsonl"))


def test_budget_counts_stale_tokens_and_refuses_another_call(tmp_path, monkeypatch):
    coach = session(tmp_path, monkeypatch)
    call = coach.start(observation(), "stuck")
    coach.respond(call, advice(), stale=True)
    call = coach.start(observation(), "stuck again")
    coach.respond(call, advice(), stale=False, observation=observation())
    assert coach.budget["calls"] == 2
    assert coach.budget["known_tokens"] == 240
    assert coach.budget["exhausted"]
    with pytest.raises(RuntimeError, match="limit"):
        coach.start(observation(), "repeat")


@pytest.mark.parametrize("usage", [TokenUsage(), TokenUsage(100, None)])
def test_unknown_usage_stops_coaching_instead_of_treating_it_as_free(tmp_path, monkeypatch, usage):
    coach = session(tmp_path, monkeypatch)
    call = coach.start(observation(), "stuck")
    coach.respond(call, advice(usage), stale=False)
    assert coach.budget["unknown_usage"]
    assert coach.budget["exhausted"]


def test_intervention_distinguishes_selection_execution_and_observed_progress(tmp_path, monkeypatch):
    coach = session(tmp_path, monkeypatch)
    before = observation()
    call = coach.start(before, "three repeated exits")
    coach.respond(call, advice(), stale=False, observation=before)
    coach.selected("talk:1")
    assert not coach.interventions[-1]["selected"]
    coach.selected("interact:5:1")
    coach.outcome("interact:5:1", Outcome.INTERRUPTED, "clock menu opened")
    assert coach.interventions[-1]["action_outcome"] == "interrupted"
    assert not coach.interventions[-1]["story_progress"]
    coach.observe(replace(before, opening_flags=OpeningFlags(False, False, False, True)))
    row = coach.interventions[-1]
    assert row["selected"] and row["story_progress"]
    assert row["progress_evidence"]["opening_flags"]["after"]["set_wall_clock"]
    assert row["trigger"] == "three repeated exits"


def test_unfollowed_stale_or_failed_hints_do_not_claim_execution(tmp_path, monkeypatch):
    coach = session(tmp_path, monkeypatch)
    call = coach.start(observation(), "stuck")
    coach.respond(call, advice(), stale=True)
    coach.selected("interact:5:1")
    coach.observe(replace(observation(), rival_house_state=3))
    assert not coach.interventions[-1]["selected"]
    assert not coach.interventions[-1]["story_progress"]
    call = coach.start(observation(), "again")
    coach.failed(call, "timeout")
    assert coach.interventions[-1]["status"] == "error"
    assert coach.budget["unknown_usage"]


def test_progress_can_arrive_before_the_action_finishes(tmp_path, monkeypatch):
    coach = session(tmp_path, monkeypatch)
    call = coach.start(observation(), "stuck")
    coach.respond(call, advice(), stale=False)
    coach.selected("interact:5:1")
    coach.observe(replace(observation(), opening_flags=OpeningFlags(False, False, False, True)))
    coach.outcome("interact:5:1", Outcome.SUCCESS, None)
    row = coach.interventions[-1]
    assert row["story_progress"]
    assert row["action_outcome"] == "success"


@pytest.mark.parametrize("value", ["-1", "abc", "2.5"])
def test_invalid_budget_configuration_is_rejected(tmp_path, monkeypatch, value):
    with pytest.raises(ValueError, match="nonnegative integer"):
        session(tmp_path, monkeypatch, calls=value)
