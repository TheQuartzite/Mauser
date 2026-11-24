"""Unpacking mod archives and working out how their contents map onto the game."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from typing import Optional

PLUGIN_EXTS = (".esm", ".esp", ".esl")
DATA_FILE_EXTS = PLUGIN_EXTS + (".ba2",)
DATA_DIRS = {"meshes", "textures", "materials", "scripts", "sound", "music", "interface", "strings",
             "video", "f4se", "mcm", "tools", "vis", "lodsettings", "shadersfx", "misc", "programs",
             "actors", "terrain", "source", "config", "fonts"}
ROOT_FILE_EXTS = (".exe", ".dll")
JUNK = {"__macosx", ".ds_store", "thumbs.db", "desktop.ini"}


class ArchiveError(Exception):
    pass


def _safe_parts(name: str) -> Optional[list]:
    """Split an archive member name into path parts, or None if it tries to
    leave the destination folder."""
    name = name.replace("\\", "/")
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return parts


def _extract_zip(path: str, dest: str) -> None:
    try:
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                if stat.S_ISLNK(info.external_attr >> 16):
                    continue
                parts = _safe_parts(info.filename)
                if parts is None:
                    raise ArchiveError("archive contains an unsafe path: %s" % info.filename)
                target = os.path.join(dest, *parts)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as e:
        raise ArchiveError("could not unpack zip: %s" % e)


def find_7zip() -> Optional[str]:
    """Path of a 7-Zip program: one on the system, or the copy install.sh puts in
    the app's own environment. It unpacks both .7z and .rar."""
    packed = getattr(sys, "_MEIPASS", "")           # set when running as the single-file app
    if packed and os.path.isfile(os.path.join(packed, "7zz")):
        return os.path.join(packed, "7zz")
    for name in ("7zz", "7z"):
        found = shutil.which(name)
        if found:
            return found
    try:
        import importlib.util
        spec = importlib.util.find_spec("py7zz")      # located only, never imported
    except (ImportError, ValueError):
        spec = None
    if spec is not None and spec.origin:
        bundled = os.path.join(os.path.dirname(spec.origin), "bin", "7zz")
        if os.path.isfile(bundled):
            if not os.access(bundled, os.X_OK):
                try:
                    os.chmod(bundled, 0o755)
                except OSError:
                    return None
            return bundled
    return None


def _extract_7zip_tool(tool: str, path: str, dest: str) -> None:
    """Unpack with 7-Zip after checking the archive's file list is safe."""
    try:
        listing = subprocess.run([tool, "l", "-slt", "-ba", "-p", "--", path], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ArchiveError("could not read the archive: %s" % e)
    if listing.returncode != 0:
        detail = listing.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ArchiveError("could not read the archive%s" % (": " + detail[-1] if detail else ""))
    for raw in listing.stdout.decode("utf-8", "replace").splitlines():
        if raw.startswith("Path = "):
            if _safe_parts(raw[7:]) is None:
                raise ArchiveError("archive contains an unsafe path: %s" % raw[7:])
        elif raw.startswith(("Symbolic Link = ", "Hard Link = ")) and raw.split(" = ", 1)[1].strip():
            raise ArchiveError("archive contains links, which mods should not need")
        elif raw.startswith("Attributes = ") and any(len(tok) == 10 and tok[0] == "l" and tok[1] in "r-"
                                                     for tok in raw[13:].split()):
            raise ArchiveError("archive contains links, which mods should not need")
    try:
        res = subprocess.run([tool, "x", "-y", "-p", "-bd", "-o" + dest, "--", path], stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE, timeout=7200)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ArchiveError("could not unpack the archive: %s" % e)
    if res.returncode != 0:
        detail = res.stderr.decode("utf-8", "replace").strip().splitlines()
        raise ArchiveError("could not unpack the archive%s" % (": " + detail[-1] if detail else ""))


def _extract_py7zr(path: str, dest: str) -> None:
    try:
        import py7zr
    except ImportError:
        raise ArchiveError("No 7z unpacker is available. Run install.sh again, or unpack the archive with "
                           "your file manager and use 'Install from folder'.")
    try:
        with py7zr.SevenZipFile(path, "r") as z:
            for name in z.getnames():
                if _safe_parts(name) is None:
                    raise ArchiveError("archive contains an unsafe path: %s" % name)
            z.extractall(dest)
    except ArchiveError:
        raise
    except Exception as e:      # py7zr raises many types (bad data, unsupported filters, ...)
        raise ArchiveError("could not unpack 7z: %s" % e)


def _extract_with_tool(path: str, dest: str) -> None:
    tools = (("bsdtar", ["bsdtar", "-xf", path, "-C", dest]),
             ("unrar", ["unrar", "x", "-o+", "-y", "-p-", path, dest + os.sep]),
             ("unar", ["unar", "-force-overwrite", "-no-directory", "-output-directory", dest, path]))
    tried = []
    for name, cmd in tools:
        if shutil.which(name) is None:
            continue
        tried.append(name)
        try:
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=7200)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if res.returncode == 0:
            return
    if tried:
        raise ArchiveError("could not unpack this archive (tried %s)." % ", ".join(tried))
    raise ArchiveError("No RAR unpacker is available. Run install.sh again, or unpack the archive with your "
                       "file manager and use 'Install from folder'.")


def _scrub(dest: str) -> None:
    """Remove links and anything that resolves outside the destination."""
    real_dest = os.path.realpath(dest)
    for folder, dirs, files in os.walk(dest):
        for name in list(dirs) + files:
            p = os.path.join(folder, name)
            if os.path.islink(p):
                os.unlink(p)
                if name in dirs:
                    dirs.remove(name)
            elif not os.path.realpath(p).startswith(real_dest + os.sep):
                raise ArchiveError("archive wrote outside its folder")


def extract(path: str, dest: str) -> None:
    """Unpack a .zip, .7z or .rar into `dest` (created if needed)."""
    if not os.path.isfile(path):
        raise ArchiveError("file not found: %s" % path)
    os.makedirs(dest, exist_ok=True)
    low = path.lower()
    if zipfile.is_zipfile(path):
        _extract_zip(path, dest)
    elif low.endswith((".7z", ".rar")):
        tool = find_7zip()
        if tool is not None:
            try:
                _extract_7zip_tool(tool, path, dest)
            except ArchiveError:
                if not low.endswith(".rar"):
                    raise
                # some system 7z builds cannot read RAR; try the other tools
                shutil.rmtree(dest, ignore_errors=True)
                os.makedirs(dest, exist_ok=True)
                _extract_with_tool(path, dest)
        elif low.endswith(".7z"):
            _extract_py7zr(path, dest)
        else:
            _extract_with_tool(path, dest)
    else:
        raise ArchiveError("unsupported file type: %s" % os.path.basename(path))
    _scrub(dest)


@dataclass
class Layout:
    kind: str            # "fomod", "data", "root" or "unknown"
    base: str            # folder whose contents go into Data ("data") or the game folder ("root")
    note: str = ""


def _listing(folder: str):
    dirs, files = [], []
    for entry in os.listdir(folder):
        if entry.lower() in JUNK:
            continue
        (dirs if os.path.isdir(os.path.join(folder, entry)) else files).append(entry)
    return dirs, files


def find_layout(root: str, depth: int = 0) -> Layout:
    """Work out where a mod's files belong by looking at what it contains."""
    dirs, files = _listing(root)
    low_dirs = {d.lower(): d for d in dirs}
    if "fomod" in low_dirs:
        fomod = os.path.join(root, low_dirs["fomod"])
        if any(f.lower() == "moduleconfig.xml" for f in os.listdir(fomod)):
            return Layout("fomod", root)
    root_files = [f for f in files if f.lower().endswith(ROOT_FILE_EXTS)]
    if "data" in low_dirs:
        if root_files:
            return Layout("root", root, "Installs into the game folder (for example a script extender).")
        return Layout("data", os.path.join(root, low_dirs["data"]))
    if any(f.lower().endswith(DATA_FILE_EXTS) for f in files) or any(d in DATA_DIRS for d in low_dirs):
        return Layout("data", root)
    if root_files:
        return Layout("root", root, "Installs into the game folder.")
    if len(dirs) == 1 and depth < 6:
        inner = find_layout(os.path.join(root, dirs[0]), depth + 1)
        if inner.kind != "unknown" or not files:
            return inner
    return Layout("unknown", root, "This archive does not look like a normal Fallout 4 mod.")


def copy_tree(src: str, dst: str) -> int:
    """Copy a folder's files into dst, merging names case-insensitively.
    Returns the number of files copied."""
    count = 0
    os.makedirs(dst, exist_ok=True)
    for entry in os.listdir(src):
        if entry.lower() in JUNK:
            continue
        s = os.path.join(src, entry)
        if os.path.islink(s):
            continue
        existing = {e.lower(): e for e in os.listdir(dst)}
        d = os.path.join(dst, existing.get(entry.lower(), entry))
        if os.path.isdir(s):
            if os.path.isfile(d):
                os.unlink(d)
            count += copy_tree(s, d)
        else:
            if os.path.isdir(d):
                continue
            shutil.copyfile(s, d)
            count += 1
    return count
