from jev_plays_emerald.planner_memory import EvidenceLedger


def test_verified_lesson_survives_a_fresh_ledger(tmp_path):
    path = tmp_path / "planner-memory.json"
    ledger = EvidenceLedger(path)
    ledger.record_hypothesis("meet_neighbor", (0, 9), "May's House", "walk:0:9:14:8")
    ledger.verify_hypothesis(
        "meet_neighbor", "walk:0:9:14:8", "rival_house_state:0->3"
    )

    verified = EvidenceLedger(path).summary("meet_neighbor", (0, 9))["verified"]

    assert verified[0]["action_id"] == "walk:0:9:14:8"
    assert verified[0]["evidence"] == "rival_house_state:0->3"
    assert not path.with_suffix(".tmp").exists()


def test_unverified_advice_never_becomes_a_fact(tmp_path):
    ledger = EvidenceLedger(tmp_path / "planner-memory.json")
    ledger.record_hypothesis(
        "meet_neighbor", (1, 4), "Try the machine", "interact:10:7"
    )

    summary = EvidenceLedger(ledger.path).summary("meet_neighbor", (1, 4))

    assert summary["verified"] == []
    assert summary["rejected"] == []


def test_rejected_hypothesis_is_remembered_but_not_verified(tmp_path):
    path = tmp_path / "planner-memory.json"
    ledger = EvidenceLedger(path)
    ledger.record_hypothesis("meet_neighbor", (1, 4), "Try the machine", "interact:10:7")
    ledger.reject_hypothesis("meet_neighbor", "interact:10:7")

    summary = EvidenceLedger(path).summary("meet_neighbor", (1, 4))

    assert summary["verified"] == []
    assert summary["rejected"][0]["action_id"] == "interact:10:7"


def test_identical_dead_ends_merge_counts_and_stay_map_scoped(tmp_path):
    ledger = EvidenceLedger(tmp_path / "planner-memory.json")
    ledger.record_dead_end("meet_neighbor", (1, 4), "talk:1", "Talk", "blocked", 3)
    ledger.record_dead_end("meet_neighbor", (1, 4), "talk:1", "Talk", "blocked", 4)

    local = ledger.summary("meet_neighbor", (1, 4))["dead_ends"]

    assert len(local) == 1
    assert local[0]["count"] == 4
    assert ledger.summary("meet_neighbor", (0, 9))["dead_ends"] == []


def test_corrupt_and_future_memory_start_empty(tmp_path):
    path = tmp_path / "planner-memory.json"
    path.write_text('{"schema_version":999}')

    assert EvidenceLedger(path).summary("meet_neighbor", (0, 9)) == {
        "verified": [],
        "rejected": [],
        "dead_ends": [],
    }

    path.write_text("not JSON")
    assert EvidenceLedger(path).summary("meet_neighbor", (0, 9)) == {
        "verified": [],
        "rejected": [],
        "dead_ends": [],
    }
