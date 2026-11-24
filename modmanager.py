#!/usr/bin/env python3
"""Mauser (formerly Der Modifikationsmann): a native Linux mod manager for Fallout 4 running under Proton.

    python3 modmanager.py              start the app
    python3 modmanager.py --diagnose   print what it detected about your install
    python3 modmanager.py nxm://...    install from a Nexus "Mod manager download" link
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
FROZEN = bool(getattr(sys, "frozen", False))            # running as the single-file app
RESOURCES = getattr(sys, "_MEIPASS", HERE)              # where icon.svg lives


def _restore_child_environment() -> None:
    """The single-file app runs with private library and plugin paths. Programs it
    starts (the web browser, Steam, the file manager) must not inherit them, or they
    can fail to start. This puts the normal environment back for those programs; the
    app itself has already loaded what it needs."""
    original = os.environ.get("LD_LIBRARY_PATH_ORIG")
    if original:
        os.environ["LD_LIBRARY_PATH"] = original
    else:
        os.environ.pop("LD_LIBRARY_PATH", None)
    for var in ("QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QTWEBENGINEPROCESS_PATH"):
        os.environ.pop(var, None)
    packed = getattr(sys, "_MEIPASS", "")
    if packed and os.environ.get("PATH"):
        os.environ["PATH"] = os.pathsep.join(p for p in os.environ["PATH"].split(os.pathsep) if p != packed)


def _selftest(app) -> int:
    """Checks that matter for the packaged app; prints a short report."""
    import subprocess
    from PySide6.QtGui import QImageReader
    import archives
    import net
    import theme
    out = []
    out.append("packaged: %s" % FROZEN)
    formats = sorted(bytes(f).decode() for f in QImageReader.supportedImageFormats())
    out.append("image formats: %s" % " ".join(formats))
    out.append("fonts loaded: %s" % (", ".join(theme.load_fonts(RESOURCES)) or "none"))
    out.append("7-Zip: %s" % archives.find_7zip())
    out.append("certificates: %d" % net.tls_context().cert_store_stats().get("x509_ca", 0))
    try:
        env = subprocess.run(["env"], stdout=subprocess.PIPE, timeout=10).stdout.decode("utf-8", "replace")
        leaked = [line.split("=")[0] for line in env.splitlines()
                  if line.startswith(("LD_LIBRARY_PATH=", "QT_PLUGIN_PATH=", "QML2_IMPORT_PATH=")) and "_MEI" in line]
        out.append("private paths passed to other programs: %s" % (", ".join(leaked) or "none"))
    except (OSError, subprocess.SubprocessError) as e:
        out.append("could not check child environment: %s" % e)
    sys.__stdout__.write("\n".join(out) + "\n")
    return 0


def _log_to_file(base_dir: str) -> None:
    """The single-file app has no launcher script, so it keeps its own log."""
    try:
        os.makedirs(base_dir, exist_ok=True)
        path = os.path.join(base_dir, "modmanager.log")
        if os.path.exists(path) and os.path.getsize(path) > 1000000:
            os.unlink(path)
        log = open(path, "a", buffering=1, encoding="utf-8", errors="replace")
        sys.stdout = sys.stderr = log
    except OSError:
        pass


def _other_copies() -> list:
    """Process ids of other running copies of this app that belong to this user."""
    mine, parent, uid, found = os.getpid(), os.getppid(), os.getuid(), []
    names = ("mauser", "dermodifikationsmann")
    try:
        pids = [int(p) for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return found
    for pid in pids:
        if pid in (mine, parent):
            continue
        try:
            if os.stat("/proc/%d" % pid).st_uid != uid:
                continue
            exe = os.path.basename(os.readlink("/proc/%d/exe" % pid)).lower()
            with open("/proc/%d/cmdline" % pid, "rb") as f:
                args = f.read(2000).split(b"\0")
        except OSError:
            continue
        script = any(os.path.basename(a) == b"modmanager.py" for a in args[:3])
        if exe.split(" ")[0] in names or script:
            found.append(pid)
    return found


def _stop_copies(pids: list) -> None:
    import signal
    import time
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in pids:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        end = time.time() + 1.5
        while time.time() < end and any(os.path.exists("/proc/%d" % p) for p in pids):
            time.sleep(0.1)
        pids = [p for p in pids if os.path.exists("/proc/%d" % p)]
        if not pids:
            return


def run_gui() -> int:
    try:
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("PySide6 is missing. Run install.sh in %s" % HERE)
        return 1
    from common import APP_NAME
    from core import Manager, default_base_dir
    from gui import MainWindow


    from PySide6.QtCore import QTimer
    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    if not os.environ.get("QT_QPA_PLATFORM") and os.environ.get("WAYLAND_DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "wayland;xcb"          # if Wayland cannot be used, fall back to X11 rather than not starting
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName("mauser")
    if FROZEN:
        app.setLibraryPaths(app.libraryPaths())     # fix Qt's own plugin locations before clearing the variables
        _restore_child_environment()
    if "--selftest" in sys.argv:
        return _selftest(app)
    if FROZEN:
        _log_to_file(default_base_dir())

    # Links ("Mod manager download" on Nexus) and files passed on the command line.
    incoming = [a for a in sys.argv[1:] if a.lower().startswith("nxm://") or os.path.exists(a)]
    channel = "mauser-%d" % os.getuid()
    probe = QLocalSocket()
    probe.connectToServer(channel)
    if probe.waitForConnected(700):
        # Already running: hand the link over to that window, which answers and comes to the front.
        probe.write(("\n".join(incoming) or "show").encode("utf-8"))
        probe.flush()
        probe.waitForBytesWritten(2000)
        answered = probe.waitForReadyRead(2500) and bytes(probe.readAll()).startswith(b"ok")
        probe.disconnectFromServer()
        if answered:
            return 0
        # Something holds the place of a running copy but does not answer: an older version, or a copy
        # that is stuck with no window. Starting silently "into" it is why the app could seem not to open.
        from PySide6.QtWidgets import QMessageBox
        others = _other_copies()
        if os.environ.get("MAUSER_REPLACE") != "1" and QMessageBox.question(
                None, APP_NAME, "Another copy of %s is already running but is not answering. It may be an older "
                "version, or one that is stuck without a window.\n\nClose it and start this one?" % APP_NAME
        ) != QMessageBox.StandardButton.Yes:
            return 0
        _stop_copies(others)
    QLocalServer.removeServer(channel)
    server = QLocalServer()
    server.listen(channel)
    icon = os.path.join(RESOURCES, "icon.svg")
    if os.path.exists(icon):
        # Drawn at fixed sizes here, so the window icon does not depend on an SVG icon plug-in being present.
        from PySide6.QtGui import QImage, QPixmap
        with open(icon, "r", encoding="utf-8") as f:
            source = f.read()
        window_icon = QIcon()
        for n in (16, 22, 24, 32, 48, 64, 128, 256):
            image = QImage.fromData(source.replace('width="128" height="128"', 'width="%d" height="%d"' % (n, n), 1).encode("utf-8"), "SVG")
            if not image.isNull():
                window_icon.addPixmap(QPixmap.fromImage(image))
        app.setWindowIcon(window_icon if not window_icon.isNull() else QIcon(icon))
    manager = Manager()
    from gui import apply_theme
    apply_theme(app, manager, RESOURCES)
    win = MainWindow(manager)
    win.show()
    if not manager.settings.get("setup_done"):
        QTimer.singleShot(250, win.run_setup)
    if FROZEN:
        import desktop
        from common import nexus_enabled
        note = desktop.register(os.path.abspath(sys.executable), icon, default_base_dir(), nexus_enabled(manager.settings))
        if note:
            QTimer.singleShot(0, lambda: win.say(note))

    def receive():
        conn = server.nextPendingConnection()
        if conn is None:
            return
        conn.waitForReadyRead(1500)
        text = bytes(conn.readAll()).decode("utf-8", "replace")[:8000]
        conn.write(b"ok")
        conn.flush()
        conn.waitForBytesWritten(1000)
        conn.disconnectFromServer()
        # a second start means "show me the app": bring the window back, wherever it went
        win.showNormal()
        win.raise_()
        win.activateWindow()
        if text.strip() and text.strip() != "show":
            QTimer.singleShot(0, lambda: win.handle_message(text))
        else:
            win.say("Started again: this window was already open.")

    server.newConnection.connect(receive)
    if incoming:
        QTimer.singleShot(600, lambda: win.handle_message("\n".join(incoming)))
    code = app.exec()
    server.close()
    return code


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--diagnose":
        from common import diagnostics
        from core import Manager
        print(diagnostics(Manager()))
        sys.exit(0)
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    try:
        code = run_gui()
    except Exception:
        # Say why on the real error stream too: the launcher shows that when a start fails.
        import traceback
        text = traceback.format_exc()
        for stream in {sys.stderr, sys.__stderr__}:
            try:
                stream.write("Mauser could not start:\n" + text)
                stream.flush()
            except Exception:
                pass
        code = 1
    sys.exit(code)
