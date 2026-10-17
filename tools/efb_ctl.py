"""List / stop / ensure a single BATCRadar HTTP server for local development."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import settings as app_settings  # noqa: E402


def _port() -> int:
    return app_settings.listen_port(env_port=os.environ.get("EFB_PORT"))


def _url(path: str = "/api/version") -> str:
    return f"http://127.0.0.1:{_port()}{path}"


def _probe() -> dict | None:
    try:
        with urllib.request.urlopen(_url(), timeout=1.5) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"raw": data}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def _wmic_python_rows() -> list[tuple[int, str]]:
    """Return (pid, commandline) for python processes that look like this app."""
    try:
        out = subprocess.check_output(
            [
                "wmic",
                "process",
                "where",
                "name='python.exe' or name='pythonw.exe' or name='BATCRadar.exe'",
                "get",
                "ProcessId,CommandLine",
                "/FORMAT:CSV",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (OSError, subprocess.CalledProcessError):
        return _ps_python_rows()

    rows: list[tuple[int, str]] = []
    root_s = str(ROOT).lower()
    for line in out.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("node,"):
            continue
        # CSV: Node,CommandLine,ProcessId
        parts = line.split(",")
        if len(parts) < 3:
            continue
        pid_s = parts[-1].strip()
        cmd = ",".join(parts[1:-1]).strip().strip('"')
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        low = cmd.lower()
        if "batcradar.exe" in low:
            rows.append((pid, cmd))
            continue
        if "server.py" in low and (root_s in low or "server.py" in low):
            # Prefer path match; still accept bare server.py started from ROOT cwd
            if root_s in low or "\\server.py" in low or "/server.py" in low:
                rows.append((pid, cmd))
    return rows


def _ps_python_rows() -> list[tuple[int, str]]:
    script = (
        "$root = [regex]::Escape('" + str(ROOT).replace("'", "''") + "');"
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe' OR Name='BATCRadar.exe'\" |"
        " ForEach-Object {"
        "  $c = [string]$_.CommandLine;"
        "  if (-not $c) { return };"
        "  $l = $c.ToLowerInvariant();"
        "  if ($l -like '*batcradar.exe*' -or ($l -like '*server.py*' -and $c -match $root)) {"
        "    '{0}`t{1}' -f $_.ProcessId, $c"
        "  }"
        " }"
    )
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", script],
            text=True,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    rows: list[tuple[int, str]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line or "\t" not in line:
            continue
        pid_s, cmd = line.split("\t", 1)
        try:
            rows.append((int(pid_s), cmd))
        except ValueError:
            continue
    return rows


def _listeners_on_port(port: int) -> list[int]:
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"],
            text=True,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    pids: list[int] = []
    suffix = f":{port}"
    for line in out.splitlines():
        if "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        local = parts[1]
        if not local.endswith(suffix):
            continue
        try:
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid and pid not in pids:
            pids.append(pid)
    return pids


def cmd_status() -> int:
    port = _port()
    print(f"  Port (settings/default): {port}")
    live = _probe()
    if live:
        ver = live.get("version", "?")
        print(f"  HTTP:   OK  http://127.0.0.1:{port}  v{ver}")
    else:
        print(f"  HTTP:   nicht erreichbar auf http://127.0.0.1:{port}")

    procs = _wmic_python_rows()
    if not procs:
        # Fallback: anything listening on our port
        listen = _listeners_on_port(port)
        if listen:
            print(f"  Listener auf :{port}: PIDs {', '.join(map(str, listen))} (nicht sicher BATCRadar)")
        else:
            print("  Prozesse: keiner")
        return 0 if live else 1

    print("  Prozesse:")
    for pid, cmd in procs:
        short = cmd if len(cmd) < 120 else cmd[:117] + "..."
        print(f"    PID {pid}: {short}")
    return 0


def _kill_pid(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def cmd_stop() -> int:
    port = _port()
    killed: list[int] = []
    for pid, _cmd in _wmic_python_rows():
        if _kill_pid(pid):
            killed.append(pid)
    # Also clear whatever still owns the listen port (stale waitress etc.)
    for pid in _listeners_on_port(port):
        if pid not in killed and _kill_pid(pid):
            killed.append(pid)

    # Wait until probe fails / port free
    for _ in range(20):
        if _probe() is None and not _listeners_on_port(port):
            break
        time.sleep(0.15)

    if killed:
        print(f"  Beendet: PID {', '.join(map(str, killed))}")
    else:
        print("  Kein BATCRadar-Server gefunden.")
    return 0


def cmd_ensure() -> int:
    """If healthy server already serves our port, keep it. Else stop leftovers and start one."""
    port = _port()
    live = _probe()
    if live:
        ver = live.get("version", "?")
        print(f"  Schon aktiv: http://127.0.0.1:{port}  v{ver}")
        print("  Kein zweiter Server gestartet.")
        return 0

    cmd_stop()
    print(f"  Starte server.py auf Port {port} ...")
    # Caller (START.bat) runs server in foreground; ensure is for agents
    creation = 0
    if sys.platform == "win32":
        creation = subprocess.CREATE_NEW_CONSOLE  # type: ignore[attr-defined]
    subprocess.Popen(
        [sys.executable, str(ROOT / "server.py")],
        cwd=str(ROOT),
        creationflags=creation,
    )
    for _ in range(40):
        time.sleep(0.25)
        live = _probe()
        if live:
            print(f"  OK: http://127.0.0.1:{port}  v{live.get('version', '?')}")
            return 0
    print("  Warnung: Server startet, antwortet aber noch nicht auf /api/version.")
    return 1


def main() -> int:
    p = argparse.ArgumentParser(description="BATCRadar single-server control")
    p.add_argument("action", choices=("status", "stop", "ensure"))
    args = p.parse_args()
    if args.action == "status":
        return cmd_status()
    if args.action == "stop":
        return cmd_stop()
    return cmd_ensure()


if __name__ == "__main__":
    raise SystemExit(main())
