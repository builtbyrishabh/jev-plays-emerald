import importlib.util
import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from jev_plays_emerald import __main__ as launcher
from jev_plays_emerald.__main__ import verify_rom

PROJECT_ROOT = Path(__file__).parents[1]
POKEBOT_ROOT = PROJECT_ROOT / ".cache" / "pokebot-gen3"


def test_wrong_rom_is_rejected(tmp_path: Path) -> None:
    rom = tmp_path / "wrong.gba"
    rom.write_bytes(b"not the supported Emerald ROM")

    with pytest.raises(ValueError, match="ROM"):
        verify_rom(rom)


def test_profile_metadata_quotes_legal_rom_filename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pokebot = tmp_path / "pokebot"
    (pokebot / "roms").mkdir(parents=True)
    (pokebot / "profiles").mkdir()
    rom = tmp_path / "Emerald #1: copy\n.gba"
    rom.write_bytes(b"fixture")
    monkeypatch.setattr(launcher, "POKEBOT_ROOT", pokebot)

    launcher.configure_profile(rom, "test-profile")

    metadata = YAML(typ="safe").load((pokebot / "profiles" / "test-profile" / "metadata.yml"))
    assert metadata["rom"]["file_name"] == rom.name


def test_mode_keeps_control_without_pressing_buttons_in_battle() -> None:
    sys.path.insert(0, str(POKEBOT_ROOT))
    try:
        from modules.context import context
        from modules.modes import BattleAction

        from jev_plays_emerald.mode import JevEmeraldMode

        class FakeEmulator:
            def __init__(self) -> None:
                self.held_buttons = {"A"}

            def reset_held_buttons(self) -> None:
                self.held_buttons.clear()

        previous_emulator = context.emulator
        try:
            context.emulator = FakeEmulator()
            mode = JevEmeraldMode()

            assert next(mode.run()) is None
            assert context.emulator.held_buttons == set()
            assert mode.on_battle_started(None) is BattleAction.CustomAction
        finally:
            context.emulator = previous_emulator
    finally:
        sys.path.remove(str(POKEBOT_ROOT))


def test_trainer_battle_listener_preserves_custom_action(monkeypatch: pytest.MonkeyPatch) -> None:
    sys.path.insert(0, str(POKEBOT_ROOT))
    try:
        from modules.battle_state import EncounterType
        from modules.context import context
        from modules.memory import GameState
        from modules.modes import BattleAction, FrameInfo
        from modules.modes import _listeners

        from jev_plays_emerald.mode import JevEmeraldMode

        class FakeEmulator:
            def reset_held_buttons(self) -> set[str]:
                return set()

        previous = (context.bot_mode, context.bot_mode_instance, context.controller_stack, context.emulator)
        try:
            mode = JevEmeraldMode()
            context.bot_mode = mode.name()
            context.bot_mode_instance = mode
            context.controller_stack = []
            context.emulator = FakeEmulator()
            listener = _listeners.BattleListener()
            listener._in_battle = True
            monkeypatch.setattr(_listeners, "get_game_state", lambda: GameState.BATTLE)
            monkeypatch.setattr(_listeners, "get_encounter_type", lambda: EncounterType.Trainer)
            monkeypatch.setattr(_listeners, "get_opponent", lambda: None)
            monkeypatch.setattr(_listeners, "DefaultBattleStrategy", lambda: object())
            frame = FrameInfo(1, GameState.BATTLE, [], [], [], None)

            listener.handle_frame(mode, frame)

            assert listener._current_action is BattleAction.CustomAction
            [plugin_hook] = context.controller_stack
            with pytest.raises(StopIteration):
                next(plugin_hook)
        finally:
            context.bot_mode, context.bot_mode_instance, context.controller_stack, context.emulator = previous
    finally:
        sys.path.remove(str(POKEBOT_ROOT))


def test_plugin_registers_the_no_input_mode() -> None:
    sys.path.insert(0, str(POKEBOT_ROOT))
    try:
        spec = importlib.util.spec_from_file_location("jev_emerald_plugin", PROJECT_ROOT / "plugins" / "jev_emerald.py")
        assert spec is not None and spec.loader is not None
        plugin_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin_module)

        [mode] = plugin_module.JevEmeraldPlugin().get_additional_bot_modes()

        assert mode.name() == "Jev Emerald"
    finally:
        sys.path.remove(str(POKEBOT_ROOT))


def test_plugin_publishes_initial_custom_state() -> None:
    sys.path.insert(0, str(POKEBOT_ROOT))
    try:
        from modules.web.http import custom_state

        spec = importlib.util.spec_from_file_location("jev_emerald_plugin", PROJECT_ROOT / "plugins" / "jev_emerald.py")
        assert spec is not None and spec.loader is not None
        plugin_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin_module)
        custom_state.clear()

        plugin_module.JevEmeraldPlugin().on_profile_loaded(None)

        assert custom_state == {"jev_emerald": {"mode": "Jev Emerald", "status": "idle"}}
    finally:
        sys.path.remove(str(POKEBOT_ROOT))
