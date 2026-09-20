"""PokéBot mode that keeps the emulator neutral until gameplay is implemented."""

from collections.abc import Generator
from typing import TYPE_CHECKING

from modules.context import context
from modules.modes import BattleAction, BotMode

if TYPE_CHECKING:
    from modules.encounter import EncounterInfo


class JevEmeraldMode(BotMode):
    """Own PokéBot's frame loop without issuing controller input."""

    @staticmethod
    def name() -> str:
        return "Jev Emerald"

    def run(self) -> Generator[None, None, None]:
        context.emulator.reset_held_buttons()
        while True:
            yield

    def on_battle_started(self, encounter: "EncounterInfo | None") -> BattleAction:
        """Keep battle frames in this mode instead of starting an upstream policy."""

        return BattleAction.CustomAction
