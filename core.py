"""Mod library and deployment.

Installed mods are kept unpacked in a staging folder, one folder per mod.
"Deploying" links the files of every enabled mod into the game folder, later
mods in the list winning conflicts. A manifest records every file put there,
so deployment can always be undone exactly: only files this tool placed are
ever removed, and any game file it had to replace is kept and put back.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Optional

from steam import GamePaths, normalize_game_dir

APP_DIR_NAME = "mauser"
OLD_APP_DIR_NAME = "der-modifikationsmann"      # the app's earlier name
PLUGIN_EXTS = (".esm", ".esp", ".esl")


def default_base_dir() -> str:
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    new, old = os.path.join(data_home, APP_DIR_NAME), os.path.join(data_home, OLD_APP_DIR_NAME)
    # Mods placed in the game by symlink point into the old folder, so an existing library stays where it is.
    return old if os.path.isdir(old) and not os.path.isdir(new) else new


@dataclass
class Mod:
    id: str
    name: str
    enabled: bool = True
    target: str = "data"          # "data": files go into Data; "root": into the game folder
    source: str = ""              # archive it came from
    installed: str = ""
    files: int = 0
    origin: str = ""              # where it was downloaded from, e.g. "nexus:4598"
    file_name: str = ""           # the name of the file on the mod's page, e.g. "Trees"
    file_note: str = ""           # what the author wrote under that file
    version: str = ""
    page: str = ""                # address of the mod's page
    file_id: str = ""             # the site's id of the downloaded file
    risk: str = ""                # "high" or "low" when set by hand; "" = judged from the files
    category: str = ""            # the site's category for the mod, e.g. "Weapons"
    tags: list = field(default_factory=list)      # the user's own labels
    size: int = 0                 # bytes on disk; 0 = not measured yet
    pinned: bool = False          # kept at the bottom of the list (wins conflicts) when mods are added

    def to_json(self) -> dict:
        return dict(self.__dict__)

    @staticmethod
    def from_json(d: dict) -> "Mod":
        return Mod(id=str(d["id"]), name=str(d.get("name", d["id"])), enabled=bool(d.get("enabled", True)),
                   target="root" if d.get("target") == "root" else "data", source=str(d.get("source", "")),
                   installed=str(d.get("installed", "")), files=int(d.get("files", 0)),
                   origin=str(d.get("origin", "")), file_name=str(d.get("file_name", "")),
                   file_note=str(d.get("file_note", "")), version=str(d.get("version", "")), page=str(d.get("page", "")),
                   file_id=str(d.get("file_id", "")), risk=d.get("risk") if d.get("risk") in ("high", "low") else "",
                   pinned=bool(d.get("pinned", False)), category=str(d.get("category", "")),
                   tags=[str(t)[:40] for t in d.get("tags", []) if str(t).strip()][:12] if isinstance(d.get("tags"), list) else [],
                   size=int(d.get("size", 0) or 0))


@dataclass
class DeployReport:
    linked: int = 0
    kept: int = 0
    removed: int = 0
    backed_up: int = 0
    restored: int = 0
    methods: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)

    def summary(self) -> str:
        parts = ["%d files in place" % (self.linked + self.kept)]
        if self.linked:
            parts.append("%d added" % self.linked)
        if self.removed:
            parts.append("%d removed" % self.removed)
        if self.backed_up:
            parts.append("%d game files set aside" % self.backed_up)
        if self.restored:
            parts.append("%d game files put back" % self.restored)
        if self.errors:
            parts.append("%d problems" % len(self.errors))
        return ", ".join(parts)


class Manager:
    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or default_base_dir()
        self.mods_dir = os.path.join(self.base_dir, "mods")
        self.backup_dir = os.path.join(self.base_dir, "backups")
        self.state_path = os.path.join(self.base_dir, "state.json")
        self.manifest_path = os.path.join(self.base_dir, "deployed.json")
        os.makedirs(self.mods_dir, exist_ok=True)
        self.mods: list = []
        self.game_dir = ""
        self.prefix = ""
        self.link_mode = "auto"       # "auto" (hard link, then symlink, then copy), "symlink" or "copy"
        self.settings: dict = {}      # other saved options, e.g. the Nexus API key
        self.downloads_dir = os.path.join(self.base_dir, "downloads")
        self.cache_dir = os.path.join(self.base_dir, "cache")
        self._load()

    # -- state --------------------------------------------------------------

    def _load(self) -> None:
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(doc, dict):
            return
        self.game_dir = str(doc.get("game_dir", ""))
        self.prefix = str(doc.get("prefix", ""))
        mode = doc.get("link_mode", "auto")
        self.link_mode = mode if mode in ("auto", "symlink", "copy") else "auto"
        if isinstance(doc.get("settings"), dict):
            self.settings = doc["settings"]
        for m in doc.get("mods", []):
            try:
                mod = Mod.from_json(m)
            except (KeyError, TypeError, ValueError):
                continue
            if os.path.isdir(self.mod_dir(mod)):
                self.mods.append(mod)

    def save(self) -> None:
        doc = {"game_dir": self.game_dir, "prefix": self.prefix, "link_mode": self.link_mode,
               "settings": self.settings, "mods": [m.to_json() for m in self.mods]}
        self._write_json(self.state_path, doc, private=True)

    @staticmethod
    def _write_json(path: str, doc, private: bool = False, compact: bool = False) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        # private files (they hold the API key) are readable by the owner only
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if private else 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=None if compact else 1, separators=(",", ":") if compact else None)
        os.replace(tmp, path)

    def _manifest(self) -> dict:
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as f:
                doc = json.load(f)
            if isinstance(doc, dict) and isinstance(doc.get("files"), dict):
                doc.setdefault("dirs", [])
                doc.setdefault("game_dir", "")
                return doc
        except (OSError, ValueError):
            pass
        return {"game_dir": "", "files": {}, "dirs": []}

    @property
    def paths(self) -> Optional[GamePaths]:
        if not self.game_dir:
            return None
        return GamePaths(self.game_dir, self.prefix or None)

    def set_game(self, game_dir: str, prefix: str = "") -> DeployReport:
        """Point the manager at a game folder. Files deployed to a previous
        folder are removed from it first."""
        game_dir = normalize_game_dir(game_dir)
        report = DeployReport()
        old = self._manifest()
        if old["files"] and old["game_dir"] and os.path.realpath(old["game_dir"]) != os.path.realpath(game_dir):
            report = self.purge()
        self.game_dir = game_dir
        self.prefix = prefix
        self.save()
        return report

    # -- library ------------------------------------------------------------

    def mod_dir(self, mod: Mod) -> str:
        return os.path.join(self.mods_dir, mod.id)

    def temp_dir(self) -> str:
        """Scratch folder on the same disk as the library (so installs are a rename)."""
        tmp = os.path.join(self.base_dir, "tmp")
        os.makedirs(tmp, exist_ok=True)
        return tempfile.mkdtemp(prefix="unpack_", dir=tmp)

    def clear_temp(self) -> None:
        shutil.rmtree(os.path.join(self.base_dir, "tmp"), ignore_errors=True)

    def _new_id(self, name: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()[:48] or "mod"
        taken = {m.id for m in self.mods} | set(os.listdir(self.mods_dir))
        candidate, n = slug, 2
        while candidate in taken:
            candidate = "%s-%d" % (slug, n)
            n += 1
        return candidate

    def add_mod(self, name: str, tree: str, target: str = "data", source: str = "") -> Mod:
        """Take ownership of an unpacked mod folder and add it, enabled, at the
        end of the list (highest priority)."""
        mod = Mod(self._new_id(name), name.strip() or "Unnamed mod", True,
                  "root" if target == "root" else "data", source,
                  datetime.datetime.now().isoformat(timespec="seconds"))
        dest = self.mod_dir(mod)
        try:
            os.rename(tree, dest)
        except OSError:
            shutil.copytree(tree, dest, symlinks=False)
            shutil.rmtree(tree, ignore_errors=True)
        self.forget_files(mod.id)
        mod.files = len(self._walk(mod))
        self.mods.append(mod)
        self.settle_pins()
        self.save()
        return mod

    def settle_pins(self) -> bool:
        """Pinned mods stay below the others, keeping their own order. Returns True if anything moved."""
        ordered = [m for m in self.mods if not m.pinned] + [m for m in self.mods if m.pinned]
        moved = [m.id for m in ordered] != [m.id for m in self.mods]
        self.mods[:] = ordered
        return moved

    def recount(self, mod: Mod) -> None:
        self.forget_files(mod.id)
        mod.files = len(self._walk(mod))
        mod.size = 0
        self.measure(mod)

    def measure(self, mod: Mod) -> int:
        """Bytes the mod takes on disk, worked out once and remembered."""
        if mod.size <= 0 and mod.files:
            total = 0
            for path, _parts in self._walk(mod):
                try:
                    total += os.lstat(path).st_size
                except OSError:
                    pass
            mod.size = max(total, 1)
        return mod.size

    def remove_mod(self, mod_id: str) -> DeployReport:
        mod = self.get(mod_id)
        if mod is None:
            return DeployReport()
        self.mods.remove(mod)
        self.forget_files(mod.id)
        self.save()
        report = self.deploy() if self.game_dir else DeployReport()
        shutil.rmtree(self.mod_dir(mod), ignore_errors=True)
        return report

    def get(self, mod_id: str) -> Optional[Mod]:
        for m in self.mods:
            if m.id == mod_id:
                return m
        return None

    def set_enabled(self, mod_id: str, enabled: bool) -> None:
        mod = self.get(mod_id)
        if mod is not None and mod.enabled != enabled:
            mod.enabled = enabled
            self.save()

    def rename(self, mod_id: str, name: str) -> None:
        mod = self.get(mod_id)
        if mod is not None and name.strip():
            mod.name = name.strip()
            self.save()

    def move(self, index: int, new_index: int) -> None:
        if 0 <= index < len(self.mods) and 0 <= new_index < len(self.mods):
            self.mods.insert(new_index, self.mods.pop(index))
            self.save()

    def _walk(self, mod: Mod) -> list:
        """(absolute path, parts relative to the game folder) for each of a mod's files.

        Reading a large mod's folder is the slowest thing this class does and the answer is wanted
        many times over (conflicts, deploying, plugins, risk), so it is remembered until the mod
        changes; see forget_files."""
        cache = self.__dict__.setdefault("_walk_cache", {})
        hit = cache.get(mod.id)
        if hit is None or hit[0] != mod.target:
            hit = (mod.target, list(self._walk_disk(mod)))
            cache[mod.id] = hit
        return hit[1]

    def forget_files(self, mod_id: str = "") -> None:
        """Drop the remembered file list of one mod, or of all (after files may have changed on disk)."""
        cache = self.__dict__.setdefault("_walk_cache", {})
        if mod_id:
            cache.pop(mod_id, None)
        else:
            cache.clear()
        self.__dict__.pop("_risk_cache", None)

    def _walk_disk(self, mod: Mod):
        root = self.mod_dir(mod)
        prefix = ["Data"] if mod.target == "data" else []
        for folder, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(folder, d))]
            rel = os.path.relpath(folder, root)
            rel_parts = [] if rel == "." else rel.split(os.sep)
            for fn in files:
                path = os.path.join(folder, fn)
                if os.path.islink(path):
                    continue
                yield path, prefix + rel_parts + [fn]

    def risk(self, mod: Mod) -> tuple:
        """(level, how, why): level is "high" or "low"; how is "set by you" or "judged from its files".

        High risk: the mod becomes part of a save. Add it on a new save, and expect saves made with
        it to break if it is removed. Low risk: adding or removing it barely changes a save."""
        if mod.risk in ("high", "low"):
            return mod.risk, "set by you", ""
        key = (mod.id, mod.files, mod.installed)
        cache = self.__dict__.setdefault("_risk_cache", {})
        if key not in cache:
            scripts = plugins = 0
            for _path, parts in self._walk(mod):
                low = parts[-1].lower()
                if low.endswith(".pex"):
                    scripts += 1
                elif low.endswith(PLUGIN_EXTS) and len(parts) == 2 and parts[0].lower() == "data":
                    plugins += 1
            if scripts:
                cache[key] = ("high", "it has %d script%s, and running scripts are stored in saves" % (scripts, "" if scripts == 1 else "s"))
            elif plugins:
                cache[key] = ("high", "it has a plugin, and what a plugin adds or changes is referred to by saves")
            else:
                cache[key] = ("low", "it has no plugin and no scripts, only replacement files")
        level, why = cache[key]
        return level, "judged from its files", why

    def mod_plugins(self, mod: Mod) -> list:
        """Plugin files this mod puts directly into Data."""
        out = []
        for _path, parts in self._walk(mod):
            if len(parts) == 2 and parts[0].lower() == "data" and parts[1].lower().endswith(PLUGIN_EXTS):
                out.append(parts[1])
        return sorted(out, key=str.lower)

    def plan(self) -> dict:
        """Which file ends up at each game path: key (lower-case path) ->
        (mod id, source file, path parts). Later mods override earlier ones."""
        wanted: dict = {}
        for mod in self.mods:
            if not mod.enabled:
                continue
            for path, parts in self._walk(mod):
                wanted["/".join(parts).lower()] = (mod.id, path, parts)
        return wanted

    def conflicts(self) -> dict:
        """mod id -> {"wins": [paths it overrides], "loses": [paths overridden by a later mod]}."""
        owners: dict = {}
        for mod in self.mods:
            if not mod.enabled:
                continue
            for _path, parts in self._walk(mod):
                owners.setdefault("/".join(parts).lower(), []).append(mod.id)
        out = {m.id: {"wins": [], "loses": []} for m in self.mods}
        for key, ids in owners.items():
            if len(ids) < 2:
                continue
            out[ids[-1]]["wins"].append(key)
            for loser in ids[:-1]:
                out[loser]["loses"].append(key)
        return out

    # -- deployment ---------------------------------------------------------

    @staticmethod
    def _resolve_case(base: str, parts: list, created: list, cache: dict) -> str:
        """Destination path under base, reusing existing folders whose names differ
        only in case (Windows games do not care about case; Linux does)."""
        cur = base
        for i, part in enumerate(parts):
            if cur not in cache:
                try:
                    cache[cur] = {e.lower(): e for e in os.listdir(cur)}
                except OSError:
                    cache[cur] = {}
            listing = cache[cur]
            actual = listing.get(part.lower())
            if actual is None:
                actual = part
                listing[part.lower()] = part
                if i < len(parts) - 1:
                    os.mkdir(os.path.join(cur, part))
                    created.append(os.path.relpath(os.path.join(cur, part), base))
            cur = os.path.join(cur, actual)
        return cur

    def _place(self, src: str, dst: str) -> str:
        if self.link_mode == "auto":
            try:
                os.link(src, dst)
                return "hard"
            except OSError:
                pass
        if self.link_mode in ("auto", "symlink"):
            try:
                os.symlink(src, dst)
                return "sym"
            except OSError:
                pass
        shutil.copy2(src, dst)
        return "copy"

    def _remove_deployed(self, game_dir: str, entry: dict, report: DeployReport) -> None:
        path = os.path.join(game_dir, entry["path"])
        if os.path.lexists(path):
            replaced = False
            src = entry.get("src", "")
            if entry.get("method") == "hard" and src and os.path.exists(src) and not os.path.islink(path):
                try:
                    replaced = os.stat(path).st_ino != os.stat(src).st_ino
                except OSError:
                    replaced = False
            if replaced:
                # Something rewrote this file after it was deployed; keep that version.
                keep = os.path.join(self.backup_dir, "changed", entry["path"])
                os.makedirs(os.path.dirname(keep), exist_ok=True)
                if os.path.lexists(keep):
                    os.unlink(keep)
                shutil.move(path, keep)
            else:
                os.unlink(path)
            report.removed += 1
        backup = os.path.join(self.backup_dir, "original", entry["path"])
        if entry.get("backup") and os.path.lexists(backup) and not os.path.lexists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            shutil.move(backup, path)
            report.restored += 1

    def deploy(self) -> DeployReport:
        """Make the game folder match the enabled mods."""
        report = DeployReport()
        if not self.game_dir or not os.path.isdir(self.game_dir):
            report.errors.append("The game folder is not set or does not exist.")
            return report
        game_dir = self.game_dir
        manifest = self._manifest()
        if manifest["files"] and manifest["game_dir"] and \
                os.path.realpath(manifest["game_dir"]) != os.path.realpath(game_dir):
            self.purge()
            manifest = self._manifest()
        manifest["game_dir"] = game_dir
        files: dict = manifest["files"]
        created: list = manifest["dirs"]
        before = (len(files), list(created), manifest.get("game_dir"))
        wanted = self.plan()
        try:
            # 1. take away what is no longer wanted, or now comes from a different file
            for key in list(files):
                entry = files[key]
                want = wanted.get(key)
                same = want is not None and want[0] == entry.get("mod") and want[1] == entry.get("src")
                if same and os.path.lexists(os.path.join(game_dir, entry["path"])):
                    report.kept += 1
                    continue
                keep_backup = want is not None and entry.get("backup")
                try:
                    if keep_backup:
                        # still overriding the same game file: remove our file, leave the backup alone
                        tmp_entry = dict(entry, backup=False)
                        self._remove_deployed(game_dir, tmp_entry, report)
                        wanted[key] = want + (True,)
                    else:
                        self._remove_deployed(game_dir, entry, report)
                    del files[key]
                except OSError as e:
                    report.errors.append("%s: %s" % (entry["path"], e))
            # 2. put the new files in place
            cache: dict = {}
            for key, want in wanted.items():
                if key in files:
                    continue
                mod_id, src, parts = want[0], want[1], want[2]
                had_backup = len(want) > 3
                try:
                    dst = self._resolve_case(game_dir, parts, created, cache)
                    backed_up = had_backup
                    if os.path.lexists(dst):
                        if os.path.isdir(dst) and not os.path.islink(dst):
                            report.errors.append("%s: a folder with this name is in the way" % "/".join(parts))
                            continue
                        rel = os.path.relpath(dst, game_dir)
                        keep = os.path.join(self.backup_dir, "original", rel)
                        os.makedirs(os.path.dirname(keep), exist_ok=True)
                        if os.path.lexists(keep):
                            os.unlink(dst)          # an original is already saved; this one is a leftover
                        else:
                            shutil.move(dst, keep)
                            report.backed_up += 1
                        backed_up = True
                    method = self._place(src, dst)
                    report.methods[method] = report.methods.get(method, 0) + 1
                    files[key] = {"path": os.path.relpath(dst, game_dir), "mod": mod_id, "src": src,
                                  "method": method, "backup": backed_up}
                    report.linked += 1
                except OSError as e:
                    report.errors.append("%s: %s" % ("/".join(parts), e))
            # 3. tidy folders this tool created that are now empty
            manifest["dirs"] = self._prune_dirs(game_dir, created)
        finally:
            changed = report.linked or report.removed or report.restored or report.backed_up or report.errors
            if changed or before != (len(files), manifest["dirs"], game_dir) or not os.path.exists(self.manifest_path):
                self._write_json(self.manifest_path, manifest, compact=True)      # a large library makes this file big: skip when nothing moved
        return report

    @staticmethod
    def _prune_dirs(game_dir: str, created: list) -> list:
        remaining = []
        for rel in sorted(set(created), key=lambda r: r.count(os.sep), reverse=True):
            path = os.path.join(game_dir, rel)
            try:
                os.rmdir(path)              # only succeeds when empty
            except OSError:
                if os.path.isdir(path):
                    remaining.append(rel)
        return remaining

    def purge(self) -> DeployReport:
        """Remove every deployed file and put back any game files that were set
        aside, leaving the game folder as it was."""
        report = DeployReport()
        manifest = self._manifest()
        game_dir = manifest["game_dir"] or self.game_dir
        if not game_dir:
            return report
        files = manifest["files"]
        try:
            for key in list(files):
                try:
                    self._remove_deployed(game_dir, files[key], report)
                    del files[key]
                except OSError as e:
                    report.errors.append("%s: %s" % (files[key]["path"], e))
            manifest["dirs"] = self._prune_dirs(game_dir, manifest["dirs"])
        finally:
            self._write_json(self.manifest_path, manifest)
        return report

    def deployed_count(self) -> int:
        return len(self._manifest()["files"])

    def link_method_hint(self) -> str:
        """How files will be placed, judged from where the library and game live."""
        if self.link_mode != "auto":
            return self.link_mode
        try:
            same = os.stat(self.mods_dir).st_dev == os.stat(self.game_dir).st_dev
        except OSError:
            return "unknown"
        return "hard links" if same else "symlinks (library and game are on different disks)"
