"""Launch Jev's Pokémon Emerald mode through PokéBot Gen3."""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

EMERALD_SHA1 = "f3ae088181bf583e55daf962a92bb46f4f1d07b7"
POKEBOT_REVISION = "5dd898f830775d448b06db6f5cd65b930540f146"
MODE_NAME = "Jev Emerald"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
POKEBOT_PATCH = PROJECT_ROOT / "patches" / "pokebot-custom-trainer-action.patch"
POKEBOT_ROOT = PROJECT_ROOT / ".cache" / "pokebot-gen3"


def verify_rom(path: Path) -> None:
    """Reject ROMs other than the supported English Emerald revision."""

    actual_sha1 = hashlib.sha1(path.read_bytes()).hexdigest()
    if actual_sha1 != EMERALD_SHA1:
        raise ValueError(
            "ROM does not match the supported unmodified English Pokémon Emerald "
            f"revision (expected SHA-1 {EMERALD_SHA1}, got {actual_sha1})"
        )


def configure_profile(rom: Path, profile_name: str) -> None:
    """Create this app's local profile once, without changing an existing one."""

    if Path(profile_name).name != profile_name or profile_name in {"", ".", ".."}:
        raise ValueError("Profile name must be one directory name")

    rom_target = POKEBOT_ROOT / "roms" / rom.name
    if rom_target.exists():
        verify_rom(rom_target)
    else:
        rom_target.symlink_to(rom.resolve())

    profile = POKEBOT_ROOT / "profiles" / profile_name
    metadata = profile / "metadata.yml"
    http_config = profile / "http.yml"
    expected_metadata = (
        "version: 1\n"
        "rom:\n"
        f"  file_name: {json.dumps(rom_target.name, ensure_ascii=False)}\n"
        "  game_code: BPEE\n"
        "  revision: 0\n"
        "  language: E\n"
    )
    expected_http = "http_server:\n  enable: true\n  ip: 127.0.0.1\n  port: 8888\n"

    if profile.exists():
        if not metadata.is_file() or metadata.read_text() != expected_metadata:
            raise RuntimeError(f"Existing profile is not managed by this launcher: {profile}")
        if not http_config.is_file() or http_config.read_text() != expected_http:
            raise RuntimeError(f"Existing profile HTTP configuration differs; refusing to overwrite {http_config}")
        return

    profile.mkdir()
    metadata.write_text(expected_metadata)
    http_config.write_text(expected_http)


def runtime_environment() -> dict[str, str]:
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("The verified runtime currently supports Apple Silicon macOS only")
    try:
        prefix = subprocess.run(
            ["brew", "--prefix", "mgba"], check=True, text=True, capture_output=True
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        raise RuntimeError("mGBA 0.10.x is required; install it with `brew install mgba`") from error
    environment = os.environ.copy()
    environment["DYLD_LIBRARY_PATH"] = str(Path(prefix) / "lib")
    return environment


def verify_runtime() -> None:
    if not (POKEBOT_ROOT / ".git").is_dir():
        raise RuntimeError("Run `python3.13 scripts/bootstrap.py` first")
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=POKEBOT_ROOT, check=True, text=True, capture_output=True
    ).stdout.strip()
    if revision != POKEBOT_REVISION:
        raise RuntimeError(f"PokéBot revision is {revision}; expected {POKEBOT_REVISION}")
    tracked_diff = subprocess.run(
        ["git", "diff", "HEAD", "--binary"], cwd=POKEBOT_ROOT, check=True, text=True, capture_output=True
    ).stdout.strip()
    if tracked_diff != POKEBOT_PATCH.read_text().strip():
        raise RuntimeError("PokéBot trainer-action patch is missing or the checkout has other tracked changes")
    if not (POKEBOT_ROOT / "plugins" / "jev_emerald.py").is_file():
        raise RuntimeError("Jev plugin is not installed; rerun `python3.13 scripts/bootstrap.py`")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", required=True, type=Path, help="path to the user-provided Emerald ROM")
    parser.add_argument("--profile", default="jev-emerald", help="local PokéBot profile name")
    parser.add_argument("--check", action="store_true", help="validate and prepare without starting the emulator")
    arguments = parser.parse_args(argv)

    rom = arguments.rom.expanduser().resolve()
    verify_rom(rom)
    verify_runtime()
    configure_profile(rom, arguments.profile)
    if arguments.check:
        print(f"ROM verified; profile ready: {arguments.profile}")
        return 0

    print("Viewer: http://127.0.0.1:8888/stream_video?fps=15")
    command = [
        str(PROJECT_ROOT / ".venv" / "bin" / "python"),
        "pokebot.py",
        arguments.profile,
        "--bot-mode",
        MODE_NAME,
        "--headless",
        "--no-audio",
    ]
    subprocess.run(command, cwd=POKEBOT_ROOT, env=runtime_environment(), check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
