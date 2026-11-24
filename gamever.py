"""Which Fallout 4 is installed, and which game versions a download says it is for.

Nexus Mods has no game-version field on files, so this reads what authors write in
file names, file notes and descriptions. It is a reading of free text: a hint, not a fact.

The game has three generations that mods (script-extender plug-ins above all) care about:
  og  "old-gen"      1.10.163 and earlier
  ng  "next-gen"     1.10.980 / 1.10.984  (2024)
  ae  "Anniversary"  1.11 and later       (November 2025)
"""
from __future__ import annotations

import re
import struct
from typing import Optional

LABELS = {"og": "old-gen", "ng": "next-gen", "ae": "Anniversary"}
ORDER = ("og", "ng", "ae")


def exe_version(path: str) -> Optional[tuple]:
    """Version number stored inside a Windows program, e.g. (1, 10, 163, 0)."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    at = 0
    while True:
        at = data.find(b"\xbd\x04\xef\xfe", at)          # start of the fixed version block
        if at < 0 or at + 16 > len(data):
            return None
        struc, ms, ls = struct.unpack_from("<III", data, at + 4)
        if struc == 0x00010000 and 0 < (ms >> 16) < 100:
            return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
        at += 4


def generation(version: Optional[tuple]) -> str:
    if not version:
        return ""
    v = tuple(version[:3])
    if v < (1, 10, 980):
        return "og"
    if v < (1, 11, 0):
        return "ng"
    return "ae"


def describe(version: Optional[tuple]) -> str:
    """'1.10.163 (old-gen)', or '' when unknown."""
    if not version:
        return ""
    return "%s (%s)" % (".".join(str(n) for n in version[:3]), LABELS[generation(version)])


_PRE_NG = re.compile(r"\bpre[\s\-_]*(?:ng\b|next[\s\-_]*gen)|\bbefore\s+(?:the\s+)?next[\s\-_]*gen", re.I)
_OG = re.compile(r"\bold[\s\-_]*gen\b|\bdowngrad\w*|\blegacy\s+version", re.I)
_OG_CASED = re.compile(r"(?<![A-Za-z0-9])OG(?![A-Za-z0-9])")
_NG = re.compile(r"\bnext[\s\-_]*gen\b|\bnextgen\b|\bpost[\s\-_]*ng\b", re.I)
_NG_CASED = re.compile(r"(?<![A-Za-z0-9])NG(?![A-Za-z0-9])")
_AE = re.compile(r"\banniversary\b", re.I)
_AE_CASED = re.compile(r"(?<![A-Za-z0-9])AE(?![A-Za-z0-9])")
_NUMBER = re.compile(r"(?<![\d.])1[._](10|11)[._](\d{2,4})(?!\d)")


def mentions(text: str) -> set:
    """The generations a piece of text names: a subset of {'og', 'ng', 'ae'}."""
    if not text:
        return set()
    found = set()
    text, pre = _PRE_NG.subn(" ", text)
    if pre:
        found.add("og")
    for m in _NUMBER.finditer(text):
        found.add(generation((1, int(m.group(1)), int(m.group(2)))))
    if _OG.search(text) or _OG_CASED.search(text):
        found.add("og")
    if _NG.search(text) or _NG_CASED.search(text):
        found.add("ng")
    if _AE.search(text) or _AE_CASED.search(text):
        found.add("ae")
    return found


def names(gens) -> str:
    return ", ".join(LABELS[g] for g in ORDER if g in gens)


def file_mentions(file) -> set:
    return mentions(" \n ".join((file.name or "", file.file_name or "", file.description or "")))


def fit(file, gen: str) -> str:
    """'yes' the file says it is for this generation, 'no' it names only others, '' it does not say."""
    said = file_mentions(file)
    if not said or not gen:
        return ""
    return "yes" if gen in said else "no"


def best_file(files: list, gen: str):
    """The file to offer first: main files before the rest, ones labelled for the installed game
    before unlabelled ones, and those before ones labelled for another version. Among equals the
    newest wins. Returns None for an empty list."""
    if not files:
        return None
    rank = {"yes": 0, "": 1, "no": 2}

    def key(pair):
        i, f = pair
        return (0 if (f.category or "").lower() in ("main", "release") else 1, rank[fit(f, gen)],
                tuple(-ord(ch) for ch in (f.uploaded or "")), i)

    return min(enumerate(files), key=key)[1]
