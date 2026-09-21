"""The action vocabulary is a cross-language contract; nothing else enforces it.

An ID the TypeScript decision service can offer must resolve to a Python
executor, or the run picks an action it cannot perform. `schema/actions.json`
is the single source of truth and this is the test that holds both sides to it.
"""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / ".cache/pokebot-gen3"))

from jev_plays_emerald.actions import ACTION_EXECUTORS

SCHEMA = json.loads((Path(__file__).parents[1] / "schema" / "actions.json").read_text())


def test_every_schema_kind_has_a_python_executor():
    missing = set(SCHEMA["kinds"]) - set(ACTION_EXECUTORS)
    assert not missing, f"the service could offer {sorted(missing)} with nothing to run it"


def test_every_python_executor_is_declared_in_the_schema():
    """An undeclared executor is unreachable once enumeration moves to TypeScript."""

    missing = set(ACTION_EXECUTORS) - set(SCHEMA["kinds"])
    assert not missing, f"{sorted(missing)} is missing from schema/actions.json"


def test_each_kind_declares_its_id_grammar_and_whether_it_is_enumerated():
    for kind, entry in SCHEMA["kinds"].items():
        assert entry["id"].split(":")[0] == kind, f"{kind} declares a mismatched ID prefix"
        assert isinstance(entry["enumerated"], bool)
        assert entry["summary"]
