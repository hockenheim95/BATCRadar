"""MSFS exe.xml Launch.Addon: start BATCRadar with the simulator.

Source of truth is the on-disk exe.xml file(s), not settings.json.
Supports MSFS 2020 and 2024 (Steam + Microsoft Store).
"""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

ADDON_NAME = "BATCRadar"


def _localappdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))


def _roaming() -> Path:
    return Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))


def candidate_targets() -> list[dict[str, Any]]:
    """All known exe.xml locations (exist or not)."""
    la = _localappdata()
    ro = _roaming()
    return [
        {
            "id": "msfs2024-steam",
            "label": "MSFS 2024 (Steam)",
            "path": ro / "Microsoft Flight Simulator 2024" / "exe.xml",
            "config_dir": ro / "Microsoft Flight Simulator 2024",
        },
        {
            "id": "msfs2024-store",
            "label": "MSFS 2024 (Store)",
            "path": la
            / "Packages"
            / "Microsoft.Limitless_8wekyb3d8bbwe"
            / "LocalCache"
            / "exe.xml",
            "config_dir": la / "Packages" / "Microsoft.Limitless_8wekyb3d8bbwe" / "LocalCache",
        },
        {
            "id": "msfs2020-steam",
            "label": "MSFS 2020 (Steam)",
            "path": ro / "Microsoft Flight Simulator" / "exe.xml",
            "config_dir": ro / "Microsoft Flight Simulator",
        },
        {
            "id": "msfs2020-store",
            "label": "MSFS 2020 (Store)",
            "path": la
            / "Packages"
            / "Microsoft.FlightSimulator_8wekyb3d8bbwe"
            / "LocalCache"
            / "exe.xml",
            "config_dir": la
            / "Packages"
            / "Microsoft.FlightSimulator_8wekyb3d8bbwe"
            / "LocalCache",
        },
    ]


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def current_exe() -> Path | None:
    """Absolute path written into exe.xml. Only meaningful for the built EXE."""
    if not is_frozen():
        return None
    return Path(sys.executable).resolve()


def _norm_path(p: str | Path) -> str:
    try:
        return str(Path(p).resolve()).casefold()
    except OSError:
        return str(p).replace("/", "\\").casefold()


def _text(el: ET.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def _is_disabled(addon: ET.Element) -> bool:
    raw = _text(addon.find("Disabled")).casefold()
    if not raw:
        return False
    return raw in ("true", "1", "yes")


def _managed_targets() -> list[dict[str, Any]]:
    """Targets whose MSFS config directory exists (we may create exe.xml there)."""
    out: list[dict[str, Any]] = []
    for t in candidate_targets():
        cfg: Path = t["config_dir"]
        if cfg.is_dir():
            out.append(t)
    return out


def _empty_document() -> ET.Element:
    root = ET.Element("SimBase.Document", Type="Launch", version="1,0")
    ET.SubElement(root, "Descr").text = "Launch"
    ET.SubElement(root, "Filename").text = "EXE.xml"
    ET.SubElement(root, "Disabled").text = "False"
    return root


def _load_root(path: Path) -> ET.Element:
    if not path.is_file():
        return _empty_document()
    tree = ET.parse(path)
    root = tree.getroot()
    if root.tag != "SimBase.Document":
        raise ValueError(f"Unexpected root element: {root.tag}")
    return root


def _our_addons(root: ET.Element) -> list[ET.Element]:
    found: list[ET.Element] = []
    for addon in root.findall("Launch.Addon"):
        if _text(addon.find("Name")).casefold() == ADDON_NAME.casefold():
            found.append(addon)
    return found


def _entry_matches(addon: ET.Element, exe: Path) -> bool:
    if _is_disabled(addon):
        return False
    path = _text(addon.find("Path"))
    if not path:
        return False
    return _norm_path(path) == _norm_path(exe)


def _entry_active(addon: ET.Element) -> bool:
    """User intent: a non-disabled BATCRadar Launch.Addon (path may be stale)."""
    return not _is_disabled(addon)


def _needs_heal(ours: list[ET.Element], exe: Path) -> bool:
    """True if any active addon exists and the on-disk state is not a single matching entry."""
    active = [a for a in ours if _entry_active(a)]
    if not active:
        return False
    return not (len(ours) == 1 and _entry_matches(ours[0], exe))


def _write_our_addon(root: ET.Element, exe: Path) -> None:
    for addon in _our_addons(root):
        root.remove(addon)
    addon = ET.SubElement(root, "Launch.Addon")
    ET.SubElement(addon, "Name").text = ADDON_NAME
    ET.SubElement(addon, "Disabled").text = "False"
    ET.SubElement(addon, "Path").text = str(exe)


def _heal_target(target: dict[str, Any], exe: Path) -> bool:
    """Rewrite Path to current exe when autostart is already on (stale install folder).

    Returns True if exe.xml was written.
    """
    path: Path = target["path"]
    if not path.is_file():
        return False
    root = _load_root(path)
    ours = _our_addons(root)
    if not _needs_heal(ours, exe):
        return False
    _write_our_addon(root, exe)
    _write_xml(path, root)
    return True


def heal_paths() -> None:
    """Best-effort: point existing enabled Launch.Addon entries at this EXE.

    Call on frozen startup (and from status) so ZIP updates in a new folder keep
    MSFS autostart without the user toggling the checkbox again.
    """
    if not is_frozen():
        return
    exe = current_exe()
    if exe is None:
        return
    for t in _managed_targets():
        try:
            _heal_target(t, exe)
        except (OSError, ET.ParseError, ValueError):
            pass


def _read_target(target: dict[str, Any], exe: Path | None) -> dict[str, Any]:
    path: Path = target["path"]
    info: dict[str, Any] = {
        "id": target["id"],
        "label": target["label"],
        "path": str(path),
        "present": path.is_file(),
        "enabled": False,
        "ok": True,
        "error": "",
    }
    if exe is None:
        info["ok"] = False
        info["error"] = "not_frozen"
        return info
    try:
        if not path.is_file():
            return info
        root = _load_root(path)
        ours = _our_addons(root)
        # Intent: any non-disabled BATCRadar entry counts as on (heal fixes Path).
        info["enabled"] = any(_entry_active(a) for a in ours)
    except (OSError, ET.ParseError, ValueError) as exc:
        info["ok"] = False
        info["error"] = str(exc)
    return info


def _write_xml(path: Path, root: ET.Element) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(ET, "indent"):
        ET.indent(root, space="    ")
    tree = ET.ElementTree(root)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tree.write(tmp, encoding="utf-8", xml_declaration=True)
    if path.is_file():
        bak = path.with_suffix(path.suffix + ".bak")
        try:
            bak.write_bytes(path.read_bytes())
        except OSError:
            pass
    tmp.replace(path)


def _set_target(target: dict[str, Any], exe: Path, enabled: bool) -> dict[str, Any]:
    path: Path = target["path"]
    info: dict[str, Any] = {
        "id": target["id"],
        "label": target["label"],
        "path": str(path),
        "present": path.is_file(),
        "enabled": False,
        "ok": True,
        "error": "",
    }
    try:
        root = _load_root(path)
        if enabled:
            _write_our_addon(root, exe)
        else:
            for addon in _our_addons(root):
                root.remove(addon)
        _write_xml(path, root)
        # Verify from disk
        verified = _read_target(target, exe)
        info.update(verified)
        if enabled and not info["enabled"]:
            info["ok"] = False
            info["error"] = info.get("error") or "verify_failed"
        if not enabled and info["enabled"]:
            info["ok"] = False
            info["error"] = info.get("error") or "verify_failed"
    except (OSError, ET.ParseError, ValueError) as exc:
        info["ok"] = False
        info["error"] = str(exc)
        try:
            info["enabled"] = _read_target(target, exe).get("enabled", False)
        except Exception:
            info["enabled"] = False
    return info


def status() -> dict[str, Any]:
    """Verified autostart state. exe.xml is the only source of truth."""
    heal_paths()
    frozen = is_frozen()
    exe = current_exe()
    managed = _managed_targets()
    targets = [_read_target(t, exe) for t in managed]

    if not frozen:
        return {
            "available": False,
            "enabled": False,
            "frozen": False,
            "exe": None,
            "targets": targets,
            "error": "Requires the built BATCRadar.exe (not python server.py).",
        }
    if not managed:
        return {
            "available": False,
            "enabled": False,
            "frozen": True,
            "exe": str(exe) if exe else None,
            "targets": [],
            "error": "No MSFS 2020/2024 config folder found.",
        }

    # Enabled only if every managed target has a matching active entry.
    enabled = bool(targets) and all(t.get("ok") and t.get("enabled") for t in targets)
    bad = next((t for t in targets if not t.get("ok")), None)
    return {
        "available": True,
        "enabled": enabled,
        "frozen": True,
        "exe": str(exe) if exe else None,
        "targets": targets,
        "error": (bad or {}).get("error") or "",
    }


def set_enabled(want: bool) -> dict[str, Any]:
    """Write exe.xml, then re-read. Returns the same shape as status()."""
    frozen = is_frozen()
    exe = current_exe()
    if not frozen or exe is None:
        out = status()
        out["ok"] = False
        out["error"] = "Requires the built BATCRadar.exe (not python server.py)."
        return out

    managed = _managed_targets()
    if not managed:
        out = status()
        out["ok"] = False
        out["error"] = "No MSFS 2020/2024 config folder found."
        return out

    results = [_set_target(t, exe, bool(want)) for t in managed]
    disk_enabled = bool(results) and all(t.get("enabled") for t in results)
    write_ok = bool(results) and all(t.get("ok") for t in results)
    if want:
        ok = write_ok and disk_enabled
    else:
        ok = write_ok and all(not t.get("enabled") for t in results)

    bad = next((t for t in results if not t.get("ok")), None)
    return {
        "ok": ok,
        "available": True,
        "enabled": disk_enabled,
        "frozen": True,
        "exe": str(exe),
        "targets": results,
        "error": "" if ok else ((bad or {}).get("error") or "verify_failed"),
    }
