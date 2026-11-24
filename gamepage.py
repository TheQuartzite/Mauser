"""The 'Game settings' page: resolution, window mode, vsync and field of view, without the game's launcher."""
from __future__ import annotations

import re
from typing import Callable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget,
)

import gameini

COMMON_SIZES = ((1280, 720), (1280, 800), (1366, 768), (1600, 900), (1920, 1080), (1920, 1200), (2560, 1080),
                (2560, 1440), (2560, 1600), (3440, 1440), (3840, 2160))


class GameSettingsTab(QWidget):
    logLine = Signal(str)
    saved = Signal()

    def __init__(self, ini_dir: Callable[[], Optional[str]], parent=None):
        super().__init__(parent)
        self.setObjectName("asphalt")
        self._ini_dir = ini_dir
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 8)
        outer.setSpacing(12)
        self.note = QLabel()
        self.note.setObjectName("banner")
        self.note.setWordWrap(True)
        self.note.hide()
        outer.addWidget(self.note)
        row = QHBoxLayout()
        panel = QFrame()
        panel.setObjectName("wall")
        panel.setMaximumWidth(760)
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(0, 0, 0, 16)
        stripe = QFrame()
        stripe.setObjectName("stripe")
        stripe.setFixedHeight(10)
        pl.addWidget(stripe)
        inner = QVBoxLayout()
        inner.setContentsMargins(20, 10, 20, 0)
        inner.setSpacing(12)
        pl.addLayout(inner)
        title = QLabel("Game settings")
        title.setObjectName("title")
        inner.addWidget(title)
        intro = QLabel("What the game's launcher would set, plus field of view. Saved into the game's own settings "
                       "files; the game reads them the next time it starts.")
        intro.setWordWrap(True)
        inner.addWidget(intro)
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)
        size_row = QHBoxLayout()
        self.size = QComboBox()
        self.size.setEditable(True)
        self.size.setMaxVisibleItems(14)
        self.size.setMinimumWidth(190)
        self.size.setToolTip("Pick a size or type your own, for example 2560 x 1440")
        screen_btn = QPushButton("Use this screen's size")
        screen_btn.clicked.connect(self._use_screen)
        size_row.addWidget(self.size)
        size_row.addWidget(screen_btn)
        size_row.addStretch(1)
        form.addRow("Resolution", size_row)
        self.mode = QComboBox()
        for key, text in gameini.MODES:
            self.mode.addItem(text, key)
        form.addRow("Shown as", self.mode)
        self.vsync = QCheckBox("Match the screen's refresh rate (vsync)")
        self.vsync.setToolTip("Leave this on unless you limit the frame rate another way. The game's physics misbehave "
                              "at very high frame rates.")
        form.addRow("Frame rate", self.vsync)
        self.fov_world = QSlider(Qt.Orientation.Horizontal)
        self.fov_first = QSlider(Qt.Orientation.Horizontal)
        for label, slider in (("Field of view", self.fov_world), ("Field of view, first person", self.fov_first)):
            slider.setRange(40, 140)
            slider.setPageStep(5)
            slider.setAccessibleName(label)
            shown = QLabel("70°")
            shown.setObjectName("heading")
            shown.setFixedWidth(44)
            shown.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            slider.valueChanged.connect(lambda v, target=shown: target.setText("%d°" % v))
            line = QHBoxLayout()
            line.addWidget(slider, 1)
            line.addWidget(shown)
            form.addRow(label, line)
        inner.addLayout(form)
        hint = QLabel("The game's own field of view is 70. Between 80 and 100 suits most monitors.")
        hint.setWordWrap(True)
        inner.addWidget(hint)
        buttons = QHBoxLayout()
        self.apply_btn = QPushButton("Save")
        self.apply_btn.setProperty("sign", "yellow")
        self.apply_btn.clicked.connect(self.apply)
        reload_btn = QPushButton("Read again from the game")
        reload_btn.clicked.connect(self.reload)
        defaults = QPushButton("Field of view back to 70")
        defaults.clicked.connect(lambda: (self.fov_world.setValue(gameini.DEFAULT_FOV), self.fov_first.setValue(gameini.DEFAULT_FOV)))
        buttons.addWidget(self.apply_btn)
        buttons.addWidget(reload_btn)
        buttons.addWidget(defaults)
        buttons.addStretch(1)
        inner.addLayout(buttons)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        inner.addWidget(self.status)
        row.addWidget(panel, 0, Qt.AlignmentFlag.AlignTop)
        row.addStretch(1)
        outer.addLayout(row, 1)

    # -- helpers ------------------------------------------------------------

    def _screen_size(self) -> Optional[tuple]:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return None
        size, ratio = screen.size(), screen.devicePixelRatio()
        return int(round(size.width() * ratio)), int(round(size.height() * ratio))

    def _use_screen(self) -> None:
        size = self._screen_size()
        if size:
            self.size.setCurrentText("%d x %d" % size)

    def parsed_size(self) -> Optional[tuple]:
        m = re.fullmatch(r"\s*(\d{3,5})\s*[xX×*,\s]\s*(\d{3,5})\s*", self.size.currentText())
        if not m:
            return None
        w, h = int(m.group(1)), int(m.group(2))
        return (w, h) if 640 <= w <= 16384 and 360 <= h <= 16384 else None

    def reload(self) -> None:
        folder = self._ini_dir()
        self.apply_btn.setEnabled(bool(folder))
        if not folder:
            self.note.setText("The game's settings folder was not found. Start Fallout 4 once from Steam, then come back.")
            self.note.show()
            return
        self.note.hide()
        s = gameini.load(folder)
        sizes = set(COMMON_SIZES) | {(s.width, s.height)}
        screen = self._screen_size()
        if screen:
            sizes.add(screen)
            sizes = {x for x in sizes if x[0] <= max(screen[0], s.width) and x[1] <= max(screen[1], s.height)}
        self.size.clear()
        for w, h in sorted(sizes):
            self.size.addItem("%d x %d%s" % (w, h, "  (this screen)" if (w, h) == screen else ""), (w, h))
        self.size.setCurrentText("%d x %d" % (s.width, s.height))
        self.mode.setCurrentIndex(max(0, self.mode.findData(s.mode)))
        self.vsync.setChecked(s.vsync)
        self.fov_world.setValue(s.fov_world)
        self.fov_first.setValue(s.fov_first)
        self.status.setText("")

    def apply(self) -> bool:
        folder = self._ini_dir()
        if not folder:
            return False
        text = self.size.currentText().replace("(this screen)", "")
        self.size.setCurrentText(text.strip())
        size = self.parsed_size()
        if size is None:
            self.status.setText("That resolution is not understood. Write it as width x height, for example 1920 x 1080.")
            return False
        s = gameini.DisplaySettings(size[0], size[1], self.mode.currentData(), self.vsync.isChecked(),
                                    self.fov_world.value(), self.fov_first.value())
        try:
            changed = gameini.save(folder, s)
        except OSError as e:
            self.status.setText("Not saved: %s" % e)
            self.logLine.emit("Game settings not saved: %s" % e)
            return False
        summary = "%d x %d, %s, vsync %s, field of view %d° / %d° first person" % (
            s.width, s.height, dict(gameini.MODES)[s.mode].lower(), "on" if s.vsync else "off", s.fov_world, s.fov_first)
        if changed:
            self.status.setText("Saved to %s. Used the next time the game starts." % " and ".join(changed))
            self.logLine.emit("Game settings saved (%s): %s." % (", ".join(changed), summary))
            self.saved.emit()
        else:
            self.status.setText("Nothing to save: the game already has these settings.")
        return True
