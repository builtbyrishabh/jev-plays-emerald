import json

import pytest

from jev_plays_emerald.report import main, summarize


def test_responses_count_stale_without_double_counting_decisions(tmp_path):
    path = tmp_path / "run.jsonl"
    rows = [
        {"event": "request"}, {"event": "request"}, {"event": "request"},
        {"event": "response", "disposition": "accepted", "usage": {"input_tokens": 100, "output_tokens": 5}, "cost": {"estimated_usd": 0.01}},
        {"event": "decision", "source": "model", "usage": {"input_tokens": 100, "output_tokens": 5}},
        {"event": "response", "disposition": "stale", "usage": {"input_tokens": 50, "output_tokens": 2}, "cost": {"estimated_usd": 0.005}},
        {"event": "error", "message": "timeout"},
    ]
    path.write_text("\n".join(map(json.dumps, rows)))
    result = summarize(path)
    jev = result["jev"]
    assert jev["responses"] == 2
    assert jev["stale"] == 1
    assert jev["input_tokens"] == 150
    assert jev["output_tokens"] == 7
    assert jev["recorded_estimated_usd"] == pytest.approx(0.015)
    assert jev["unresolved_requests"] == 1
    assert result["unscoped_error_events"] == 1


def test_planner_errors_missing_usage_and_cached_tokens_are_explicit(tmp_path):
    path = tmp_path / "run.jsonl"
    rows = [
        *({"event": "planner-request", "call": call} for call in range(1, 5)),
        {"event": "planner-response", "call": 1, "disposition": "accepted", "model": "luna", "usage": {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 20}},
        {"event": "planner-response", "call": 2, "disposition": "stale", "model": "luna", "usage": {}},
        {"event": "planner-error", "call": 3, "message": "failed"},
    ]
    path.write_text("\n".join(map(json.dumps, rows)))
    planner = summarize(path)["planner"]
    assert planner["input_tokens"] == 100  # Cached input is a subset, not extra input.
    assert planner["cached_input_tokens"] == 80
    assert planner["responses_missing_input_tokens"] == 1
    assert planner["responses_missing_cached_input_tokens"] == 1
    assert planner["error_events"] == 1
    assert planner["unresolved_requests"] == 1
    assert planner["recorded_estimated_usd"] is None
    assert planner["responses_missing_cost"] == 2


def test_json_cli_keeps_runs_separate(tmp_path, capsys):
    paths = [tmp_path / name for name in ("a.jsonl", "b.jsonl")]
    for path in paths:
        path.write_text('{"event":"decision","source":"deterministic"}\n')
    assert main([*map(str, paths), "--json"]) == 0
    reports = json.loads(capsys.readouterr().out)
    assert len(reports) == 2
    assert all(report["deterministic_decisions"] == 1 for report in reports)


def test_luna_decision_telemetry_never_uses_jev_price(tmp_path):
    from jev_plays_emerald.jev import JevChoice, TokenUsage
    from jev_plays_emerald.telemetry import DecisionTelemetry

    path = tmp_path / "luna.jsonl"
    telemetry = DecisionTelemetry(path, model="gpt-5.6-luna")
    telemetry.planner_event("run-start", variant="luna-only", decision_model="gpt-5.6-luna")
    choice = JevChoice("walk:1", {"walk:1": 1.0}, None, TokenUsage(100, 10), 1.0)
    telemetry.responded(context_id="ctx", attempt=1, result=choice, disposition="accepted")
    record = json.loads(path.read_text().splitlines()[-1])
    assert record["model"] == "gpt-5.6-luna"
    assert record["cost"]["estimated_usd"] is None
    assert record["cost"]["input_usd_per_token"] is None
    report = summarize(path)
    assert report["runs"][0]["variant"] == "luna-only"
    assert report["jev"]["models"] == ["gpt-5.6-luna"]


def test_completion_uses_run_end_and_keeps_loss_evidence(tmp_path):
    path = tmp_path / "run.jsonl"
    rows = [{"event": "battle-ended", "outcome": "Won"},
            {"event": "battle-ended", "outcome": "Lost"},
            {"event": "run-end", "completed": False, "stop": "decision-budget"}]
    path.write_text("\n".join(map(json.dumps, rows)))
    report = summarize(path)
    assert report["run_end"]["completed"] is False
    assert report["battles"]["losses"] == 1
    assert report["battles"]["recovered_after_loss"] is False


def test_aggregate_includes_failed_attempt_tokens_and_unknown_usage(tmp_path):
    from jev_plays_emerald.report import aggregate
    reports = []
    for index, completed in enumerate([True, False, True, True]):
        path = tmp_path / f"{index}.jsonl"
        path.write_text("\n".join(map(json.dumps, [
            {"event": "run-start", "variant": "hybrid"},
            {"event": "request"},
            {"event": "response", "usage": {"input_tokens": 100, "output_tokens": 10}},
            {"event": "planner-request"},
            {"event": "planner-error"},
            {"event": "run-end", "completed": completed, "elapsed_seconds": 5},
        ])))
        reports.append(summarize(path))
    arm = aggregate(reports)["hybrid"]
    assert arm["attempts"] == 4
    assert arm["completed"] == 3
    assert arm["completion_rate"] == 0.75
    assert arm["known_tokens"] == 440
    assert arm["known_tokens_per_success"] == pytest.approx(440 / 3)
    assert arm["longest_success_streak"] == 2
    assert arm["unknown_usage_events"] == 4
