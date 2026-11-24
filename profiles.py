"""Profiles: separate setups that share one mod library.

A profile remembers which mods are on, their order, the load order, and its own copies of the
game's three settings files. Switching saves the current setup into the profile being left and
brings back the one being entered. Mods themselves are stored once; a mod installed while one
profile is active is simply off in the others until switched on there.
"""
from __future__ import annotations

import os
import re
import shutil

INI_NAMES = ("Fallout4.ini", "Fallout4Prefs.ini", "Fallout4Custom.ini")
DEFAULT = "Default"


def names(settings: dict) -> list:
    store = settings.get("profiles")
    found = sorted(store, key=str.lower) if isinstance(store, dict) else []
    cur = current(settings)
    return found if cur in found else sorted(found + [cur], key=str.lower)


def current(settings: dict) -> str:
    return str(settings.get("profile") or DEFAULT)


def _folder(base_dir: str, name: str) -> str:
    return os.path.join(base_dir, "profiles", re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "profile")


def valid_name(name: str, settings: dict) -> str:
    """'' when the name can be used, else what is wrong with it."""
    name = name.strip()
    if not name:
        return "Give the profile a name."
    if len(name) > 40:
        return "That name is too long."
    if name.lower() in [n.lower() for n in names(settings)]:
        return "There is already a profile with that name."
    return ""


def capture(mgr, order, ini_dir, name: str) -> None:
    """Store the present setup under `name`."""
    store = mgr.settings.setdefault("profiles", {})
    store[name] = {"mods": [{"id": m.id, "enabled": bool(m.enabled)} for m in mgr.mods],
                   "plugins": [{"name": e.name, "enabled": bool(e.enabled)} for e in order.entries] if order is not None else
                   (store.get(name) or {}).get("plugins", [])}
    if ini_dir:
        folder = _folder(mgr.base_dir, name)
        os.makedirs(folder, exist_ok=True)
        present = {e.lower(): e for e in os.listdir(ini_dir)}
        for ini in INI_NAMES:
            src = present.get(ini.lower())
            dst = os.path.join(folder, ini)
            if src:
                shutil.copyfile(os.path.join(ini_dir, src), dst)
            elif os.path.exists(dst):
                os.unlink(dst)


def restore_mods(mgr, name: str) -> None:
    """Put mods in the profile's order and switch them as it has them. Unknown mods go last, switched off."""
    saved = (mgr.settings.get("profiles") or {}).get(name) or {}
    by_id = {m.id: m for m in mgr.mods}
    placed, seen = [], set()
    for entry in saved.get("mods") or []:
        mod = by_id.get(str(entry.get("id")))
        if mod is not None and mod.id not in seen:
            mod.enabled = bool(entry.get("enabled"))
            placed.append(mod)
            seen.add(mod.id)
    rest = [m for m in mgr.mods if m.id not in seen]
    if "mods" in saved:
        for m in rest:
            m.enabled = False
    mgr.mods[:] = placed + rest


def restore_plugins(mgr, order, name: str) -> None:
    saved = (mgr.settings.get("profiles") or {}).get(name) or {}
    if order is None or "plugins" not in saved:
        return
    by_name = {e.name.lower(): e for e in order.entries}
    placed, used = [], set()
    for p in saved["plugins"]:
        e = by_name.get(str(p.get("name", "")).lower())
        if e is not None and e.name.lower() not in used:
            e.enabled = bool(p.get("enabled"))
            placed.append(e)
            used.add(e.name.lower())
    order.entries[:] = placed + [e for e in order.entries if e.name.lower() not in used]


def restore_inis(mgr, ini_dir, name: str) -> list:
    """Copy the profile's settings files back into the game's folder. Returns the names restored."""
    folder = _folder(mgr.base_dir, name)
    done = []
    if not ini_dir or not os.path.isdir(folder):
        return done
    present = {e.lower(): e for e in os.listdir(ini_dir)}
    for ini in INI_NAMES:
        src = os.path.join(folder, ini)
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(ini_dir, present.get(ini.lower(), ini)))
            done.append(ini)
    return done


def delete(mgr, name: str) -> None:
    (mgr.settings.get("profiles") or {}).pop(name, None)
    shutil.rmtree(_folder(mgr.base_dir, name), ignore_errors=True)


def rename(mgr, old: str, new: str) -> None:
    store = mgr.settings.setdefault("profiles", {})
    if old in store:
        store[new] = store.pop(old)
    src, dst = _folder(mgr.base_dir, old), _folder(mgr.base_dir, new)
    if os.path.isdir(src) and not os.path.exists(dst):
        os.rename(src, dst)
    if current(mgr.settings) == old:
        mgr.settings["profile"] = new
