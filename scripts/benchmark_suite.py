"""Run a fixed fresh-game comparison, retaining every attempt in a new directory.

No resume: an existing output directory is rejected. The immutable manifest
captures executable source content, git revision, and common run settings.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from threading import Event
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from jev_plays_emerald.report import aggregate, summarize

ROOT = Path(__file__).resolve().parents[1]


def source_fingerprint(root: Path) -> str:
    """Hash runnable source/config, including uncommitted files, never credentials."""
    digest = hashlib.sha256()
    excluded = {"node_modules", "__pycache__", ".venv", ".git", ".cache"}
    paths = []
    for directory in ("src", "scripts", "service/src", "plugins", "schema", "knowledge", "patches"):
        parent = root / directory
        if parent.exists():
            paths.extend(path for path in parent.rglob("*") if path.is_file()
                         and not path.is_symlink() and not excluded.intersection(path.parts)
                         and path.suffix in {".py", ".ts", ".tsx", ".js", ".json", ".patch", ".css", ".html"})
    paths.extend(root / name for name in ("pyproject.toml", "uv.lock", "service/package.json", "service/pnpm-lock.yaml")
                 if (root / name).exists())
    for path in sorted(set(paths)):
        digest.update(str(path.relative_to(root)).encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def create_manifest(output: Path, manifest: dict) -> None:
    output.mkdir(parents=True, exist_ok=False)
    with (output / "manifest.json").open("x") as file:
        file.write(json.dumps(manifest, indent=2) + "\n")
    (output / "manifest.json").chmod(0o444)


def schedule(hybrid_runs: int, control_runs: int) -> list[str]:
    return [variant for index in range(max(hybrid_runs, control_runs))
            for variant in ("hybrid", "jev-only", "jev-grounded", "luna-only")
            if index < (hybrid_runs if variant == "hybrid" else control_runs)]


def run_jobs(tasks, work, jobs):
    """Yield finished results with planned indices; never queue over the limit."""
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        remaining = iter(enumerate(tasks))
        pending = {}
        for index, task in remaining:
            pending[executor.submit(work, task)] = index
            if len(pending) == jobs:
                break
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                index = pending.pop(future)
                yield index, future.result()
                next_task = next(remaining, None)
                if next_task is not None:
                    next_index, task = next_task
                    pending[executor.submit(work, task)] = next_index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--hybrid-runs", type=int, default=5)
    parser.add_argument("--control-runs", type=int, default=3)
    parser.add_argument("--seconds", type=float, default=900)
    parser.add_argument("--max-decisions", type=int, default=150)
    parser.add_argument("--max-planner-calls", type=int, default=8)
    parser.add_argument("--max-planner-tokens", type=int, default=50_000)
    parser.add_argument("--rom", type=Path, default=ROOT / ".cache/pokebot-gen3/roms/Pokemon - Emerald Version (USA, Europe).gba")
    args = parser.parse_args(argv)
    if args.jobs <= 0 or args.hybrid_runs <= 0 or args.control_runs < 0 or args.seconds <= 0 or args.max_decisions <= 0 or args.max_planner_calls <= 0 or args.max_planner_tokens <= 0:
        parser.error("run counts and budgets must be positive (control runs may be zero)")
    fingerprint = source_fingerprint(ROOT)
    runs = schedule(args.hybrid_runs, args.control_runs)
    settings = {"seconds": args.seconds, "max_decisions": args.max_decisions,
                "max_planner_calls": args.max_planner_calls, "max_planner_tokens": args.max_planner_tokens}
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(),
                "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "source_sha256": fingerprint, "settings": settings, "schedule": runs, "jobs": args.jobs,
                "decision_model": "typesafe-ai/jev", "luna_model": "gpt-5.6-luna",
                "planner_backend": "codex", "authored_hints": False, "suppress_futile": False,
                "memory": "fresh", "checkpoint": False,
                "runtime": {"python": sys.version, "node": subprocess.check_output(["node", "--version"], text=True).strip(),
                            "codex": subprocess.check_output(["codex", "--version"], text=True).strip()}}
    if args.rom:
        manifest["rom_sha256"] = hashlib.sha256(args.rom.read_bytes()).hexdigest()
    create_manifest(args.output, manifest)
    counters = Counter()
    tasks = []
    for variant in runs:
        counters[variant] += 1
        tasks.append((variant, args.output.resolve() / f"{variant}-{counters[variant]:02d}"))
    source_drift = Event()

    def execute(task):
        variant, output = task
        if source_drift.is_set() or source_fingerprint(ROOT) != fingerprint:
            source_drift.set()
            return {"skipped": output.name, "reason": "source-drift"}

        command = [sys.executable, str(ROOT / "scripts/benchmark.py"), "--variant", variant, "--output", str(output)]
        for key, value in settings.items():
            command.extend(["--" + key.replace("_", "-"), str(value)])
        if args.rom:
            command.extend(["--rom", str(args.rom.resolve())])
        print(f"Starting {output.name}", flush=True)
        with (args.output / f"{output.name}.console.log").open("x") as console:
            process = subprocess.run(command, cwd=ROOT, stdout=console, stderr=subprocess.STDOUT)
        output.mkdir(exist_ok=True)
        log = output / "decisions.jsonl"
        # A setup crash still counts as an attempt, and its console is retained.
        if not log.exists():
            log.write_text(json.dumps({"event": "run-start", "variant": variant}) + "\n")
        report = summarize(log)
        report["process_returncode"] = process.returncode
        if source_fingerprint(ROOT) != fingerprint:
            source_drift.set()
        return report

    finished = {}
    reports = []
    for index, report in run_jobs(tasks, execute, args.jobs):
        finished[index] = report
        reports = [finished[index] for index in sorted(finished) if "skipped" not in finished[index]]
        result = {"manifest": "manifest.json", "runs": reports, "arms": aggregate(reports),
                  "source_drift": source_drift.is_set(), "suite_complete": len(finished) == len(tasks),
                  "skipped": [finished[index] for index in sorted(finished) if "skipped" in finished[index]],
                  "planned_attempt_indices": [index for index in sorted(finished) if "skipped" not in finished[index]]}
        (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result["arms"]), flush=True)
    return 0 if not source_drift.is_set() and aggregate(reports).get("hybrid", {}).get("longest_success_streak", 0) >= 5 else 1


if __name__ == "__main__":
    raise SystemExit(main())
