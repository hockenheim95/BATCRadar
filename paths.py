"""App folders: source tree while developing, EXE folder after a build."""

from __future__ import annotations

import sys
from pathlib import Path


def root() -> Path:
    """Writable install folder (next to BATCRadar.exe, or the project folder)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundle() -> Path:
    """Packed files (web/, VERSION, shipped JSON). Same as root() in development."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", root()))
    return Path(__file__).resolve().parent


def data_file(name: str, *, write: bool = False) -> Path:
    """JSON under data/. Reads prefer a local copy, then the shipped file. Writes always go next to the EXE."""
    user = root() / "data" / name
    if write:
        user.parent.mkdir(parents=True, exist_ok=True)
        return user
    if user.is_file():
        return user
    return bundle() / "data" / name
