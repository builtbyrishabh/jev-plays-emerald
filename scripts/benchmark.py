"""Bounded fresh Emerald runs for Jev, Jev + Luna, and Luna-only comparisons.

Run with `uv run --env-file .env python scripts/benchmark.py --help`.
Requires the bootstrapped local emulator and the user's matching ROM. Never
loads a save or changes game memory; each output directory must be new.
"""

import argparse
from dataclasses import asdict
from datetime import datetime
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


class LunaPlayer:
    """Benchmark-only player over the same legal actions, without probabilities."""

    def __init__(self, request_log: Path):
        self.request_log = request_log

    async def choose(self, *, state, options, instructions):
        from jev_plays_emerald.jev import JevChoice, TokenUsage
        from jev_plays_emerald.planner_knowledge import knowledge_for
        from jev_plays_emerald.replay import observation_from_state

        # Preserve the original single-model baseline's opening reference material.
        state = {**state, "walkthroughKnowledge": knowledge_for(observation_from_state(state))}
        request = {"state": state, "options": options,
                   "instructions": instructions, "timeoutMs": 120_000}
        # This is the actual provider input, including the baseline's reference
        # material added after the production mode's generic request event.
        with self.request_log.open("a") as output:
            output.write(json.dumps(request) + "\n")

        result = subprocess.run(
            ["node", "src/baseline.ts"], cwd=ROOT / "service",
            input=json.dumps(request),
            text=True, capture_output=True, timeout=130,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "Luna baseline failed")
        answer = json.loads(result.stdout)
        if answer.get("choice") not in options:
            raise ValueError("Luna returned an action outside the offered menu")
        usage = answer.get("usage") or {}
        return JevChoice(answer["choice"], {}, None,
                         TokenUsage(usage.get("inputTokens"), usage.get("outputTokens"),
                                    usage.get("cachedInputTokens")),
                         answer["latencyMs"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=("jev-only", "jev-grounded", "hybrid", "luna-only"), required=True)
    parser.add_argument("--target", choices=("rival", "first-gym"), default="rival")
    parser.add_argument("--output", type=Path, required=True, help="new run directory; never overwritten")
    parser.add_argument("--rom", type=Path, default=ROOT / ".cache/pokebot-gen3/roms/Pokemon - Emerald Version (USA, Europe).gba")
    parser.add_argument("--seconds", type=float, default=900)
    parser.add_argument("--max-decisions", type=int, default=150)
    parser.add_argument("--max-planner-calls", type=int, default=8)
    parser.add_argument("--max-planner-tokens", type=int, default=50_000)
    args = parser.parse_args()
    if args.seconds <= 0 or args.max_decisions <= 0 or args.max_planner_calls <= 0 or args.max_planner_tokens <= 0:
        parser.error("budgets must be positive")

    from jev_plays_emerald.__main__ import verify_rom, verify_runtime
    verify_rom(args.rom)
    verify_runtime()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    os.environ["JEV_TARGET"] = args.target
    os.environ["JEV_PLANNER_MAX_CALLS"] = str(args.max_planner_calls)
    os.environ["JEV_PLANNER_MAX_TOKENS"] = str(args.max_planner_tokens)
    model = "openai/gpt-5.6-luna"
    if args.variant == "hybrid":
        os.environ["JEV_PLANNER_MODEL"] = model
    else:
        os.environ.pop("JEV_PLANNER_MODEL", None)
    os.environ["JEV_BASELINE_MODEL"] = model

    # Upstream provides the native emulator setup; gameplay uses its real
    # listeners and our production mode. No GUI or special test battle policy.
    sys.path.insert(0, str(ROOT / ".cache/pokebot-gen3"))
    import modules.gui.multi_select_window  # noqa: F401
    from tests.utility import _set_up_test_emulator
    from modules.context import context
    from modules.memory import get_game_state
    from modules.modes import FrameInfo, get_bot_listeners
    from modules.profiles import Profile
    from modules.roms import load_rom_data
    from modules.tasks import get_global_script_context, get_tasks
    from jev_plays_emerald.mode import JevEmeraldMode
    from jev_plays_emerald.planner import PlannerMemory
    from jev_plays_emerald.telemetry import DecisionTelemetry

    _set_up_test_emulator(Profile(load_rom_data(args.rom.resolve()), args.output, datetime.now()))
    context.emulator.set_video_enabled(True)
    context.stats.has_encounter_with_personality_value = lambda _: False
    context.frame += 1
    context.emulator.run_single_frame()
    decision_model = model if args.variant == "luna-only" else "typesafe-ai/jev"
    telemetry = DecisionTelemetry(args.output / "decisions.jsonl", model=decision_model)
    metadata = dict(variant=args.variant, target=args.target, decision_model=decision_model,
                    planner_model=model if args.variant == "hybrid" else None,
                    authored_hints=False, memory="fresh", seconds_budget=args.seconds,
                    decision_budget=args.max_decisions, planner_call_budget=args.max_planner_calls,
                    planner_token_budget=args.max_planner_tokens, checkpoint=False, suppress_futile=False)
    telemetry.planner_event("run-start", **metadata)
    mode = JevEmeraldMode(telemetry=telemetry,
                         gateway=LunaPlayer(args.output / "baseline-requests.jsonl") if args.variant == "luna-only" else None,
                         suppress_futile=False)
    if args.variant in {"hybrid", "jev-grounded"}:
        class UncoachedMemory(PlannerMemory):
            """Control arm: same grounded brief, zero coach calls."""

            def reason(self, observation):
                return None

        memory_type = UncoachedMemory if args.variant == "jev-grounded" else PlannerMemory
        mode._planner = memory_type()
    runner = mode.run()
    context.controller_stack.clear()
    context.controller_stack.append(runner)
    context.bot_mode_instance = mode
    context._current_bot_mode = mode.name()
    context.bot_listeners = get_bot_listeners(context.rom)
    previous = None
    started = time.monotonic()
    last_decision = None
    decisions = 0
    next_progress = 0.0
    stop = "time-budget"
    gameplay_failure = None
    saved_milestones: set[str] = set()
    interrupted = False
    def request_stop(_signal, _frame):
        # Raising inside an mGBA C callback can swallow KeyboardInterrupt.
        nonlocal interrupted
        interrupted = True
    previous_interrupt_handler = signal.signal(signal.SIGINT, request_stop)
    print(json.dumps({"event": "start", **metadata}), flush=True)
    try:
        while time.monotonic() - started < args.seconds:
            if interrupted:
                stop = "interrupted"
                break
            context.frame += 1
            script = get_global_script_context()
            frame = FrameInfo(
                frame_count=context.emulator.get_frame_count(), game_state=get_game_state(),
                active_tasks=[t.symbol.lower() for t in get_tasks()],
                script_stack=script.stack if script.is_active else [],
                controller_stack=[item.__qualname__ for item in context.controller_stack],
                previous_frame=previous,
            )
            for listener in context.bot_listeners.copy():
                listener.handle_frame(mode, frame)
            if context.controller_stack:
                try:
                    next(context.controller_stack[-1])
                except (StopIteration, GeneratorExit):
                    context.controller_stack.pop()
            obs = mode.observation
            if obs is not None and obs.game_state in {"OVERWORLD", "BATTLE", "CHOOSE_STARTER"}:
                flags = obs.opening_flags
                milestones = {
                    "starter": bool(obs.party), "rival": flags.defeated_rival_route103,
                    "pokedex": flags.received_pokedex, "petalburg": flags.petalburg_tutorial,
                    "woods": flags.devon_goods_saved, "badge": flags.stone_badge,
                }
                for milestone, reached in milestones.items():
                    if reached and milestone not in saved_milestones:
                        saved_milestones.add(milestone)
                        context.emulator.get_screenshot().save(args.output / f"milestone-{milestone}.png")
                        (args.output / f"milestone-{milestone}.ss1").write_bytes(context.emulator.get_save_state())
                        telemetry.planner_event("milestone", name=milestone, seconds=round(time.monotonic() - started, 2))
            context.emulator.run_single_frame()
            previous = frame
            previous.previous_frame = None
            if mode.status.last_decision is not last_decision:
                last_decision = mode.status.last_decision
                if last_decision and last_decision.source == "model":
                    decisions += 1
            if mode.completed:
                stop = "completed"
                break
            if mode.paused:
                stop = "paused"
                break
            if decisions >= args.max_decisions:
                stop = "decision-budget"
                break
            elapsed = time.monotonic() - started
            if elapsed >= next_progress:
                obs = mode.observation
                print(json.dumps({"event": "progress", "seconds": round(elapsed, 1),
                                  "decisions": decisions, "phase": mode.status.phase,
                                  "map": obs.position.map_name if obs and obs.position else None,
                                  "planner_calls": (mode.planner_view or {}).get("calls", 0)}), flush=True)
                next_progress = elapsed + 15
            if mode.status.phase == "pending":
                time.sleep(0.005)
    except KeyboardInterrupt:
        stop = "interrupted"
    except Exception as error:
        stop = "error"
        gameplay_failure = str(error)
        telemetry.planner_event("benchmark-error", message=str(error))
        raise
    finally:
        signal.signal(signal.SIGINT, previous_interrupt_handler)
        gameplay_elapsed = time.monotonic() - started
        gameplay_error = gameplay_failure or mode.status.last_error
        mode.set_paused(True)
        # Finish accounting for a request already sent, without executing its
        # answer. Pausing makes any late response stale and still measurable.
        for pending in (mode._pending_decision, mode._pending_plan):
            if pending:
                try:
                    pending.future.result(timeout=135)
                except Exception:
                    pass
        mode._poll_plan_response()
        mode._poll_model_response()
        context.emulator.get_screenshot().save(args.output / "final.png")
        (args.output / "final.ss1").write_bytes(context.emulator.get_save_state())
        summary = {**metadata, "completed": mode.completed, "stop": stop,
                   "checkpoint": mode.checkpoint, "manual_actions": mode.manual_actions,
                   "elapsed_seconds": round(time.monotonic() - started, 2),
                   "gameplay_elapsed_seconds": round(gameplay_elapsed, 2),
                   "model_decisions": decisions, "error": gameplay_error,
                   "observation": asdict(mode.observation) if mode.observation else None}
        telemetry.planner_event("run-end", **summary)
        (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        runner.close()
        from jev_plays_emerald import service
        if service._SERVICE:
            service._SERVICE.close()
        print(json.dumps({key: value for key, value in summary.items() if key != "observation"}), flush=True)
    return 0 if mode.completed else 1


if __name__ == "__main__":
    raise SystemExit(main())
