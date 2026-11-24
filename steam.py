"""Locating Fallout 4, its Proton prefix and the files the game reads from it.

Everything here is plain path work on the Linux filesystem: no Wine or
protontricks is needed to find or edit these files.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Optional

APP_ID = "377160"
GAME_FOLDER = "Fallout 4"


def child(folder: str, name: str) -> Optional[str]:
    """Case-insensitive lookup of a directory entry (Windows names vary in case)."""
    direct = os.path.join(folder, name)
    if os.path.lexists(direct):
        return direct
    try:
        low = name.lower()
        for entry in os.listdir(folder):
            if entry.lower() == low:
                return os.path.join(folder, entry)
    except OSError:
        pass
    return None


def descend(folder: Optional[str], *names: str) -> Optional[str]:
    cur = folder
    for n in names:
        if cur is None:
            return None
        cur = child(cur, n)
    return cur


def steam_libraries() -> list:
    home = os.path.expanduser("~")
    roots = [
        os.path.join(home, ".steam", "steam"),
        os.path.join(home, ".steam", "root"),
        os.path.join(home, ".local", "share", "Steam"),
        os.path.join(home, ".var", "app", "com.valvesoftware.Steam", ".local", "share", "Steam"),
        os.path.join(home, ".var", "app", "com.valvesoftware.Steam", "data", "Steam"),
    ]
    libs: list = []
    seen = set()

    def add(path: str) -> None:
        real = os.path.realpath(path)
        if os.path.isdir(path) and real not in seen:
            seen.add(real)
            libs.append(path)

    for root in roots:
        if not os.path.isdir(root):
            continue
        add(root)
        vdf = descend(root, "steamapps", "libraryfolders.vdf")
        if vdf:
            try:
                with open(vdf, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        parts = line.strip().split('"')
                        if len(parts) >= 4 and parts[1] == "path":
                            add(parts[3].replace("\\\\", "\\"))
            except OSError:
                pass
    return libs


def find_game_dirs() -> list:
    found = []
    for lib in steam_libraries():
        game = descend(lib, "steamapps", "common", GAME_FOLDER)
        if game and looks_like_game(game):
            found.append(game)
    return found


def looks_like_game(folder: str) -> bool:
    return bool(folder) and os.path.isdir(folder) and (
        child(folder, "Fallout4.exe") is not None or descend(folder, "Data", "Fallout4.esm") is not None)


def normalize_game_dir(folder: str) -> str:
    """Accept the game folder or its Data folder."""
    folder = os.path.abspath(folder)
    if os.path.basename(folder).lower() == "data" and child(folder, "Fallout4.esm"):
        return os.path.dirname(folder)
    return folder


@dataclass
class GamePaths:
    game_dir: str
    prefix: Optional[str] = None           # .../compatdata/377160/pfx

    @property
    def data_dir(self) -> str:
        return child(self.game_dir, "Data") or os.path.join(self.game_dir, "Data")

    def guess_prefix(self) -> Optional[str]:
        common = os.path.dirname(os.path.abspath(self.game_dir))
        steamapps = os.path.dirname(common)
        candidates = []
        if os.path.basename(common).lower() == "common":
            candidates.append(descend(steamapps, "compatdata", APP_ID, "pfx"))
        for lib in steam_libraries():       # the prefix can live in a different library
            candidates.append(descend(lib, "steamapps", "compatdata", APP_ID, "pfx"))
        for c in candidates:
            if c and os.path.isdir(c):
                return c
        return None

    def effective_prefix(self) -> Optional[str]:
        if self.prefix and os.path.isdir(self.prefix):
            inner = child(self.prefix, "pfx")     # accept compatdata/377160 as well
            if inner and os.path.isdir(inner) and not child(self.prefix, "drive_c"):
                return inner
            return self.prefix
        return self.guess_prefix()

    def _user_dir(self) -> Optional[str]:
        pfx = self.effective_prefix()
        return descend(pfx, "drive_c", "users", "steamuser") if pfx else None

    def appdata_dir(self, create: bool = False) -> Optional[str]:
        """Folder holding Plugins.txt (AppData/Local/Fallout4)."""
        user = self._user_dir()
        if not user:
            return None
        found = descend(user, "AppData", "Local", "Fallout4")
        if found is None and create:
            local = descend(user, "AppData", "Local")
            if local:
                found = os.path.join(local, "Fallout4")
                os.makedirs(found, exist_ok=True)
        return found

    def plugins_txt(self, create_dir: bool = False) -> Optional[str]:
        folder = self.appdata_dir(create_dir)
        if not folder:
            return None
        return child(folder, "Plugins.txt") or os.path.join(folder, "Plugins.txt")

    def ini_dir(self) -> Optional[str]:
        """Folder holding Fallout4.ini and Fallout4Custom.ini (Documents/My Games/Fallout4)."""
        user = self._user_dir()
        if not user:
            return None
        for docs in ("Documents", "My Documents"):
            found = descend(user, docs, "My Games", "Fallout4")
            if found and os.path.isdir(found):
                return found
        return None

    def custom_ini(self) -> Optional[str]:
        folder = self.ini_dir()
        if not folder:
            return None
        return child(folder, "Fallout4Custom.ini") or os.path.join(folder, "Fallout4Custom.ini")

    def has_f4se(self) -> bool:
        return child(self.game_dir, "f4se_loader.exe") is not None


LOOSE_FILE_KEYS = {"binvalidateolderfiles": "bInvalidateOlderFiles=1",
                   "sresourcedatadirsfinal": "sResourceDataDirsFinal="}


def loose_files_enabled(text: str) -> bool:
    section = ""
    seen = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
        elif section == "archive" and "=" in line and not line.startswith(";"):
            k, v = line.split("=", 1)
            seen[k.strip().lower()] = v.strip()
    return seen.get("binvalidateolderfiles") == "1" and seen.get("sresourcedatadirsfinal") == ""


def with_loose_files(text: str) -> str:
    """Return ini text with the two [Archive] settings that make the game load
    loose mod files. Other content is kept as it is."""
    newline = "\r\n" if "\r\n" in text or not text else "\n"
    lines = text.splitlines()
    out = []
    section = ""
    pending = dict(LOOSE_FILE_KEYS)
    found_section = False

    def flush():
        for value in pending.values():
            out.append(value)
        pending.clear()

    for raw in lines:
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            if section == "archive":
                flush()
            section = line[1:-1].strip().lower()
            found_section = found_section or section == "archive"
            out.append(raw)
            continue
        if section == "archive" and "=" in line and not line.startswith(";"):
            key = line.split("=", 1)[0].strip().lower()
            if key in LOOSE_FILE_KEYS:
                out.append(LOOSE_FILE_KEYS[key])
                pending.pop(key, None)
                continue
        out.append(raw)
    if section == "archive":
        flush()
    if not found_section:
        if out and out[-1].strip():
            out.append("")
        out.append("[Archive]")
        flush()
    return newline.join(out) + newline


def enable_loose_files(ini_path: str) -> bool:
    """Make sure Fallout4Custom.ini enables loose files. Returns True if it changed."""
    text = ""
    if os.path.exists(ini_path):
        with open(ini_path, "r", encoding="utf-8", errors="replace", newline="") as f:
            text = f.read()
        if loose_files_enabled(text):
            return False
        backup = ini_path + ".modifikationsmann-backup"
        if not os.path.exists(backup):
            shutil.copyfile(ini_path, backup)
    tmp = ini_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(with_loose_files(text))
    os.replace(tmp, ini_path)
    return True
