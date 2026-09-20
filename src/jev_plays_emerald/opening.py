"""Legal semantic choices for the Emerald opening."""

from __future__ import annotations

from jev_plays_emerald.actions import Action
from jev_plays_emerald.state import Observation


def legal_actions(observation: Observation) -> tuple[Action, ...]:
    if observation.game_state == "CHOOSE_STARTER" or observation.menu_phase == "starter":
        return tuple(
            Action(f"starter:{starter.casefold()}", f"Choose {starter}", observation.context_id)
            for starter in ("Treecko", "Torchic", "Mudkip")
        )

    if observation.game_state == "BATTLE" and observation.battle_phase in {"action", "move"}:
        if observation.active_battler is None:
            return ()
        return tuple(
            Action(
                f"battle-move:{index}",
                _move_label(move),
                observation.context_id,
            )
            for index, move in enumerate(observation.active_battler.moves)
            if move.pp > 0 and move.usable
        )

    return ()


def _move_label(move) -> str:
    accuracy = f"{move.accuracy * 100:g}%" if move.accuracy <= 1 else f"{move.accuracy:g}%"
    mechanics = f"{move.type}, {move.pp}/{move.max_pp} PP, {accuracy} accuracy"
    if move.power > 0:
        mechanics += f", {move.power} power"
    description = f" {move.description}" if move.description else ""
    return f"Use {move.name} ({mechanics}).{description}"
