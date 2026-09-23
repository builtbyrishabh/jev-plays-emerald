"""Offline accounting from response events, including stale model work.

Run with ``python -m jev_plays_emerald.report runs/decisions.jsonl --json``.
Missing usage remains unknown; recorded prices are estimates, never invoices.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path


def summarize(path: Path) -> dict:
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    counts = Counter(record.get("event") for record in records)
    metadata = [record for record in records if record.get("event") == "run-start"]
    ends = [row for row in records if row.get("event") == "run-end"]
    run_end = ends[-1] if ends else None
    outcomes = [row.get("outcome") for row in records if row.get("event") == "battle-ended"]
    losses = sum(str(outcome).lower() == "lost" for outcome in outcomes)
    result = {
        "log": str(path),
        "runs": metadata,
        "run_end": run_end,
        "battles": {"outcomes": outcomes, "losses": losses,
                    "recovered_after_loss": bool(losses and run_end and run_end.get("completed"))},
        "deterministic_decisions": sum(
            row.get("event") == "decision" and row.get("source") == "deterministic"
            for row in records
        ),
        "unscoped_error_events": counts["error"],
    }
    for tier, prefix in (("jev", ""), ("planner", "planner-")):
        responses = [row for row in records if row.get("event") == prefix + "response"]
        errors = counts["planner-error"] if prefix else counts["request-error"]
        usage = [row.get("usage") or {} for row in responses]
        costs = [(row.get("cost") or {}).get("estimated_usd") for row in responses]
        summary = {
            "requests": counts[prefix + "request"],
            "responses": len(responses),
            "responses_missing_usage": sum(item.get("input_tokens") is None or item.get("output_tokens") is None for item in usage),
            "latency_ms": sum(row.get("latency_ms") or 0 for row in responses),
            "accepted": sum(row.get("disposition") == "accepted" for row in responses),
            "stale": sum(row.get("disposition") == "stale" for row in responses),
            "error_events": errors,
            "unresolved_requests": max(0, counts[prefix + "request"] - len(responses) - errors),
            "recorded_estimated_usd": sum(cost for cost in costs if cost is not None)
            if any(cost is not None for cost in costs) else None,
            "responses_missing_cost": sum(cost is None for cost in costs),
            "models": sorted({row["model"] for row in responses if row.get("model")}),
        }
        for field in ("input_tokens", "output_tokens", "cached_input_tokens"):
            summary[field] = sum(item.get(field) or 0 for item in usage)
            summary["responses_missing_" + field] = sum(item.get(field) is None for item in usage)
        # Historical logs have no model field; the run marker identifies which
        # model drove the decision tier when benchmarking Luna without Jev.
        if tier == "jev":
            summary["models"] = sorted(set(summary["models"]) | {
                row["decision_model"] for row in metadata if row.get("decision_model")
            })
        result[tier] = summary
    return result


def aggregate(reports: list[dict]) -> dict:
    """Keep failures in the cost denominator; only explicit run-end proves success."""
    variants = sorted({report["runs"][-1].get("variant", "unknown")
                       if report["runs"] else "unknown" for report in reports})
    result = {}
    for variant in variants:
        selected = [report for report in reports
                    if (report["runs"][-1].get("variant", "unknown")
                        if report["runs"] else "unknown") == variant]
        completed = streak = longest = tokens = unknown = 0
        elapsed = 0.0
        stops = Counter()
        losses = recovered = 0
        for report in selected:
            end = report["run_end"] or {}
            success = end.get("completed") is True
            completed += success
            streak = streak + 1 if success else 0
            longest = max(longest, streak)
            stops[end.get("stop", "missing-run-end")] += 1
            elapsed += end.get("elapsed_seconds", 0)
            losses += report["battles"]["losses"]
            recovered += report["battles"]["recovered_after_loss"]
            for tier in ("jev", "planner"):
                row = report[tier]
                tokens += row["input_tokens"] + row["output_tokens"]
                unknown += row["responses_missing_usage"] + row["error_events"] + row["unresolved_requests"]
        result[variant] = {
            "attempts": len(selected), "completed": completed,
            "completion_rate": completed / len(selected),
            "longest_success_streak": longest, "known_tokens": tokens,
            "known_tokens_per_success": tokens / completed if completed else None,
            "unknown_usage_events": unknown, "elapsed_seconds": elapsed,
            "elapsed_seconds_per_success": elapsed / completed if completed else None,
            "stops": dict(stops), "battle_losses": losses,
            "runs_completed_after_loss": recovered,
            "tiers": {
                tier: {field: sum(report[tier][field] for report in selected)
                       for field in ("requests", "responses", "input_tokens", "output_tokens",
                                     "cached_input_tokens", "responses_missing_input_tokens",
                                     "responses_missing_output_tokens", "responses_missing_cached_input_tokens",
                                     "responses_missing_usage", "error_events", "unresolved_requests", "latency_ms")}
                for tier in ("jev", "planner")
            },
        }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("logs", type=Path, nargs="+")
    parser.add_argument("--json", action="store_true", help="emit per-log machine-readable accounting")
    arguments = parser.parse_args(argv)
    reports = [summarize(path) for path in arguments.logs]
    if arguments.json:
        print(json.dumps(reports, indent=2))
        return 0
    for report in reports:
        print(report["log"])
        print(f"{'tier':<12} {'responses':>9} {'stale':>6} {'input':>10} {'output':>9} {'cached':>9} {'USD estimate':>14}")
        for tier in ("jev", "planner"):
            row = report[tier]
            name = "decision" if tier == "jev" else tier
            cost = row["recorded_estimated_usd"]
            print(f"{name:<12} {row['responses']:>9} {row['stale']:>6} {row['input_tokens']:>10} {row['output_tokens']:>9} {row['cached_input_tokens']:>9} {cost if cost is not None else 'unknown':>14}")
            print(f"  Missing input/output/cache/cost: {row['responses_missing_input_tokens']}/{row['responses_missing_output_tokens']}/{row['responses_missing_cached_input_tokens']}/{row['responses_missing_cost']}; errors: {row['error_events']}; unresolved requests: {row['unresolved_requests']}")
        print(f"  Unscoped errors: {report['unscoped_error_events']}")
    print("Token sums cover known usage only. Errors and unresolved requests may have incurred unreported usage. Cached tokens are part of input; missing cache data is not zero cache use. Costs use recorded estimates only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
