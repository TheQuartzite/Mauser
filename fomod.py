"""FOMOD guided installers: the XML option menus many mods ship with
(fomod/ModuleConfig.xml). Parsing and the rules for which files get
installed live here; the dialog that shows the choices is in the GUI.
"""
from __future__ import annotations

import os
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable, Optional

from archives import copy_tree

# file_state(name) -> "Active", "Inactive" or "Missing" for a file in the game's Data folder
FileState = Callable[[str], str]


class FomodError(Exception):
    pass


@dataclass
class FileItem:
    source: str
    destination: str
    is_folder: bool
    priority: int = 0


@dataclass
class Option:
    name: str
    description: str = ""
    image: str = ""
    files: list = field(default_factory=list)
    flags: dict = field(default_factory=dict)
    default_type: str = "Optional"
    type_patterns: list = field(default_factory=list)     # (dependency, type name)


@dataclass
class Group:
    name: str
    kind: str                     # SelectExactlyOne, SelectAtMostOne, SelectAtLeastOne, SelectAny, SelectAll
    options: list = field(default_factory=list)


@dataclass
class Step:
    name: str
    visible: Optional[tuple] = None
    groups: list = field(default_factory=list)


@dataclass
class Fomod:
    name: str = ""
    required: list = field(default_factory=list)
    steps: list = field(default_factory=list)
    conditional: list = field(default_factory=list)       # (dependency, [FileItem])


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _kids(el, name: str) -> list:
    return [c for c in el if _local(c.tag) == name]


def _kid(el, name: str):
    found = _kids(el, name)
    return found[0] if found else None


def _ordered(items: list, order: str, key) -> list:
    order = (order or "Ascending").lower()
    if order == "explicit":
        return items
    return sorted(items, key=lambda x: key(x).lower(), reverse=(order == "descending"))


def _files(el) -> list:
    out = []
    if el is None:
        return out
    for c in el:
        tag = _local(c.tag)
        if tag not in ("file", "folder"):
            continue
        source = (c.get("source") or "").strip()
        dest = c.get("destination")
        try:
            priority = int(c.get("priority") or 0)
        except ValueError:
            priority = 0
        out.append(FileItem(source, source if dest is None else dest.strip(), tag == "folder", priority))
    return out


def _dependency(el) -> Optional[tuple]:
    """Parse a <dependencies>-style element into a nested tuple."""
    if el is None:
        return None
    op = (el.get("operator") or "And").lower()
    children = []
    for c in el:
        tag = _local(c.tag)
        if tag == "flagDependency":
            children.append(("flag", c.get("flag") or "", c.get("value") or ""))
        elif tag == "fileDependency":
            children.append(("file", c.get("file") or "", c.get("state") or "Active"))
        elif tag == "dependencies":
            sub = _dependency(c)
            if sub is not None:
                children.append(sub)
        else:                                   # game / script-extender / manager version checks
            children.append(("true",))
    return ("or" if op == "or" else "and", children)


def evaluate(dep: Optional[tuple], flags: dict, file_state: FileState) -> bool:
    if dep is None:
        return True
    kind = dep[0]
    if kind == "true":
        return True
    if kind == "flag":
        return flags.get(dep[1], "") == dep[2]
    if kind == "file":
        return file_state(dep[1]).lower() == dep[2].lower()
    results = [evaluate(c, flags, file_state) for c in dep[1]]
    if not results:
        return True
    return any(results) if kind == "or" else all(results)


def parse(xml_bytes: bytes) -> Fomod:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        # Some installers declare one encoding and are saved in another.
        text = None
        for enc in ("utf-8-sig", "utf-16", "cp1252"):
            try:
                text = xml_bytes.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise FomodError("the installer file could not be decoded")
        if text.lstrip().startswith("<?xml"):
            text = text[text.index("?>") + 2:]
        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            raise FomodError("the installer file is not valid XML: %s" % e)
    fm = Fomod()
    name_el = _kid(root, "moduleName")
    fm.name = (name_el.text or "").strip() if name_el is not None else ""
    fm.required = _files(_kid(root, "requiredInstallFiles"))
    steps_el = _kid(root, "installSteps")
    if steps_el is not None:
        steps = []
        for s in _kids(steps_el, "installStep"):
            step = Step(s.get("name") or "")
            vis = _kid(s, "visible")
            if vis is not None:
                inner = _kid(vis, "dependencies")
                step.visible = _dependency(inner if inner is not None else vis)
            groups_el = _kid(s, "optionalFileGroups")
            groups = []
            for g in (_kids(groups_el, "group") if groups_el is not None else []):
                group = Group(g.get("name") or "", g.get("type") or "SelectAny")
                plugins_el = _kid(g, "plugins")
                options = []
                for p in (_kids(plugins_el, "plugin") if plugins_el is not None else []):
                    opt = Option(p.get("name") or "")
                    d = _kid(p, "description")
                    opt.description = (d.text or "").strip() if d is not None else ""
                    img = _kid(p, "image")
                    opt.image = (img.get("path") or "") if img is not None else ""
                    opt.files = _files(_kid(p, "files"))
                    cf = _kid(p, "conditionFlags")
                    for fl in (_kids(cf, "flag") if cf is not None else []):
                        opt.flags[fl.get("name") or ""] = (fl.text or "").strip()
                    td = _kid(p, "typeDescriptor")
                    if td is not None:
                        simple = _kid(td, "type")
                        dt = _kid(td, "dependencyType")
                        if simple is not None:
                            opt.default_type = simple.get("name") or "Optional"
                        elif dt is not None:
                            default = _kid(dt, "defaultType")
                            if default is not None:
                                opt.default_type = default.get("name") or "Optional"
                            pats = _kid(dt, "patterns")
                            for pat in (_kids(pats, "pattern") if pats is not None else []):
                                t = _kid(pat, "type")
                                opt.type_patterns.append((_dependency(_kid(pat, "dependencies")),
                                                          (t.get("name") if t is not None else None) or "Optional"))
                    options.append(opt)
                group.options = _ordered(options, plugins_el.get("order") if plugins_el is not None else "",
                                         lambda o: o.name)
                groups.append(group)
            step.groups = _ordered(groups, groups_el.get("order") if groups_el is not None else "",
                                   lambda g: g.name)
            steps.append(step)
        fm.steps = _ordered(steps, steps_el.get("order"), lambda s: s.name)
    cond = _kid(root, "conditionalFileInstalls")
    pats = _kid(cond, "patterns") if cond is not None else None
    for pat in (_kids(pats, "pattern") if pats is not None else []):
        fm.conditional.append((_dependency(_kid(pat, "dependencies")), _files(_kid(pat, "files"))))
    return fm


def option_type(opt: Option, flags: dict, file_state: FileState) -> str:
    """Required, Recommended, Optional, CouldBeUsable or NotUsable."""
    for dep, type_name in opt.type_patterns:
        if evaluate(dep, flags, file_state):
            return type_name
    return opt.default_type


def default_selection(group: Group, flags: dict, file_state: FileState) -> set:
    types = [option_type(o, flags, file_state) for o in group.options]
    usable = [i for i, t in enumerate(types) if t != "NotUsable"]
    if group.kind == "SelectAll":
        return set(range(len(group.options)))
    chosen = {i for i, t in enumerate(types) if t in ("Required", "Recommended")}
    if group.kind in ("SelectExactlyOne", "SelectAtMostOne"):
        if chosen:
            return {min(chosen)}
        if group.kind == "SelectExactlyOne" and usable:
            return {usable[0]}
        return set()
    if group.kind == "SelectAtLeastOne" and not chosen and usable:
        return {usable[0]}
    return chosen


def valid_selection(group: Group, chosen: set) -> bool:
    n = len(chosen)
    if group.kind == "SelectExactlyOne":
        return n == 1 or not group.options
    if group.kind == "SelectAtMostOne":
        return n <= 1
    if group.kind == "SelectAtLeastOne":
        return n >= 1 or not group.options
    if group.kind == "SelectAll":
        return n == len(group.options)
    return True


class Session:
    """Tracks the choices made so far and answers what to show and install."""

    def __init__(self, fm: Fomod, file_state: Optional[FileState] = None):
        self.fm = fm
        self.file_state: FileState = file_state or (lambda _name: "Missing")
        self.choices: dict = {}                 # (step index, group index) -> set of option indices

    def flags_before(self, step_index: int) -> dict:
        flags: dict = {}
        for si, step in enumerate(self.fm.steps[:step_index]):
            if not evaluate(step.visible, flags, self.file_state):
                continue
            for gi, group in enumerate(step.groups):
                for oi in sorted(self.selection(si, gi, flags)):
                    flags.update(group.options[oi].flags)
        return flags

    def selection(self, si: int, gi: int, flags: Optional[dict] = None) -> set:
        key = (si, gi)
        if key not in self.choices:
            if flags is None:
                flags = self.flags_before(si)
            self.choices[key] = default_selection(self.fm.steps[si].groups[gi], flags, self.file_state)
        return self.choices[key]

    def step_visible(self, si: int) -> bool:
        return evaluate(self.fm.steps[si].visible, self.flags_before(si), self.file_state)

    def next_visible(self, si: int, direction: int = 1) -> Optional[int]:
        i = si + direction
        while 0 <= i < len(self.fm.steps):
            if self.step_visible(i):
                return i
            i += direction
        return None

    def result(self) -> list:
        """All files to install, lowest priority first."""
        flags = self.flags_before(len(self.fm.steps))
        items = list(self.fm.required)
        running: dict = {}
        for si, step in enumerate(self.fm.steps):
            if not evaluate(step.visible, running, self.file_state):
                continue
            for gi, group in enumerate(step.groups):
                for oi in sorted(self.selection(si, gi, running)):
                    items += group.options[oi].files
                    running.update(group.options[oi].flags)
        for dep, files in self.fm.conditional:
            if evaluate(dep, flags, self.file_state):
                items += files
        return sorted(items, key=lambda it: it.priority)      # stable: document order within a priority


def _resolve(base: str, rel: str) -> Optional[str]:
    """Find rel under base ignoring case; None if it escapes or does not exist."""
    cur = base
    for part in rel.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            return None
        match = None
        try:
            for entry in os.listdir(cur):
                if entry.lower() == part.lower():
                    match = entry
                    break
        except OSError:
            return None
        if match is None:
            return None
        cur = os.path.join(cur, match)
    return cur


def _merged_path(root: str, rel: str) -> Optional[str]:
    """Path for rel under root, reusing existing names that differ only in case."""
    cur = root
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    for part in parts:
        match = part
        if os.path.isdir(cur):
            for entry in os.listdir(cur):
                if entry.lower() == part.lower():
                    match = entry
                    break
        cur = os.path.join(cur, match)
    return cur


def install(base: str, items: list, dest: str) -> tuple:
    """Copy the chosen files from the unpacked archive (`base`) into `dest`,
    laid out relative to the game's Data folder. Returns (files copied, missing sources)."""
    os.makedirs(dest, exist_ok=True)
    count = 0
    missing = []
    for it in items:
        src = _resolve(base, it.source) if it.source else base
        if src is None or not os.path.exists(src):
            missing.append(it.source)
            continue
        if it.is_folder:
            if not os.path.isdir(src):
                missing.append(it.source)
                continue
            target = _merged_path(dest, it.destination) if it.destination else dest
            if target is None:
                continue
            count += copy_tree(src, target)
        else:
            if not os.path.isfile(src):
                missing.append(it.source)
                continue
            rel = it.destination or os.path.basename(src)
            if rel.replace("\\", "/").endswith("/"):
                rel = rel + os.path.basename(src)
            target = _merged_path(dest, rel)
            if target is None:
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.isdir(target):
                continue
            shutil.copyfile(src, target)
            count += 1
    return count, missing
