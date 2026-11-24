"""Interface for Mauser: main window, guided-installer dialog, settings."""
from __future__ import annotations

import os
import shutil
import sys
import time
import traceback
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QEventLoop, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QMenu, QInputDialog,
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QProgressDialog, QPushButton, QRadioButton, QScrollArea, QSplitter,
    QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

import archives
import crashwatch
import fomod
import presence
import modlist
import re
import gamever
import net
import providers as prov
import steam
import theme
from browse import BrowseTab, human_size
from gamepage import GameSettingsTab
import savefile
import dialogs
import profiles
import tools
from core import Manager, Mod
from loadorder import LoadOrder
from common import APP_NAME, APP_VERSION, SWAP_MOD_NAME, SWAP_SOURCE, OLD_SWAP_SOURCES, clean_mod_name, diagnostics, nexus_enabled, nexus_key


class _Task(QThread):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn
        self.value = None
        self.error: Optional[BaseException] = None

    def run(self):
        try:
            self.value = self.fn()
        except BaseException as e:      # reported to the caller on the main thread
            self.error = e

def run_busy(parent, label: str, fn: Callable):
    """Run fn off the main thread and wait for it; returns its result or raises.

    In the main window the wait is shown on the progress strip and the window stays usable:
    you can keep browsing, and anything that would clash with the job is queued or declined
    (see MainWindow._guard). Elsewhere (Settings, first-time setup) it is a 'please wait' box."""
    task = _Task(fn)
    mascot = getattr(parent, "mascot", None)
    if mascot is not None:
        mascot.set_state("work")
    strip = hasattr(parent, "progress_row") and hasattr(parent, "_in_loop")
    box = None
    if strip:
        parent._strip_use += 1
        parent.progress_label.setText(label)
        parent.progress_bar.setRange(0, 0)
        parent.progress_bar.setFormat("")
        parent.progress_cancel.hide()
        parent.progress_row.show()
        parent._in_loop += 1
    else:
        box = QProgressDialog(label, "", 0, 0, parent)
        box.setCancelButton(None)
        box.setWindowTitle(APP_NAME)
        box.setWindowModality(Qt.WindowModality.WindowModal)
        box.setMinimumDuration(400)
    loop = QEventLoop()
    task.finished.connect(loop.quit)
    task.start()
    try:
        if task.isRunning():
            loop.exec()
        task.wait()
    finally:
        if strip:
            parent._in_loop -= 1
            parent.progress_cancel.show()
            parent.progress_row.hide()
        else:
            box.reset()
            box.deleteLater()
    if mascot is not None:
        mascot.set_state("error" if task.error is not None else "idle")
    if task.error is not None:
        raise task.error
    return task.value


class OrderTree(QTreeWidget):
    """A list whose rows can be dragged into a new order."""
    reordered = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QTreeWidget.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(True)

    def dropEvent(self, event):
        super().dropEvent(event)
        QTimer.singleShot(0, self.reordered.emit)       # rebuild only after Qt has finished the drop


class Mascot(theme.RatLabel):
    """Vinny beside the activity board. He shows what the app is doing: idle, work (running), done or error.
    'done' and 'error' go back to idle by themselves after a few seconds."""

    def __init__(self, size: int = 92, parent=None):
        super().__init__(size, parent)
        self.setFixedSize(size, size)
        self._back = QTimer(self)
        self._back.setSingleShot(True)
        self._back.timeout.connect(lambda: self.set_state("idle"))

    def set_state(self, state: str, hold_ms: int = 0) -> None:
        if state not in theme.RAT_STATES:
            state = "idle"
        self._back.stop()
        if state in ("done", "error"):
            self._back.start(hold_ms or (4000 if state == "done" else 8000))
        if state != self.state:
            self.state = state
            self.refresh()

    def refresh(self) -> None:
        super().refresh()
        self.setToolTip({"idle": "%s is waiting. Nothing going on.", "work": "%s is working on it…", "done": "%s says: done.",
                         "error": "%s hit a problem. The activity board says what."}[self.state] % theme.MASCOT_NAME)


def alert(parent, title: str, text: str, serious: bool = False) -> None:
    """A message box for bad news, with the worried rat instead of the stock icon."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    pix = theme.rat_pixmap("error", 72, parent.devicePixelRatioF() if parent is not None else 1.0)
    if pix.isNull():
        box.setIcon(QMessageBox.Icon.Critical if serious else QMessageBox.Icon.Warning)
    else:
        box.setIconPixmap(pix)
    mascot = getattr(parent, "mascot", None)
    if mascot is not None:
        mascot.set_state("error")
    box.exec()

def apply_theme(app, mgr: Manager, resources: str = "") -> None:
    """Load the fonts (first call) and set the app-wide stylesheet for the chosen theme and tile colour."""
    if resources:
        theme.load_fonts(resources)
        app.setStyle("Fusion")
    theme.use(str(mgr.settings.get("theme", theme.DEFAULT_THEME)), str(mgr.settings.get("tile", theme.DEFAULT_TILE)))
    app.setStyleSheet(theme.stylesheet(theme.write_arrow(mgr.cache_dir), theme.write_tick(mgr.cache_dir)))


class FomodDialog(QDialog):
    """Shows a guided installer's steps and records the choices in a fomod.Session."""

    def __init__(self, parent, session: fomod.Session, base: str, title: str):
        super().__init__(parent)
        self.session = session
        self.base = base
        self.setWindowTitle("Install options: %s" % title)
        self.resize(900, 620)
        self.step = -1
        self.buttons_for: list = []          # (group index, [(option index, widget)], none button)
        lay = QVBoxLayout(self)
        self.heading = QLabel()
        self.heading.setObjectName("title")
        lay.addWidget(self.heading)
        body = QHBoxLayout()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        body.addWidget(self.scroll, 3)
        side = QVBoxLayout()
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(300, 200)
        self.desc = QLabel()
        self.desc.setWordWrap(True)
        self.desc.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.desc.setTextFormat(Qt.TextFormat.PlainText)
        side.addWidget(self.image)
        side.addWidget(self.desc, 1)
        body.addLayout(side, 2)
        lay.addLayout(body, 1)
        row = QHBoxLayout()
        self.back = QPushButton("Back")
        self.next = QPushButton("Next")
        self.next.setProperty("sign", "yellow")
        cancel = QPushButton("Cancel")
        self.back.clicked.connect(lambda: self.go(-1))
        self.next.clicked.connect(lambda: self.go(1))
        cancel.clicked.connect(self.reject)
        row.addStretch(1)
        for b in (self.back, self.next, cancel):
            row.addWidget(b)
        lay.addLayout(row)
        first = self.session.next_visible(-1)
        if first is None:
            QTimer.singleShot(0, self.accept)
        else:
            self.show_step(first)

    def show_step(self, index: int) -> None:
        self.step = index
        step = self.session.fm.steps[index]
        flags = self.session.flags_before(index)
        self.heading.setText(step.name or "Options")
        holder = QWidget()
        v = QVBoxLayout(holder)
        self.buttons_for = []
        first_option = None
        for gi, group in enumerate(step.groups):
            box = QGroupBox(group.name)
            bl = QVBoxLayout(box)
            chosen = self.session.selection(index, gi, flags)
            single = group.kind in ("SelectExactlyOne", "SelectAtMostOne")
            exclusive = QButtonGroup(box) if single else None
            widgets = []
            none_btn = None
            if group.kind == "SelectAtMostOne":
                none_btn = QRadioButton("None")
                none_btn.setChecked(not chosen)
                exclusive.addButton(none_btn)
                bl.addWidget(none_btn)
            for oi, opt in enumerate(group.options):
                kind = fomod.option_type(opt, flags, self.session.file_state)
                w = QRadioButton(opt.name) if single else QCheckBox(opt.name)
                if exclusive is not None:
                    exclusive.addButton(w)
                w.setChecked(oi in chosen)
                if group.kind == "SelectAll" or (kind == "Required" and not single):
                    w.setChecked(True)
                    w.setEnabled(False)
                elif kind == "NotUsable":
                    w.setChecked(False)
                    w.setEnabled(False)
                if kind == "Recommended":
                    w.setText(opt.name + "  (recommended)")
                w.setProperty("opt", (gi, oi))
                w.installEventFilter(self)
                w.toggled.connect(lambda _on, g=gi, o=oi: self.describe(g, o))
                bl.addWidget(w)
                widgets.append((oi, w))
                if first_option is None:
                    first_option = (gi, oi)
            self.buttons_for.append((gi, widgets, none_btn))
            v.addWidget(box)
        v.addStretch(1)
        self.scroll.setWidget(holder)
        self.back.setEnabled(self.session.next_visible(index, -1) is not None)
        self.next.setText("Next")
        if first_option:
            self.describe(*first_option)
        else:
            self.desc.setText("")
            self.image.clear()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Enter:
            ref = obj.property("opt")
            if ref:
                self.describe(ref[0], ref[1])
        return super().eventFilter(obj, event)

    def describe(self, gi: int, oi: int) -> None:
        opt = self.session.fm.steps[self.step].groups[gi].options[oi]
        self.desc.setText(opt.description)
        self.image.clear()
        if opt.image:
            path = fomod._resolve(self.base, opt.image)
            if path and os.path.isfile(path):
                pix = QPixmap(path)
                if not pix.isNull():
                    self.image.setPixmap(pix.scaled(360, 260, Qt.AspectRatioMode.KeepAspectRatio,
                                                    Qt.TransformationMode.SmoothTransformation))

    def store(self) -> bool:
        step = self.session.fm.steps[self.step]
        for gi, widgets, _none in self.buttons_for:
            chosen = {oi for oi, w in widgets if w.isChecked()}
            if not fomod.valid_selection(step.groups[gi], chosen):
                need = {"SelectExactlyOne": "exactly one option", "SelectAtLeastOne": "at least one option",
                        "SelectAtMostOne": "no more than one option"}.get(step.groups[gi].kind, "a valid choice")
                QMessageBox.information(self, "Install options", "\"%s\" needs %s." % (step.groups[gi].name, need))
                return False
            self.session.choices[(self.step, gi)] = chosen
        # choices after this step may depend on it; let them fall back to their defaults
        for key in [k for k in self.session.choices if k[0] > self.step]:
            del self.session.choices[key]
        return True

    def go(self, direction: int) -> None:
        if direction > 0 and not self.store():
            return
        target = self.session.next_visible(self.step, direction)
        if target is None:
            if direction > 0:
                self.accept()
            return
        self.show_step(target)

class SettingsDialog(QDialog):
    def __init__(self, parent, mgr: Manager):
        super().__init__(parent)
        self.mgr = mgr
        self.setWindowTitle("Settings")
        self.resize(820, 680)
        self.purge_requested = False
        self.setup_requested = False
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.game = QLineEdit(mgr.game_dir)
        self.prefix = QLineEdit(mgr.prefix)
        self.prefix.setPlaceholderText("Found automatically. Only set this if detection fails.")
        form.addRow("Fallout 4 folder", self._with_buttons(self.game, [("Browse…", lambda: self._browse(self.game)),
                                                                          ("Find", self._detect)]))
        form.addRow("Proton prefix", self._with_buttons(self.prefix, [("Browse…", lambda: self._browse(self.prefix))]))
        self.mode = QComboBox()
        for key, text in (("auto", "Automatic (hard links, falling back to symlinks, then copies)"),
                          ("symlink", "Symlinks"), ("copy", "Copies (uses more disk space)")):
            self.mode.addItem(text, key)
        self.mode.setCurrentIndex(max(0, self.mode.findData(mgr.link_mode)))
        form.addRow("Place mod files using", self.mode)
        self.theme_box = QComboBox()
        self.theme_box.setMaxVisibleItems(14)
        for key, t in theme.THEMES.items():
            self.theme_box.addItem(t["label"], key)
        self.theme_box.setCurrentIndex(max(0, self.theme_box.findData(str(mgr.settings.get("theme", theme.DEFAULT_THEME)))))
        form.addRow("Theme", self.theme_box)
        self.tile = QComboBox()
        for key in theme.TILES:
            self.tile.addItem(key.capitalize(), key)
        self.tile.setCurrentIndex(max(0, self.tile.findData(str(mgr.settings.get("tile", theme.DEFAULT_TILE)))))
        form.addRow("Tile colour", self.tile)
        self.tile.setToolTip("The coloured band of the Bahnhof themes")
        self.theme_box.currentIndexChanged.connect(
            lambda _i: self.tile.setEnabled(str(self.theme_box.currentData()).startswith("bahnhof")))
        self.tile.setEnabled(str(self.theme_box.currentData()).startswith("bahnhof"))
        self.nexus_on = QCheckBox("Switch on the experimental Nexus Mods features (browse, one-click install, update checks)")
        self.nexus_on.setChecked(nexus_enabled(mgr.settings))
        self.nexus_on.setToolTip("These use your own Nexus Mods API key. Nexus Mods has not yet approved this app, and its rules "
                                 "allow personal keys only for personal use and testing, so it may stop working.")
        form.addRow("Nexus Mods", self.nexus_on)
        self.nexus_key = QLineEdit(str(mgr.settings.get("nexus_key", "")))
        self.nexus_on.toggled.connect(self.nexus_key.setEnabled)
        self.nexus_key.setEnabled(self.nexus_on.isChecked())
        self.nexus_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.nexus_key.setPlaceholderText("Personal API key from your Nexus Mods account settings")
        form.addRow("Nexus Mods API key", self._with_buttons(self.nexus_key, [("Check", self._check_key)]))
        self.key_status = QLabel(prov.NexusProvider.key_help)
        self.key_status.setWordWrap(True)
        form.addRow("", self.key_status)
        self.discord_on = QCheckBox("Show what I am doing in Discord (Rich Presence)")
        self.discord_on.setChecked(bool(mgr.settings.get("discord_on")))
        form.addRow("Discord", self.discord_on)
        self.discord_save = QCheckBox("While playing, include my character's name, level, place and hours played")
        self.discord_save.setChecked(bool(mgr.settings.get("discord_save", True)))
        form.addRow("", self.discord_save)
        self.discord_id = QLineEdit(str(mgr.settings.get("discord_id", "")))
        self.discord_id.setPlaceholderText("Application ID from discord.com/developers")
        self.discord_id.setAccessibleName("Discord application ID")
        form.addRow("Discord application ID", self.discord_id)
        note = QLabel("Discord shows the name of the application the ID belongs to, as \"Playing <name>\". Mod names are never sent. "
                      + ("Now: %s." % parent.presence.status if getattr(parent, "presence", None) is not None else ""))
        note.setWordWrap(True)
        form.addRow("", note)
        lay.addLayout(form)
        self.report = QPlainTextEdit(diagnostics(mgr))
        self.report.setReadOnly(True)
        lay.addWidget(QLabel("What was detected:"))
        lay.addWidget(self.report, 1)
        row = QHBoxLayout()
        copy = QPushButton("Copy report")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.report.toPlainText()))
        purge = QPushButton("Remove all mod files from the game…")
        purge.clicked.connect(self._purge)
        about = QPushButton("About")
        about.clicked.connect(self._about)
        row.addWidget(about)
        again = QPushButton("Run first-time setup again")
        again.clicked.connect(lambda: (setattr(self, "setup_requested", True), self.reject()))
        row.addWidget(copy)
        row.addWidget(purge)
        row.addWidget(again)
        row.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        row.addWidget(buttons)
        lay.addLayout(row)

    @staticmethod
    def _with_buttons(edit, buttons):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(edit, 1)
        for text, slot in buttons:
            b = QPushButton(text)
            b.clicked.connect(slot)
            h.addWidget(b)
        return w

    def _browse(self, edit) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose folder", edit.text() or os.path.expanduser("~"))
        if folder:
            edit.setText(folder)

    def _detect(self) -> None:
        found = steam.find_game_dirs()
        if found:
            self.game.setText(found[0])
        else:
            QMessageBox.information(self, "Find game", "Fallout 4 was not found in your Steam libraries. "
                                                       "Use Browse and pick the folder that contains Fallout4.exe.")

    def _about(self) -> None:
        """Version, what the app is, and the licences of what it contains."""
        folder = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        try:
            with open(os.path.join(folder, "THIRD-PARTY-NOTICES.md"), "r", encoding="utf-8") as f:
                notices = f.read()
        except OSError:
            notices = "The notices file is missing from this copy. See THIRD-PARTY-NOTICES.md where you got the app."
        dlg = QDialog(self)
        dlg.setWindowTitle("About %s" % APP_NAME)
        dlg.resize(720, 560)
        lay = QVBoxLayout(dlg)
        head = QLabel("<b>%s %s</b><br>A mod manager for Fallout 4 on Linux, for the Steam version running through Proton."
                      % (APP_NAME, APP_VERSION))
        head.setWordWrap(True)
        lay.addWidget(head)
        text = QPlainTextEdit(notices)
        text.setReadOnly(True)
        lay.addWidget(text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        self._about_dialog = dlg
        dlg.exec()

    def _check_key(self) -> None:
        key = self.nexus_key.text().strip()
        if not key:
            self.key_status.setText(prov.NexusProvider.key_help)
            return
        try:
            user = run_busy(self, "Checking the key with Nexus Mods…", prov.NexusProvider(key).validate)
        except prov.ProviderError as e:
            self.key_status.setText(str(e))
            return
        self.key_status.setText("Signed in as %s. %s" % (
            user.get("name") or "unknown", "Premium account: installs are one click." if user.get("is_premium")
            else "Free account: installs go through the download button on the Nexus page."))

    def _purge(self) -> None:
        if QMessageBox.question(self, "Remove all mod files",
                                "This takes every file this app placed out of the game folder and puts back "
                                "any game files it set aside. Your mod library is kept, and mods are disabled "
                                "so they are not put straight back.\n\nContinue?") == QMessageBox.StandardButton.Yes:
            self.purge_requested = True
            self._accept()

    def _accept(self) -> None:
        game = self.game.text().strip()
        if game:
            game = steam.normalize_game_dir(game)
            if not steam.looks_like_game(game):
                alert(self, "Settings", "That folder does not contain Fallout4.exe or Data/Fallout4.esm.")
                return
        prefix = self.prefix.text().strip()
        if prefix and not (os.path.isdir(prefix) and (steam.child(prefix, "drive_c")
                                                       or steam.descend(prefix, "pfx", "drive_c"))):
            alert(self, "Settings", "That prefix folder has no drive_c inside it. It is normally "
                                                  ".../steamapps/compatdata/377160/pfx")
            return
        self.result_game, self.result_prefix, self.result_mode = game, prefix, self.mode.currentData()
        self.result_key = self.nexus_key.text().strip()
        self.result_nexus_on = self.nexus_on.isChecked()
        self.result_tile = self.tile.currentData()
        self.result_theme = self.theme_box.currentData()
        self.result_discord = (self.discord_on.isChecked(), self.discord_id.text().strip(), self.discord_save.isChecked())
        self.accept()

class MainWindow(QMainWindow):
    def __init__(self, mgr: Manager):
        super().__init__()
        self.mgr = mgr
        self.order: Optional[LoadOrder] = None
        self._filling = False
        # While a job waits on a download or on work in the background, the window stays usable.
        # _in_loop counts those waits; a guarded action that arrives during one came from the user.
        self._in_loop = 0
        self._strip_use = 0
        self._depth = 0
        self._pending: list = []
        for name in ("install_from_provider", "install_requirements", "install_many_from_browser", "handle_message",
                     "_install_many", "install_archives", "install_folder"):
            setattr(self, name, self._guard(getattr(self, name), name, queue=True))
        for name in ("remove_mod", "move_mod", "toggle_pin_mod", "update_selected", "check_updates", "load_mod_list",
                     "switch_profile", "new_profile", "rename_profile", "delete_profile", "open_tools", "toggle_f4se",
                     "open_settings", "run_setup", "choose_risk", "_mods_dragged", "_apply_mod_change"):
            setattr(self, name, self._guard(getattr(self, name), name, queue=False))
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 800)
        self.setAcceptDrops(True)

        # Header: the name plate, the section signs, and the two things you do most.
        header = self.header = theme.Backdrop()
        hl = QHBoxLayout(header)
        hl.setContentsMargins(24, 16, 24, 16)
        hl.setSpacing(18)
        plate = QFrame()
        plate.setObjectName("plate")
        pl = QHBoxLayout(plate)
        pl.setContentsMargins(12, 4, 18, 4)
        pl.setSpacing(10)
        self.plate_face = QLabel()
        self.plate_face.setPixmap(theme.mark_pixmap(40, self.devicePixelRatioF()))
        pl.addWidget(self.plate_face)
        pl.addWidget(QLabel(APP_NAME))
        hl.addWidget(plate)
        nav = QHBoxLayout()
        nav.setSpacing(0)
        self.nav_buttons = []
        for i, (text, end) in enumerate((("Installed", "first"), ("Browse", ""), ("Game settings", ""))):
            b = QPushButton(text)
            b.setProperty("sign", "nav")
            b.setProperty("navend", end)
            b.setCheckable(True)
            b.setChecked(i == 0)
            b.clicked.connect(lambda _c=False, index=i: self._go_to(index))
            nav.addWidget(b)
            self.nav_buttons.append(b)
        settings_btn = QPushButton("Settings")
        settings_btn.setProperty("sign", "nav")
        settings_btn.setProperty("navend", "last")
        settings_btn.clicked.connect(self.open_settings)
        nav.addWidget(settings_btn)
        hl.addLayout(nav)
        hl.addStretch(1)
        self.f4se_action = QPushButton("F4SE  OFF")
        self.f4se_action.setProperty("sign", "board")
        self.f4se_action.setToolTip("Click to make Steam's Play button start the game through the script "
                                    "extender (F4SE), or to switch that off again")
        self.f4se_action.clicked.connect(self.toggle_f4se)
        hl.addWidget(self.f4se_action)
        play = QPushButton("Play")
        play.setProperty("sign", "yellow")
        play.setToolTip("Start Fallout 4 through Steam")
        play.setMinimumWidth(96)
        play.clicked.connect(self.play)
        hl.addWidget(play)

        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(24, 18, 24, 8)
        lay.setSpacing(12)
        self.banner = QLabel()
        self.banner.setObjectName("banner")
        self.banner.setWordWrap(True)
        self.banner.hide()
        lay.addWidget(self.banner)
        row = QHBoxLayout()
        install = QPushButton("Install mod…")
        install.setProperty("sign", "yellow")
        install.setToolTip("Install from .zip, .7z or .rar files (or drop them on the window)")
        install.clicked.connect(self.install_archives)
        row.addWidget(install)
        row.addSpacing(12)
        row.addWidget(QLabel("Profile"))
        self.profile_box = QComboBox()
        self.profile_box.setMinimumContentsLength(12)
        self.profile_box.setMaxVisibleItems(14)
        self.profile_box.setAccessibleName("Profile")
        self.profile_box.setToolTip("A profile is its own set of switched-on mods, order, load order and game settings files.\n"
                                    "All profiles share one mod library. New, rename and delete are under More.")
        self.profile_box.activated.connect(lambda _i: self.switch_profile(self.profile_box.currentText()))
        row.addWidget(self.profile_box)
        row.addStretch(1)
        updates = QPushButton("Check for updates")
        updates.setToolTip("Ask Nexus Mods whether newer files exist for the mods installed from there")
        updates.clicked.connect(self.check_updates)
        row.addWidget(updates)
        more_menu = QMenu(self)
        more_menu.addAction("Install from a folder…", self.install_folder)
        more_menu.addSeparator()
        more_menu.addAction("Conflicts…", self.show_conflicts)
        more_menu.addAction("Check a save…", self.check_save)
        more_menu.addAction("Tools…", self.open_tools)
        more_menu.addSeparator()
        profile_menu = more_menu.addMenu("Profiles")
        profile_menu.addAction("New profile…", self.new_profile)
        profile_menu.addAction("Rename this profile…", self.rename_profile)
        profile_menu.addAction("Delete this profile…", self.delete_profile)
        more_menu.addSeparator()
        more_menu.addAction("Save mod list…", self.save_mod_list)
        more_menu.addAction("Load mod list…", lambda: self.load_mod_list())
        more = QPushButton("More")
        more.setToolTip("Install from a folder, conflicts, save check, tools, profiles, and mod lists")
        more.setMenu(more_menu)
        row.addWidget(more)
        lay.addLayout(row)

        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("Mods. When two mods contain the same file, the one lower in the list wins. Drag to reorder."))
        self.mod_tree = OrderTree()
        self.mod_tree.reordered.connect(self._mods_dragged)
        self.mod_tree.setHeaderLabels(["Mod", "Downloaded file", "Files", "Conflicts", "Goes into", "Risk"])
        self.mod_tree.setRootIsDecorated(False)
        for col, width in enumerate((184, 126, 44, 92, 100, 50)):
            self.mod_tree.setColumnWidth(col, width)
        self.mod_tree.header().setStretchLastSection(True)
        self.mod_tree.headerItem().setToolTip(5, "HIGH: becomes part of a save. Add it on a new save; removing it can break saves "
                                                 "made with it.\nlow: adding or removing it barely changes a save.")
        self.mod_tree.itemChanged.connect(self._mod_changed)
        ll.addWidget(self.mod_tree, 1)
        self.mod_menu = QMenu(self)
        self.mod_menu.addAction("Details", self.show_mod_details)
        self.mod_menu.addAction("Open its folder", self.open_mod_folder)
        self.mod_menu.addAction("Conflicts of this mod…", self.show_conflicts)
        self.mod_menu.addSeparator()
        self.mod_menu.addAction("Update to the newer file", self.update_selected)
        self.mod_menu.addAction("Set save risk…", self.choose_risk)
        self.mod_menu.addAction("Pin to the bottom, or unpin", self.toggle_pin_mod)
        self.mod_menu.addSeparator()
        self.mod_menu.addAction("Move up", lambda: self.move_mod(-1))
        self.mod_menu.addAction("Move down", lambda: self.move_mod(1))
        self.mod_menu.addSeparator()
        self.mod_menu.addAction("Remove…", self.remove_mod)
        self.mod_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.mod_tree.customContextMenuRequested.connect(
            lambda pos: self.mod_menu.exec(self.mod_tree.viewport().mapToGlobal(pos)) if self.mod_tree.itemAt(pos) else None)
        self.mod_tree.itemDoubleClicked.connect(lambda _i, col: self.show_mod_details() if col != 0 else None)
        row = QHBoxLayout()
        remove = QPushButton("Remove…")
        remove.clicked.connect(self.remove_mod)
        details = QPushButton("Details")
        details.clicked.connect(self.show_mod_details)
        actions = QPushButton("This mod")
        actions.setToolTip("Everything else for the selected mod: update, risk, pin, folder, conflicts, moving.\n"
                           "The same menu opens when you right-click a mod.")
        actions.setMenu(self.mod_menu)
        row.addWidget(remove)
        row.addWidget(details)
        row.addWidget(actions)
        row.addStretch(1)
        ll.addLayout(row)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("Load order. Plugins lower in the list load later and override earlier ones. Drag to reorder."))
        self.plugin_tree = OrderTree()
        self.plugin_tree.reordered.connect(self._plugins_dragged)
        self.plugin_tree.setHeaderLabels(["Plugin", "Type", "Problems"])
        self.plugin_tree.setRootIsDecorated(False)
        self.plugin_tree.setColumnWidth(0, 300)
        self.plugin_tree.itemChanged.connect(self._plugin_changed)
        rl.addWidget(self.plugin_tree, 1)
        self.counts = QLabel("")
        self.counts.setWordWrap(True)
        rl.addWidget(self.counts)
        self.plugin_menu = QMenu(self)
        self.plugin_menu.addAction("Fix this plugin's problem…", self.fix_plugin)
        self.plugin_menu.addSeparator()
        self.plugin_menu.addAction("Pin to the bottom, or unpin", self.toggle_pin_plugin)
        self.plugin_menu.addAction("Move up", lambda: self.move_plugin(-1))
        self.plugin_menu.addAction("Move down", lambda: self.move_plugin(1))
        self.plugin_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.plugin_tree.customContextMenuRequested.connect(
            lambda pos: self.plugin_menu.exec(self.plugin_tree.viewport().mapToGlobal(pos)) if self.plugin_tree.itemAt(pos) else None)
        self.plugin_tree.itemDoubleClicked.connect(lambda item, col: self.fix_plugin() if col == 2 and item.text(2) else None)
        row = QHBoxLayout()
        self.fix_btn = QPushButton("Fix problems…")
        self.fix_btn.setToolTip("For plugins that need something missing: install what is needed, switch it on, "
                                "or switch the plugin off.\nAlso opens by double-clicking a problem.")
        self.fix_btn.clicked.connect(self.fix_problems)
        sort = QPushButton("Sort by requirements")
        sort.clicked.connect(self.sort_plugins)
        this_plugin = QPushButton("This plugin")
        this_plugin.setToolTip("Pin and move the selected plugin. The same menu opens when you right-click a plugin.")
        this_plugin.setMenu(self.plugin_menu)
        row.addWidget(self.fix_btn)
        row.addWidget(sort)
        row.addWidget(this_plugin)
        row.addStretch(1)
        rl.addLayout(row)
        split.addWidget(right)
        split.setSizes([700, 560])
        lay.addWidget(split, 1)

        self.providers = prov.all_providers(nexus_key(self.mgr.settings), nexus_enabled(self.mgr.settings))
        self.browser = BrowseTab(self.providers, os.path.join(self.mgr.cache_dir, "thumbs"))
        self.browser.layout().setContentsMargins(24, 18, 24, 8)
        self.browser.logLine.connect(self.say)
        self.browser.requirementsRequested.connect(self.install_requirements)
        self.browser.queueRequested.connect(self.install_many_from_browser)
        self.browser.installed_lookup = lambda: ({m.origin for m in self.mgr.mods if m.origin},
                                                 {m.name.strip().lower() for m in self.mgr.mods})
        self._game_version_key = None
        self._last_section = 0
        self._updates: dict = {}             # mod id -> newer ModFile, from the last update check
        self._tool = None                    # (process, tool, snapshot of Data before it started)
        self._tool_timer = QTimer(self)
        self._tool_timer.setInterval(1000)
        self._tool_timer.timeout.connect(self._poll_tool)
        self.presence = None                 # Discord Rich Presence, when switched on in Settings
        self._presence_save = (0.0, None)    # (when read, SaveInfo) so saves are not re-read on every tick
        self.crash_watch = crashwatch.Watcher()
        self._crash_box = None
        self._watch_timer = QTimer(self)
        self._watch_timer.setInterval(3000)
        self._watch_timer.timeout.connect(self._watch_game)
        self._watch_timer.start()
        self._queue_running = False
        self.browser.installRequested.connect(self.install_from_provider)
        self.tabs = QStackedWidget()
        self.tabs.addWidget(central)
        self.tabs.addWidget(self.browser)
        self.game_page = GameSettingsTab(lambda: self.mgr.paths.ini_dir() if self.mgr.paths is not None else None)
        self.game_page.logLine.connect(self.say)
        self.game_page.saved.connect(self.cheer)
        self.tabs.addWidget(self.game_page)
        self.tabs.currentChanged.connect(self._section_changed)

        root = QWidget()
        root.setObjectName("asphalt")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(header)
        band = QFrame()
        band.setObjectName("band")
        band.setFixedHeight(12)
        outer.addWidget(band)
        outer.addWidget(self.tabs, 1)

        # Shared by both sections: download progress, then the activity board.
        bottom = QVBoxLayout()
        bottom.setContentsMargins(24, 4, 24, 4)
        bottom.setSpacing(8)
        self.progress_row = QWidget()
        pr = QHBoxLayout(self.progress_row)
        pr.setContentsMargins(0, 0, 0, 0)
        pr.setSpacing(12)
        self.progress_label = QLabel()
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_cancel = QPushButton("Cancel")
        pr.addWidget(self.progress_label)
        pr.addWidget(self.progress_bar, 1)
        pr.addWidget(self.progress_cancel)
        self.progress_row.hide()
        bottom.addWidget(self.progress_row)
        log_row = QHBoxLayout()
        log_row.setSpacing(12)
        self.mascot = Mascot(92)
        log_row.addWidget(self.mascot, 0, Qt.AlignmentFlag.AlignBottom)
        self.log = QPlainTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        self.log.setFixedHeight(112)
        self.log.setMaximumBlockCount(2000)
        log_row.addWidget(self.log, 1)
        log_buttons = QVBoxLayout()
        copy_log = QPushButton("Copy log")
        copy_log.setToolTip("Copy everything on the activity board, to paste into a bug report")
        copy_log.clicked.connect(lambda: QApplication.clipboard().setText(self.log.toPlainText()))
        log_buttons.addWidget(copy_log)
        report_btn = QPushButton("Game report")
        report_btn.setToolTip("Write a full report about the game's last run (mods, load order, logs) and open it.\n"
                              "One is written by itself whenever the game appears to crash.")
        report_btn.clicked.connect(lambda: self.make_game_report(None))
        log_buttons.addWidget(report_btn)
        log_buttons.addStretch(1)
        log_row.addLayout(log_buttons)
        bottom.addLayout(log_row)
        outer.addLayout(bottom)
        edge_row = QHBoxLayout()
        edge_row.setContentsMargins(24, 4, 24, 10)
        self.edge = theme.PlatformEdge()
        edge_row.addWidget(self.edge)
        outer.addLayout(edge_row)
        self.setCentralWidget(root)
        QTimer.singleShot(0, self._startup)

    # -- helpers --------------------------------------------------------

    _QUEUE_WORDS = {"install_from_provider": "That install", "install_requirements": "Those requirements",
                    "install_many_from_browser": "Those mods", "handle_message": "That download",
                    "_install_many": "Those files", "install_archives": "That install", "install_folder": "That install"}

    def _guard(self, fn, name: str, queue: bool):
        """Wrap an action so it cannot run in the middle of another job. Installs asked for meanwhile
        wait their turn; anything else that changes mods is declined with a line on the activity board."""
        def run(*args, **kwargs):
            if self._in_loop > 0:
                if queue:
                    self._pending.append((fn, args, kwargs))
                    self.say("%s is queued and starts when the current job is done (%d waiting)."
                             % (self._QUEUE_WORDS.get(name, "That"), len(self._pending)))
                else:
                    self.say("Busy with another job. Try that again when it has finished.")
                    if name in ("_mods_dragged", "_apply_mod_change"):
                        QTimer.singleShot(0, self._fill_mods)          # put the list back as it was
                return None
            self._depth += 1
            try:
                return fn(*args, **kwargs)
            finally:
                self._depth -= 1
                if self._depth == 0 and self._in_loop == 0 and self._pending and not self._draining:
                    QTimer.singleShot(0, self._drain)
        return run

    _draining = False

    def _drain(self) -> None:
        """Run what was queued while a job was in progress, oldest first."""
        if self._draining or self._in_loop > 0 or self._depth > 0:
            return
        self._draining = True
        try:
            while self._pending and self._in_loop == 0:
                fn, args, kwargs = self._pending.pop(0)
                try:
                    fn(*args, **kwargs)
                except Exception as e:
                    traceback.print_exc()
                    self.say("A queued job failed: %s: %s" % (type(e).__name__, e))
        finally:
            self._draining = False

    def _go_to(self, index: int) -> None:
        self.tabs.setCurrentIndex(index)
        self._section_changed(self.tabs.currentIndex())      # keeps the signs right when already there

    def _section_changed(self, index: int) -> None:
        for i, b in enumerate(self.nav_buttons):
            b.setChecked(i == index)
        if index == 1:
            self.browser.ensure_loaded()
        QTimer.singleShot(0, self._update_presence)
        if index == 2 and self._last_section != 2:      # not on a second click, which would discard edits
            self.game_page.reload()
        self._last_section = index

    def restyle(self) -> None:
        """After a theme change: redraw what the stylesheet does not reach."""
        self.header.update()
        self.edge.update()
        self.mascot.refresh()
        self.plate_face.setPixmap(theme.mark_pixmap(40, self.devicePixelRatioF()))
        self.browser.restyle()
        self.refresh()

    def cheer(self) -> None:
        """Something finished well: the rat holds up its tick for a moment."""
        self.mascot.set_state("done")

    def say(self, text: str) -> None:
        """Put a line on the activity board and in the log file."""
        stamp = time.strftime("%H:%M:%S")
        for line in text.splitlines() or [""]:
            self.log.appendPlainText("%s  %s" % (stamp, line))
            print("%s  %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), line), flush=True)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def download(self, url: str, dest: str, label: str) -> str:
        """Download with the progress strip showing; the rest of the window waits.
        Returns dest, or raises net.NetError (also when cancelled)."""
        state = {"done": 0, "total": 0, "cancel": False}
        task = _Task(lambda: net.download(url, dest, lambda d, t: state.update(done=d, total=t),
                                          lambda: state["cancel"]))

        def cancel():
            state["cancel"] = True

        def tick():
            total, done = state["total"], state["done"]
            if total > 0:
                self.progress_bar.setRange(0, max(1, total >> 10))
                self.progress_bar.setValue(min(done >> 10, max(1, total >> 10)))
                self.progress_bar.setFormat("%s of %s  (%d%%)" % (human_size(done), human_size(total),
                                                                    int(done * 100 / total)))
            else:
                self.progress_bar.setRange(0, 0)          # size unknown: moving stripe
                self.progress_bar.setFormat(human_size(done))

        self.progress_label.setText(label)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat("Connecting…")
        self.progress_cancel.clicked.connect(cancel)
        self.progress_row.show()
        self.mascot.set_state("work")
        self._strip_use += 1
        self._in_loop += 1
        timer = QTimer()
        timer.timeout.connect(tick)
        timer.start(120)
        loop = QEventLoop()
        task.finished.connect(loop.quit)
        started = time.time()
        task.start()
        if task.isRunning():
            loop.exec()
        task.wait()
        timer.stop()
        tick()
        try:
            self.progress_cancel.clicked.disconnect(cancel)
        except (RuntimeError, TypeError):
            pass
        self._in_loop -= 1
        self.mascot.set_state("error" if task.error is not None else "idle")
        # leave a finished bar on screen briefly so quick downloads are still seen, unless the strip is needed again
        use = self._strip_use
        QTimer.singleShot(1500 if task.error is None else 0,
                          lambda: self.progress_row.hide() if self._strip_use == use and self._in_loop == 0 else None)
        if task.error is not None:
            raise task.error
        self.say("Downloaded %s in %.1f s." % (human_size(state["done"]) or "0 B", time.time() - started))
        return dest

    def _startup(self) -> None:
        self._setup_presence()
        self.say("%s %s started%s." % (APP_NAME, APP_VERSION, " (single-file app)" if getattr(sys, "frozen", False) else ""))
        self.mgr.clear_temp()
        if not self.mgr.game_dir or not steam.looks_like_game(self.mgr.game_dir):
            found = steam.find_game_dirs()
            if found:
                self.mgr.set_game(found[0])
                self.say("Found Fallout 4 at %s" % found[0])
        self.refresh()

    def _load_order(self) -> Optional[LoadOrder]:
        p = self.mgr.paths
        if p is None:
            return None
        return LoadOrder(p.data_dir, p.plugins_txt(create_dir=True), p.game_dir)

    def _read_game_version(self) -> None:
        """Find out which Fallout 4 is installed (read once per change of the game's program file)."""
        p = self.mgr.paths
        exe = steam.child(p.game_dir, "Fallout4.exe") if p is not None else None
        try:
            st = os.stat(exe) if exe else None
            key = (exe, st.st_mtime_ns, st.st_size) if st else None
        except OSError:
            key = None
        if key == self._game_version_key:
            return
        self._game_version_key = key
        version = gamever.exe_version(exe) if key else None
        self.browser.game_version = version
        if version:
            self.say("Your Fallout 4 is version %s." % gamever.describe(version))
        elif key:
            self.say("Could not read the version number from Fallout4.exe, so game-version checks are off.")

    def refresh(self, enable_plugins: Optional[set] = None) -> None:
        self._read_game_version()
        p = self.mgr.paths
        notes = []
        if p is None or not steam.looks_like_game(p.game_dir):
            notes.append("Fallout 4 was not found. Open Settings and choose the folder that contains Fallout4.exe.")
            self.order = None
        else:
            if p.effective_prefix() is None:
                notes.append("The game's Proton prefix was not found, so the load order cannot be saved. Start "
                             "Fallout 4 once from Steam to create it, or set its location in Settings.")
            elif p.ini_dir() is None:
                notes.append("The game's settings folder does not exist yet. Start Fallout 4 once from Steam "
                             "(to the main menu), then come back; this app will then switch on loose mod files.")
            if self.order is None or self.order.data_dir != p.data_dir or \
                    self.order.plugins_txt != p.plugins_txt():
                self.order = self._load_order()
            else:
                self.order.reload()
            if self.order is not None and enable_plugins:
                self.order.reload(enable_new=enable_plugins)
        self.banner.setText("\n\n".join(notes))
        self.banner.setVisible(bool(notes))
        if self._settle_plugin_pins():
            self._save_order()
        self._fill_mods()
        self._fill_plugins()
        self._fill_profiles()
        swap = self._swap_mod()
        self.f4se_action.setText("F4SE  %s" % ("ON" if swap is not None and swap.enabled else "OFF"))

    def apply(self, enable_plugins: Optional[set] = None) -> None:
        """Deploy the current mod list, then bring the load order and ini in line."""
        if self.mgr.paths is None:
            self.refresh()
            return
        try:
            report = run_busy(self, "Updating the game folder…", self.mgr.deploy)
        except Exception as e:
            traceback.print_exc()
            alert(self, APP_NAME, "Updating the game folder failed:\n%s" % e)
            self.refresh()
            return
        self.say("Game folder updated: %s." % report.summary())
        self.mascot.set_state("error" if report.errors else "done")
        for err in report.errors[:10]:
            self.say("  problem: %s" % err)
        self.refresh(enable_plugins)
        self._save_order()
        self._ensure_ini()

    def _ensure_ini(self) -> None:
        p = self.mgr.paths
        if p is None or not any(m.enabled for m in self.mgr.mods):
            return
        ini = p.custom_ini()
        if not ini:
            return
        try:
            if steam.enable_loose_files(ini):
                self.say("Switched on loose mod files in %s" % ini)
        except OSError as e:
            self.say("Could not update %s: %s" % (ini, e))

    def _save_order(self) -> None:
        if self.order is None or not self.order.plugins_txt:
            return
        try:
            self.order.save()
        except OSError as e:
            self.say("Could not save the load order: %s" % e)

    # -- mods list ------------------------------------------------------

    def _fill_mods(self) -> None:
        self._filling = True
        current = self._current_mod_id()
        self.mod_tree.clear()
        conflicts = self.mgr.conflicts() if self.mgr.mods else {}
        for mod in self.mgr.mods:
            c = conflicts.get(mod.id, {"wins": [], "loses": []})
            text = []
            if c["wins"]:
                text.append("overrides %d" % len(c["wins"]))
            if c["loses"]:
                text.append("%d overridden" % len(c["loses"]))
            which = " ".join(x for x in (mod.file_name, ("v" + mod.version) if mod.version else "") if x)
            item = QTreeWidgetItem([mod.name, which or clean_mod_name(mod.source) if mod.source and mod.source != SWAP_SOURCE else which,
                                    str(mod.files), ", ".join(text), "Game folder" if mod.target == "root" else "Data", ""])
            level, how, why = self.mgr.risk(mod)
            if mod.source == SWAP_SOURCE or mod.source in OLD_SWAP_SOURCES:
                level, how, why = "low", "made by this app", "it only swaps the launcher"
            item.setText(5, "HIGH" if level == "high" else "low")
            if level == "high":
                item.setForeground(5, QBrush(QColor(theme.c("problem"))))
            item.setFlags((item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable)
                          & ~Qt.ItemFlag.ItemIsDropEnabled)
            if mod.pinned:
                item.setText(4, item.text(4) + ", pinned")
            newer = self._updates.get(mod.id)
            if newer is not None:
                item.setText(1, "%s  → %s available" % (item.text(1), ("v" + newer.version) if newer.version else "newer file"))
                item.setForeground(1, QBrush(QColor(theme.c("link"))))
            item.setCheckState(0, Qt.CheckState.Checked if mod.enabled else Qt.CheckState.Unchecked)
            item.setData(0, Qt.ItemDataRole.UserRole, mod.id)
            tip = self._mod_facts(mod)
            if newer is not None:
                tip.insert(0, "Update available: \"%s\"%s, uploaded %s. Select the mod and press Update." % (
                    newer.name, " version %s" % newer.version if newer.version else "", newer.uploaded or "?"))
            if mod.pinned:
                tip.insert(0, "Pinned: stays at the bottom of the list.")
            if c["loses"]:
                tip.append("Overridden by later mods:\n  " + "\n  ".join(sorted(c["loses"])[:25]))
            if c["wins"]:
                tip.append("Overrides earlier mods:\n  " + "\n  ".join(sorted(c["wins"])[:25]))
            tip.insert(0, "Save risk: %s (%s%s). %s" % (
                "HIGH" if level == "high" else "low", how, ": " + why if why else "",
                "Add it on a new save; removing it can break saves made with it." if level == "high"
                else "Adding or removing it barely changes a save."))
            for col in range(6):
                item.setToolTip(col, "\n".join(tip))
            self.mod_tree.addTopLevelItem(item)
            if mod.id == current:
                self.mod_tree.setCurrentItem(item)
        self._filling = False

    def _mod_facts(self, mod: Mod) -> list:
        """What tells this mod apart from others of the same name, as lines of text."""
        facts = []
        if mod.file_name or mod.version:
            facts.append("Downloaded file: %s" % " ".join(x for x in (mod.file_name, ("version " + mod.version) if mod.version else "") if x))
        if mod.file_note.strip():
            facts.append("Author's note on that file: %s" % " ".join(mod.file_note.split())[:400])
        if mod.source:
            facts.append("Archive: %s" % mod.source)
        if mod.page:
            facts.append("Page: %s" % mod.page)
        if mod.installed:
            facts.append("Installed: %s" % mod.installed.replace("T", " "))
        try:
            plugins = self.mgr.mod_plugins(mod)
        except Exception:
            plugins = []
        if plugins:
            facts.append("Plugins: %s" % ", ".join(plugins[:12]))
        # what is inside, two folders deep: often the quickest way to tell "trees" from "landscape"
        seen, skip = set(), 1 if mod.target == "data" else 0
        for _path, parts in self.mgr._walk(mod):
            rel = parts[skip:]
            if len(rel) >= 3:
                seen.add("%s/%s" % (rel[0], rel[1]))
            elif len(rel) == 2:
                seen.add(rel[0] + "/")
        inside = sorted((s for s in seen if not (s.endswith("/") and any(o.startswith(s) and o != s for o in seen))), key=str.lower)
        if inside:
            facts.append("Contains: %s%s" % (", ".join(inside[:10]), " and %d more" % (len(inside) - 10) if len(inside) > 10 else ""))
        return facts

    def show_mod_details(self) -> None:
        mod = self.mgr.get(self._current_mod_id())
        if mod is None:
            return
        box = QMessageBox(self)
        box.setWindowTitle(mod.name)
        box.setText(mod.name)
        box.setInformativeText("\n\n".join(self._mod_facts(mod)) or "Nothing more is known about this mod.")
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._details_box = box
        box.exec()

    def _current_mod_id(self) -> str:
        item = self.mod_tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else ""

    def _mod_changed(self, item, column: int) -> None:
        if self._filling or column != 0:
            return
        mod_id = item.data(0, Qt.ItemDataRole.UserRole)
        enabled = item.checkState(0) == Qt.CheckState.Checked
        text = item.text(0)
        # The list is rebuilt as part of applying the change, which deletes this very
        # item. Doing that while Qt is still delivering its "changed" signal crashes,
        # so the work is queued to run just after.
        QTimer.singleShot(0, lambda: self._apply_mod_change(mod_id, enabled, text))

    def _apply_mod_change(self, mod_id: str, enabled: bool, text: str) -> None:
        mod = self.mgr.get(mod_id)
        if mod is None:
            return
        if text.strip() and text != mod.name:
            self.mgr.rename(mod.id, text)
        if enabled != mod.enabled:
            if self.mgr.risk(mod)[0] == "high":
                self.say("\"%s\" is HIGH RISK: %s" % (mod.name, "best switched on for a new save." if enabled else
                         "saves made with it switched on may break now that it is off."))
            self.mgr.set_enabled(mod.id, enabled)
            self.apply(set(self.mgr.mod_plugins(mod)) if enabled else None)

    def move_mod(self, delta: int) -> None:
        ids = [m.id for m in self.mgr.mods]
        cur = self._current_mod_id()
        if cur not in ids:
            return
        i = ids.index(cur)
        if not 0 <= i + delta < len(ids):
            return
        self.mgr.move(i, i + delta)
        self.apply()

    def remove_mod(self) -> None:
        mod = self.mgr.get(self._current_mod_id())
        if mod is None:
            return
        warning = ""
        if self.mgr.risk(mod)[0] == "high":
            warning = ("\n\nThis mod is HIGH RISK: saves made while it was installed may no longer load properly "
                       "without it.")
        if QMessageBox.question(self, "Remove mod", "Remove \"%s\" from the game and delete it from your "
                                                    "mod library?%s" % (mod.name, warning)) != QMessageBox.StandardButton.Yes:
            return
        try:
            report = run_busy(self, "Removing…", lambda: self.mgr.remove_mod(mod.id))
        except Exception as e:
            alert(self, APP_NAME, "Removing failed:\n%s" % e)
            self.refresh()
            return
        self.say("Removed %s. Game folder: %s." % (mod.name, report.summary()))
        self.refresh()
        self._save_order()

    def choose_risk(self) -> None:
        """Set the save-risk tag of the selected mod by hand, or hand it back to the automatic judgement."""
        mod = self.mgr.get(self._current_mod_id())
        if mod is None:
            return
        menu = QMenu(self)
        saved, mod.risk = mod.risk, ""
        judged = self.mgr.risk(mod)[0]
        mod.risk = saved
        for value, text in (("", "Judge from its files (currently %s)" % ("HIGH" if judged == "high" else "low")),
                            ("high", "High risk: needs a new save, removing it breaks saves"),
                            ("low", "Low risk: adding or removing changes barely anything")):
            action = menu.addAction(text)
            action.setCheckable(True)
            action.setChecked(mod.risk == value)
            action.setData(value)
        picked = menu.exec(QCursor.pos())
        if picked is not None:
            self.set_risk(mod.id, picked.data())

    def set_risk(self, mod_id: str, value: str) -> None:
        mod = self.mgr.get(mod_id)
        if mod is not None and value in ("", "high", "low") and mod.risk != value:
            mod.risk = value
            self.mgr.save()
            self.say("Save risk of \"%s\": %s." % (mod.name, {"": "judged from its files again", "high": "set to HIGH",
                                                             "low": "set to low"}[value]))
            self._fill_mods()

    # -- mod lists ------------------------------------------------------------

    def save_mod_list(self) -> None:
        mods = [m for m in self.mgr.mods]
        if not mods:
            QMessageBox.information(self, "Save mod list", "There are no mods to put in a list yet.")
            return
        name, ok = QInputDialog.getText(self, "Save mod list", "Name for this list:", text="My Fallout 4 mods")
        if not ok:
            return
        default = os.path.join(os.path.expanduser("~"), re.sub(r"[^A-Za-z0-9 _.-]+", "", name).strip() or "mod list")
        path, _ = QFileDialog.getSaveFileName(self, "Save mod list", default + modlist.EXTENSION,
                                              "Mauser mod lists (*%s)" % modlist.EXTENSION)
        if not path:
            return
        if not path.endswith(modlist.EXTENSION):
            path += modlist.EXTENSION
        plugins = [(e.name, e.enabled) for e in self.order.entries] if self.order is not None else []
        data = modlist.export(name, "%s %s" % (APP_NAME, APP_VERSION), gamever.describe(self.browser.game_version),
                              mods, plugins, (SWAP_SOURCE,) + tuple(OLD_SWAP_SOURCES))
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(modlist.dumps(data))
        except OSError as e:
            alert(self, "Save mod list", "Could not write the file:\n%s" % e)
            return
        by_hand = [m["name"] for m in data["mods"] if not m["origin"]]
        self.say("Mod list \"%s\" saved to %s: %d mods, %d plugins." % (data["name"], path, len(data["mods"]), len(plugins)))
        if by_hand:
            self.say("  %d of them were installed from files, so a list cannot fetch them again: %s."
                     % (len(by_hand), ", ".join(by_hand[:12]) + ("…" if len(by_hand) > 12 else "")))
        self.cheer()

    def load_mod_list(self, path: str = "") -> None:
        if self.mgr.paths is None:
            QMessageBox.information(self, APP_NAME, "Choose your Fallout 4 folder in Settings first.")
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Load mod list", os.path.expanduser("~"),
                                                  "Mauser mod lists (*%s);;All files (*)" % modlist.EXTENSION)
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                data = modlist.loads(f.read(8_000_001))
        except (OSError, modlist.ListError) as e:
            alert(self, "Load mod list", str(e))
            return
        have, fetch, manual = [], [], []
        for entry in data["mods"]:
            if modlist.match(entry, self.mgr.mods) is not None:
                have.append(entry)
            elif entry["origin"].startswith("nexus:"):
                fetch.append(entry)
            else:
                manual.append(entry)
        mine = gamever.describe(self.browser.game_version)
        text = "\"%s\"%s\n%d mods, %d plugins.\n\n" % (data["name"], ", saved %s" % data["created"].replace("T", " ") if data["created"] else "",
                                                          len(data["mods"]), len(data["plugins"]))
        if data["game"] and mine and data["game"] != mine:
            text += "Made for Fallout 4 %s; yours is %s. Some files may not suit your version.\n\n" % (data["game"], mine)
        text += "Already installed: %d\nTo download from Nexus Mods: %d\n" % (len(have), len(fetch))
        if manual:
            text += "Cannot be fetched (installed from files originally): %d\n   %s\n" % (
                len(manual), "\n   ".join((m["name"] + (" (%s)" % m["archive"] if m["archive"] else "")) for m in manual[:10])
                + ("\n   …" if len(manual) > 10 else ""))
        high = [m["name"] for m in fetch if m["risk"] == "high"]
        text += ("\nThen your mods and plugins are put in the list's order, and mods the list has switched off are switched off. "
                 "Mods you have that the list does not mention are kept, after the listed ones.")
        if fetch and not self._nexus().api_key:
            text += "\n\nNothing can be downloaded, so only the order would be applied: %s" % self._nexus().key_help
        if QMessageBox.question(self, "Load mod list", text + "\n\nGo ahead?") != QMessageBox.StandardButton.Yes:
            return
        self.say("Loading mod list \"%s\" from %s." % (data["name"], path))
        left = []
        if fetch and self._nexus().api_key:
            _done, left = self.install_queue(self._nexus(), [
                {"name": m["name"], "mod_id": m["origin"].split(":", 1)[1], "url": m["page"], "file_id": m["file_id"],
                 "file_name": m["file_name"]} for m in fetch], "Mod list")
        # order and switches, for everything now present
        placed, seen = [], set()
        for entry in data["mods"]:
            mod = modlist.match(entry, [m for m in self.mgr.mods if m.id not in seen])
            if mod is None:
                continue
            seen.add(mod.id)
            mod.enabled = entry["enabled"]
            if entry["risk"] and not mod.risk:
                mod.risk = entry["risk"]
            placed.append(mod)
        self.mgr.mods[:] = placed + [m for m in self.mgr.mods if m.id not in seen]
        self.mgr.save()
        self.apply({p["name"] for p in data["plugins"] if p["enabled"]})
        if self.order is not None and data["plugins"]:
            by_name = {e.name.lower(): e for e in self.order.entries}
            ordered, used = [], set()
            for p in data["plugins"]:
                e = by_name.get(p["name"].lower())
                if e is not None and e.name.lower() not in used:
                    e.enabled = p["enabled"]
                    ordered.append(e)
                    used.add(e.name.lower())
            self.order.entries[:] = ordered + [e for e in self.order.entries if e.name.lower() not in used]
            self._save_order()
            self._fill_plugins()
        missing = [m["name"] for m in manual] + [n for n in left]
        self.say("Mod list applied: %d of %d mods in place%s." % (len(placed), len(data["mods"]),
                 ". Still missing: " + ", ".join(missing) if missing else ""))
        if high:
            self.say("  High risk mods in this list (start a new save): %s." % ", ".join(high))
        self.mascot.set_state("error" if missing else "done")

    # -- ordering by drag, pins, counters --------------------------------------

    def _mods_dragged(self) -> None:
        ids = [self.mod_tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) for i in range(self.mod_tree.topLevelItemCount())]
        by_id = {m.id: m for m in self.mgr.mods}
        if sorted(ids) != sorted(by_id):              # a drop went somewhere odd: show the real order again
            self._fill_mods()
            return
        if ids == [m.id for m in self.mgr.mods]:
            return
        self.mgr.mods[:] = [by_id[i] for i in ids]
        if self.mgr.settle_pins():
            self.say("Pinned mods stay at the bottom of the list.")
        self.mgr.save()
        self.apply()

    def _plugins_dragged(self) -> None:
        if self.order is None:
            return
        names = [self.plugin_tree.topLevelItem(i).text(0) for i in range(self.plugin_tree.topLevelItemCount())]
        by_name = {e.name: e for e in self.order.entries}
        if sorted(names) == sorted(by_name) and names != [e.name for e in self.order.entries]:
            self.order.entries[:] = [by_name[n] for n in names]
            if self._settle_plugin_pins():
                self.say("Pinned plugins stay at the bottom of the load order.")
            self._save_order()
        self._fill_plugins()

    def _settle_plugin_pins(self) -> bool:
        pins = {str(n).lower() for n in self.mgr.settings.get("pinned_plugins", [])}
        if not pins or self.order is None:
            return False
        ordered = [e for e in self.order.entries if e.name.lower() not in pins] + \
                  [e for e in self.order.entries if e.name.lower() in pins]
        moved = [e.name for e in ordered] != [e.name for e in self.order.entries]
        self.order.entries[:] = ordered
        return moved

    def toggle_pin_mod(self) -> None:
        mod = self.mgr.get(self._current_mod_id())
        if mod is None:
            return
        mod.pinned = not mod.pinned
        moved = self.mgr.settle_pins()
        self.mgr.save()
        self.say("\"%s\" %s." % (mod.name, "pinned: it stays at the bottom of the list" if mod.pinned else "unpinned"))
        if moved:
            self.apply()
        else:
            self._fill_mods()

    def toggle_pin_plugin(self) -> None:
        item = self.plugin_tree.currentItem()
        if item is None or self.order is None:
            return
        name = item.text(0)
        pins = [str(n) for n in self.mgr.settings.get("pinned_plugins", [])]
        if name.lower() in [p.lower() for p in pins]:
            pins = [p for p in pins if p.lower() != name.lower()]
            self.say("%s unpinned." % name)
        else:
            pins.append(name)
            self.say("%s pinned: it stays at the bottom of the load order." % name)
        self.mgr.settings["pinned_plugins"] = pins
        self.mgr.save()
        if self._settle_plugin_pins():
            self._save_order()
        self._fill_plugins()

    def _update_counts(self) -> None:
        p = self.mgr.paths
        if self.order is None or p is None:
            self.counts.setText("")
            return
        base = list(self.order.implicit)
        full = sum(1 for n in base if not n.lower().endswith(".esl")) + sum(1 for e in self.order.entries if e.enabled and not e.is_light)
        light = sum(1 for n in base if n.lower().endswith(".esl")) + sum(1 for e in self.order.entries if e.enabled and e.is_light)
        try:
            archives = [n for n in os.listdir(p.data_dir) if n.lower().endswith(".ba2")]
        except OSError:
            archives = []
        plain = [n for n in archives if "textures" not in n.lower()]
        self.plugin_counts = {"full": full, "light": light, "archives": len(archives), "plain_archives": len(plain)}
        bad = theme.c("problem") if theme.c("kind") == "aero" else theme.c("note_error")
        parts = ["Plugins: %s of 254 full, %d of 4096 light" % (
            ('<span style="color: %s; font-weight: 700">%d</span>' % (bad, full)) if full > 254 else str(full), light)]
        shown = str(len(plain))
        if len(plain) > 1024 or (len(plain) > 256 and gamever.generation(self.browser.game_version) in ("og", "ng")):
            shown = '<span style="color: %s; font-weight: 700">%d</span>' % (bad, len(plain))
        parts.append("Archives (.ba2): %d, %s of them not texture archives" % (len(archives), shown))
        self.counts.setText(". ".join(parts) + ".")
        self.counts.setToolTip("The game loads at most 254 full plugins and 4096 light ones.\n"
                               "Archives: older game versions stop working properly past about 256 non-texture archives.\n"
                               "The game update of August 2026 raised the archive limit to 1024.")

    # -- updates ---------------------------------------------------------------

    def check_updates(self) -> None:
        nexus = self._nexus()
        if not nexus.api_key:
            QMessageBox.information(self, "Check for updates", nexus.key_help)
            return
        targets = [m for m in self.mgr.mods if m.origin.startswith("nexus:") and m.file_id]
        skipped = len([m for m in self.mgr.mods if m.source != SWAP_SOURCE and m.source not in OLD_SWAP_SOURCES]) - len(targets)

        def check(m):
            try:
                return m, nexus.newer_file(m.origin.split(":", 1)[1], m.file_id), ""
            except prov.ProviderError as e:
                return m, None, str(e)

        def work():
            from concurrent.futures import ThreadPoolExecutor
            found, failed = {}, []
            with ThreadPoolExecutor(max_workers=4) as pool:        # four questions at a time
                for m, newer, error in pool.map(check, targets):
                    if error:
                        failed.append("%s (%s)" % (m.name, error))
                    elif newer is not None:
                        found[m.id] = newer
            return found, failed
        self._updates, failed = run_busy(self, "Asking Nexus Mods about %d mods…" % len(targets), work)
        self.say("Update check: %d of %d mods have a newer file%s." % (
            len(self._updates), len(targets), "; %d could not be checked (installed from a file, or before version 1.0)" % skipped
            if skipped > 0 else ""))
        for mod_id, newer in self._updates.items():
            mod = self.mgr.get(mod_id)
            self.say("  %s: \"%s\"%s is available (you have %s)." % (
                mod.name, newer.name, " version %s" % newer.version if newer.version else "", mod.version or "an older file"))
        for line in failed[:10]:
            self.say("  could not check %s" % line)
        self._fill_mods()
        self.mascot.set_state("error" if failed else "done")

    def update_selected(self) -> None:
        old = self.mgr.get(self._current_mod_id())
        if old is None:
            return
        newer = self._updates.get(old.id)
        if newer is None:
            QMessageBox.information(self, "Update", "No newer file is known for this mod. Press \"Check for updates\" first.")
            return
        warning = ("\n\nThis mod is HIGH RISK: updating it in the middle of a playthrough can upset saves made with the old version. "
                   "Check the mod's page for update instructions.") if self.mgr.risk(old)[0] == "high" else ""
        if QMessageBox.question(self, "Update", "Replace \"%s\"%s with \"%s\"%s?%s" % (
                old.name, " (%s)" % old.version if old.version else "", newer.name,
                " version %s" % newer.version if newer.version else "", warning)) != QMessageBox.StandardButton.Yes:
            return
        nexus = self._nexus()
        index, before = self.mgr.mods.index(old), {m.id for m in self.mgr.mods}
        stub = prov.ModSummary(provider="nexus", id=old.origin.split(":", 1)[1], name=old.name, url=old.page)
        self.install_from_provider(nexus, stub, newer)
        fresh = [m for m in self.mgr.mods if m.id not in before]
        if not fresh:
            self.say("\"%s\" was not updated." % old.name)
            return
        new = fresh[0]
        new.enabled, new.risk, new.pinned = old.enabled, old.risk, old.pinned
        name = old.name
        self.mgr.mods.remove(new)
        self.mgr.mods.insert(index + 1, new)
        self._updates.pop(old.id, None)
        self.mgr.remove_mod(old.id)
        new.name = name
        self.mgr.save()
        self.say("\"%s\" updated to %s." % (name, ("version %s" % new.version) if new.version else "\"%s\"" % new.file_name))
        self.apply()
        self.cheer()

    # -- conflicts, saves -------------------------------------------------------

    def show_conflicts(self) -> None:
        dlg = dialogs.ConflictDialog(self, self.mgr, self._current_mod_id())
        self._dialog = dlg
        dlg.exec()

    def check_save(self) -> None:
        p = self.mgr.paths
        if p is None or self.order is None:
            QMessageBox.information(self, APP_NAME, "Choose your Fallout 4 folder in Settings first.")
            return
        present = {n.lower(): True for n in self.order.implicit}
        present.update({e.name.lower(): bool(e.enabled) for e in self.order.entries})
        dlg = dialogs.SaveCheckDialog(self, p.ini_dir(), present, {e.name.lower(): e.name for e in self.order.entries})
        self._dialog = dlg
        dlg.exec()

    # -- profiles --------------------------------------------------------------

    def _fill_profiles(self) -> None:
        self.profile_box.blockSignals(True)
        self.profile_box.clear()
        self.profile_box.addItems(profiles.names(self.mgr.settings))
        self.profile_box.setCurrentText(profiles.current(self.mgr.settings))
        self.profile_box.blockSignals(False)

    def _ini_dir(self):
        return self.mgr.paths.ini_dir() if self.mgr.paths is not None else None

    def switch_profile(self, name: str) -> None:
        cur = profiles.current(self.mgr.settings)
        if not name or name == cur:
            return
        if self.crash_watch.running:
            QMessageBox.information(self, "Profiles", "Close Fallout 4 before switching profile.")
            self._fill_profiles()
            return
        profiles.capture(self.mgr, self.order, self._ini_dir(), cur)
        known = name in (self.mgr.settings.get("profiles") or {})
        self.mgr.settings["profile"] = name
        if known:
            profiles.restore_mods(self.mgr, name)
        self.mgr.save()
        restored = profiles.restore_inis(self.mgr, self._ini_dir(), name) if known else []
        self._updates = {}
        self.apply()
        if known:
            profiles.restore_plugins(self.mgr, self.order, name)
            self._settle_plugin_pins()
            self._save_order()
            self._fill_plugins()
        else:
            profiles.capture(self.mgr, self.order, self._ini_dir(), name)
            self.mgr.save()
        self._last_section = -1                      # the Game settings page reads the files again
        self.say("Profile \"%s\" is now active: %d of %d mods switched on%s." % (
            name, sum(1 for m in self.mgr.mods if m.enabled), len(self.mgr.mods),
            ", game settings files restored" if restored else ""))
        self._fill_profiles()

    def new_profile(self) -> None:
        name = dialogs.ask_profile_name(self, self.mgr.settings, "New profile")
        if not name:
            return
        cur = profiles.current(self.mgr.settings)
        profiles.capture(self.mgr, self.order, self._ini_dir(), cur)
        profiles.capture(self.mgr, self.order, self._ini_dir(), name)       # starts as a copy of the current one
        self.mgr.settings["profile"] = name
        self.mgr.save()
        self.say("Profile \"%s\" created as a copy of \"%s\", and made active." % (name, cur))
        self._fill_profiles()

    def rename_profile(self) -> None:
        cur = profiles.current(self.mgr.settings)
        name = dialogs.ask_profile_name(self, self.mgr.settings, "Rename profile", cur)
        if not name:
            return
        profiles.capture(self.mgr, self.order, self._ini_dir(), cur)
        profiles.rename(self.mgr, cur, name)
        self.mgr.save()
        self.say("Profile \"%s\" renamed to \"%s\"." % (cur, name))
        self._fill_profiles()

    def delete_profile(self) -> None:
        cur = profiles.current(self.mgr.settings)
        others = [n for n in profiles.names(self.mgr.settings) if n != cur]
        if not others:
            QMessageBox.information(self, "Profiles", "This is the only profile, so it cannot be deleted.")
            return
        if QMessageBox.question(self, "Delete profile", "Delete the profile \"%s\"? Your mods stay installed; only this "
                                "profile's choices and its copies of the game settings files are deleted.\n\n\"%s\" becomes "
                                "active." % (cur, others[0])) != QMessageBox.StandardButton.Yes:
            return
        self.switch_profile(others[0])
        profiles.delete(self.mgr, cur)
        self.mgr.save()
        self.say("Profile \"%s\" deleted." % cur)
        self._fill_profiles()

    # -- tools ----------------------------------------------------------------

    def open_tools(self) -> None:
        saved = [t for t in self.mgr.settings.get("tools", []) if isinstance(t, dict) and t.get("exe")]
        dlg = dialogs.ToolsDialog(self, saved)
        self._dialog = dlg
        dlg.exec()
        self.mgr.settings["tools"] = dlg.tools
        self.mgr.save()
        if 0 <= dlg.run_index < len(dlg.tools):
            self.run_tool(dlg.tools[dlg.run_index])

    def run_tool(self, tool: dict) -> None:
        p = self.mgr.paths
        if p is None:
            QMessageBox.information(self, APP_NAME, "Choose your Fallout 4 folder in Settings first.")
            return
        if self._tool is not None:
            QMessageBox.information(self, "Tools", "\"%s\" is still running. Close it first." % self._tool[1]["name"])
            return
        before = run_busy(self, "Noting what is in the game folder…", lambda: tools.snapshot(p.data_dir))
        log = os.path.join(self.mgr.base_dir, "tool.log")
        try:
            proc = tools.launch(p, tool["exe"], tool.get("args", ""), log)
        except tools.ToolError as e:
            self.say("Could not start %s: %s" % (tool["name"], e))
            alert(self, "Tools", str(e))
            return
        self._tool = (proc, tool, before)
        self._tool_timer.start()
        self.mascot.set_state("work")
        self.say("Started %s in the game's Proton setup. Its messages go to %s. Files it creates are collected when it closes."
                 % (tool["name"], log))

    def _poll_tool(self) -> None:
        if self._tool is None:
            self._tool_timer.stop()
            return
        if self._in_loop > 0:                 # another job is running: collect the tool's files after it
            return
        proc, tool, before = self._tool
        code = proc.poll()
        if code is None:
            return
        self._tool_timer.stop()
        self._tool = None
        self._tool_finished(tool, before, code)

    def _tool_finished(self, tool: dict, before: dict, code: int) -> None:
        p = self.mgr.paths
        if p is None:
            return
        after = tools.snapshot(p.data_dir)
        new, changed = tools.changes(before, after)
        self.say("%s closed%s. New files in Data: %d, changed: %d." % (
            tool["name"], "" if code == 0 else " (exit code %s)" % code, len(new), len(changed)))
        plan = self.mgr.plan()
        from_mods = [c for c in changed if ("data/" + c.replace(os.sep, "/")).lower() in plan]
        other = [c for c in changed if c not in from_mods]
        if not new and not from_mods:
            if other:
                self.say("  Changed files that belong to the game itself were left as they are: %s" % ", ".join(other[:8]))
            self.mascot.set_state("idle" if code == 0 else "error")
            return
        target = next((m for m in self.mgr.mods if m.source == tools.OVERWRITE_SOURCE), None)
        try:
            if target is None:
                tree = os.path.join(self.mgr.temp_dir(), "overwrite")
                os.makedirs(tree, exist_ok=True)
                dest = tree
            else:
                dest = self.mgr.mod_dir(target)
            for rel in new:
                out = os.path.join(dest, rel)
                os.makedirs(os.path.dirname(out), exist_ok=True)
                shutil.move(os.path.join(p.data_dir, rel), out)
            for rel in from_mods:
                out = os.path.join(dest, rel)
                os.makedirs(os.path.dirname(out), exist_ok=True)
                shutil.copy2(os.path.join(p.data_dir, rel), out)
            if target is None:
                target = self.mgr.add_mod(tools.OVERWRITE_NAME, tree, "data", tools.OVERWRITE_SOURCE)
                target.pinned = True
                self.mgr.mods.remove(target)          # last of all, so what the tools made wins
                self.mgr.mods.append(target)
            else:
                self.mgr.recount(target)
            self.mgr.save()
        except OSError as e:
            self.say("Could not collect the tool's files: %s" % e)
            alert(self, "Tools", "The files %s made could not be collected:\n%s" % (tool["name"], e))
            return
        self.say("  Collected %d files into \"%s\": %s%s" % (
            len(new) + len(from_mods), target.name, ", ".join((new + from_mods)[:8]), "…" if len(new) + len(from_mods) > 8 else ""))
        if from_mods:
            self.say("  %d of them replace files from your mods. The Overwrite mod now wins for those; depending on how mod "
                     "files are placed, the original mod's copy may have been changed too." % len(from_mods))
        self.apply(set(self.mgr.mod_plugins(target)))
        self.cheer()

    # -- fixing plugins that need something --------------------------------------

    def _needs(self, name: str) -> tuple:
        """(needed plugins that are not installed, needed plugins that are installed but switched off)."""
        if self.order is None:
            return [], []
        entry = next((e for e in self.order.entries if e.name == name), None)
        if entry is None:
            return [], []
        have = {e.name.lower(): e for e in self.order.entries}
        base = {n.lower() for n in self.order.implicit}
        missing, off = [], []
        for m in entry.info.masters:
            low = m.lower()
            if low in base:
                continue
            if low not in have:
                missing.append(m)
            elif not have[low].enabled:
                off.append(have[low].name)
        return missing, off

    def _owner_of(self, plugin: str):
        low = plugin.lower()
        for mod in self.mgr.mods:
            if any(p.lower() == low for p in self.mgr.mod_plugins(mod)):
                return mod
        return None

    def set_plugins_enabled(self, names: list, enabled: bool) -> None:
        if self.order is None:
            return
        wanted = {n.lower() for n in names}
        for e in self.order.entries:
            if e.name.lower() in wanted:
                e.enabled = enabled
        self._save_order()
        self._fill_plugins()

    def fix_plugin(self) -> None:
        item = self.plugin_tree.currentItem()
        if item is not None:
            self.fix_problems([item.text(0)])

    def fix_problems(self, only=None) -> None:
        """Offer the ways out for plugins that need something: switch on what is merely off, install what
        is missing, or switch the plugin off (right for a compatibility patch for a mod you do not use)."""
        if self.order is None:
            return
        names = [n for n in (only or [e.name for e in self.order.entries]) if n in self.order.problems()]
        if not names:
            QMessageBox.information(self, "Fix problems", "That plugin has no problem to fix." if only else
                                    "No plugin has a problem.")
            return
        need = {n: self._needs(n) for n in names}
        off = sorted({o for n in names for o in need[n][1]}, key=str.lower)
        missing = sorted({m for n in names for m in need[n][0]}, key=str.lower)
        blocked = [n for n in names if need[n][0]]
        box = QMessageBox(self)
        box.setWindowTitle("Fix problems")
        box.setText("%d plugin%s need%s something." % (len(names), "" if len(names) == 1 else "s", "s" if len(names) == 1 else ""))
        lines = []
        if off:
            lines.append("Installed but switched off (%d): %s" % (len(off), ", ".join(off[:8]) + ("…" if len(off) > 8 else "")))
        if missing:
            lines.append("Not installed (%d): %s" % (len(missing), ", ".join(missing[:8]) + ("…" if len(missing) > 8 else "")))
            lines.append("If a plugin is a patch that joins two mods and you only use one of them, switching the patch off "
                         "is the right fix. If you do want the other mod, find and install it.")
        box.setInformativeText("\n\n".join(lines))
        on_btn = box.addButton("Switch on what is off", QMessageBox.ButtonRole.ActionRole) if off else None
        find_btn = box.addButton("Find what is missing…", QMessageBox.ButtonRole.ActionRole) if missing else None
        off_btn = box.addButton("Switch off the %d plugin%s that cannot work" % (len(blocked), "" if len(blocked) == 1 else "s"),
                                QMessageBox.ButtonRole.ActionRole) if blocked else None
        box.addButton(QMessageBox.StandardButton.Cancel)
        self._fix_box = box
        box.exec()
        picked = box.clickedButton()
        if picked is None:
            return
        if picked is on_btn:
            self.set_plugins_enabled(off, True)
            self.say("Switched on: %s." % ", ".join(off))
        elif picked is off_btn:
            self.set_plugins_enabled(blocked, False)
            self.say("Switched off %d plugins whose requirements are not installed: %s." % (len(blocked), ", ".join(blocked)))
        elif picked is find_btn:
            self.find_missing({n: need[n][0] for n in blocked})

    def find_missing(self, wanted: dict) -> None:
        """wanted: plugin -> [missing plugin files]. Looks up, on Nexus, the requirements of the mod each
        plugin came from, ticks the ones whose names resemble the missing files, and installs what is chosen."""
        nexus = self._nexus()
        owners, loose = {}, []
        for plugin, files in wanted.items():
            mod = self._owner_of(plugin)
            if mod is not None and mod.origin.startswith("nexus:") and nexus.api_key:
                owners.setdefault(mod.id, (mod, []))[1].extend(f for f in files if f not in owners[mod.id][1])
            else:
                loose.extend(f for f in files if f not in loose)
        found: dict = {}        # mod id on Nexus -> (Requirement, reason)
        unmatched = list(loose)

        def work():
            out = []
            for mod, files in owners.values():
                try:
                    det = nexus.details(prov.ModSummary(provider="nexus", id=mod.origin.split(":", 1)[1], name=mod.name))
                except prov.ProviderError as e:
                    out.append((mod, files, None, str(e)))
                    continue
                out.append((mod, files, [r for r in det.requirements if r.kind == "mod" and r.mod_id], ""))
            return out
        results = run_busy(self, "Reading requirements from Nexus Mods…", work) if owners else []
        for mod, files, reqs, error in results:
            if reqs is None:
                self.say("  Could not read the requirements of %s: %s" % (mod.name, error))
                unmatched.extend(f for f in files if f not in unmatched)
                continue
            for f in files:
                best = max(reqs, key=lambda r: dialogs.resemblance(f, r.name), default=None)
                if best is not None and dialogs.resemblance(f, best.name) >= 0.6:
                    found.setdefault(best.mod_id, (best, []))[1].append(f)
                elif f not in unmatched:
                    unmatched.append(f)
        if not found:
            first = os.path.splitext(unmatched[0])[0] if unmatched else ""
            words = re.sub(r"(?<=[a-z])(?=[A-Z])|[_\-.]+", " ", first).strip()
            if QMessageBox.question(self, "Find what is missing",
                                    "No requirement listed on Nexus Mods matches %s.\n\nSearch Nexus Mods for \"%s\" in the "
                                    "Browse tab?%s" % (", ".join(unmatched[:6]) or "the missing files", words,
                                    "\n\n(%d more can be searched for the same way afterwards.)" % (len(unmatched) - 1)
                                    if len(unmatched) > 1 else "")) == QMessageBox.StandardButton.Yes:
                self.search_in_browse(words)
            return
        dlg = dialogs.PickDialog(self, "Find what is missing",
                                 "These mods on Nexus Mods look like what is missing. Tick the ones to download and install.",
                                 [("%s   (provides %s)%s" % (r.name, ", ".join(files), "  [already installed]" if self.browser.is_installed(r) else ""),
                                   not self.browser.is_installed(r), not self.browser.is_installed(r)) for r, files in found.values()],
                                 ("Not matched to anything, look for these yourself: %s" % ", ".join(unmatched)) if unmatched else "")
        self._dialog = dlg
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = [list(found.values())[i][0] for i in dlg.chosen()]
        if chosen:
            self.install_queue(nexus, [{"name": r.name, "mod_id": r.mod_id, "url": r.url} for r in chosen], "Missing requirements")

    def search_in_browse(self, words: str) -> None:
        self._go_to(1)
        self.browser.search_edit.setText(words)
        self.browser.start_search()

    def open_mod_folder(self) -> None:
        mod = self.mgr.get(self._current_mod_id())
        if mod is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.mgr.mod_dir(mod)))

    # -- plugins --------------------------------------------------------

    def _fill_plugins(self) -> None:
        self._filling = True
        current = self.plugin_tree.currentItem().text(0) if self.plugin_tree.currentItem() else ""
        self.plugin_tree.clear()
        pins = {str(n).lower() for n in self.mgr.settings.get("pinned_plugins", [])}
        if self.order is not None:
            problems = self.order.problems()
            for e in self.order.entries:
                kind = "Master" if e.is_master else ""
                if e.is_light:
                    kind = (kind + ", light").strip(", ")
                issue = problems.get(e.name, [])
                if e.name.lower() in pins:
                    kind = (kind + ", pinned").strip(", ")
                item = QTreeWidgetItem([e.name, kind, "; ".join(issue)])
                item.setFlags((item.flags() | Qt.ItemFlag.ItemIsUserCheckable) & ~Qt.ItemFlag.ItemIsDropEnabled)
                item.setCheckState(0, Qt.CheckState.Checked if e.enabled else Qt.CheckState.Unchecked)
                if issue:
                    for col in range(3):
                        item.setForeground(col, QBrush(QColor(theme.c("problem"))))
                        item.setToolTip(col, "\n".join(issue))
                elif e.info.masters:
                    item.setToolTip(0, "Needs: " + ", ".join(e.info.masters))
                self.plugin_tree.addTopLevelItem(item)
                if e.name == current:
                    self.plugin_tree.setCurrentItem(item)
        self._filling = False
        self._update_counts()
        bad = len(self.order.problems()) if self.order is not None else 0
        self.fix_btn.setEnabled(bad > 0)
        self.fix_btn.setText("Fix problems (%d)…" % bad if bad else "Fix problems…")

    def _plugin_changed(self, item, column: int) -> None:
        if self._filling or column != 0 or self.order is None:
            return
        name = item.text(0)
        enabled = item.checkState(0) == Qt.CheckState.Checked
        QTimer.singleShot(0, lambda: self._apply_plugin_change(name, enabled))      # see _mod_changed

    def _apply_plugin_change(self, name: str, enabled: bool) -> None:
        if self.order is None:
            return
        for entry in self.order.entries:
            if entry.name == name:
                if entry.enabled != enabled:
                    entry.enabled = enabled
                    self._save_order()
                    self._fill_plugins()
                return

    def move_plugin(self, delta: int) -> None:
        if self.order is None or self.plugin_tree.currentItem() is None:
            return
        i = self.plugin_tree.indexOfTopLevelItem(self.plugin_tree.currentItem())
        if not 0 <= i + delta < len(self.order.entries):
            return
        self.order.move(i, i + delta)
        if self._settle_plugin_pins():
            self.say("Pinned plugins stay at the bottom of the load order.")
        self._save_order()
        self._fill_plugins()

    def sort_plugins(self) -> None:
        if self.order is None:
            return
        self.order.sort()
        self._settle_plugin_pins()
        self._save_order()
        self._fill_plugins()
        self.say("Load order sorted: masters first, then each plugin after the ones it needs.")

    # -- installing -----------------------------------------------------

    # -- Discord ------------------------------------------------------------------

    def _setup_presence(self) -> None:
        """Start, restart or stop Rich Presence to match Settings."""
        wanted = bool(self.mgr.settings.get("discord_on")) and str(self.mgr.settings.get("discord_id", "")).strip()
        if self.presence is not None and (not wanted or self.presence.client_id != "".join(c for c in str(wanted) if c.isdigit())):
            self.presence.close()
            self.presence = None
        if wanted and self.presence is None:
            self.presence = presence.Presence(str(wanted))
        self._update_presence()

    def _newest_save(self):
        """The most recent save, re-read at most once a minute."""
        when, info = self._presence_save
        if time.time() - when < 60:
            return info
        info = None
        saves = savefile.list_saves(self._ini_dir())
        if saves:
            try:
                info = savefile.read(saves[0][0])
            except savefile.SaveError:
                info = None
        self._presence_save = (time.time(), info)
        return info

    def _update_presence(self) -> None:
        if self.presence is None:
            return
        mods = sum(1 for m in self.mgr.mods if m.enabled)
        if self.crash_watch.running:
            info = self._newest_save() if self.mgr.settings.get("discord_save", True) else None
            bits = []
            if info is not None:
                if info.player:
                    bits.append("%s%s" % (info.player, ", level %d" % info.level if info.level else ""))
                if info.location:
                    bits.append(info.location)
                if info.hours >= 0:
                    bits.append("%d h played" % round(info.hours) if info.hours >= 1 else "%d min played" % round(info.hours * 60))
            act = presence.activity("Playing Fallout 4 with %d mods" % mods, " · ".join(bits), self.crash_watch.started)
        elif self.tabs.currentIndex() == 1:
            act = presence.activity("Browsing mods", "Fallout 4, %s" % self.browser.provider.name)
        elif self.tabs.currentIndex() == 2:
            act = presence.activity("Tuning game settings", "Fallout 4")
        else:
            act = presence.activity("Managing mods", "Fallout 4, %d mods switched on" % mods)
        self.presence.show(act)

    def closeEvent(self, event):
        if self._in_loop > 0 and QMessageBox.question(
                self, APP_NAME, "A job is still running. Quit anyway? An install in progress is abandoned."
        ) != QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        event.accept()
        self._watch_timer.stop()
        self._tool_timer.stop()
        if self.presence is not None:
            self.presence.close()
        # Whatever is still waiting in the background must not keep a windowless copy alive.
        import threading
        watchdog = threading.Timer(3.0, lambda: os._exit(0))
        watchdog.daemon = True
        watchdog.start()
        QApplication.quit()

    def changeEvent(self, event):
        # Coming back to the window: mod folders may have been edited outside the app, so read them afresh when next needed.
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow() and self._in_loop == 0:
            self.mgr.forget_files()
        super().changeEvent(event)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        e.acceptProposedAction()
        QTimer.singleShot(0, lambda: self._install_many(paths))

    def install_archives(self) -> None:
        start = os.path.join(os.path.expanduser("~"), "Downloads")
        paths, _ = QFileDialog.getOpenFileNames(self, "Install mods", start if os.path.isdir(start) else "",
                                                "Mod archives (*.zip *.7z *.rar);;All files (*)")
        self._install_many(paths)

    def install_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Install an unpacked mod")
        if folder:
            self._install_many([folder])

    def _install_many(self, paths: list, name_hint: str = "", confirm_programs: bool = False) -> int:
        if not paths:
            return 0
        if self.mgr.paths is None:
            QMessageBox.information(self, APP_NAME, "Choose your Fallout 4 folder in Settings first.")
            return 0
        new_plugins: set = set()
        installed = 0
        for path in paths:
            try:
                mod = self._install_one(path, name_hint, confirm_programs)
            except (archives.ArchiveError, fomod.FomodError, OSError) as e:
                self.say("Could not install %s: %s" % (os.path.basename(path), e))
                alert(self, "Install mod", "%s\n\n%s" % (os.path.basename(path), e))
                continue
            except Exception as e:
                traceback.print_exc()
                self.say("Unexpected error installing %s: %s: %s" % (os.path.basename(path), type(e).__name__, e))
                alert(self, "Install mod", "%s\n\nUnexpected error: %s" % (os.path.basename(path), e))
                continue
            if mod is not None:
                installed += 1
                plugins = self.mgr.mod_plugins(mod)
                new_plugins.update(plugins)
                self.say("Added \"%s\" to your mods: %d files, goes into the %s%s." % (
                    mod.name, mod.files, "game folder" if mod.target == "root" else "Data folder",
                    ", plugins: " + ", ".join(plugins) if plugins else ", no plugins"))
            else:
                self.say("Skipped %s (cancelled)." % os.path.basename(path))
        self.mgr.clear_temp()
        if installed:
            self.apply(new_plugins)
        return installed

    # -- installing from a provider -------------------------------------

    def _register_menu_entry(self) -> None:
        """Refresh the menu entry: it claims Nexus download links only while the Nexus features are on."""
        if getattr(sys, "frozen", False):
            import desktop
            icon = os.path.join(getattr(sys, "_MEIPASS", ""), "icon.svg")
            note = desktop.register(os.path.abspath(sys.executable), icon, self.mgr.base_dir, nexus_enabled(self.mgr.settings))
            if note:
                self.say(note)

    def _nexus(self) -> prov.NexusProvider:
        return next(p for p in self.providers if p.id == "nexus")

    def _fetch_and_install(self, url: str, mod: prov.ModSummary, file: prov.ModFile) -> None:
        # Sources other than Nexus get a warning when an archive holds programs.
        confirm_programs = mod.provider != "nexus"
        name = prov.safe_file_name(file.file_name or os.path.basename(url.split("?", 1)[0]), "download.zip")
        if not name.lower().endswith(prov.ARCHIVE_EXTS):
            tail = os.path.basename(url.split("?", 1)[0]).lower()
            name += next((ext for ext in prov.ARCHIVE_EXTS if tail.endswith(ext)), ".zip")
        dest = os.path.join(self.mgr.downloads_dir, name)
        host = url.split("/")[2] if url.count("/") >= 2 else "?"
        self.say("Downloading \"%s\" (%s) from %s" % (file.name or mod.name, human_size(file.size) or "size unknown", host))
        try:
            self.download(url, dest, "Downloading %s" % (file.name or mod.name))
        except net.NetError as e:
            self.say("Download stopped: %s" % e)
            if "cancelled" not in str(e):
                alert(self, "Download", "%s\n\n%s" % (file.name or mod.name, e))
            return
        except Exception as e:
            traceback.print_exc()
            self.say("Download failed unexpectedly: %s: %s" % (type(e).__name__, e))
            alert(self, "Download", "%s\n\nUnexpected error: %s" % (file.name or mod.name, e))
            return
        hint = mod.name
        taken = {m.name.strip().lower() for m in self.mgr.mods}
        if file.name and file.name.strip().lower() != mod.name.strip().lower() and (
                (file.category and file.category.lower() not in ("main", "release")) or hint.strip().lower() in taken):
            hint = "%s - %s" % (mod.name, file.name)       # second file of the same mod: keep them apart by name
        try:
            before = {m.id for m in self.mgr.mods}
            if self._install_many([dest], hint, confirm_programs):
                for m in self.mgr.mods:                       # remember where it came from
                    if m.id not in before and mod.provider and mod.id:
                        m.origin = "%s:%s" % (mod.provider, mod.id)
                        m.file_name, m.file_note, m.version, m.page = file.name, file.description, file.version, mod.url
                        m.file_id = str(file.id or "")
                        level, _how, why = self.mgr.risk(m)
                        if level == "high":
                            self.say("  \"%s\" is HIGH RISK (%s). Best added on a new save; removing it later can "
                                     "break saves made with it." % (m.name, why))
                self.mgr.save()
                self._fill_mods()
            else:
                self.say("Nothing was installed from that download.")
        finally:
            try:
                os.unlink(dest)
            except OSError:
                pass

    def install_from_provider(self, provider, mod: prov.ModSummary, file: prov.ModFile, quiet: bool = False) -> None:
        if self.mgr.paths is None:
            QMessageBox.information(self, APP_NAME, "Choose your Fallout 4 folder in Settings first.")
            return
        self.say("Install requested: \"%s\", file \"%s\" (%s)." % (mod.name, file.name, provider.name))
        try:
            url = run_busy(self, "Asking %s for the file…" % provider.name, lambda: provider.download_url(mod, file))
        except prov.NeedsWebsite as e:
            opened = QDesktopServices.openUrl(QUrl(e.url))
            self.say("This account cannot download directly, so the file's page was %s: %s"
                     % ("opened in your web browser" if opened else "NOT opened (no browser answered)", e.url))
            self.say("Waiting for you to press the download button on that page. This app picks it up from there.")
            if quiet:
                return
            QApplication.clipboard().setText(e.url)
            QMessageBox.information(self, "Install from %s" % provider.name,
                                    "%s\n\nIf no page opened, the address has been copied: paste it into your "
                                    "browser." % e)
            return
        except prov.ProviderError as e:
            self.say("Could not get the file from %s: %s" % (provider.name, e))
            alert(self, "Install from %s" % provider.name, str(e))
            return
        except Exception as e:
            traceback.print_exc()
            self.say("Unexpected error asking %s for the file: %s: %s" % (provider.name, type(e).__name__, e))
            alert(self, "Install from %s" % provider.name, "Unexpected error: %s" % e)
            return
        self._fetch_and_install(url, mod, file)

    def install_requirements(self, provider, parent_mod: prov.ModSummary, wanted: list) -> None:
        """Download and install the chosen requirements one after another, main file of each."""
        self.say("Requirements of \"%s\": %d queued (%s)." % (parent_mod.name, len(wanted), ", ".join(r.name for r in wanted)))
        self.install_queue(provider, [{"name": r.name, "mod_id": r.mod_id, "url": r.url} for r in wanted], "Requirements")

    def install_many_from_browser(self, provider, mods: list) -> None:
        """Several mods ticked on the Browse board."""
        self.say("%d mods queued from %s: %s." % (len(mods), provider.name, ", ".join(m.name for m in mods)))
        self.install_queue(provider, [{"name": m.name, "mod_id": m.id, "url": m.url} for m in mods], "Queue")

    def install_queue(self, provider, jobs: list, title: str = "Queue") -> tuple:
        """Install a list of mods one after another. Each job: name, mod_id, and optionally file_id /
        file_name to pick a particular file (otherwise the main file that suits the game version).
        Returns (number installed, names not installed)."""
        if self.mgr.paths is None:
            QMessageBox.information(self, APP_NAME, "Choose your Fallout 4 folder in Settings first.")
            return 0, [j["name"] for j in jobs]
        gen = gamever.generation(self.browser.game_version)
        direct = not hasattr(provider, "is_premium") or run_busy(self, "Checking your account…", provider.is_premium)
        if not direct and len(jobs) > 1:
            if QMessageBox.question(
                    self, title, "Your Nexus account is not Premium, so each of these %d mods opens its page in your "
                    "web browser, and you press the download button on each one.\n\nOpen them all?" % len(jobs)
            ) != QMessageBox.StandardButton.Yes:
                return 0, [j["name"] for j in jobs]
        done, left, names = 0, [], [j["name"] for j in jobs]
        self._queue_running = True
        try:
            for n, job in enumerate(jobs, 1):
                name = job["name"]
                stub = prov.ModSummary(provider=provider.id, id=str(job["mod_id"]), name=name, url=job.get("url", ""))
                try:
                    det = run_busy(self, "Reading %s (%d of %d)…" % (name, n, len(jobs)), lambda: provider.details(stub))
                except Exception as e:
                    self.say("  %s: could not read its page: %s" % (name, e))
                    left.append(name)
                    continue
                file = None
                if job.get("file_id"):
                    file = next((f for f in det.files if str(f.id) == str(job["file_id"])), None)
                if file is None and job.get("file_name"):
                    file = next((f for f in det.files if f.name == job["file_name"]), None)
                    if file is not None and job.get("file_id"):
                        self.say("  %s: the recorded file has been replaced on the site; taking the current \"%s\"%s."
                                 % (name, file.name, " (version %s)" % file.version if file.version else ""))
                wanted_exact = file is not None
                if file is None:
                    if job.get("file_id") or job.get("file_name"):
                        self.say("  %s: the recorded file \"%s\" is no longer on the site; choosing from what is there."
                                 % (name, job.get("file_name") or job.get("file_id")))
                    file = gamever.best_file(det.files, gen)
                if file is None:
                    self.say("  %s: has no files to download." % name)
                    left.append(name)
                    continue
                if not wanted_exact and gamever.fit(file, gen) == "no":
                    self.say("  %s: no file says it is for your game version, so it was left for you to pick by hand." % name)
                    left.append(name)
                    continue
                mains = [f for f in det.files if (f.category or "").lower() == "main"]
                if not wanted_exact and len(mains) > 1:
                    self.say("  %s has %d main files; taking \"%s\". Open its page in Browse if you wanted another."
                             % (name, len(mains), file.name))
                before = len(self.mgr.mods)
                self.install_from_provider(provider, det.summary, file, quiet=True)
                if len(self.mgr.mods) > before:
                    done += 1
                elif direct:
                    left.append(name)
                further = [x.name for x in det.requirements if x.kind == "mod" and not x.optional
                           and not self.browser.is_installed(x) and x.name not in names]
                if further:
                    self.say("  Note: %s itself needs: %s." % (name, ", ".join(further)))
        finally:
            self._queue_running = False
        if direct:
            self.say("%s: %d installed%s." % (title, done, ", not installed: " + ", ".join(left) if left else ""))
            self.mascot.set_state("error" if left else "done")
        else:
            self.say("%s: the pages are open in your web browser. Press the download button on each." % title)
        self.browser.refresh_marks()
        return done, left

    def handle_nxm(self, link_text: str) -> None:
        """A 'Mod manager download' button was pressed on Nexus Mods."""
        link = prov.parse_nxm(link_text)
        if link is None:
            self.say("Ignored a link that is not a Nexus download link.")
            return
        self.say("Received a download link from Nexus Mods: mod %s, file %s (%s)."
                 % (link.mod_id, link.file_id, link.game))
        if link.game != prov.GAME_DOMAIN:
            QMessageBox.information(self, APP_NAME, "That download is for another game (%s). This app only "
                                                    "manages Fallout 4." % link.game)
            return
        nexus = self._nexus()
        if not nexus.api_key:
            QMessageBox.information(self, APP_NAME, "That download link from Nexus Mods cannot be used yet.\n\n" + nexus.key_help)
            return
        try:
            mod, file = run_busy(self, "Reading the mod's details…", lambda: nexus.file_info(link.mod_id, link.file_id))
            url = run_busy(self, "Asking Nexus Mods for the file…",
                           lambda: nexus.download_url(mod, file, link.key, link.expires))
        except prov.ProviderError as e:
            self.say("Nexus Mods refused the download link: %s" % e)
            alert(self, "Install from Nexus Mods", str(e))
            return
        except Exception as e:
            traceback.print_exc()
            self.say("Unexpected error handling the Nexus link: %s: %s" % (type(e).__name__, e))
            alert(self, "Install from Nexus Mods", "Unexpected error: %s" % e)
            return
        self._fetch_and_install(url, mod, file)

    def handle_message(self, text: str) -> None:
        """Something sent by a second launch of the app (a link or a file to install)."""
        self.showNormal()
        self.raise_()
        self.activateWindow()
        if text.strip() and text.strip() != "show":
            self.say("Received from outside the app: %s" % text.strip().split("?")[0][:200])
        for line in text.splitlines():
            line = line.strip()
            if line.lower().startswith("nxm://"):
                self.handle_nxm(line)
            elif line and os.path.exists(line):
                self._install_many([line])

    def _file_state(self, name: str) -> str:
        p = self.mgr.paths
        if p is None or steam.child(p.data_dir, name) is None:
            return "Missing"
        low = name.lower()
        if self.order is not None:
            if low in {n.lower() for n in self.order.implicit}:
                return "Active"
            for e in self.order.entries:
                if e.name.lower() == low:
                    return "Active" if e.enabled else "Inactive"
        return "Active" if not low.endswith((".esp", ".esm", ".esl")) else "Inactive"

    def _install_one(self, path: str, name_hint: str = "", confirm_programs: bool = False) -> Optional[Mod]:
        work = self.mgr.temp_dir()
        unpacked = os.path.join(work, "unpacked")
        if os.path.isdir(path):
            run_busy(self, "Copying %s…" % os.path.basename(path),
                     lambda: shutil.copytree(path, unpacked, symlinks=False))
        else:
            run_busy(self, "Unpacking %s…" % os.path.basename(path), lambda: archives.extract(path, unpacked))
        layout = archives.find_layout(unpacked)
        unpacked_files = sum(len(files) for _f, _d, files in os.walk(unpacked))
        self.say("Unpacked %s: %d files, recognised as %s." % (os.path.basename(path), unpacked_files, {
            "fomod": "a mod with a guided installer", "data": "a normal mod (goes into Data)",
            "root": "a game-folder mod", "unknown": "not a normal mod layout"}.get(layout.kind, layout.kind)))
        name = name_hint or clean_mod_name(path)
        if confirm_programs:
            programs = sorted(os.path.relpath(os.path.join(folder, f), unpacked)
                              for folder, _d, files in os.walk(unpacked) for f in files
                              if f.lower().endswith((".exe", ".dll", ".bat", ".cmd", ".ps1", ".vbs", ".scr", ".msi")))
            if programs and QMessageBox.question(
                    self, "Install mod",
                    "%s contains %d program files that will run with the game:\n\n%s%s\n\nScript-extender plugins "
                    "are normally .dll files, so this can be fine, but only continue if you trust where this came "
                    "from.\n\nInstall it?" % (name, len(programs), "\n".join(programs[:12]),
                                              "\n…" if len(programs) > 12 else "")) != QMessageBox.StandardButton.Yes:
                return None
        target = "data"
        tree = layout.base
        if layout.kind == "fomod":
            config = fomod._resolve(layout.base, "fomod/ModuleConfig.xml")
            with open(config, "rb") as f:
                fm = fomod.parse(f.read())
            session = fomod.Session(fm, self._file_state)
            if fm.steps:
                dlg = FomodDialog(self, session, layout.base, fm.name or name)
                if not dlg.exec():
                    return None
            tree = os.path.join(work, "chosen")
            count, missing = run_busy(self, "Installing…", lambda: fomod.install(layout.base, session.result(), tree))
            if missing:
                self.say("%s: the installer referred to %d files that are not in the archive." % (name, len(missing)))
            if count == 0:
                raise archives.ArchiveError("The chosen options did not include any files.")
            if fm.name and not name_hint:
                name = fm.name
        elif layout.kind == "root":
            target = "root"
        elif layout.kind == "unknown":
            if QMessageBox.question(self, "Install mod",
                                    "%s\n\n%s\n\nInstall its contents into the Data folder anyway?"
                                    % (os.path.basename(path), layout.note)) != QMessageBox.StandardButton.Yes:
                return None
        if any(m.name.lower() == name.lower() for m in self.mgr.mods):
            answer = QMessageBox.question(
                self, "Install mod", "A mod called \"%s\" is already installed.\n\nReplace it? Choosing No "
                                     "installs this one alongside it." % name,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel)
            if answer == QMessageBox.StandardButton.Cancel:
                return None
            if answer == QMessageBox.StandardButton.Yes:
                old = next(m for m in self.mgr.mods if m.name.lower() == name.lower())
                index = self.mgr.mods.index(old)
                self.mgr.remove_mod(old.id)
                mod = self.mgr.add_mod(name, tree, target, os.path.basename(path))
                self.mgr.move(len(self.mgr.mods) - 1, index)       # keep its place in the order
                return mod
        return self.mgr.add_mod(name, tree, target, os.path.basename(path))

    # -- F4SE, play, settings -------------------------------------------

    def _swap_mod(self) -> Optional[Mod]:
        for m in self.mgr.mods:
            if m.source == SWAP_SOURCE or m.source in OLD_SWAP_SOURCES:
                return m
        return None

    def toggle_f4se(self) -> None:
        p = self.mgr.paths
        if p is None:
            return
        swap = self._swap_mod()
        if swap is not None:
            self.mgr.remove_mod(swap.id)
            self.say("Steam's Play button starts the normal launcher again.")
            self.refresh()
            return
        loader = steam.child(p.game_dir, "f4se_loader.exe")
        if loader is None:
            QMessageBox.information(self, "F4SE", "F4SE is not installed yet. Download it, install its archive "
                                                  "here like any other mod, then press this again.")
            return
        work = self.mgr.temp_dir()
        tree = os.path.join(work, "swap")
        os.makedirs(tree)
        shutil.copyfile(loader, os.path.join(tree, "Fallout4Launcher.exe"))
        self.mgr.add_mod(SWAP_MOD_NAME, tree, "root", SWAP_SOURCE)
        self.mgr.clear_temp()
        self.apply()
        self.cheer()
        self.say("Steam's Play button now starts the game through F4SE. The original launcher is kept and "
                 "comes back when you switch this off.")

    def play(self) -> None:
        if self.order is not None:
            bad = self.order.problems()
            if bad and QMessageBox.question(
                    self, "Play", "%d enabled plugins have problems (see the Problems column). The game may "
                                  "crash on start.\n\nPlay anyway?" % len(bad)) != QMessageBox.StandardButton.Yes:
                return
        if not QDesktopServices.openUrl(QUrl("steam://rungameid/%s" % steam.APP_ID)):
            alert(self, "Play", "Could not ask Steam to start the game. Start it from Steam instead.")

    # -- watching the game ----------------------------------------------------

    def _watch_game(self) -> None:
        was_running = self.crash_watch.running
        try:
            ended = self.crash_watch.poll()
        except Exception:
            traceback.print_exc()
            return
        theme.animations_paused = self.crash_watch.running          # no moving decorations while the game runs
        self._update_presence()
        if self.crash_watch.running and not was_running:
            self.say("Fallout 4 is running. %s is keeping an eye on it." % theme.MASCOT_NAME)
        elif was_running and not self.crash_watch.running:
            self.header.update()
        if ended is not None:
            self._game_ended(*ended)

    def _game_ended(self, started: float, ended: float) -> None:
        minutes, seconds = divmod(int(ended - started), 60)
        p = self.mgr.paths
        if p is None:
            return
        ev = crashwatch.gather(p, started, ended - started)
        if not ev.reasons:
            self.say("Fallout 4 closed after %d min %02d s. No sign of a crash." % (minutes, seconds))
            return
        self.say("Fallout 4 stopped after %d min %02d s and appears to have crashed: %s." % (minutes, seconds, "; ".join(ev.reasons)))
        path = self.make_game_report(True, ev, open_it=False, session=(started, ended))
        self.mascot.set_state("error", 20000)
        if not path:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Fallout 4 crashed")
        box.setText("Fallout 4 appears to have crashed.")
        box.setInformativeText("%s.\n\nA full report was written:\n%s" % ("; ".join(ev.reasons).capitalize(), path))
        pix = theme.rat_pixmap("error", 72, self.devicePixelRatioF())
        if not pix.isNull():
            box.setIconPixmap(pix)
        open_btn = box.addButton("Open report", QMessageBox.ButtonRole.AcceptRole)
        folder_btn = box.addButton("Open folder", QMessageBox.ButtonRole.ActionRole)
        copy_btn = box.addButton("Copy report", QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Close)

        def clicked(button):
            if button is open_btn:
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            elif button is folder_btn:
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
            elif button is copy_btn:
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        QApplication.clipboard().setText(f.read())
                except OSError:
                    pass
        box.buttonClicked.connect(clicked)
        box.setModal(False)
        self._crash_box = box              # kept so it is not thrown away while open
        box.show()

    def make_game_report(self, crashed: Optional[bool], ev=None, open_it: bool = True, session: Optional[tuple] = None) -> str:
        """Write a report about the last (or current) game session. Returns its path, '' on failure."""
        started, ended = session or self.crash_watch.last_session
        if session is None and self.crash_watch.running:
            started, ended = self.crash_watch.started, time.time()
        if not ended:
            ended = time.time()
        p = self.mgr.paths
        if ev is None and p is not None:
            ev = crashwatch.gather(p, started or ended, 10 ** 9)       # by hand: collect logs, judge nothing
            ev.reasons = []
        try:
            text = crashwatch.build_report("%s %s" % (APP_NAME, APP_VERSION), self.mgr, self.order, p,
                                           self.browser.game_version, started, ended, crashed, ev, self.log.toPlainText())
            folder = os.path.join(self.mgr.base_dir, "crash-reports")
            path = crashwatch.save_report(folder, text, ended, crashed)
            old = sorted(n for n in os.listdir(folder) if n.endswith(".txt"))
            for name in old[:-40]:                                       # keep the newest 40
                os.unlink(os.path.join(folder, name))
        except Exception as e:
            traceback.print_exc()
            self.say("Could not write the game report: %s: %s" % (type(e).__name__, e))
            return ""
        self.say("Report written: %s" % path)
        if open_it:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        return path

    def run_setup(self) -> None:
        """The first-time setup. Also reachable from Settings."""
        from firstrun import SetupWizard
        key_before = (str(self.mgr.settings.get("nexus_key", "")), nexus_enabled(self.mgr.settings))
        dlg = SetupWizard(self, self.mgr, lambda: (apply_theme(QApplication.instance(), self.mgr), self.restyle()), run_busy)
        self._setup_dialog = dlg
        dlg.exec()
        if dlg.finished_setup:
            self.say("Setup finished. Welcome aboard.")
            if (str(self.mgr.settings.get("nexus_key", "")), nexus_enabled(self.mgr.settings)) != key_before:
                self._register_menu_entry()
                self.providers = prov.all_providers(nexus_key(self.mgr.settings), nexus_enabled(self.mgr.settings))
                self.browser.set_providers(self.providers)
            self.order = None
            self.cheer()
        self.restyle()

    def open_settings(self) -> None:
        dlg = SettingsDialog(self, self.mgr)
        if not dlg.exec():
            if dlg.setup_requested:
                self.run_setup()
            return
        if dlg.purge_requested:
            for m in self.mgr.mods:
                m.enabled = False
            self.mgr.save()
            report = run_busy(self, "Removing mod files…", self.mgr.purge)
            self.say("All mod files removed from the game: %s." % report.summary())
            for err in report.errors[:10]:
                self.say("  problem: %s" % err)
        game_changed = os.path.realpath(dlg.result_game or "") != os.path.realpath(self.mgr.game_dir or "")
        self.mgr.link_mode = dlg.result_mode
        if (dlg.result_tile != self.mgr.settings.get("tile", theme.DEFAULT_TILE)
                or dlg.result_theme != self.mgr.settings.get("theme", theme.DEFAULT_THEME)):
            self.mgr.settings["tile"] = dlg.result_tile
            self.mgr.settings["theme"] = dlg.result_theme
            apply_theme(QApplication.instance(), self.mgr)
            self.restyle()
        on, app_id, with_save = dlg.result_discord
        if on and not "".join(c for c in app_id if c.isdigit()):
            self.say("Discord was not switched on: it needs an application ID.")
            on = False
        self.mgr.settings.update({"discord_on": on, "discord_id": app_id, "discord_save": with_save})
        self._setup_presence()
        if on:
            QTimer.singleShot(6000, lambda: self.say("Discord: %s." % self.presence.status) if self.presence is not None else None)
        if dlg.result_key != str(self.mgr.settings.get("nexus_key", "")) or dlg.result_nexus_on != nexus_enabled(self.mgr.settings):
            self.mgr.settings["nexus_key"] = dlg.result_key
            self.mgr.settings["nexus_enabled"] = dlg.result_nexus_on
            self.providers = prov.all_providers(nexus_key(self.mgr.settings), dlg.result_nexus_on)
            self._register_menu_entry()
            self.browser.set_providers(self.providers)
            if self.tabs.currentIndex() == 1:
                self.browser.ensure_loaded()
        if dlg.result_game and (game_changed or dlg.result_prefix != self.mgr.prefix):
            self.mgr.set_game(dlg.result_game, dlg.result_prefix)
        self.mgr.save()
        self.order = None
        if dlg.purge_requested:
            self.refresh()
            self._save_order()
        else:
            self.apply()

