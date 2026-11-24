"""Small helpers shared by the command line and the interface."""
from __future__ import annotations

import os
import re
import shutil

import steam
from core import Manager

APP_NAME = "Mauser"
APP_VERSION = "1.0.1"
SWAP_MOD_NAME = "Start game through F4SE"
SWAP_SOURCE = "(made by Mauser)"
OLD_SWAP_SOURCES = ("(made by Der Modifikationsmann)",)      # from before the app was renamed


def clean_mod_name(filename: str) -> str:
    """'Cool Mod-12345-1-2-1699999999.7z' -> 'Cool Mod' (drops the Nexus id suffix)."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    stem = re.sub(r"-\d+(-[A-Za-z0-9]+)*-\d{9,}$", "", stem)
    return stem.replace("_", " ").strip() or "Unnamed mod"


# The master switch for everything that talks to Nexus Mods. It is False while the app has no approval from
# Nexus Mods to use their API in a public release. To bring the features back, set this to True: nothing else
# was removed, and keys people had already entered are still in their settings.
NEXUS_AVAILABLE = False

NEXUS_UNAVAILABLE_TEXT = ("Nexus Mods support is switched off in this version while %s waits for approval from Nexus Mods. "
                          "Download mods in your web browser and install them from the file." % APP_NAME)

NEXUS_OFF_TEXT = ("The Nexus Mods features (browsing, one-click installs, update checks) are experimental and switched off. "
                  "They can be switched on in Settings.")


def nexus_enabled(settings: dict) -> bool:
    """Nexus Mods features are opt-in: they use each person's own API key, an arrangement Nexus Mods has not approved
    for public apps. Installs that already had a key entered before this switch existed keep working."""
    if not NEXUS_AVAILABLE:
        return False
    if "nexus_enabled" not in settings:
        settings["nexus_enabled"] = bool(settings.get("nexus_key"))
    return bool(settings["nexus_enabled"])


def nexus_key(settings: dict) -> str:
    return str(settings.get("nexus_key", "")) if nexus_enabled(settings) else ""


def diagnostics(mgr: Manager) -> str:
    lines = ["%s %s diagnostics" % (APP_NAME, APP_VERSION)]
    lines.append("Log file: %s" % os.path.join(mgr.base_dir, "modmanager.log"))
    lines.append("Steam libraries found: %s" % (", ".join(steam.steam_libraries()) or "none"))
    lines.append("Game folder: %s" % (mgr.game_dir or "not set"))
    p = mgr.paths
    if p is not None:
        lines.append("  looks like Fallout 4: %s" % steam.looks_like_game(p.game_dir))
        lines.append("  Data folder: %s" % p.data_dir)
        lines.append("  Proton prefix: %s%s" % (p.effective_prefix() or "NOT FOUND",
                                               " (set by hand)" if mgr.prefix else ""))
        txt = p.plugins_txt()
        lines.append("  Plugins.txt: %s%s" % (txt or "NOT FOUND", "" if not txt or os.path.exists(txt)
                                             else " (will be created)"))
        ini = p.custom_ini()
        state = ""
        if ini and os.path.exists(ini):
            try:
                with open(ini, "r", encoding="utf-8", errors="replace") as f:
                    state = " (loose files %s)" % ("enabled" if steam.loose_files_enabled(f.read()) else "NOT enabled")
            except OSError:
                state = " (unreadable)"
        elif ini:
            state = " (will be created)"
        lines.append("  Fallout4Custom.ini: %s%s" % (ini or "NOT FOUND", state))
        lines.append("  F4SE present: %s" % p.has_f4se())
    lines.append("Nexus Mods features: %s" % ("not available in this version" if not NEXUS_AVAILABLE else "%s, API key %s" % (
        "on" if nexus_enabled(mgr.settings) else "off", "entered" if mgr.settings.get("nexus_key") else "not entered")))
    handler = ""
    if shutil.which("xdg-mime"):
        try:
            import subprocess
            handler = subprocess.run(["xdg-mime", "query", "default", "x-scheme-handler/nxm"], stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, timeout=5).stdout.decode("utf-8", "replace").strip()
        except (OSError, subprocess.SubprocessError):
            handler = ""
    lines.append("Nexus download button opens: %s" % (handler or "unknown"))
    lines.append("Mod library: %s" % mgr.mods_dir)
    lines.append("  mods: %d (%d enabled), files deployed: %d" % (
        len(mgr.mods), sum(1 for m in mgr.mods if m.enabled), mgr.deployed_count()))
    lines.append("  files are placed with: %s" % (mgr.link_method_hint() if mgr.game_dir else "unknown"))
    import archives
    tool = archives.find_7zip()
    other = [t for t in ("bsdtar", "unrar", "unar") if shutil.which(t)]
    if tool:
        support = "7z and rar via %s" % tool
    else:
        try:
            import importlib.util
            if importlib.util.find_spec("py7zr") is None:
                raise ImportError
            seven = "7z yes (slower built-in reader)"
        except ImportError:
            seven = "7z NO"
        support = "%s, rar %s" % (seven, "via " + other[0] if other else "NO")
        support += "  (run install.sh again to add 7-Zip)"
    lines.append("Archive support: zip yes, %s" % support)
    return "\n".join(lines)
