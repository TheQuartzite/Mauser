"""Plugins (.esm / .esp / .esl) and the game's Plugins.txt load-order file."""
from __future__ import annotations

import os
import shutil
import struct
from dataclasses import dataclass, field
from typing import Optional

PLUGIN_EXTS = (".esm", ".esp", ".esl")
OFFICIAL = ["Fallout4.esm", "DLCRobot.esm", "DLCworkshop01.esm", "DLCCoast.esm", "DLCworkshop02.esm",
            "DLCworkshop03.esm", "DLCNukaWorld.esm", "DLCUltraHighResolution.esm"]
FLAG_MASTER = 0x1
FLAG_LIGHT = 0x200


@dataclass
class PluginInfo:
    masters: list = field(default_factory=list)
    master_flag: bool = False
    light_flag: bool = False
    readable: bool = True


def read_plugin_info(path: str) -> PluginInfo:
    """Read the header record of a plugin: its flags and the plugins it depends on."""
    info = PluginInfo()
    try:
        with open(path, "rb") as f:
            head = f.read(24)
            if len(head) < 24 or head[:4] != b"TES4":
                info.readable = False
                return info
            size, flags = struct.unpack_from("<II", head, 4)
            data = f.read(min(size, 1 << 20))
    except OSError:
        info.readable = False
        return info
    info.master_flag = bool(flags & FLAG_MASTER)
    info.light_flag = bool(flags & FLAG_LIGHT)
    pos = 0
    while pos + 6 <= len(data):
        sig = data[pos:pos + 4]
        (n,) = struct.unpack_from("<H", data, pos + 4)
        pos += 6
        if sig == b"MAST":
            raw = data[pos:pos + n].split(b"\0")[0]
            try:
                info.masters.append(raw.decode("utf-8"))
            except UnicodeDecodeError:
                info.masters.append(raw.decode("cp1252", "replace"))
        pos += n
    return info


@dataclass
class PluginEntry:
    name: str
    enabled: bool = False
    info: PluginInfo = field(default_factory=PluginInfo)

    @property
    def is_master(self) -> bool:
        return self.info.master_flag or self.name.lower().endswith((".esm", ".esl"))

    @property
    def is_light(self) -> bool:
        return self.info.light_flag or self.name.lower().endswith(".esl")


def creation_club_plugins(game_dir: str) -> list:
    """Plugins the game loads by itself from Fallout4.ccc."""
    out = []
    try:
        for entry in os.listdir(game_dir):
            if entry.lower() == "fallout4.ccc":
                with open(os.path.join(game_dir, entry), "r", encoding="utf-8", errors="replace") as f:
                    out = [line.strip() for line in f if line.strip()]
    except OSError:
        pass
    return out


class LoadOrder:
    def __init__(self, data_dir: str, plugins_txt: Optional[str], game_dir: str = ""):
        self.data_dir = data_dir
        self.plugins_txt = plugins_txt
        self.implicit = OFFICIAL + creation_club_plugins(game_dir or os.path.dirname(data_dir))
        self._implicit_low = {n.lower() for n in self.implicit}
        self.entries: list = []
        self.header: list = []
        self.reload()

    def _installed(self) -> dict:
        found = {}
        try:
            for entry in sorted(os.listdir(self.data_dir), key=str.lower):
                if entry.lower().endswith(PLUGIN_EXTS) and os.path.isfile(os.path.join(self.data_dir, entry)):
                    found.setdefault(entry.lower(), entry)
        except OSError:
            pass
        return found

    def reload(self, enable_new: Optional[set] = None) -> None:
        """Read Plugins.txt and the Data folder. Plugins missing from the file are
        added at the end; those named in `enable_new` start enabled."""
        enable_new = {n.lower() for n in (enable_new or set())}
        installed = self._installed()
        listed: list = []
        self.header = []
        if self.plugins_txt and os.path.exists(self.plugins_txt):
            with open(self.plugins_txt, "r", encoding="utf-8", errors="replace") as f:
                for raw in f:
                    line = raw.strip()
                    if not line:
                        continue
                    if line.startswith("#"):
                        self.header.append(line)
                        continue
                    enabled = line.startswith("*")
                    listed.append((line.lstrip("*").strip(), enabled))
        previous = {e.name.lower(): e for e in self.entries}
        entries = []
        seen = set()
        for name, enabled in listed:
            low = name.lower()
            if low in seen or low in self._implicit_low or low not in installed:
                continue
            seen.add(low)
            entries.append(PluginEntry(installed[low], enabled or low in enable_new))
        for low, actual in installed.items():
            if low in seen or low in self._implicit_low:
                continue
            was = previous.get(low)
            entries.append(PluginEntry(actual, low in enable_new or bool(was and was.enabled)))
        for e in entries:
            e.info = read_plugin_info(os.path.join(self.data_dir, e.name))
        self.entries = entries

    def save(self) -> None:
        if not self.plugins_txt:
            raise OSError("Plugins.txt location is unknown (Proton prefix not found).")
        os.makedirs(os.path.dirname(self.plugins_txt), exist_ok=True)
        if os.path.exists(self.plugins_txt):
            backup = self.plugins_txt + ".modifikationsmann-backup"
            if not os.path.exists(backup):
                shutil.copyfile(self.plugins_txt, backup)
        header = self.header or ["# This file is used by Fallout4 to keep track of your downloaded content."]
        lines = header + [("*" if e.enabled else "") + e.name for e in self.entries]
        tmp = self.plugins_txt + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write("\r\n".join(lines) + "\r\n")
        os.replace(tmp, self.plugins_txt)

    def move(self, index: int, new_index: int) -> None:
        if 0 <= index < len(self.entries) and 0 <= new_index < len(self.entries):
            self.entries.insert(new_index, self.entries.pop(index))

    def sort(self) -> None:
        """Masters first, then every plugin after the plugins it depends on.
        Otherwise the current order is kept."""
        by_name = {e.name.lower(): e for e in self.entries}
        position = {e.name.lower(): i for i, e in enumerate(self.entries)}
        ordered: list = []
        placed = set()

        def place(entry: PluginEntry, trail: tuple) -> None:
            low = entry.name.lower()
            if low in placed or low in trail:
                return
            deps = [by_name[m.lower()] for m in entry.info.masters if m.lower() in by_name]
            for dep in sorted(deps, key=lambda d: position[d.name.lower()]):
                if dep.is_master or not entry.is_master:
                    place(dep, trail + (low,))
            placed.add(low)
            ordered.append(entry)

        for group in (True, False):
            for e in self.entries:
                if e.is_master == group:
                    place(e, ())
        self.entries = ordered

    def problems(self) -> dict:
        """Plugin name -> list of issue descriptions for enabled plugins."""
        active = set(self._implicit_low)
        order = {}
        # The game loads masters before other plugins whatever the file says.
        effective = [e for e in self.entries if e.is_master] + [e for e in self.entries if not e.is_master]
        for i, e in enumerate(effective):
            order[e.name.lower()] = i
            if e.enabled:
                active.add(e.name.lower())
        issues: dict = {}
        for e in self.entries:
            if not e.enabled:
                continue
            found = []
            if not e.info.readable:
                found.append("file could not be read as a plugin")
            for m in e.info.masters:
                low = m.lower()
                if low not in active:
                    found.append("needs %s, which is missing or disabled" % m)
                elif low in order and order[low] > order[e.name.lower()]:
                    found.append("needs %s, which loads after it" % m)
            if found:
                issues[e.name] = found
        return issues
