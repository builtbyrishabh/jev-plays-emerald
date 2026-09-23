#!/usr/bin/env python3
"""Install the pinned PokéBot runtime in this repository's ignored cache."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# The launcher's pins are stdlib-only, so they import before dependencies exist.
sys.path.insert(0, str(PROJECT_ROOT / "src"))
from jev_plays_emerald.__main__ import POKEBOT_PATCH, POKEBOT_REVISION, POKEBOT_ROOT  # noqa: E402

CACHE_DIR = PROJECT_ROOT / ".cache"
POKEBOT_DIR = POKEBOT_ROOT
POKEBOT_URL = "https://github.com/40Cakes/pokebot-gen3.git"
PREVIOUS_PATCH_SHA256 = "38a123d0e3e9b0481fb032de1f88955907cf0be976a4d7fa67c3c757894bf272"
LIBMGBA_TAG = "0.2.0-2"
LIBMGBA_ARCHIVE = "libmgba-py_0.2.0_macos-arm64.zip"
LIBMGBA_URL = f"https://github.com/hanzi/libmgba-py/releases/download/{LIBMGBA_TAG}/{LIBMGBA_ARCHIVE}"
LIBMGBA_SHA256 = "abd90c6e4fd98d2a0bffbda16d5b94fa6944654b61353d4592af7b4b4a3484b7"


def run(*command: str, cwd: Path = PROJECT_ROOT, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(command, cwd=cwd, env=env, check=True, text=True, capture_output=True)
    return result.stdout.strip()


def verify_host() -> None:
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError(f"Python 3.13 is required; found {platform.python_version()}")
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("This bootstrap currently supports Apple Silicon macOS only")


def ensure_pokebot_checkout() -> None:
    if not POKEBOT_DIR.exists():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        run("git", "clone", "--filter=blob:none", "--no-checkout", POKEBOT_URL, str(POKEBOT_DIR))
        run("git", "checkout", "--detach", POKEBOT_REVISION, cwd=POKEBOT_DIR)
    elif not (POKEBOT_DIR / ".git").is_dir():
        raise RuntimeError(f"Refusing to replace non-Git path: {POKEBOT_DIR}")

    head = run("git", "rev-parse", "HEAD", cwd=POKEBOT_DIR)
    if head != POKEBOT_REVISION:
        raise RuntimeError(f"Existing PokéBot checkout is at {head}; expected {POKEBOT_REVISION}")
    expected_patch = POKEBOT_PATCH.read_text().strip()
    tracked_status = run("git", "status", "--porcelain", "--untracked-files=no", cwd=POKEBOT_DIR)
    if not tracked_status:
        run("git", "apply", str(POKEBOT_PATCH), cwd=POKEBOT_DIR)
    actual_patch = run("git", "diff", "HEAD", "--binary", cwd=POKEBOT_DIR)
    if hashlib.sha256(actual_patch.encode()).hexdigest() == PREVIOUS_PATCH_SHA256:
        # Upgrade only our exact previous patch; never discard user edits.
        subprocess.run(["git", "apply", "--reverse", "-"], cwd=POKEBOT_DIR,
                       input=actual_patch + "\n", text=True, capture_output=True, check=True)
        run("git", "apply", str(POKEBOT_PATCH), cwd=POKEBOT_DIR)
        actual_patch = run("git", "diff", "HEAD", "--binary", cwd=POKEBOT_DIR)
    if actual_patch != expected_patch:
        raise RuntimeError("PokéBot checkout has tracked changes other than the required trainer-action patch")
    if not (POKEBOT_DIR / "LICENSE").is_file():
        raise RuntimeError("Pinned PokéBot checkout is missing its GPL-3.0 license")


def verify_archive(path: Path) -> None:
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != LIBMGBA_SHA256:
        raise RuntimeError(f"Native archive checksum mismatch: expected {LIBMGBA_SHA256}, got {actual}")


def install_native_archive() -> None:
    archive = CACHE_DIR / LIBMGBA_ARCHIVE
    if not archive.exists():
        temporary = archive.with_suffix(".part")
        urllib.request.urlretrieve(LIBMGBA_URL, temporary)
        temporary.replace(archive)
    verify_archive(archive)

    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (POKEBOT_DIR / member.filename).resolve()
            if not target.is_relative_to(POKEBOT_DIR.resolve()):
                raise RuntimeError(f"Unsafe native archive member: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            data = bundle.read(member)
            if target.exists() and target.read_bytes() != data:
                raise RuntimeError(f"Refusing to overwrite modified native file: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(data)


def _matching_plain_directory(source: Path, target: Path) -> bool:
    source_files = {path.relative_to(source) for path in source.rglob("*") if path.is_file()}
    target_files = {path.relative_to(target) for path in target.rglob("*") if path.is_file()}
    if source_files != target_files:
        return False
    if any(path.is_symlink() for path in target.rglob("*")):
        return False
    return all((source / path).read_bytes() == (target / path).read_bytes() for path in source_files)


def _install_source_link(source: Path, target: Path) -> None:
    if target.is_symlink():
        if target.resolve() == source.resolve():
            return
        raise RuntimeError(f"Refusing to replace unrelated link: {target}")
    if target.exists():
        if source.is_file() and target.is_file() and source.read_bytes() == target.read_bytes():
            target.unlink()
        elif source.is_dir() and target.is_dir() and _matching_plain_directory(source, target):
            shutil.rmtree(target)
        else:
            raise RuntimeError(f"Refusing to replace unrelated path: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    relative_source = os.path.relpath(source, target.parent)
    target.symlink_to(relative_source, target_is_directory=source.is_dir())


def install_local_sources() -> None:
    _install_source_link(
        PROJECT_ROOT / "plugins" / "jev_emerald.py",
        POKEBOT_DIR / "plugins" / "jev_emerald.py",
    )


def native_environment() -> dict[str, str]:
    try:
        prefix = run("brew", "--prefix", "mgba")
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        raise RuntimeError("mGBA 0.10.x is required; install it with `brew install mgba`") from error
    environment = os.environ.copy()
    library_path = str(Path(prefix) / "lib")
    if existing := environment.get("DYLD_LIBRARY_PATH"):
        library_path = f"{library_path}:{existing}"
    environment["DYLD_LIBRARY_PATH"] = library_path
    return environment


def probe_native_import() -> str:
    python = PROJECT_ROOT / ".venv" / "bin" / "python"
    code = (
        "import mgba, mgba.core; "
        "from modules.libmgba import LibmgbaEmulator; "
        "print(f'libmgba-py={mgba.__file__}'); "
        "print(f'pokebot-emulator={LibmgbaEmulator.__module__}.{LibmgbaEmulator.__name__}')"
    )
    return run(str(python), "-c", code, cwd=POKEBOT_DIR, env=native_environment())


def mark_requirements_checked() -> None:
    python = PROJECT_ROOT / ".venv" / "bin" / "python"
    requirements_hash = run(
        str(python),
        "-c",
        "from requirements import get_requirements_hash; print(get_requirements_hash())",
        cwd=POKEBOT_DIR,
    )
    (POKEBOT_DIR / ".last-requirements-check").write_text(requirements_hash)


def probe_plugin_registration() -> str:
    python = PROJECT_ROOT / ".venv" / "bin" / "python"
    code = (
        "from modules.plugins import load_plugins; "
        "load_plugins(); "
        "from modules.modes import get_bot_mode_names; "
        "names = get_bot_mode_names(); "
        "assert 'Jev Emerald' in names, names; "
        "print('plugin-mode=Jev Emerald')"
    )
    return run(str(python), "-c", code, cwd=POKEBOT_DIR, env=native_environment())


def main() -> None:
    verify_host()
    ensure_pokebot_checkout()
    run("uv", "sync", "--locked")
    install_native_archive()
    install_local_sources()
    probe = probe_native_import()
    mark_requirements_checked()
    plugin_probe = probe_plugin_registration()
    print(f"PokéBot revision: {POKEBOT_REVISION}")
    print(f"libmgba-py archive: {LIBMGBA_TAG}/{LIBMGBA_ARCHIVE} ({LIBMGBA_SHA256})")
    print(probe)
    print(plugin_probe)


if __name__ == "__main__":
    main()
