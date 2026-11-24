"""Menu entry and link registration for the single-file app.

The folder install does this in install.sh. The single-file app has no
installer, so it registers itself the first time it runs and again whenever the
file has been moved.
"""
from __future__ import annotations

import os
import shutil
import subprocess

ENTRY_NAME = "mauser.desktop"
OLD_ENTRY_NAMES = ("der-modifikationsmann.desktop",)


def _apps_dir() -> str:
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(data_home, "applications")


def entry_text(exe: str, icon: str, nxm: bool = True) -> str:
    return "\n".join([
        "[Desktop Entry]", "Type=Application", "Name=Mauser",
        "Comment=Install and manage Fallout 4 mods", 'Exec="%s" %%u' % exe, "Icon=%s" % icon, "Terminal=false",
        "Categories=Game;Utility;"] + (["MimeType=x-scheme-handler/nxm;"] if nxm else []) + ["StartupWMClass=mauser", ""])


def register(exe: str, icon_source: str, data_dir: str, nxm: bool = True) -> str:
    """Create or refresh the menu entry for `exe` and make it the handler for
    Nexus download links. Returns a short description of what changed ('' if nothing)."""
    if any(c in exe for c in '"`$\\\n'):
        return "The app's location contains a character menu entries cannot handle; it was not added to the menu."
    icon = os.path.join(data_dir, "mauser-icon-2.svg")       # new name per redesign, so desktops drop the cached old picture
    try:
        os.makedirs(data_dir, exist_ok=True)
        for stale in ("icon.svg",):
            try:
                os.unlink(os.path.join(data_dir, stale))
            except OSError:
                pass
        if os.path.isfile(icon_source):
            shutil.copyfile(icon_source, icon)
        apps = _apps_dir()
        os.makedirs(apps, exist_ok=True)
        for name in OLD_ENTRY_NAMES:                  # the entry from before the rename
            try:
                os.unlink(os.path.join(apps, name))
            except OSError:
                pass
        path = os.path.join(apps, ENTRY_NAME)
        text = entry_text(exe, icon, nxm)
        old = ""
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                old = f.read()
        if old == text:
            return ""
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(path, 0o755)
    except OSError as e:
        return "Could not add the app to the menu: %s" % e
    for cmd in [["update-desktop-database", apps]] + ([["xdg-mime", "default", ENTRY_NAME, "x-scheme-handler/nxm"]] if nxm else []):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
            except (OSError, subprocess.SubprocessError):
                pass
    return ("Added to the application menu and set as the app for Nexus download links." if nxm
            else "Added to the application menu.")
