"""PokéBot Gen3 plugin entrypoint for Jev Plays Emerald."""

from collections import deque
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from aiohttp import web

from modules.context import context
from modules.main import work_queue
from modules.modes import BotListener, BotMode, FrameInfo
from modules.plugin_interface import BotPlugin

from jev_plays_emerald.mode import JevEmeraldMode

VIEWER_DIR = Path(__file__).resolve().parents[1] / "web"


class ViewerStatePublisher:
    """Build browser data only from immutable values already owned by the mode."""

    def __init__(self) -> None:
        self._last_decision: object | None = None
        self._recent_choices: deque[dict[str, object]] = deque(maxlen=6)

    def build(self, mode: JevEmeraldMode) -> dict[str, object]:
        status = mode.status
        observation = mode.observation
        completed = bool(getattr(mode, "completed", False))
        phase = status.phase
        if (
            observation is not None
            and observation.opening_flags.defeated_rival_route103
            and not completed
        ):
            phase = "checkpoint"

        decision = _decision_view(
            status.last_decision,
            dict(status.available_actions)
            if status.last_decision is not None
            and status.last_decision.context_id == status.context_id
            else {},
        )
        if status.last_decision is not None and status.last_decision is not self._last_decision:
            self._recent_choices.append(decision)
            self._last_decision = status.last_decision

        return {
            "version": 1,
            "mode": mode.name(),
            "paused": mode.paused,
            "goal": _current_goal(observation, completed),
            "planner": getattr(mode, "planner_view", None),
            "status": {
                "phase": phase,
                "context_id": status.context_id,
                "pending_attempt": status.pending_attempt,
                "last_error": status.last_error,
                "last_battle_outcome": status.last_battle_outcome,
                "last_decision": decision,
            },
            "observation": _observation_view(observation),
            "available_actions": [_action_view(action) for action in mode.available_actions],
            "active_action": _action_view(mode.active_action),
            "recent_choices": list(self._recent_choices),
            "progress": _progress_view(observation, completed),
        }


class _ViewerStateListener(BotListener):
    def __init__(self, publisher: ViewerStatePublisher) -> None:
        self._publisher = publisher

    def handle_frame(self, bot_mode: BotMode, frame: FrameInfo) -> None:
        if not isinstance(bot_mode, JevEmeraldMode):
            return
        from modules.web.http import custom_state

        custom_state["jev_emerald"] = self._publisher.build(bot_mode)


def _decision_view(decision: Any, labels: dict[str, str]) -> dict[str, object] | None:
    if decision is None:
        return None
    return {
        "context_id": decision.context_id,
        "source": decision.source,
        "action_id": decision.action_id,
        "probabilities": (
            dict(decision.probabilities) if decision.probabilities is not None else None
        ),
        "labels": labels,
        "confidence": decision.confidence,
        "latency_ms": decision.latency_ms,
    }


def _action_view(action: Any) -> dict[str, str] | None:
    if action is None:
        return None
    return {"id": action.id, "label": action.label}


def _observation_view(observation: Any) -> dict[str, object] | None:
    if observation is None:
        return None
    position = observation.position
    opponent = observation.opponent
    return {
        "context_id": observation.context_id,
        "game_state": observation.game_state,
        "menu_phase": observation.menu_phase,
        "battle_phase": observation.battle_phase,
        "position": (
            None
            if position is None
            else {
                "map_id": list(position.map_id),
                "coordinates": list(position.coordinates),
                "facing": position.facing,
            }
        ),
        "party": [
            {
                "species": member.species,
                "level": member.level,
                "hp": member.hp,
                "max_hp": member.max_hp,
                "status": member.status,
            }
            for member in observation.party
        ],
        "opponent": (
            None
            if opponent is None
            else {
                "species": opponent.species,
                "level": opponent.level,
                "hp": opponent.hp,
                "max_hp": opponent.max_hp,
                "status": opponent.status,
            }
        ),
        "recent_dialogue": list(observation.recent_dialogue),
    }


def _progress_view(observation: Any, completed: bool) -> dict[str, bool]:
    if observation is None:
        return {"starter": False, "rescue": False, "rival": False, "completed": completed}
    return {
        "starter": bool(observation.party),
        "rescue": observation.opening_flags.rescued_birch,
        "rival": observation.opening_flags.defeated_rival_route103,
        "completed": completed,
    }


def _current_goal(observation: Any, completed: bool) -> str:
    if completed:
        return "Opening complete"
    if observation is None:
        return "Waiting for the first game observation"
    if observation.opening_flags.defeated_rival_route103:
        return "Loaded checkpoint already has the rival flag"
    if not observation.party:
        return "Choose a starter and rescue Professor Birch"
    if not observation.opening_flags.rescued_birch:
        return "Rescue Professor Birch"
    if (
        observation.lab_state < 3
        and not observation.opening_flags.rival_left_for_route103
    ):
        return "Meet Professor Birch in his lab"
    return "Reach Route 103 and win the rival battle"


async def _handle_control(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except (TypeError, ValueError):
        return web.json_response({"error": "expected a JSON object"}, status=400)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"paused"}
        or type(payload["paused"]) is not bool
    ):
        return web.json_response(
            {"error": "paused must be a boolean and the only field"}, status=400
        )

    paused = payload["paused"]

    def set_paused_on_owner_thread() -> None:
        mode = context.bot_mode_instance
        if isinstance(mode, JevEmeraldMode):
            mode.set_paused(paused)

    work_queue.put_nowait(set_paused_on_owner_thread)
    return web.json_response({"queued": True, "paused": paused}, status=202)


def add_viewer_routes(app: web.Application) -> None:
    app.router.add_post("/jev/control", _handle_control)
    app.router.add_static("/jev", VIEWER_DIR)


def _install_http_route() -> None:
    """Add the local control endpoint before aiohttp freezes the upstream app."""

    from modules.web import http

    original = http.http_server
    if getattr(original, "_jev_viewer_route", False):
        return

    def http_server_with_viewer(host: str, port: int) -> web.AppRunner:
        runner = original(host, port)
        add_viewer_routes(runner.app)
        return runner

    http_server_with_viewer._jev_viewer_route = True
    http.http_server = http_server_with_viewer


class JevEmeraldPlugin(BotPlugin):
    def __init__(self) -> None:
        self._publisher = ViewerStatePublisher()

    def get_additional_bot_modes(self) -> Iterable[type[BotMode]]:
        yield JevEmeraldMode

    def get_additional_bot_listeners(self) -> Iterable[BotListener]:
        yield _ViewerStateListener(self._publisher)

    def on_profile_loaded(self, profile: object) -> None:
        from modules.web.http import custom_state

        custom_state["jev_emerald"] = {"mode": JevEmeraldMode.name(), "status": "idle"}
        _install_http_route()
