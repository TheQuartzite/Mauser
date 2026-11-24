"""The game's own display settings, read from and written to its ini files.

  Fallout4Prefs.ini   [Display]  iSize W, iSize H, bFull Screen, bBorderless, iPresentInterval
  Fallout4Prefs.ini and Fallout4Custom.ini  [Display]  fDefaultWorldFOV, fDefault1stPersonFOV

These are the settings the game's launcher writes, plus field of view, which the launcher does not offer.
Everything else in the files is left exactly as it was.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Optional

BACKUP_SUFFIX = ".mauser-backup"
DEFAULT_FOV = 70
MODES = (("fullscreen", "Fullscreen"), ("borderless", "Borderless window"), ("window", "Window"))


@dataclass
class DisplaySettings:
    width: int = 1920
    height: int = 1080
    mode: str = "borderless"          # fullscreen, borderless or window
    vsync: bool = True
    fov_world: int = DEFAULT_FOV      # third person and the world in general
    fov_first: int = DEFAULT_FOV      # first-person view


def read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="latin-1", newline="") as f:     # latin-1 keeps every byte as it is
            return f.read()
    except OSError:
        return ""


def get_value(text: str, section: str, key: str) -> Optional[str]:
    current, section, key = "", section.lower(), key.lower()
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip().lower()
        elif current == section and "=" in line and not line.startswith(";"):
            name, value = line.split("=", 1)
            if name.strip().lower() == key:
                return value.strip()
    return None


def set_values(text: str, section: str, values: dict) -> str:
    """Return ini text with these keys set in the section; other content untouched."""
    newline = "\r\n" if "\r\n" in text or not text else "\n"
    pending = {k.lower(): (k, v) for k, v in values.items()}
    out, current, found = [], "", False

    def flush():
        for name, value in pending.values():
            out.append("%s=%s" % (name, value))
        pending.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            if current == section.lower():
                flush()
            current = line[1:-1].strip().lower()
            found = found or current == section.lower()
            out.append(raw)
            continue
        if current == section.lower() and "=" in line and not line.startswith(";"):
            name = line.split("=", 1)[0].strip()
            if name.lower() in pending:
                out.append("%s=%s" % (name, pending.pop(name.lower())[1]))
                continue
        out.append(raw)
    if current == section.lower():
        flush()
    if not found:
        if out and out[-1].strip():
            out.append("")
        out.append("[%s]" % section)
        flush()
    return newline.join(out) + newline


def _write(path: str, text: str) -> None:
    if os.path.exists(path):
        backup = path + BACKUP_SUFFIX
        if not os.path.exists(backup):
            shutil.copyfile(path, backup)
        if not os.access(path, os.W_OK):
            raise OSError("%s is read-only. Remove the write protection and try again." % os.path.basename(path))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="latin-1", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def _int(value: Optional[str], default: int) -> int:
    try:
        return int(float(value)) if value is not None else default
    except ValueError:
        return default


def files(ini_dir: str) -> tuple:
    """(prefs path, custom path); case of existing files is respected."""
    def pick(name):
        low = name.lower()
        try:
            for entry in os.listdir(ini_dir):
                if entry.lower() == low:
                    return os.path.join(ini_dir, entry)
        except OSError:
            pass
        return os.path.join(ini_dir, name)
    return pick("Fallout4Prefs.ini"), pick("Fallout4Custom.ini")


def load(ini_dir: str) -> DisplaySettings:
    prefs_path, custom_path = files(ini_dir)
    prefs, custom = read_text(prefs_path), read_text(custom_path)
    s = DisplaySettings()
    s.width = _int(get_value(prefs, "Display", "iSize W"), s.width)
    s.height = _int(get_value(prefs, "Display", "iSize H"), s.height)
    full = _int(get_value(prefs, "Display", "bFull Screen"), 0)
    borderless = _int(get_value(prefs, "Display", "bBorderless"), 1)
    s.mode = "fullscreen" if full else ("borderless" if borderless else "window")
    s.vsync = _int(get_value(prefs, "Display", "iPresentInterval"), 1) != 0

    def fov(key):
        for text in (custom, prefs):                      # the custom file wins, as it does in the game
            value = get_value(text, "Display", key)
            if value is not None:
                return _int(value, DEFAULT_FOV)
        return DEFAULT_FOV
    s.fov_world, s.fov_first = fov("fDefaultWorldFOV"), fov("fDefault1stPersonFOV")
    return s


def save(ini_dir: str, s: DisplaySettings) -> list:
    """Write the settings. Returns the names of the files that changed.
    Raises OSError when Fallout4Prefs.ini does not exist yet or a file cannot be written."""
    prefs_path, custom_path = files(ini_dir)
    if not os.path.exists(prefs_path):
        raise OSError("Fallout4Prefs.ini does not exist yet. Start the game's launcher once so it creates it.")
    changed = []
    prefs = read_text(prefs_path)
    new_prefs = set_values(prefs, "Display", {
        "iSize W": str(int(s.width)), "iSize H": str(int(s.height)),
        "bFull Screen": "1" if s.mode == "fullscreen" else "0",
        "bBorderless": "1" if s.mode == "borderless" else "0",
        "iPresentInterval": "1" if s.vsync else "0",
        "fDefaultWorldFOV": str(int(s.fov_world)), "fDefault1stPersonFOV": str(int(s.fov_first))})
    if new_prefs != prefs:
        _write(prefs_path, new_prefs)
        changed.append(os.path.basename(prefs_path))
    custom = read_text(custom_path)
    new_custom = set_values(custom, "Display", {"fDefaultWorldFOV": str(int(s.fov_world)),
                                                "fDefault1stPersonFOV": str(int(s.fov_first))})
    if new_custom != custom:
        _write(custom_path, new_custom)
        changed.append(os.path.basename(custom_path))
    return changed
