"""The replay harness must read a log faithfully and score it honestly."""

import asyncio
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from jev_plays_emerald.jev import JevChoice, TokenUsage
from jev_plays_emerald.replay import (
    Answer,
    Situation,
    ask_all,
    collapse,
    observation_from_state,
    read_situations,
    recorded_answers,
    report,
    variant,
)
from jev_plays_emerald.state import (
    ActiveBattler,
    InventoryItem,
    MapExit,
    MapObject,
    MapPosition,
    MapSign,
    MoveState,
    Observation,
    OpeningFlags,
    OpponentBattler,
    PartyMember,
    RecentOutcome,
)
from jev_plays_emerald.actions import Outcome

MISSION = (
    "You are playing Pokemon Emerald. Your mission, in order: get a starter Pokemon. "
    "Choose exactly one legal action."
)


def log(path: Path, records: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
    return path


def request(context_id: str, instructions: str = MISSION, criteria=None, **state) -> dict:
    return {
        "event": "request",
        "context_id": context_id,
        "attempt": 1,
        "state": {"position": {"map_id": [0, 9], "coordinates": [10, 9], "facing": "Up"}, **state},
        "questions": {
            "action": {
                "type": "choice",
                "instructions": instructions,
                "criteria": criteria or {"walk:0:9:5:8": "a door", "talk:1": "a person"},
            }
        },
    }


def response(context_id: str, choice: str, probabilities: dict[str, float]) -> dict:
    return {
        "event": "response",
        "context_id": context_id,
        "attempt": 1,
        "choice": choice,
        "probabilities": list(probabilities.items()),
        "usage": {"input_tokens": 100, "output_tokens": 10},
    }


def test_a_repeated_situation_keeps_its_own_answer(tmp_path):
    """A context ID recurs every time a stuck run comes back to the same spot."""

    path = log(tmp_path / "decisions.jsonl", [
        request("ctx"),
        response("ctx", "walk:0:9:5:8", {"walk:0:9:5:8": 0.9, "talk:1": 0.1}),
        request("ctx"),
        response("ctx", "talk:1", {"walk:0:9:5:8": 0.4, "talk:1": 0.6}),
    ])
    situations = read_situations(path)
    assert [s.recorded_choice for s in situations] == ["walk:0:9:5:8", "talk:1"]
    assert situations[0].recorded_probabilities["walk:0:9:5:8"] == 0.9


def test_an_unanswered_request_is_kept_without_a_choice(tmp_path):
    path = log(tmp_path / "decisions.jsonl", [request("ctx")])
    assert read_situations(path)[0].recorded_choice is None


def test_deterministic_decisions_are_not_situations(tmp_path):
    """Only requests carry a menu; a single legal action never reaches Jev."""

    path = log(tmp_path / "decisions.jsonl", [
        {"event": "decision", "source": "deterministic", "action_id": "dialogue:advance"},
        {"event": "outcome", "action_id": "dialogue:advance", "outcome": "success"},
    ])
    assert read_situations(path) == []


def test_collapse_counts_repeats_of_one_situation(tmp_path):
    path = log(tmp_path / "decisions.jsonl", [request("ctx"), request("ctx"), request("other")])
    situations = read_situations(path)
    # The third differs only by ID, so the menu and place still make it the same spot.
    assert len(collapse(situations)) == 1
    assert collapse(situations)[0].occurrences == 3


def test_collapse_preserves_state_and_label_differences():
    base = situation()
    variants = [
        base,
        replace(base, state={**base.state, "recent_dialogue": ["Come find me"]}),
        replace(base, state={**base.state, "party": [{"hp": 1}]}),
        replace(base, state={"position": {"map_id": [0, 9], "coordinates": [1, 1]}}),
        replace(base, criteria={**base.criteria, "talk:1": "the rival"}),
    ]
    assert len(collapse(variants)) == len(variants)


def test_export_preserves_recorded_and_replayed_usage(tmp_path, monkeypatch):
    from jev_plays_emerald.replay import main

    class Client:
        async def choose(self, **kwargs):
            return JevChoice("talk:1", {"talk:1": 1.0}, None, TokenUsage(120, 10, 80), 1.0)

    monkeypatch.setattr("jev_plays_emerald.service.default_choice_client", Client)
    path = log(tmp_path / "log.jsonl", [request("ctx"), response("ctx", "talk:1", {"talk:1": 1.0})])
    output = tmp_path / "answers.json"
    assert main([str(path), "--variant", "mission", "--out", str(output)]) == 0
    answers = json.loads(output.read_text())
    assert answers["recorded"][0]["input_tokens"] == 100
    assert answers["recorded"][0]["output_tokens"] == 10
    assert answers["recorded"][0]["cached_input_tokens"] is None
    assert answers["mission"][0]["input_tokens"] == 120
    assert answers["mission"][0]["output_tokens"] == 10
    assert answers["mission"][0]["cached_input_tokens"] == 80


def situation(instructions: str = MISSION, **changes) -> Situation:
    base = Situation(
        context_id="ctx",
        state={"position": {"map_id": [0, 9], "coordinates": [10, 9], "facing": "Up"}},
        criteria={"walk:0:9:14:8": "the neighbour's door", "talk:1": "a person"},
        instructions=instructions,
    )
    return replace(base, **changes)


def test_mission_variant_drops_every_hint():
    hinted = situation(f"{MISSION} Go into Mays House and talk to the child there.")
    assert variant("mission")[1](hinted) == MISSION


def test_current_variant_rebuilds_the_prompt_from_recorded_state(monkeypatch):
    monkeypatch.setenv("JEV_TARGET", "first-gym")
    recorded = situation(
        state={
            "game_state": "OVERWORLD",
            "position": {
                "map_id": [0, 9],
                "coordinates": [10, 9],
                "facing": "Up",
                "map_name": "Littleroot Town",
            },
            "recent_dialogue": ["Our daughter is upstairs, I think."],
            "opening_flags": {
                "rescued_birch": False,
                "received_pokedex": False,
                "defeated_rival_route103": False,
                "set_wall_clock": True,
            },
        }
    )

    rewritten = variant("current")[1](recorded)

    assert rewritten.startswith("Goal: earn the Stone Badge.")
    assert "latest relevant dialogue" in rewritten
    assert "Go into Mays House" not in rewritten


def test_hint_variant_swaps_the_hint_and_keeps_the_mission():
    hinted = situation(f"{MISSION} Go into Mays House.")
    rewritten = variant("hint:Head north instead.")[1](hinted)
    assert rewritten == f"{MISSION} Head north instead."


def test_drop_variant_removes_only_matching_sentences():
    hinted = situation(f"{MISSION} Meet the neighbour. Then head north.")
    rewritten = variant("drop:neighbour")[1](hinted)
    assert "neighbour" not in rewritten
    assert rewritten.endswith("Then head north.")


def test_an_unknown_variant_is_refused():
    with pytest.raises(ValueError):
        variant("improvise")


def test_a_recorded_observation_rebuilds_exactly():
    """Replaying an old log must not need the code that wrote it."""

    original = Observation(
        context_id="ctx",
        game_state="OVERWORLD",
        position=MapPosition((0, 18), (10, 21), "Up", "Route103"),
        controllable=True,
        menu_phase="none",
        battle_phase="none",
        party=(PartyMember("Torchic", 5, 19, 19, "Healthy", (MoveState("Scratch", 35, 35),)),),
        inventory=(InventoryItem("Potion", 1, "healing"),),
        opening_flags=OpeningFlags(True, False, False, True, True),
        active_battler=ActiveBattler(0, (MoveState("Scratch", 35, 35),)),
        recent_outcomes=(RecentOutcome("walk:0:18:10:21", Outcome.SUCCESS, None),),
        opponent=OpponentBattler("Poochyena", 2, 7, 7, "Healthy"),
        trainer_id=532,
        can_run=True,
        tasks=("Task_A",),
        scripts=("Route103_EventScript_Rival",),
        rival_house_state=3,
        lab_state=3,
        player_gender="male",
        exits=(MapExit((0, 18), (10, 21), (0, 10), "Oldale Town", "South"),),
        objects=(MapObject(2, (10, 3), "Route103_EventScript_Rival", "None", False, False),),
        signs=(MapSign((11, 9), "Script", "Route103_EventScript_Sign", ""),),
    )
    state = asdict(original)
    state["observation_note"] = "HP values are exact observations."
    assert observation_from_state(state) == original


def test_an_older_log_without_todays_fields_still_rebuilds():
    state = {
        "context_id": "ctx",
        "game_state": "OVERWORLD",
        "position": {"map_id": [0, 9], "coordinates": [10, 9], "facing": "Up"},
        "opening_flags": {"rescued_birch": False, "received_pokedex": False,
                          "defeated_rival_route103": False},
        "objects": [{"local_id": 1, "coordinates": [7, 7], "script_symbol": "X"}],
    }
    rebuilt = observation_from_state(state)
    assert rebuilt.position.map_name == ""
    assert rebuilt.objects[0].loaded is True


def answer(sit: Situation, choice: str, probabilities: dict[str, float]) -> Answer:
    return Answer(sit, sit.instructions, choice, probabilities, 100)


def test_report_scores_agreement_and_target_mass():
    first = situation()
    second = situation()
    baseline = [
        answer(first, "walk:0:9:14:8", {"walk:0:9:14:8": 0.8, "talk:1": 0.2}),
        answer(second, "talk:1", {"walk:0:9:14:8": 0.3, "talk:1": 0.7}),
    ]
    variant_answers = [
        answer(first, "walk:0:9:14:8", {"walk:0:9:14:8": 0.6, "talk:1": 0.4}),
        answer(second, "walk:0:9:14:8", {"walk:0:9:14:8": 0.9, "talk:1": 0.1}),
    ]
    scored = report("swapped", variant_answers, baseline, "walk:0:9:14:8")
    assert scored.agreement == 0.5
    assert scored.target_mass == pytest.approx(0.75)
    assert scored.changed == [("(0, 9) (10, 9)", "talk:1", "walk:0:9:14:8")]
    assert report("recorded", baseline, baseline, "walk:0:9:14:8").target_mass == pytest.approx(0.55)


def test_a_situation_without_a_logged_answer_is_not_scored():
    lonely = situation()
    baseline = recorded_answers([lonely])
    scored = report("current", [answer(lonely, "talk:1", {"talk:1": 1.0})], baseline, None)
    assert scored.agreement is None


class FakeClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.asked: list[str] = []
        self._fail = fail

    async def choose(self, *, state, options, instructions):
        self.asked.append(instructions)
        if self._fail:
            raise RuntimeError("gateway said no")
        return JevChoice(
            choice=next(iter(options)),
            probabilities={action: 1.0 if i == 0 else 0.0 for i, action in enumerate(options)},
            confidence=0.9,
            usage=TokenUsage(120, 10),
            latency_ms=1.0,
        )


def test_ask_all_sends_the_rewritten_instructions():
    client = FakeClient()
    hinted = situation(f"{MISSION} Go into Mays House.")
    answers = asyncio.run(ask_all(client, [hinted], variant("mission")[1]))
    assert client.asked == [MISSION]
    assert answers[0].choice == "walk:0:9:14:8"


def test_a_provider_failure_is_recorded_not_raised():
    answers = asyncio.run(ask_all(FakeClient(fail=True), [situation()], variant("recorded")[1]))
    assert answers[0].error == "gateway said no"
    assert report("recorded", answers, answers, None).errors == 1
