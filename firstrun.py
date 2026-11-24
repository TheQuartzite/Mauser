"""First-time setup: five short stops with Vinny, shown the first time the app starts."""
from __future__ import annotations

import os
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QStackedWidget,
    QVBoxLayout, QWidget,
)

import gamever
import providers as prov
import steam
import theme
import common
from common import APP_NAME, nexus_enabled

STOPS = (("WELCOME ABOARD", "Welcome aboard"),
         ("FIND THE GAME", "Where is Fallout 4?"),
         ("GETTING MODS", "Getting mods"),
         ("PICK YOUR LOOK", "Pick your look"),
         ("READY TO DEPART", "Ready to depart"))


class SetupWizard(QDialog):
    def __init__(self, parent, mgr, apply_theme: Callable, run_busy: Optional[Callable] = None):
        super().__init__(parent)
        self.mgr, self._apply_theme, self._run_busy = mgr, apply_theme, run_busy
        self._before = {k: mgr.settings.get(k) for k in ("theme", "tile")}
        self.finished_setup = False
        self.setWindowTitle("%s: first-time setup" % APP_NAME)
        self.setModal(True)
        self.resize(880, 620)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # The station: tiled wall, the departure board, and Vinny arriving.
        self.header = theme.Backdrop()
        self.header.setFixedHeight(150)
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(24, 16, 24, 16)
        hl.setSpacing(18)
        self.board = QLabel()
        self.board.setObjectName("setupboard")
        self.board.setTextFormat(Qt.TextFormat.PlainText)
        self.board.setMinimumWidth(520)
        self.board.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        hl.addWidget(self.board, 1)
        self.vinny = theme.RatLabel(112)
        self.vinny.setFixedSize(118, 118)
        hl.addWidget(self.vinny)
        outer.addWidget(self.header)
        band = QFrame()
        band.setObjectName("band")
        band.setFixedHeight(10)
        outer.addWidget(band)

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(28, 20, 28, 16)
        bl.setSpacing(14)
        self.heading = QLabel()
        self.heading.setObjectName("title")
        bl.addWidget(self.heading)
        self.pages = QStackedWidget()
        bl.addWidget(self.pages, 1)
        for build in (self._page_welcome, self._page_game, self._page_nexus, self._page_look, self._page_ready):
            self.pages.addWidget(build())
        row = QHBoxLayout()
        self.skip_btn = QPushButton("Skip setup")
        self.skip_btn.setToolTip("Everything here can also be set later in Settings")
        self.skip_btn.clicked.connect(self._skip)
        self.back_btn = QPushButton("Back")
        self.back_btn.clicked.connect(lambda: self.go(self.pages.currentIndex() - 1))
        self.next_btn = QPushButton("Next stop")
        self.next_btn.setProperty("sign", "yellow")
        self.next_btn.setDefault(True)
        self.next_btn.clicked.connect(self._next)
        row.addWidget(self.skip_btn)
        row.addStretch(1)
        row.addWidget(self.back_btn)
        row.addWidget(self.next_btn)
        bl.addLayout(row)
        outer.addWidget(body, 1)

        self._typing = QTimer(self)
        self._typing.setInterval(22)
        self._typing.timeout.connect(self._type_more)
        self._target, self._typed = "", 0
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.timeout.connect(self._arrived)
        self.go(0)

    # -- the pages ------------------------------------------------------------

    def _text(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setOpenExternalLinks(True)
        return label

    def _page(self, *widgets) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        for w in widgets:
            if isinstance(w, QWidget):
                lay.addWidget(w)
            else:
                lay.addLayout(w)
        lay.addStretch(1)
        return page

    def _page_welcome(self) -> QWidget:
        return self._page(
            self._text("<b>%s</b> installs and looks after Fallout 4 mods on Linux, for the Steam version running "
                       "through Proton. No Windows tools, no terminal." % APP_NAME),
            self._text("&bull; <b>Install</b> a mod from the file you downloaded: pick it, or drop it on the window.<br>"
                       "&bull; <b>Installed</b> keeps your mods and load order in one place; nothing is unpacked "
                       "straight into the game, so everything can be undone.<br>"
                       "&bull; <b>Game settings</b> covers resolution and field of view without the launcher.<br>"
                       "&bull; If the game crashes, a full report is written for you."),
            self._text("This is <b>%s</b>, the station mouse. He carries the parcels, and he will tell you when "
                       "something is done or has gone wrong. Four short stops and you are ready." % theme.MASCOT_NAME))

    def _page_game(self) -> QWidget:
        self.game = QLineEdit(self.mgr.game_dir or "")
        self.game.setPlaceholderText("The folder that contains Fallout4.exe")
        self.game.textChanged.connect(lambda _t: self._check_game())
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        detect = QPushButton("Look again")
        detect.clicked.connect(self._detect)
        line = QHBoxLayout()
        line.addWidget(self.game, 1)
        line.addWidget(browse)
        line.addWidget(detect)
        self.game_status = self._text("")
        return self._page(self._text("Steam libraries are searched for Fallout 4. Check the folder below, or choose it yourself."),
                          line, self.game_status)

    def _page_nexus(self) -> QWidget:
        self.nexus_on = QCheckBox("Switch on the experimental Nexus Mods features")
        self.nexus_on.setChecked(nexus_enabled(self.mgr.settings))
        self.key = QLineEdit(str(self.mgr.settings.get("nexus_key", "")))
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Personal API key")
        self.check_btn = QPushButton("Check")
        self.check_btn.clicked.connect(self._check_key)
        line = QHBoxLayout()
        line.addWidget(self.key, 1)
        line.addWidget(self.check_btn)
        self.key_status = self._text("")
        for w in (self.key, self.check_btn):
            w.setEnabled(self.nexus_on.isChecked())
            self.nexus_on.toggled.connect(w.setEnabled)
        if not common.NEXUS_AVAILABLE:
            self.nexus_on.setChecked(bool(self.mgr.settings.get("nexus_enabled")))     # kept as it was, unseen
            return self._page(
                self._text("To install a mod, download its file from Nexus Mods (or anywhere else) in your web browser, "
                           "then press <b>Install mod</b> in %s or drop the file on the window." % APP_NAME),
                self._text("Browsing Nexus Mods inside the app and one-click installs are <b>switched off in this "
                           "version</b>. They need approval from Nexus Mods, which %s has asked for and does not have "
                           "yet. They will return in a later version if it is granted." % APP_NAME),
                self._text("Nothing to set up at this stop."))
        return self._page(
            self._text("%s works without Nexus Mods: download a mod's file in your web browser, then use <b>Install mod</b> "
                       "or drop the file on the window. This stop is optional." % APP_NAME),
            self._text("<b>Experimental:</b> browsing Nexus Mods inside the app, one-click installs and update checks. "
                       "They use your own Nexus account's key. Nexus Mods has not yet approved %s for this, and its rules "
                       "allow personal keys only for personal use and testing, so these features may stop working." % APP_NAME),
            self.nexus_on,
            self._text("If you switch them on: sign in at nexusmods.com, open your account settings, go to <b>API Keys</b> "
                       "and copy the <b>Personal API Key</b> at the bottom of that page. It stays on this computer."),
            line, self.key_status)

    def _page_look(self) -> QWidget:
        self.theme_box = QComboBox()
        for key, t in theme.THEMES.items():
            self.theme_box.addItem(t["label"], key)
        self.theme_box.setCurrentIndex(max(0, self.theme_box.findData(str(self.mgr.settings.get("theme", theme.DEFAULT_THEME)))))
        self.tile_box = QComboBox()
        for key in theme.TILES:
            self.tile_box.addItem(key.capitalize(), key)
        self.tile_box.setCurrentIndex(max(0, self.tile_box.findData(str(self.mgr.settings.get("tile", theme.DEFAULT_TILE)))))
        self.theme_box.currentIndexChanged.connect(lambda _i: self._look_changed())
        self.tile_box.currentIndexChanged.connect(lambda _i: self._look_changed())
        line = QHBoxLayout()
        line.addWidget(QLabel("Theme"))
        line.addWidget(self.theme_box)
        line.addSpacing(18)
        line.addWidget(QLabel("Tile colour"))
        line.addWidget(self.tile_box)
        line.addStretch(1)
        return self._page(self._text("Choose how the app looks. It changes straight away, so try them; %s changes "
                                     "his coat to match." % theme.MASCOT_NAME),
                          line,
                          self._text("<b>Bahnhof</b> is a station by day, <b>Bahnhof at night</b> the same station "
                                     "after dark. <b>Frutiger Aero</b> is sky, glass and grass, with a dusk version. "
                                     "The tile colour belongs to the Bahnhof themes."))

    def _page_ready(self) -> QWidget:
        self.summary = self._text("")
        return self._page(self.summary,
                          self._text("When you start, %s switches on loose mod files for the game if they are not on "
                                     "already, and adds itself to your application menu. <b>F4SE</b> and <b>Play</b> "
                                     "are in the top right corner." % APP_NAME))

    # -- moving between stops ---------------------------------------------------

    def go(self, index: int) -> None:
        index = max(0, min(self.pages.count() - 1, index))
        self.pages.setCurrentIndex(index)
        self.heading.setText(STOPS[index][1])
        self.back_btn.setEnabled(index > 0)
        last = index == self.pages.count() - 1
        self.next_btn.setText("Start %s" % APP_NAME if last else "Next stop")
        self._target = "%s  ·  FIRST DEPARTURE\nSTOP %d OF %d    %s" % (APP_NAME.upper(), index + 1, len(STOPS), STOPS[index][0])
        self._typed = 0
        self.board.setText("")
        self._typing.start()
        self.vinny.set_state("work")            # he runs to each stop...
        self._settle.start(1100)
        if index == 1:
            if not self.game.text().strip():
                self._detect()
            self._check_game()
        elif index == 4:
            self._fill_summary()

    def _arrived(self) -> None:                 # ...and settles when he gets there
        self.vinny.set_state("done" if self.pages.currentIndex() == self.pages.count() - 1 else "idle")

    def _type_more(self) -> None:
        self._typed = min(len(self._target), self._typed + 2)
        self.board.setText(self._target[:self._typed] + ("_" if self._typed < len(self._target) else ""))
        if self._typed >= len(self._target):
            self._typing.stop()

    def _next(self) -> None:
        if self.pages.currentIndex() == self.pages.count() - 1:
            self._finish()
        else:
            self.go(self.pages.currentIndex() + 1)

    # -- stop 2: the game -------------------------------------------------------

    def _detect(self) -> None:
        found = steam.find_game_dirs()
        if found:
            self.game.setText(found[0])
        self._check_game()

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose the Fallout 4 folder", self.game.text() or os.path.expanduser("~"))
        if folder:
            self.game.setText(folder)

    def game_dir(self) -> str:
        text = self.game.text().strip()
        if not text:
            return ""
        folder = steam.normalize_game_dir(text)
        return folder if steam.looks_like_game(folder) else ""

    def _check_game(self) -> None:
        ok, bad = theme.c("rat_ok") if theme.c("kind") == "aero" else theme.c("link"), theme.c("problem")
        folder = self.game_dir()
        if not folder:
            self.game_status.setText('<span style="color: %s; font-weight: 600">%s</span><br>You can go on without it '
                                     'and set it later in Settings.' % (bad, "Fallout 4 was not found in that folder."
                                     if self.game.text().strip() else "Fallout 4 was not found automatically."))
            return
        paths = steam.GamePaths(folder, self.mgr.prefix or None)
        exe = steam.child(folder, "Fallout4.exe")
        version = gamever.exe_version(exe) if exe else None
        rows = ['<span style="color: %s; font-weight: 600">Found Fallout 4%s.</span>' % (
            ok, " %s" % gamever.describe(version) if version else "")]
        if paths.effective_prefix() is None or paths.ini_dir() is None:
            rows.append('<span style="color: %s">The game has not been started yet on this computer.</span> Start it once '
                        'from Steam, as far as the main menu, so it creates its settings folder.' % bad)
        else:
            rows.append("Its Proton settings folder was found.")
        rows.append("Script extender (F4SE): %s." % ("installed" if paths.has_f4se() else
                                                    "not installed. Many mods need it; it can be added later"))
        self.game_status.setText("<br>".join(rows))

    # -- stop 3: Nexus ---------------------------------------------------------

    def _check_key(self) -> None:
        key = self.key.text().strip() if self.nexus_on.isChecked() else ""
        if not key:
            self.key_status.setText("No key entered. That is fine; add one later in Settings.")
            return
        try:
            call = prov.NexusProvider(key).validate
            user = self._run_busy(self, "Checking the key with Nexus Mods…", call) if self._run_busy else call()
        except prov.ProviderError as e:
            self.key_status.setText('<span style="color: %s; font-weight: 600">%s</span>' % (theme.c("problem"), e))
            self.vinny.set_state("error")
            return
        self.key_status.setText("<b>Signed in as %s.</b> %s" % (
            user.get("name") or "unknown", "Premium account: installs are one click." if user.get("is_premium")
            else "Free account: installs go through the download button on the Nexus page."))
        self.vinny.set_state("done")

    # -- stop 4: the look ------------------------------------------------------

    def _look_changed(self) -> None:
        self.mgr.settings["theme"] = self.theme_box.currentData()
        self.mgr.settings["tile"] = self.tile_box.currentData()
        self.tile_box.setEnabled(str(self.theme_box.currentData()).startswith("bahnhof"))
        self._apply_theme()
        self.header.update()
        self.vinny.refresh()
        self._check_game()

    # -- stop 5 and leaving ------------------------------------------------------

    def _fill_summary(self) -> None:
        folder = self.game_dir()
        rows = ["<b>Game:</b> %s" % (folder.replace(os.path.expanduser("~"), "~") if folder else "not set yet (Settings)"),
                "<b>Getting mods:</b> %s" % self._nexus_summary(),
                "<b>Look:</b> %s" % self.theme_box.currentText()]
        self.summary.setText("<br>".join(rows))

    def _nexus_summary(self) -> str:
        if not common.NEXUS_AVAILABLE:
            return "from files you download yourself (Nexus Mods support is not available in this version)"
        if not self.nexus_on.isChecked():
            return "from files you download yourself; the Nexus Mods features are off"
        return ("experimental Nexus Mods features on, key entered" if self.key.text().strip()
                else "experimental Nexus Mods features on, but no key yet (Settings)")

    def _finish(self) -> None:
        folder = self.game_dir()
        if folder and os.path.realpath(folder) != os.path.realpath(self.mgr.game_dir or "/nonexistent"):
            self.mgr.set_game(folder, self.mgr.prefix)
        self.mgr.settings["nexus_key"] = self.key.text().strip()
        self.mgr.settings["nexus_enabled"] = self.nexus_on.isChecked()
        self.mgr.settings["theme"] = self.theme_box.currentData()
        self.mgr.settings["tile"] = self.tile_box.currentData()
        self.mgr.settings["setup_done"] = True
        self.mgr.save()
        self.finished_setup = True
        self.accept()

    def _skip(self) -> None:
        self.mgr.settings["setup_done"] = True      # do not ask again; Settings can start it
        for k, v in self._before.items():
            if v is None:
                self.mgr.settings.pop(k, None)
            else:
                self.mgr.settings[k] = v
        self.mgr.save()
        self._apply_theme()
        self.reject()

    def reject(self) -> None:                       # closing the window counts as "not now": ask again next time
        if not self.mgr.settings.get("setup_done"):
            for k, v in self._before.items():
                if v is None:
                    self.mgr.settings.pop(k, None)
                else:
                    self.mgr.settings[k] = v
            self._apply_theme()
        super().reject()
