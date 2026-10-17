"""Ensure only one BATCRadar HTTP server runs (MSFS exe.xml may relaunch us)."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

# Kept alive for process lifetime so the OS mutex stays held.
_mutex_handle: Any = None


def probe_version(port: int, *, timeout: float = 1.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def _try_windows_mutex() -> bool | None:
    """True = we own it, False = another process owns it, None = unavailable."""
    global _mutex_handle
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.SetLastError(0)
        handle = kernel32.CreateMutexW(None, False, "Local\\BATCRadarSingleInstance")
        if not handle:
            return None
        err = int(kernel32.GetLastError())
        _mutex_handle = handle
        # ERROR_ALREADY_EXISTS = 183
        return err != 183
    except (AttributeError, OSError, ValueError):
        return None


def claim_or_exit(port: int) -> bool:
    """Return True if this process should serve. Exit quietly if another instance is live.

    Returns False only when the caller should stop without serving (and without a
    long pause). Raises SystemExit(0) after printing a short notice when a healthy
    peer is already listening.
    """
    owned = _try_windows_mutex()

    if owned is False:
        # Another process holds the mutex — wait for its HTTP (warmup can take a bit).
        for _ in range(60):
            live = probe_version(port)
            if live is not None:
                ver = live.get("version", "?")
                print()
                print(f"  BATCRadar läuft bereits (v{ver}).")
                print(f"  http://127.0.0.1:{port}")
                print("  Kein zweiter Server gestartet.")
                print()
                raise SystemExit(0)
            time.sleep(0.25)
        print()
        print("  BATCRadar scheint schon zu laufen, antwortet aber nicht.")
        print("  Kein zweiter Server gestartet.")
        print()
        raise SystemExit(0)

    # We own the mutex (or mutex unavailable): still bail if something healthy
    # already answers on our port (e.g. leftover without our mutex name).
    live = probe_version(port)
    if live is not None:
        ver = live.get("version", "?")
        print()
        print(f"  BATCRadar läuft bereits (v{ver}).")
        print(f"  http://127.0.0.1:{port}")
        print("  Kein zweiter Server gestartet.")
        print()
        raise SystemExit(0)

    return True
