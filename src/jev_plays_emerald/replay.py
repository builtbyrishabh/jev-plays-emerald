"""Ask a recorded situation again with the instructions changed.

A decision log stores the whole request - state, menu and instructions - so a
situation Jev has already been in can be put to it again with different wording
and the two answers compared. The recorded answer is the baseline and costs
nothing; only the variants make calls.

    uv run --env-file .env python -m jev_plays_emerald.replay runs/decisions.jsonl \
        --variant 'drop:neighbour' --target walk:0:9:14:8

One call per situation per variant. An evaluation answer is a whole
distribution, so a single call already says more than one sampled choice would
- but it is still one sample of a stochastic model, and a small difference
between variants is noise, not a finding.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from jev_plays_emerald.actions import Outcome
from jev_plays_emerald.jev import JevChoice
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
from jev_plays_emerald.telemetry import JEV_INPUT_USD_PER_TOKEN

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POKEBOT_ROOT = PROJECT_ROOT / ".cache" / "pokebot-gen3"


@dataclass(frozen=True)
class Situation:
    """One recorded decision point, with the answer the run actually got."""

    context_id: str
    state: dict
    criteria: dict[str, str]
    instructions: str
    recorded_choice: str | None = None
    recorded_probabilities: dict[str, float] = field(default_factory=dict)
    recorded_input_tokens: int | None = None
    occurrences: int = 1

    @property
    def map_id(self) -> tuple[int, int] | None:
        position = self.state.get("position")
        return tuple(position["map_id"]) if position else None


@dataclass(frozen=True)
class Answer:
    situation: Situation
    instructions: str
    choice: str
    probabilities: dict[str, float]
    input_tokens: int | None
    error: str | None = None


def read_situations(path: Path) -> list[Situation]:
    """Pair every logged request with the response it received.

    A situation's context ID repeats whenever the run comes back to it, so a
    response attaches to the most recent request still waiting for one rather
    than to whichever request shares its ID.
    """

    situations: list[Situation] = []
    waiting: dict[tuple[str, int], list[int]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        event = record.get("event")
        key = (record.get("context_id", ""), record.get("attempt", 1))
        if event == "request":
            question = (record.get("questions") or {}).get("action") or {}
            situations.append(
                Situation(
                    context_id=key[0],
                    state=record.get("state") or {},
                    criteria=question.get("criteria") or {},
                    instructions=question.get("instructions") or "",
                )
            )
            waiting.setdefault(key, []).append(len(situations) - 1)
        elif event == "response" and waiting.get(key):
            index = waiting[key].pop(0)
            usage = record.get("usage") or {}
            situations[index] = replace(
                situations[index],
                recorded_choice=record.get("choice"),
                recorded_probabilities=dict(record.get("probabilities") or []),
                recorded_input_tokens=usage.get("input_tokens"),
            )
    return [s for s in situations if s.criteria]


def collapse(situations: list[Situation]) -> list[Situation]:
    """One entry per distinct situation: a stuck run records the same one often."""

    seen: dict[tuple, int] = {}
    collapsed: list[Situation] = []
    for situation in situations:
        key = (situation.map_id, tuple(sorted(situation.criteria)), situation.instructions)
        if key in seen:
            index = seen[key]
            collapsed[index] = replace(
                collapsed[index], occurrences=collapsed[index].occurrences + 1
            )
            continue
        seen[key] = len(collapsed)
        collapsed.append(situation)
    return collapsed


# --- instruction variants -----------------------------------------------------


def variant(spec: str) -> tuple[str, Callable[[Situation], str]]:
    """Turn a command-line spec into a named instruction rewrite.

    `current` is the one that answers "did my edit help": it recomputes the
    instructions from the recorded state using the code in the working tree, so
    yesterday's log can be replayed against today's wording.
    """

    if spec == "recorded":
        return spec, lambda situation: situation.instructions
    if spec == "current":
        return spec, _current_instructions
    if spec == "mission":
        return spec, lambda situation: _mission_only(situation.instructions)
    if spec.startswith("hint:"):
        replacement = spec[len("hint:") :].strip()
        return spec, lambda situation: " ".join(
            filter(None, [_mission_only(situation.instructions), replacement])
        )
    if spec.startswith("drop:"):
        phrase = spec[len("drop:") :]
        if not phrase:
            raise ValueError("drop: needs a phrase, for example drop:neighbour")
        return spec, lambda situation: _drop_sentences(situation.instructions, phrase)
    raise ValueError(f"unknown variant: {spec}")


def _drop_sentences(instructions: str, phrase: str) -> str:
    """Remove whole sentences mentioning a phrase, leaving the rest intact."""

    kept = [
        sentence
        for sentence in _sentences(instructions)
        if phrase.casefold() not in sentence.casefold()
    ]
    return " ".join(kept)


def _mission_only(instructions: str) -> str:
    """The base mission with every situational hint removed.

    The mission is the first three sentences of every request; anything after
    them is the hint for this particular situation.
    """

    return " ".join(_sentences(instructions)[:3])


def _sentences(text: str) -> list[str]:
    parts = [part.strip() for part in text.split(". ")]
    return [part if part.endswith(".") else f"{part}." for part in parts if part]


def _current_instructions(situation: Situation) -> str:
    # The instruction code reads upstream's map table, which normally comes
    # from running inside PokeBot. Nothing else in a replay touches upstream.
    if str(POKEBOT_ROOT) not in sys.path:
        if not POKEBOT_ROOT.is_dir():
            raise RuntimeError("the `current` variant needs `python3.13 scripts/bootstrap.py`")
        sys.path.insert(0, str(POKEBOT_ROOT))
    from jev_plays_emerald.opening import decision_instructions

    return decision_instructions(observation_from_state(situation.state))


def observation_from_state(state: dict) -> Observation:
    """Rebuild the Observation a request was built from.

    Fields absent from an older log fall back to their dataclass defaults, so a
    log written before a field existed still replays.
    """

    def moves(raw):
        return tuple(
            MoveState(
                m["name"], m["pp"], m["max_pp"], m.get("type", "Unknown"), m.get("power", 0),
                m.get("accuracy", 0.0), m.get("description", ""), m.get("usable", True),
            )
            for m in raw
        )

    position = state.get("position")
    battler = state.get("active_battler")
    opponent = state.get("opponent")
    return Observation(
        context_id=state.get("context_id", ""),
        game_state=state.get("game_state", "UNKNOWN"),
        position=(
            MapPosition(
                tuple(position["map_id"]), tuple(position["coordinates"]),
                position.get("facing", ""), position.get("map_name", ""),
            )
            if position
            else None
        ),
        controllable=state.get("controllable", False),
        menu_phase=state.get("menu_phase", "none"),
        battle_phase=state.get("battle_phase", "none"),
        party=tuple(
            PartyMember(p["species"], p["level"], p["hp"], p["max_hp"], p["status"], moves(p["moves"]))
            for p in state.get("party") or ()
        ),
        inventory=tuple(
            InventoryItem(i["name"], i["quantity"], i.get("battle_use", "not_usable"))
            for i in state.get("inventory") or ()
        ),
        opening_flags=OpeningFlags(**(state.get("opening_flags") or {})),
        active_battler=(
            ActiveBattler(battler["party_index"], moves(battler["moves"])) if battler else None
        ),
        recent_outcomes=tuple(
            RecentOutcome(o["action_id"], Outcome(o["outcome"]), o.get("reason"))
            for o in state.get("recent_outcomes") or ()
        ),
        opponent=(
            OpponentBattler(
                opponent["species"], opponent["level"], opponent["hp"],
                opponent["max_hp"], opponent["status"],
            )
            if opponent
            else None
        ),
        trainer_id=state.get("trainer_id"),
        can_run=state.get("can_run", False),
        tasks=tuple(state.get("tasks") or ()),
        scripts=tuple(state.get("scripts") or ()),
        rival_house_state=state.get("rival_house_state", 0),
        lab_state=state.get("lab_state", 0),
        player_gender=state.get("player_gender", "male"),
        exits=tuple(
            MapExit(
                tuple(e["target_map"]), tuple(e["target_coordinates"]),
                tuple(e["destination_id"]), e["destination_name"], e.get("direction", ""),
            )
            for e in state.get("exits") or ()
        ),
        objects=tuple(
            MapObject(
                o["local_id"], tuple(o["coordinates"]), o["script_symbol"],
                o.get("trainer_type", "None"), o.get("trainer_defeated", False),
                o.get("loaded", True),
            )
            for o in state.get("objects") or ()
        ),
        signs=tuple(
            MapSign(
                tuple(s["coordinates"]), s["kind"], s.get("script_symbol", ""),
                s.get("hidden_item", ""),
            )
            for s in state.get("signs") or ()
        ),
    )


# --- asking -------------------------------------------------------------------


async def ask_all(client, situations: list[Situation], rewrite) -> list[Answer]:
    """One call at a time, in log order, so a failure is easy to place."""

    answers: list[Answer] = []
    for situation in situations:
        instructions = rewrite(situation)
        try:
            result: JevChoice = await client.choose(
                state=dict(situation.state),
                options=dict(situation.criteria),
                instructions=instructions,
            )
        except Exception as error:  # a provider failure is a result, not a crash
            answers.append(
                Answer(situation, instructions, "", {}, None, str(error) or type(error).__name__)
            )
            continue
        answers.append(
            Answer(
                situation,
                instructions,
                result.choice,
                dict(result.probabilities),
                result.usage.input_tokens,
            )
        )
    return answers


# --- comparing ----------------------------------------------------------------


@dataclass(frozen=True)
class VariantReport:
    name: str
    calls: int
    rewritten: int
    errors: int
    input_tokens: int
    agreement: float | None
    target_mass: float | None
    target_situations: int
    changed: list[tuple[str, str, str]]

    @property
    def estimated_usd(self) -> float:
        return self.input_tokens * JEV_INPUT_USD_PER_TOKEN


def recorded_answers(situations: list[Situation]) -> list[Answer]:
    """The baseline, taken from the log rather than asked again."""

    return [
        Answer(
            situation,
            situation.instructions,
            situation.recorded_choice or "",
            situation.recorded_probabilities,
            situation.recorded_input_tokens,
            None if situation.recorded_choice else "no response was logged",
        )
        for situation in situations
    ]


def report(name: str, answers: list[Answer], baseline: list[Answer], target: str | None) -> VariantReport:
    """Score one variant against the baseline, ignoring situations that errored."""

    by_context = {id(answer.situation): answer for answer in baseline}
    comparable = 0
    agreed = 0
    changed: list[tuple[str, str, str]] = []
    masses: list[float] = []
    available = 0
    for answer in answers:
        reference = by_context.get(id(answer.situation))
        if target and any(action.startswith(target) for action in answer.situation.criteria):
            available += 1
            if not answer.error:
                masses.append(
                    sum(p for action, p in answer.probabilities.items() if action.startswith(target))
                )
        if answer.error or reference is None or reference.error:
            continue
        comparable += 1
        if answer.choice == reference.choice:
            agreed += 1
        else:
            changed.append((_where(answer.situation), reference.choice, answer.choice))
    return VariantReport(
        name=name,
        calls=sum(1 for answer in answers if answer.input_tokens is not None or answer.error),
        rewritten=sum(1 for a in answers if a.instructions != a.situation.instructions),
        errors=sum(1 for answer in answers if answer.error),
        input_tokens=sum(answer.input_tokens or 0 for answer in answers),
        agreement=(agreed / comparable) if comparable else None,
        target_mass=(sum(masses) / len(masses)) if masses else None,
        target_situations=available,
        changed=changed,
    )


def _where(situation: Situation) -> str:
    position = situation.state.get("position")
    if not position:
        return situation.state.get("game_state", "?")
    name = position.get("map_name") or str(tuple(position["map_id"]))
    return f"{name} {tuple(position['coordinates'])}"


def format_report(reports: list[VariantReport], situations: list[Situation], target: str | None,
                  *, verbose: bool = False) -> str:
    lines = [
        f"situations: {len(situations)} "
        f"(seen {sum(s.occurrences for s in situations)} times in the log)"
    ]
    if target:
        lines.append(f"target: actions starting with {target!r}")
    header = f"{'variant':<22}{'calls':>6}{'rewritten':>11}{'agrees':>9}{'p(target)':>11}{'USD':>10}"
    lines += ["", header, "-" * len(header)]
    for entry in reports:
        agreement = "-" if entry.agreement is None else f"{entry.agreement:.0%}"
        mass = "-" if entry.target_mass is None else f"{entry.target_mass:.2f}"
        cost = "-" if not entry.input_tokens else f"{entry.estimated_usd:.5f}"
        lines.append(
            f"{entry.name:<22}{entry.calls:>6}{entry.rewritten:>11}{agreement:>9}{mass:>11}{cost:>10}"
        )
        if entry.errors:
            lines.append(f"{'':<22}{entry.errors} errored")
    if target:
        lines.append(
            f"\np(target) is the mean probability mass on matching actions, over the "
            f"{reports[0].target_situations} situation(s) that offered one."
        )
    if verbose:
        for entry in reports:
            if not entry.changed:
                continue
            lines.append(f"\n{entry.name} chose differently in {len(entry.changed)} situation(s):")
            for where, before, after in entry.changed:
                lines.append(f"  {where}: {before} -> {after}")
    return "\n".join(lines)


# --- command line -------------------------------------------------------------


def _filtered(
    situations: list[Situation], *, on_map: str | None, kind: str | None, saying: str | None
) -> list[Situation]:
    if on_map:
        wanted = tuple(int(part) for part in on_map.split(","))
        situations = [s for s in situations if s.map_id == wanted]
    if kind:
        situations = [
            s for s in situations if any(action.startswith(kind) for action in s.criteria)
        ]
    if saying:
        # Ablating one hint means replaying the situations that were given it.
        situations = [s for s in situations if saying.casefold() in s.instructions.casefold()]
    return situations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "log", type=Path, nargs="+", help="one or more decisions.jsonl written by runs"
    )
    parser.add_argument(
        "--variant", action="append", default=[],
        help=(
            "recorded | current | mission | hint:<text> | drop:<phrase> "
            "(repeatable; default: current)"
        ),
    )
    parser.add_argument("--target", help="score the probability mass on actions with this ID prefix")
    parser.add_argument("--map", dest="on_map", help="only situations on this map, as 'group,number'")
    parser.add_argument("--kind", help="only situations whose menu offers this action kind")
    parser.add_argument(
        "--saying", help="only situations whose recorded instructions contain this text"
    )
    parser.add_argument("--limit", type=int, help="replay at most this many situations")
    parser.add_argument("--all", action="store_true", help="keep repeats instead of collapsing them")
    parser.add_argument("--dry-run", action="store_true", help="show the plan and one rewrite, ask nothing")
    parser.add_argument("--verbose", action="store_true", help="list the situations that changed")
    parser.add_argument("--out", type=Path, help="write the raw answers here as JSON")
    arguments = parser.parse_args(argv)

    recorded: list[Situation] = []
    for log in arguments.log:
        recorded += read_situations(log)
    situations = _filtered(
        recorded, on_map=arguments.on_map, kind=arguments.kind, saying=arguments.saying
    )
    if not arguments.all:
        situations = collapse(situations)
    if arguments.limit:
        situations = situations[: arguments.limit]
    if not situations:
        print("no recorded situations matched", file=sys.stderr)
        return 1

    try:
        variants = [variant(spec) for spec in (arguments.variant or ["current"])]
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2

    recorded_tokens = sum(s.recorded_input_tokens or 0 for s in situations)
    estimate = recorded_tokens * len(variants) * JEV_INPUT_USD_PER_TOKEN
    print(
        f"{len(situations)} situations x {len(variants)} variant(s) = "
        f"{len(situations) * len(variants)} calls, about USD {estimate:.5f} "
        "at the recorded token sizes"
    )
    if arguments.dry_run:
        for name, rewrite in variants:
            sample = rewrite(situations[0])
            changed = sum(1 for s in situations if rewrite(s) != s.instructions)
            print(f"\n[{name}] rewrites {changed}/{len(situations)} situations; first one becomes:")
            print(f"  {sample}")
        return 0

    baseline = recorded_answers(situations)
    from jev_plays_emerald.service import default_choice_client

    client = default_choice_client()
    try:
        results = {
            name: asyncio.run(ask_all(client, situations, rewrite)) for name, rewrite in variants
        }
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()

    reports = [report("recorded", baseline, baseline, arguments.target)]
    reports += [report(name, answers, baseline, arguments.target) for name, answers in results.items()]
    print()
    print(format_report(reports, situations, arguments.target, verbose=arguments.verbose))

    if arguments.out:
        arguments.out.write_text(
            json.dumps(
                {
                    name: [
                        {
                            "where": _where(answer.situation),
                            "instructions": answer.instructions,
                            "choice": answer.choice,
                            "probabilities": answer.probabilities,
                            "error": answer.error,
                        }
                        for answer in answers
                    ]
                    for name, answers in {"recorded": baseline, **results}.items()
                },
                indent=1,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
