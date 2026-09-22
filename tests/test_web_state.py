import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from queue import Queue
from types import MappingProxyType, SimpleNamespace

import pytest
from aiohttp import test_utils, web

from jev_plays_emerald.actions import Action
from jev_plays_emerald.jev import TokenUsage
from jev_plays_emerald.state import (
    Observation,
    OpeningFlags,
    OpponentBattler,
    PartyMember,
)
from jev_plays_emerald.telemetry import AgentStatus, CostEstimate, DecisionRecord

PROJECT_ROOT = Path(__file__).parents[1]
POKEBOT_ROOT = PROJECT_ROOT / ".cache" / "pokebot-gen3"


@pytest.fixture
def plugin_module():
    sys.path.insert(0, str(POKEBOT_ROOT))
    try:
        spec = importlib.util.spec_from_file_location(
            "jev_emerald_viewer_plugin", PROJECT_ROOT / "plugins" / "jev_emerald.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(POKEBOT_ROOT))


@pytest.fixture
def bootstrap_module():
    spec = importlib.util.spec_from_file_location(
        "jev_emerald_bootstrap", PROJECT_ROOT / "scripts" / "bootstrap.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mode_view(
    *,
    completed: bool = False,
    rival_flag: bool = False,
    lab_state: int = 0,
    received_pokedex: bool = True,
    planner: dict | None = None,
) -> SimpleNamespace:
    party = (
        PartyMember(
            species="Mudkip",
            level=6,
            hp=17,
            max_hp=21,
            status="Healthy",
            moves=(),
        ),
    )
    observation = Observation(
        context_id="route-103",
        game_state="BATTLE",
        position=None,
        controllable=True,
        menu_phase="none",
        battle_phase="move",
        party=party,
        inventory=(),
        opening_flags=OpeningFlags(True, received_pokedex, rival_flag),
        active_battler=None,
        recent_outcomes=(),
        opponent=OpponentBattler("Treecko", 5, 8, 19, "Healthy"),
        lab_state=lab_state,
        recent_dialogue=("MAY: Let's battle!", "PROF. BIRCH: Be careful."),
    )
    decision = DecisionRecord(
        context_id=observation.context_id,
        source="model",
        action_id="battle-move:0",
        probabilities=(("battle-move:0", 0.72), ("battle-move:1", 0.28)),
        confidence=0.81,
        latency_ms=184.5,
        usage=TokenUsage(input_tokens=42, output_tokens=7),
        cost=CostEstimate(0.000001764, 0.000000042, 0.0, "fixture", "2026-09-20"),
    )
    status = AgentStatus(
        phase="selected",
        context_id=observation.context_id,
        available_actions=(("battle-move:0", "Use Tackle"), ("battle-move:1", "Use Growl")),
        last_decision=decision,
    )
    return SimpleNamespace(
        name=lambda: "Jev Emerald",
        paused=False,
        completed=completed,
        status=status,
        observation=observation,
        available_actions=(
            Action("battle-move:0", "Use Tackle", observation.context_id),
            Action("battle-move:1", "Use Growl", observation.context_id),
        ),
        active_action=Action("battle-move:0", "Use Tackle", observation.context_id),
        api_key="must-not-leak",
        callback=lambda: None,
        planner_view=planner,
    )


def test_owner_snapshot_is_json_safe_and_contains_only_viewer_fields(plugin_module) -> None:
    snapshot = plugin_module.ViewerStatePublisher().build(_mode_view())

    encoded = json.dumps(snapshot)
    assert "must-not-leak" not in encoded
    assert snapshot["mode"] == "Jev Emerald"
    assert snapshot["status"] == {
        "phase": "selected",
        "context_id": "route-103",
        "pending_attempt": None,
        "last_error": None,
        "last_battle_outcome": None,
        "last_decision": {
            "context_id": "route-103",
            "source": "model",
            "action_id": "battle-move:0",
            "probabilities": {"battle-move:0": 0.72, "battle-move:1": 0.28},
            "labels": {"battle-move:0": "Use Tackle", "battle-move:1": "Use Growl"},
            "confidence": 0.81,
            "latency_ms": 184.5,
        },
    }
    assert snapshot["available_actions"][1] == {
        "id": "battle-move:1",
        "label": "Use Growl",
    }
    assert snapshot["active_action"] == {
        "id": "battle-move:0",
        "label": "Use Tackle",
    }
    assert snapshot["observation"]["party"][0]["hp"] == 17
    assert snapshot["observation"]["opponent"]["hp"] == 8
    assert snapshot["observation"]["recent_dialogue"] == [
        "MAY: Let's battle!",
        "PROF. BIRCH: Be careful.",
    ]
    assert snapshot["recent_choices"] == [snapshot["status"]["last_decision"]]
    assert snapshot["progress"] == {
        "starter": True,
        "rescue": True,
        "rival": False,
        "completed": False,
    }


def test_loaded_completed_save_is_labeled_as_checkpoint_not_fresh_completion(plugin_module) -> None:
    snapshot = plugin_module.ViewerStatePublisher().build(
        _mode_view(completed=False, rival_flag=True)
    )

    assert snapshot["status"]["phase"] == "checkpoint"
    assert snapshot["progress"]["rival"] is True
    assert snapshot["progress"]["completed"] is False


def test_post_lab_goal_does_not_depend_on_late_pokedex_flag(plugin_module) -> None:
    snapshot = plugin_module.ViewerStatePublisher().build(
        _mode_view(lab_state=3, received_pokedex=False)
    )

    assert snapshot["goal"] == "Reach Route 103 and win the rival battle"


def test_viewer_snapshot_preserves_structured_planner_advice(plugin_module) -> None:
    advice = {
        "hint": "Meet May upstairs.",
        "destination_action_id": "walk:0:9:14:8",
        "location": "May's House entrance at (14, 8)",
        "avoid": "Do not retry Route 101.",
        "success_signal": "rival_house_state changes",
    }
    snapshot = plugin_module.ViewerStatePublisher().build(
        _mode_view(planner={"model": "luna", "calls": 1, "pending": False,
                            "reason": "stuck", "advice": advice})
    )

    assert snapshot["planner"]["advice"] == advice


def test_pause_control_queues_owner_thread_change_without_accessing_emulator(
    plugin_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    from modules.context import context
    from jev_plays_emerald.mode import JevEmeraldMode

    class EmulatorAccessFails:
        def __getattr__(self, name: str):
            raise AssertionError(f"HTTP control touched emulator.{name}")

    queue: Queue = Queue()
    mode = JevEmeraldMode(observation_reader=object(), executor=SimpleNamespace(current_action=None))
    previous = (context.bot_mode_instance, context.emulator)
    context.bot_mode_instance = mode
    context.emulator = EmulatorAccessFails()
    monkeypatch.setattr(plugin_module, "work_queue", queue)

    async def scenario() -> None:
        app = web.Application()
        app.router.add_get(
            "/static/{path:.*}", lambda request: web.Response(status=404)
        )
        plugin_module.add_viewer_routes(app)
        client = test_utils.TestClient(test_utils.TestServer(app))
        await client.start_server()
        try:
            viewer = await client.get("/jev/index.html")
            assert viewer.status == 200
            viewer_html = await viewer.text()
            assert "Jev Plays Emerald" in viewer_html
            assert 'id="dialogue"' in viewer_html
            assert 'id="planner-location"' in viewer_html
            assert 'id="planner-avoid"' in viewer_html
            assert 'id="planner-success"' in viewer_html

            invalid = await client.post("/jev/control", json={"paused": "true"})
            assert invalid.status == 400
            assert queue.empty()

            response = await client.post("/jev/control", json={"paused": True})
            assert response.status == 202
            assert await response.json() == {"queued": True, "paused": True}
            assert mode.paused is False

            queued = queue.get_nowait()
            queued()
            assert mode.paused is True
        finally:
            await client.close()

    try:
        asyncio.run(scenario())
    finally:
        context.bot_mode_instance, context.emulator = previous


def test_view_model_makes_transient_and_checkpoint_states_explicit() -> None:
    script = """
const { actionPanelView, buildViewModel, plannerPanelView } = require('./web/app.js');
const cases = [
  [{ paused: false, status: { phase: 'pending', pending_attempt: 2 } }, ['Thinking', 'pending']],
  [{ paused: false, status: { phase: 'error', last_error: 'Gateway timed out' } }, ['Needs attention', 'error']],
  [{ paused: true, status: { phase: 'error', last_error: 'Gateway timed out' } }, ['Needs attention', 'error']],
  [{ paused: true, status: { phase: 'selected' } }, ['Paused', 'paused']],
  [{ paused: false, status: { phase: 'checkpoint' } }, ['Checkpoint loaded', 'checkpoint']],
];
for (const [fixture, expected] of cases) {
  const view = buildViewModel(fixture);
  if (view.phase.label !== expected[0] || view.phase.tone !== expected[1]) {
    throw new Error(JSON.stringify({ fixture, view, expected }));
  }
}
const stale = actionPanelView({
  available_actions: [{ id: 'move:0', label: 'Current move' }],
  status: {
    phase: 'pending', context_id: 'new',
    last_decision: { context_id: 'old', probabilities: { 'move:0': 1 }, labels: { 'move:0': 'Old move' } },
  },
});
if (stale.rows[0].probability !== null || stale.rows[0].chosen || stale.heading !== 'Available actions') {
  throw new Error(`stale distribution shown as current: ${JSON.stringify(stale)}`);
}
const completed = actionPanelView({
  available_actions: [],
  status: {
    phase: 'selected', context_id: 'choice',
    last_decision: {
      context_id: 'choice', probabilities: { a: 0.6, b: 0.4 }, labels: { a: 'Action A', b: 'Action B' },
    },
  },
});
if (completed.heading !== 'Last decision' || completed.rows.length !== 2 || completed.rows[1].probability !== 0.4) {
  throw new Error(`last distribution missing: ${JSON.stringify(completed)}`);
}
const waitingPlanner = plannerPanelView({ advice: null });
if (waitingPlanner.hint !== 'Waiting until Jev repeats an action three times') {
  throw new Error(`planner waiting message is unclear: ${JSON.stringify(waitingPlanner)}`);
}
const activePlanner = plannerPanelView({ advice: {
  hint: 'Meet May upstairs.', location: "May's House at (14, 8)",
  avoid: 'Do not retry Route 101.', success_signal: 'rival state changes',
} });
if (activePlanner.location !== "May's House at (14, 8)" || activePlanner.success !== 'rival state changes') {
  throw new Error(`structured planner fields missing: ${JSON.stringify(activePlanner)}`);
}
const followUpPlanner = plannerPanelView({ phase: 'follow_up', advice: {
  hint: 'Go upstairs and inspect the floor item.', location: "May's House entrance at (14, 8)",
  avoid: 'Do not leave the house.', success_signal: 'May appears',
} });
if (!followUpPlanner.hint.startsWith('Continue:') || !followUpPlanner.location.startsWith('Completed first action at:')) {
  throw new Error(`follow-up planner state is unclear: ${JSON.stringify(followUpPlanner)}`);
}
"""

    subprocess.run(
        ["node", "-e", script],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def test_bootstrap_migrates_matching_plugin_copy_to_source_link(
    bootstrap_module, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    pokebot = tmp_path / "pokebot"
    (project / "plugins").mkdir(parents=True)
    (project / "web").mkdir()
    (pokebot / "plugins").mkdir(parents=True)
    (pokebot / "modules" / "web" / "static").mkdir(parents=True)
    (project / "plugins" / "jev_emerald.py").write_text("plugin source\n")
    (project / "web" / "index.html").write_text("viewer source\n")
    (pokebot / "plugins" / "jev_emerald.py").write_text("plugin source\n")
    monkeypatch.setattr(bootstrap_module, "PROJECT_ROOT", project)
    monkeypatch.setattr(bootstrap_module, "POKEBOT_DIR", pokebot)

    bootstrap_module.install_local_sources()

    plugin = pokebot / "plugins" / "jev_emerald.py"
    assert plugin.is_symlink()
    assert plugin.resolve() == project / "plugins" / "jev_emerald.py"
