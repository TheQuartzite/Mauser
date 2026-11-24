"""Mod lists: a small file that records which mods you have, in what order, so the same
setup can be rebuilt on another computer or after a reinstall.

A list holds no mod files, only where each one came from. Loading a list downloads the
missing mods from the same place (which needs that site's sign-in on the new computer),
then puts mods and plugins in the recorded order.
"""
from __future__ import annotations

import datetime
import json

FORMAT = "mauser-list"
VERSION = 1
EXTENSION = ".mauserlist"


class ListError(Exception):
    pass


def export(name: str, app: str, game_version: str, mods: list, plugins: list, skip_sources: tuple = ()) -> dict:
    """mods: core.Mod objects in order; plugins: (name, enabled) in load order."""
    out = []
    for m in mods:
        if m.source in skip_sources:        # things the app generated itself
            continue
        out.append({"name": m.name, "enabled": bool(m.enabled), "origin": m.origin, "file_id": m.file_id,
                    "file_name": m.file_name, "version": m.version, "page": m.page, "risk": m.risk,
                    "target": m.target, "archive": m.source})
    return {"format": FORMAT, "version": VERSION, "name": name.strip() or "My mod list", "app": app,
            "created": datetime.datetime.now().isoformat(timespec="seconds"), "game": game_version,
            "mods": out, "plugins": [{"name": n, "enabled": bool(e)} for n, e in plugins]}


def dumps(data: dict) -> str:
    return json.dumps(data, indent=1, ensure_ascii=False) + "\n"


def loads(text: str) -> dict:
    """Parse and check a list file. Everything in it is treated as untrusted text."""
    if len(text) > 8_000_000:
        raise ListError("That file is too large to be a mod list.")
    try:
        data = json.loads(text)
    except ValueError:
        raise ListError("That file is not a mod list (it could not be read).")
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ListError("That file is not a %s mod list." % "Mauser")
    if not isinstance(data.get("version"), int) or data["version"] > VERSION:
        raise ListError("That list was made by a newer version of the app. Update the app to load it.")
    mods = []
    for m in data.get("mods") if isinstance(data.get("mods"), list) else []:
        if not isinstance(m, dict) or not str(m.get("name") or "").strip():
            continue
        origin = str(m.get("origin") or "")
        if origin and not _ORIGIN_OK(origin):
            origin = ""
        mods.append({"name": str(m["name"])[:200], "enabled": bool(m.get("enabled", True)), "origin": origin,
                     "file_id": str(m.get("file_id") or "")[:40], "file_name": str(m.get("file_name") or "")[:200],
                     "version": str(m.get("version") or "")[:60], "page": str(m.get("page") or "")[:400],
                     "risk": m.get("risk") if m.get("risk") in ("high", "low") else "",
                     "target": "root" if m.get("target") == "root" else "data", "archive": str(m.get("archive") or "")[:200]})
    plugins = []
    for p in data.get("plugins") if isinstance(data.get("plugins"), list) else []:
        if isinstance(p, dict) and str(p.get("name") or "").strip():
            plugins.append({"name": str(p["name"])[:200], "enabled": bool(p.get("enabled", True))})
    return {"name": str(data.get("name") or "Mod list")[:120], "created": str(data.get("created") or "")[:40],
            "game": str(data.get("game") or "")[:60], "app": str(data.get("app") or "")[:60], "mods": mods, "plugins": plugins}


def _ORIGIN_OK(origin: str) -> bool:
    provider, _, ident = origin.partition(":")
    return provider in ("nexus", "github") and 0 < len(ident) <= 120 and all(c.isalnum() or c in "-_./" for c in ident)


def match(entry: dict, installed: list):
    """The installed mod a list entry refers to, or None. Same download first, then the same name."""
    if entry["origin"]:
        same = [m for m in installed if m.origin == entry["origin"]]
        for m in same:
            if entry["file_id"] and m.file_id == entry["file_id"]:
                return m
        for m in same:
            if entry["file_name"] and m.file_name == entry["file_name"]:
                return m
        for m in same:
            if m.name.strip().lower() == entry["name"].strip().lower():
                return m
        if len(same) == 1 and not entry["file_id"] and not entry["file_name"]:
            return same[0]
    for m in installed:
        if m.name.strip().lower() == entry["name"].strip().lower():
            return m
    return None
