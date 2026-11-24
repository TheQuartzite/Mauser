"""Reading the plugin list out of a Fallout 4 save (.fos), to say which plugins a save expects.

A save stores the names of the plugins that were active when it was made: a count, then each
name as a length and text, starting with Fallout4.esm; then a second list for light plugins.
That block is found by looking for its first entry, which every save has.
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from typing import Optional

MAGIC = b"FO4_SAVEGAME"
_FIRST = b"\x0c\x00Fallout4.esm"
_EXTS = (".esm", ".esp", ".esl")


class SaveError(Exception):
    pass


@dataclass
class SaveInfo:
    path: str
    player: str = ""
    level: int = 0
    location: str = ""
    game_version: str = ""
    play_time: str = ""           # as the game writes it, e.g. "1d.4h.23m.1 days.4 hours.23 minutes"
    hours: float = -1.0           # the same as a number of hours; -1 when unknown
    plugins: list = field(default_factory=list)       # full plugins, in the save's order
    light: list = field(default_factory=list)


def _wstring(data: bytes, at: int) -> tuple:
    if at + 2 > len(data):
        raise SaveError("cut short")
    (n,) = struct.unpack_from("<H", data, at)
    if at + 2 + n > len(data):
        raise SaveError("cut short")
    return data[at + 2:at + 2 + n].decode("utf-8", "replace"), at + 2 + n


def _plugin_name(data: bytes, at: int) -> tuple:
    name, end = _wstring(data, at)
    if not (0 < len(name) <= 260) or not name.lower().endswith(_EXTS) or any(ord(c) < 32 for c in name):
        raise SaveError("not a plugin name")
    return name, end


def read(path: str) -> SaveInfo:
    try:
        with open(path, "rb") as f:
            data = f.read(64 * 1024 * 1024)
    except OSError as e:
        raise SaveError(str(e))
    if not data.startswith(MAGIC):
        raise SaveError("This is not a Fallout 4 save.")
    info = SaveInfo(path)
    try:                                   # the readable header: nice to have, not needed
        at = len(MAGIC) + 4 + 8            # header size, save version, save number
        info.player, at = _wstring(data, at)
        (info.level,) = struct.unpack_from("<I", data, at)
        info.location, at = _wstring(data, at + 4)
        if not (0 <= info.level < 100000) or len(info.player) > 200:
            info.player, info.level, info.location = "", 0, ""
        else:
            try:
                import re
                text, _ = _wstring(data, at)
                m = re.match(r"(\d+)d\.(\d+)h\.(\d+)m", text)
                if m and len(text) < 80:
                    info.play_time = text
                    info.hours = int(m.group(1)) * 24 + int(m.group(2)) + int(m.group(3)) / 60.0
            except (SaveError, struct.error):
                pass
    except (SaveError, struct.error):
        info.player, info.level, info.location = "", 0, ""
    at = data.find(_FIRST)
    while at > 0:
        try:
            count = data[at - 1]
            pos, names = at, []
            for _ in range(count):
                name, pos = _plugin_name(data, pos)
                names.append(name)
            if not names:
                raise SaveError("empty")
            info.plugins = names
            try:                            # light plugins follow in newer saves
                (light_count,) = struct.unpack_from("<H", data, pos)
                lights, p = [], pos + 2
                for _ in range(light_count):
                    name, p = _plugin_name(data, p)
                    lights.append(name)
                info.light = lights
            except (SaveError, struct.error):
                info.light = []
            # the game version sits just before the plugin block: text, then a 4-byte size, then the count
            for back in range(8, 40):
                start = at - 1 - 4 - back
                if start > 0 and struct.unpack_from("<H", data, start)[0] == back - 2:
                    text = data[start + 2:start + back]
                    if text.count(b".") >= 2 and all(48 <= c <= 57 or c == 46 for c in text):
                        info.game_version = text.decode("ascii")
                        break
            return info
        except (SaveError, IndexError):
            at = data.find(_FIRST, at + 1)
    raise SaveError("The plugin list could not be found in this save.")


def list_saves(ini_dir: Optional[str]) -> list:
    """(path, modified time) of the saves, newest first."""
    if not ini_dir:
        return []
    folder = None
    try:
        for entry in os.listdir(ini_dir):
            if entry.lower() == "saves":
                folder = os.path.join(ini_dir, entry)
    except OSError:
        return []
    if not folder:
        return []
    out = []
    try:
        for name in os.listdir(folder):
            if name.lower().endswith(".fos"):
                p = os.path.join(folder, name)
                out.append((p, os.path.getmtime(p)))
    except OSError:
        pass
    return sorted(out, key=lambda x: x[1], reverse=True)


def compare(info: SaveInfo, present: dict, added: Optional[dict] = None) -> dict:
    """present: lower-case plugin name -> enabled (bool) for what is installed now.
    added: lower-case name -> name as written, for the plugins that came from mods (the only ones worth listing as extra).
    Returns {"missing": not installed, "disabled": installed but off, "ok": n, "extra": on now, not in the save}."""
    wanted = info.plugins + info.light
    low = {n.lower() for n in wanted}
    return {"missing": [n for n in wanted if n.lower() not in present],
            "disabled": [n for n in wanted if n.lower() in present and not present[n.lower()]],
            "ok": sum(1 for n in wanted if present.get(n.lower())),
            "extra": sorted((added or {}).get(n, n) for n, on in present.items()
                            if on and n not in low and (added is None or n in added))}
