import json
from dataclasses import replace

import pytest

from jev_plays_emerald import planner_knowledge
from jev_plays_emerald.planner_knowledge import knowledge_for, stage_key
from jev_plays_emerald.state import MapPosition, Observation, OpeningFlags, PartyMember


def observation(**changes):
    return replace(
        Observation(
            "ctx",
            "OVERWORLD",
            MapPosition((0, 9), (11, 2), "Up", "Littleroot Town"),
            True,
            "none",
            "none",
            (),
            (),
            OpeningFlags(False, False, False, True),
            None,
            (),
        ),
        **changes,
    )


def test_male_player_gets_may_as_rival_and_neighbor():
    obs = observation(player_gender="male", rival_house_state=0)

    facts = knowledge_for(obs)

    assert stage_key(obs) == "meet_neighbor"
    assert "Brendan is the player; May is the rival." in facts
    assert "May's House is the neighbor's house." in facts


def test_female_player_gets_brendan_as_rival_and_neighbor():
    facts = knowledge_for(observation(player_gender="female", rival_house_state=0))

    assert "May is the player; Brendan is the rival." in facts
    assert "Brendan's House is the neighbor's house." in facts


def test_progress_selects_only_the_relevant_stage():
    met_neighbor = observation(rival_house_state=3)

    assert stage_key(met_neighbor) == "rescue_birch"
    assert all("upstairs item" not in fact for fact in knowledge_for(met_neighbor))


def test_party_and_rival_progress_select_later_stages():
    treecko = PartyMember("Treecko", 5, 20, 20, "Healthy", ())

    assert stage_key(observation(party=(treecko,))) == "reach_rival"
    assert stage_key(
        observation(
            party=(treecko,),
            opening_flags=OpeningFlags(True, False, True, True),
        )
    ) == "completed"


@pytest.mark.parametrize(
    "document",
    [
        {"schema_version": 2, "stages": {}},
        {"schema_version": 1, "stages": {"meet_neighbor": [123]}},
    ],
)
def test_invalid_knowledge_is_rejected(tmp_path, monkeypatch, document):
    path = tmp_path / "knowledge.json"
    path.write_text(json.dumps(document))
    monkeypatch.setattr(planner_knowledge, "KNOWLEDGE_PATH", path)

    with pytest.raises(ValueError):
        knowledge_for(observation())
