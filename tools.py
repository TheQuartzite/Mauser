"""Running Windows modding tools (xEdit, BodySlide and the like) inside the game's Proton prefix,
and collecting the files they leave in the game folder into one "Overwrite" mod.

The tools see the game exactly as the game does, mods included, because mods are placed in the
game folder as real files.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Optional

import steam

OVERWRITE_NAME = "Overwrite (made by tools)"
OVERWRITE_SOURCE = "(collected from tools)"


class ToolError(Exception):
    pass


def find_proton(paths: steam.GamePaths) -> Optional[str]:
    """The 'proton' launcher script of the Proton version the game last ran with."""
    prefix = paths.effective_prefix()
    if not prefix:
        return None
    compat = os.path.dirname(prefix)
    candidates = []
    try:
        with open(os.path.join(compat, "config_info"), "r", encoding="utf-8", errors="replace") as f:
            for line in f.read().splitlines():
                m = re.match(r"(/.+?)/(?:dist|files)/", line.strip())
                if m:
                    candidates.append(m.group(1))
    except OSError:
        pass
    for folder in candidates:
        script = os.path.join(folder, "proton")
        if os.path.isfile(script):
            return script
    # fall back to the newest Proton in any Steam library
    found = []
    for lib in steam.steam_libraries():
        common = os.path.join(lib, "steamapps", "common")
        try:
            for name in os.listdir(common):
                script = os.path.join(common, name, "proton")
                if name.lower().startswith("proton") and os.path.isfile(script):
                    found.append(script)
        except OSError:
            pass
    return sorted(found)[-1] if found else None


def steam_root(paths: steam.GamePaths) -> str:
    for lib in steam.steam_libraries():
        if os.path.isdir(os.path.join(lib, "steamapps")) and (os.path.isdir(os.path.join(lib, "ubuntu12_32"))
                                                             or os.path.exists(os.path.join(lib, "steam.sh"))):
            return lib
    for guess in ("~/.steam/steam", "~/.local/share/Steam"):
        p = os.path.expanduser(guess)
        if os.path.isdir(p):
            return os.path.realpath(p)
    return ""


def command(paths: steam.GamePaths, exe: str, args: str = "") -> tuple:
    """(argv, environment additions) to run a Windows program in the game's prefix."""
    if not os.path.isfile(exe):
        raise ToolError("The program was not found: %s" % exe)
    prefix = paths.effective_prefix()
    if not prefix:
        raise ToolError("The game's Proton prefix was not found. Start Fallout 4 once from Steam first.")
    proton = find_proton(paths)
    extra = [a for a in re.findall(r'"[^"]*"|\S+', args or "")]
    extra = [a[1:-1] if a.startswith('"') and a.endswith('"') else a for a in extra]
    if proton:
        return [proton, "run", exe] + extra, {"STEAM_COMPAT_DATA_PATH": os.path.dirname(prefix),
                                               "STEAM_COMPAT_CLIENT_INSTALL_PATH": steam_root(paths),
                                               "SteamAppId": steam.APP_ID, "SteamGameId": steam.APP_ID}
    launcher = shutil.which("protontricks-launch")
    if launcher:
        return [launcher, "--appid", steam.APP_ID, exe] + extra, {}
    raise ToolError("No Proton was found to run the program with. Start Fallout 4 once from Steam, or install Protontricks.")


def launch(paths: steam.GamePaths, exe: str, args: str = "", log_path: str = "") -> subprocess.Popen:
    argv, extra = command(paths, exe, args)
    env = dict(os.environ)
    env.update(extra)
    out = open(log_path, "ab") if log_path else subprocess.DEVNULL
    try:
        return subprocess.Popen(argv, cwd=os.path.dirname(exe), env=env, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        raise ToolError("Could not start it: %s" % e)
    finally:
        if log_path:
            out.close()


def snapshot(folder: str) -> dict:
    """relative path -> (size, modified time) for every file under folder."""
    out = {}
    for root, dirs, files in os.walk(folder):
        for name in files:
            p = os.path.join(root, name)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            out[os.path.relpath(p, folder)] = (st.st_size, st.st_mtime_ns)
    return out


def changes(before: dict, after: dict) -> tuple:
    """(new files, changed files), as relative paths."""
    new = sorted(p for p in after if p not in before)
    changed = sorted(p for p in after if p in before and after[p] != before[p])
    return new, changed
