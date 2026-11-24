"""Noticing that Fallout 4 has stopped, judging whether it crashed, and writing a full report.

The game is started by Steam, not by this app, so there is no exit code to read. A session is
followed by looking for the game's process, and when it ends the evidence left behind is weighed:
a crash log from a crash-logger mod, a Windows crash dump, error lines in Proton's log, or a
session that ended within moments of starting.
"""
from __future__ import annotations

import datetime
import os
import platform
import re
import time
from typing import Optional

import gameini
import gamever
import steam

QUICK_EXIT_SECONDS = 75          # a session shorter than this is treated as a crash at start-up
TAIL_LINES = 150
CRASH_LOG_LIMIT = 60000          # characters of a crash-logger file to include


_GAME_ARG = re.compile(rb"(^|[\\/])fallout4\.exe(\s|\"|$)")       # "Fallout4Launcher.exe" does not match
_NOT_THE_GAME = (b"bash", b"sh", b"zsh", b"fish", b"grep", b"rg", b"timeout", b"xvfb-run", b"sudo", b"env", b"cat", b"less",
                 b"nano", b"vim", b"kate", b"code")


def game_running() -> bool:
    """Is Fallout4.exe running (through Wine/Proton)? The launcher does not count."""
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return False
    me = str(os.getpid())
    for pid in pids:
        if pid == me:
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as f:
                cmd = f.read(4096).lower()
        except OSError:
            continue
        args = cmd.split(b"\0")
        first = os.path.basename(args[0].replace(b"\\", b"/")) if args else b""
        if first in _NOT_THE_GAME or first.startswith(b"python"):
            continue                          # a shell or tool that merely mentions the name
        if any(_GAME_ARG.search(a) for a in args[:3]):
            return True
    return False


def _tail(path: str, lines: int = TAIL_LINES) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 200000))
            text = f.read().decode("utf-8", "replace")
    except OSError as e:
        return "(could not be read: %s)" % e
    rows = text.splitlines()
    return "\n".join(rows[-lines:]) if rows else "(empty)"


def _newer(folder: Optional[str], since: float, pattern: str) -> list:
    """Files in folder whose name matches and that changed after `since`, newest first."""
    out = []
    if not folder:
        return out
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    for name in names:
        if re.search(pattern, name, re.I):
            path = os.path.join(folder, name)
            try:
                if os.path.getmtime(path) >= since - 2:
                    out.append(path)
            except OSError:
                pass
    return sorted(out, key=os.path.getmtime, reverse=True)


class Evidence:
    def __init__(self):
        self.crash_logs: list = []      # from Buffout 4 and similar
        self.dumps: list = []
        self.proton_log: str = ""
        self.proton_errors: list = []
        self.f4se_dir: Optional[str] = None
        self.reasons: list = []         # why this looks like a crash


_PROTON_BAD = re.compile(r"unhandled exception|unhandled page fault|EXCEPTION_ACCESS_VIOLATION|err:seh:|"
                         r"wine: Unhandled|Assertion .* failed|terminate called|out of memory|"
                         r"vkd3d.*failed|err:virtual:allocate", re.I)


def gather(paths: steam.GamePaths, started: float, duration: float) -> Evidence:
    ev = Evidence()
    ini_dir = paths.ini_dir()
    ev.f4se_dir = steam.child(ini_dir, "F4SE") if ini_dir else None
    ev.crash_logs = _newer(ev.f4se_dir, started, r"^crash-.*\.(log|txt)$") + _newer(ini_dir, started, r"^crash-.*\.(log|txt)$")
    ev.dumps = _newer(paths.game_dir, started, r"\.(dmp|mdmp)$")
    user = paths._user_dir()
    ev.dumps += _newer(steam.descend(user, "AppData", "Local", "CrashDumps") if user else None, started, r"fallout4.*\.dmp$")
    for folder in (os.path.expanduser("~"), os.environ.get("PROTON_LOG_DIR") or ""):
        log = os.path.join(folder, "steam-%s.log" % steam.APP_ID) if folder else ""
        if log and os.path.isfile(log) and os.path.getmtime(log) >= started - 2:
            ev.proton_log = log
            break
    if ev.proton_log:
        ev.proton_errors = [l for l in _tail(ev.proton_log, 4000).splitlines() if _PROTON_BAD.search(l)][-40:]
    if ev.crash_logs:
        ev.reasons.append("a crash log was written (%s)" % os.path.basename(ev.crash_logs[0]))
    if ev.dumps:
        ev.reasons.append("a crash dump was written (%s)" % os.path.basename(ev.dumps[0]))
    if ev.proton_errors:
        ev.reasons.append("Proton's log has %d error lines" % len(ev.proton_errors))
    if duration < QUICK_EXIT_SECONDS:
        ev.reasons.append("the game stopped %d seconds after starting" % duration)
    return ev


def f4se_facts(paths: steam.GamePaths, version: Optional[tuple]) -> tuple:
    """(lines describing the script extender, list of likely problems)."""
    lines, problems = [], []
    try:
        names = os.listdir(paths.game_dir)
    except OSError:
        names = []
    dlls = sorted(n for n in names if re.fullmatch(r"f4se_\d+_\d+_\d+\.dll", n, re.I))
    if not paths.has_f4se():
        return ["Script extender (F4SE): not installed"], problems
    lines.append("Script extender (F4SE): installed, built for: %s" % (", ".join(dlls) or "unknown (no f4se_x_y_z.dll found)"))
    if version and dlls:
        want = "f4se_%d_%d_%d.dll" % tuple(version[:3])
        if want.lower() not in [d.lower() for d in dlls]:
            problems.append("F4SE does not match the game. The game is %s, but F4SE has %s. Install the F4SE build for "
                            "your game version." % (".".join(str(n) for n in version[:3]), ", ".join(dlls)))
    plugin_dir = steam.descend(paths.data_dir, "F4SE", "Plugins")
    try:
        plugins = sorted(n for n in os.listdir(plugin_dir) if n.lower().endswith(".dll")) if plugin_dir else []
    except OSError:
        plugins = []
    lines.append("F4SE plug-ins (%d): %s" % (len(plugins), ", ".join(plugins) or "none"))
    return lines, problems


_F4SE_BAD = re.compile(r"couldn't load plugin|incompatible|disabled, |fatal error|failed to|not compatible|reported as incompatible", re.I)


def build_report(app: str, mgr, order, paths: Optional[steam.GamePaths], version: Optional[tuple], started: float,
                 ended: float, crashed: Optional[bool], ev: Optional[Evidence], board: str = "") -> str:
    """The whole report as text. crashed: True, False, or None when made by hand."""
    home = os.path.expanduser("~")
    out = []

    def head(title):
        out.extend(["", "=" * 78, title, "=" * 78])

    duration = max(0, ended - started)
    out.append("---- Mauser crash report ----" if crashed else "---- Mauser game report ----")
    out.append("// Vinny dropped the parcel." if crashed else "// Nothing to see here. Probably.")
    out.append("")
    out.append("Time: %s" % datetime.datetime.fromtimestamp(ended).strftime("%Y-%m-%d %H:%M:%S"))
    if started:
        out.append("Session: started %s, lasted %d min %02d s" % (
            datetime.datetime.fromtimestamp(started).strftime("%H:%M:%S"), duration // 60, duration % 60))
    verdict = {True: "The game appears to have CRASHED", False: "The game closed, with no sign of a crash",
               None: "Report made by hand"}[crashed]
    out.append("Verdict: %s" % verdict)
    if ev is not None and ev.reasons:
        out.append("Because: %s" % "; ".join(ev.reasons))
    out.append("Note: Steam starts the game, so there is no exit code. The verdict is read from what the game left behind.")

    likely = []
    f4se_lines, f4se_problems = f4se_facts(paths, version) if paths is not None else ([], [])
    likely += f4se_problems
    problems = order.problems() if order is not None else {}
    for name, issues in sorted(problems.items()):
        likely.append("Plugin %s: %s" % (name, "; ".join(issues)))
    f4se_log_hits = []
    if ev is not None and ev.f4se_dir:
        log = steam.child(ev.f4se_dir, "f4se.log")
        if log:
            f4se_log_hits = [l.strip() for l in _tail(log, 3000).splitlines() if _F4SE_BAD.search(l)][:25]
            for l in f4se_log_hits[:8]:
                likely.append("F4SE log: %s" % l)
    if order is not None:
        full = [e for e in order.entries if e.enabled and not e.is_light]
        if len(full) > 254:
            likely.append("%d full plugins are enabled; the game allows 254." % len(full))
    head("LIKELY CAUSES (%d)" % len(likely))
    out.extend(["- " + l for l in likely] or ["None found by the automatic checks. Read the crash log section below, if there is one,",
                                              "and consider the mods installed or updated most recently (marked NEW in the mod list)."])

    head("SYSTEM")
    out.append("%s" % app)
    distro = ""
    try:
        with open("/etc/os-release", "r", encoding="utf-8", errors="replace") as f:
            m = re.search(r'^PRETTY_NAME="?([^"\n]+)', f.read(), re.M)
            distro = m.group(1) if m else ""
    except OSError:
        pass
    out.append("System: %s, kernel %s, %s" % (distro or "unknown Linux", platform.release(), platform.machine()))
    try:
        with open("/proc/meminfo") as f:
            m = re.search(r"MemTotal:\s+(\d+)", f.read())
            out.append("Memory: %.1f GB" % (int(m.group(1)) / 1048576.0)) if m else None
    except OSError:
        pass
    out.append("Desktop: %s, session %s" % (os.environ.get("XDG_CURRENT_DESKTOP", "?"), os.environ.get("XDG_SESSION_TYPE", "?")))
    if paths is not None:
        out.append("Game: Fallout 4 %s" % (gamever.describe(version) or "(version not readable)"))
        out.append("Game folder: %s" % paths.game_dir.replace(home, "~"))
        prefix = paths.effective_prefix()
        out.append("Proton prefix: %s" % (prefix.replace(home, "~") if prefix else "NOT FOUND"))
        if prefix:
            for name in ("version", "config_info"):
                p = os.path.join(os.path.dirname(prefix), name)
                try:
                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        first = f.readline().strip()
                    out.append("Proton %s: %s" % (name, first.replace(home, "~")))
                except OSError:
                    pass
        out.extend(f4se_lines)
        ini_dir = paths.ini_dir()
        if ini_dir:
            s = gameini.load(ini_dir)
            out.append("Display: %d x %d, %s, vsync %s, field of view %d / %d" % (
                s.width, s.height, s.mode, "on" if s.vsync else "off", s.fov_world, s.fov_first))
    else:
        out.append("Game: not found")

    head("MODS, in order (later ones win). %d installed" % len(mgr.mods))
    conflicts = mgr.conflicts() if mgr.mods else {}
    for i, m in enumerate(mgr.mods, 1):
        new = ""
        try:
            if m.installed and started and (started - datetime.datetime.fromisoformat(m.installed).timestamp()) < 86400:
                new = "  NEW (installed in the last day)"
        except ValueError:
            pass
        c = conflicts.get(m.id, {"wins": [], "loses": []})
        out.append("%3d. [%s] %s%s" % (i, "x" if m.enabled else " ", m.name, new))
        detail = [x for x in (("file: %s" % m.file_name) if m.file_name else "", ("version %s" % m.version) if m.version else "",
                              m.origin, "%d files into %s" % (m.files, "game folder" if m.target == "root" else "Data"),
                              ("overrides %d files" % len(c["wins"])) if c["wins"] else "",
                              ("%d files overridden" % len(c["loses"])) if c["loses"] else "",
                              ("archive: %s" % m.source) if m.source else "") if x]
        out.append("       " + ", ".join(detail))

    head("LOAD ORDER")
    if order is None:
        out.append("(not available)")
    else:
        for i, e in enumerate(order.entries):
            issue = problems.get(e.name, [])
            kind = "master" if e.is_master else ("light" if e.is_light else "")
            out.append("%3d. [%s] %s%s%s" % (i, "x" if e.enabled else " ", e.name, "  (%s)" % kind if kind else "",
                                             "   <-- PROBLEM: " + "; ".join(issue) if issue else ""))
            if e.info.masters:
                out.append("       needs: %s" % ", ".join(e.info.masters))

    if ev is not None:
        for path in ev.crash_logs[:2]:
            head("CRASH LOG: %s" % os.path.basename(path))
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read(CRASH_LOG_LIMIT + 1)
                out.append(text[:CRASH_LOG_LIMIT] + ("\n... (cut: the file is longer)" if len(text) > CRASH_LOG_LIMIT else ""))
            except OSError as e:
                out.append("(could not be read: %s)" % e)
        if not ev.crash_logs:
            head("CRASH LOG")
            out.append("None. The game writes no crash log by itself. Installing the crash-logger mod \"Buffout 4\" "
                       "(needs F4SE) makes it write one that names the faulting module, and this report will include it.")
        if ev.dumps:
            head("CRASH DUMPS")
            out.extend(p.replace(home, "~") for p in ev.dumps[:5])
        head("PROTON LOG")
        if ev.proton_log:
            out.append("File: %s" % ev.proton_log.replace(home, "~"))
            out.append("-- error lines --")
            out.extend(ev.proton_errors or ["(none matched)"])
            out.append("-- last %d lines --" % 80)
            out.append(_tail(ev.proton_log, 80).replace(home, "~"))
        else:
            out.append("Not switched on. For much more detail next time: in Steam, right-click Fallout 4, Properties,")
            out.append("and set Launch Options to:   PROTON_LOG=1 %command%")
            out.append("Proton then writes ~/steam-%s.log on every run, and this report will include it." % steam.APP_ID)
        if ev.f4se_dir:
            for name in ("f4se_loader.log", "f4se.log"):
                p = steam.child(ev.f4se_dir, name)
                if p:
                    head("%s (last %d lines)" % (name, TAIL_LINES))
                    out.append(_tail(p).replace(home, "~"))

    if paths is not None and paths.plugins_txt() and os.path.exists(paths.plugins_txt()):
        head("Plugins.txt as the game reads it")
        out.append(_tail(paths.plugins_txt(), 600))
    if board:
        head("MAUSER ACTIVITY BOARD (last 60 lines)")
        out.append("\n".join(board.splitlines()[-60:]))
    out.append("")
    return "\n".join(out).replace(home, "~") if len(home) > 1 else "\n".join(out)      # no user name in a report that gets shared


def save_report(folder: str, text: str, ended: float, crashed: Optional[bool]) -> str:
    os.makedirs(folder, exist_ok=True)
    stamp = datetime.datetime.fromtimestamp(ended).strftime("%Y-%m-%d_%H.%M.%S")
    path = os.path.join(folder, "%s-%s.txt" % ("crash" if crashed else "report", stamp))
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class Watcher:
    """Follows game sessions. Call poll() every few seconds; it returns (started, ended) once when a session ends."""

    def __init__(self, probe=game_running, clock=time.time):
        self._probe, self._clock = probe, clock
        self.started: float = 0.0
        self.last_session: tuple = (0.0, 0.0)
        self._misses = 0

    @property
    def running(self) -> bool:
        return self.started > 0

    def poll(self) -> Optional[tuple]:
        alive = self._probe()
        if alive:
            self._misses = 0
            if not self.started:
                self.started = self._clock()
            return None
        if self.started:
            self._misses += 1
            if self._misses >= 2:              # gone on two looks in a row: it really ended
                self.last_session = (self.started, self._clock())
                self.started, self._misses = 0.0, 0
                return self.last_session
        return None
