"""PokéBot Gen3 plugin entrypoint for Jev Plays Emerald."""

from collections.abc import Iterable

from modules.modes import BotMode
from modules.plugin_interface import BotPlugin

from jev_plays_emerald.mode import JevEmeraldMode


class JevEmeraldPlugin(BotPlugin):
    def get_additional_bot_modes(self) -> Iterable[type[BotMode]]:
        yield JevEmeraldMode

    def on_profile_loaded(self, profile: object) -> None:
        from modules.web.http import custom_state

        custom_state["jev_emerald"] = {"mode": JevEmeraldMode.name(), "status": "idle"}
