"""The app's looks: four themes, their stylesheet, the painted decorations and
the rat mascot.

  bahnhof       station signage: tiled wall, enamel plates, yellow signs, amber board
  bahnhof-dark  the same station at night
  aero          Frutiger Aero: sky and grass gradients, glossy glass panels
  aero-dark     Frutiger Aero at dusk
"""
from __future__ import annotations

import math
import os

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFontDatabase, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QLabel, QWidget

TILES = {"yellow": "#E3CF63", "turquoise": "#86C5B4", "blue": "#7FA9D9"}
DEFAULT_TILE = "yellow"
DEFAULT_THEME = "bahnhof"


def _grad(*stops: str) -> str:
    """Vertical gradient for stylesheets: _grad('#fff', '0.5 #eee', '#ddd')."""
    parts = []
    for i, s in enumerate(stops):
        if " " in s:
            pos, colour = s.split(" ", 1)
        else:
            pos, colour = str(i / max(1, len(stops) - 1)), s
        parts.append("stop:%s %s" % (pos, colour))
    return "qlineargradient(x1:0, y1:0, x2:0, y2:1, %s)" % ", ".join(parts)


_BAHNHOF = dict(
    label="Bahnhof", kind="bahnhof", body_font="Barlow", display_font="Barlow Semi Condensed", list_font="Share Tech Mono",
    list_size=16, bw=3, radius=6, panel_radius=6,
    win_bg="#2A2C2F", on_bg="#E9E7E0", dialog_bg="#F1EFE8", text="#16181A", muted="#3C4043", border="#16181A",
    panel_bg="#F1EFE8", panel_border="transparent", field_bg="#FFFFFF", picture_bg="#CFCBBF",
    btn_bg="#FFFFFF", btn_hover="#F6F3D0", btn_pressed="#F0D722", btn_text="#16181A", btn_border="#16181A",
    primary_bg="#F0D722", primary_hover="#F7E45A", primary_text="#16181A", primary_border="#16181A",
    nav_on_bg="#F0D722", nav_on_text="#16181A",
    disabled_bg="#E3E1DA", disabled_text="#8A8D90",
    sel_bg="#F0D722", sel_text="#16181A", header_bg="#FFFFFF",
    board_bg="#101112", board_text="#FFB000", board_sel_bg="#FFB000", board_sel_text="#101112",
    log_bg="#101112", log_text="#FFB000", chip_bg="#101112", chip_text="#FFB000", chip_hover="#2A2416",
    plate_bg="#FFFFFF", plate_text="#16181A", banner_bg="#F0D722", banner_text="#16181A",
    link="#1D4E9E", problem="#B3261E", note_error="#FF9C8A", scroll="#8A8D90", focus="#1D4E9E",
    wall="#F1EFE8", grout="#D9D5C9", edge="#F1EFE8",
    rat_body="#D9D5CB", rat_pink="#E7A3A0", rat_ink="#16181A", rat_accent="#F0D722", rat_ok="#3E9A45",
)

_BAHNHOF_DARK = dict(
    _BAHNHOF, label="Bahnhof at night",
    win_bg="#151618", on_bg="#E9E7E0", dialog_bg="#222428", text="#ECEAE4", muted="#B9BCC1", border="#9A9EA5",
    panel_bg="#222428", field_bg="#2D3035", picture_bg="#3A3D42",
    btn_bg="#2D3035", btn_hover="#3B3F33", btn_pressed="#F0D722", btn_text="#ECEAE4", btn_border="#9A9EA5",
    disabled_bg="#26282B", disabled_text="#74777C",
    header_bg="#2D3035", plate_bg="#2D3035", plate_text="#ECEAE4",
    link="#8DB9FF", problem="#FF8E80", scroll="#6B6F75", focus="#8DB9FF",
    wall="#222428", grout="#34373C", edge="#8E9197",
)

_AERO = dict(
    label="Frutiger Aero", kind="aero", body_font="Hind", display_font="Hind", list_font="Hind",
    list_size=16, bw=1, radius=12, panel_radius=14,
    win_bg=_grad("#6FC2F2", "0.55 #C4EAFB", "#C4EFB4"), on_bg="#0B2A4A", dialog_bg=_grad("#DDF3FF", "#F4FBFF"),
    text="#0B2A4A", muted="#2F4F6F", border="#6FAAD6",
    panel_bg="rgba(255, 255, 255, 205)", panel_border="rgba(255, 255, 255, 230)", field_bg="#FFFFFF",
    picture_bg="rgba(120, 180, 220, 90)",
    btn_bg=_grad("#FFFFFF", "0.5 #E6F4FD", "0.51 #CFEAFA", "#EAF7FF"), btn_hover=_grad("#FFFFFF", "0.5 #D6EEFD", "0.51 #B8E0F8", "#DDF2FF"),
    btn_pressed=_grad("#B8E0F8", "#E6F4FD"), btn_text="#0B2A4A", btn_border="#6FAAD6",
    primary_bg=_grad("#D8FAB0", "0.5 #9BE36A", "0.51 #6FD043", "#A6EB7C"), primary_hover=_grad("#E6FFC8", "0.5 #AEEB80", "0.51 #7EDB52", "#B9F293"),
    primary_text="#0A3410", primary_border="#3E9A24",
    nav_on_bg=_grad("#D5EEFF", "0.5 #9FD6FA", "0.51 #74C2F5", "#A9DCFB"), nav_on_text="#082742",
    disabled_bg="rgba(255, 255, 255, 120)", disabled_text="#6C8196",
    sel_bg=_grad("#D2EDFF", "#8FD0FA"), sel_text="#082742", header_bg=_grad("#FFFFFF", "#E3F3FD"),
    board_bg="rgba(255, 255, 255, 205)", board_text="#0B2A4A", board_sel_bg=_grad("#D2EDFF", "#8FD0FA"), board_sel_text="#082742",
    log_bg="rgba(8, 44, 84, 220)", log_text="#DDF4FF", chip_bg=_grad("#1F6CA8", "#0E4474"), chip_text="#E8F7FF", chip_hover=_grad("#2A7DBD", "#145588"),
    plate_bg=_grad("#FFFFFF", "0.5 #EEF8FF", "#D9EFFC"), plate_text="#0B2A4A", banner_bg=_grad("#FFF6C4", "#FFE27A"), banner_text="#3A2B00",
    link="#0B5CAD", problem="#B3261E", note_error="#9C1B14", scroll="#7FB2D8", focus="#1E88D6",
    wall="#BFE6FA", grout="#FFFFFF", edge="#57B84A",
    rat_body="#FFFFFF", rat_pink="#F7A8B8", rat_ink="#0B2A4A", rat_accent="#7ED957", rat_ok="#2E9A28",
)

_AERO_DARK = dict(
    _AERO, label="Frutiger Aero at dusk",
    win_bg=_grad("#03162B", "0.55 #0A3A5E", "#0B4A3C"), on_bg="#E4F6FF", dialog_bg=_grad("#0C2B47", "#0A2238"),
    text="#E4F6FF", muted="#A9CDE4", border="#4F9CCB",
    panel_bg="rgba(8, 32, 54, 205)", panel_border="rgba(140, 210, 255, 90)", field_bg="#0B2740",
    picture_bg="rgba(120, 190, 240, 50)",
    btn_bg=_grad("#3B759F", "0.5 #26587F", "0.51 #1A466A", "#245A82"), btn_hover=_grad("#4A88B5", "0.5 #2F6791", "0.51 #21557C", "#2D6A95"),
    btn_pressed=_grad("#1A466A", "#2B6088"), btn_text="#EAF8FF", btn_border="#5FB6E8",
    primary_bg=_grad("#A6EE7E", "0.5 #6FD24A", "0.51 #49B82E", "#7ADB58"), primary_hover=_grad("#BDF59B", "0.5 #83DC61", "0.51 #5BC640", "#8EE36E"),
    primary_text="#04200A", primary_border="#8EE36E",
    nav_on_bg=_grad("#8FD4FF", "0.5 #55B6F2", "0.51 #2F9DE4", "#62BDF4"), nav_on_text="#031A2E",
    disabled_bg="rgba(20, 50, 75, 150)", disabled_text="#6E91A8",
    sel_bg=_grad("#1F78B8", "#135A90"), sel_text="#FFFFFF", header_bg=_grad("#17405F", "#0F3350"),
    board_bg="rgba(8, 32, 54, 205)", board_text="#E4F6FF", board_sel_bg=_grad("#1F78B8", "#135A90"), board_sel_text="#FFFFFF",
    log_bg="rgba(2, 14, 26, 225)", log_text="#BDEBFF", chip_bg=_grad("#0E4474", "#082C4E"), chip_text="#E8F7FF", chip_hover=_grad("#145588", "#0B3A63"),
    plate_bg=_grad("#2B6088", "0.5 #1C4A6E", "#143C5C"), plate_text="#EAF8FF", banner_bg=_grad("#FFE27A", "#F2C230"), banner_text="#2A1E00",
    link="#8CCBFF", problem="#FF9A8C", note_error="#FFB3A8", scroll="#3F7CA8", focus="#8FD4FF",
    wall="#0A3A5E", grout="#8FD4FF", edge="#3FA83A",
    rat_body="#EAF8FF", rat_pink="#F7A8B8", rat_ink="#031A2E", rat_accent="#7ED957", rat_ok="#7ADB58",
)

THEMES = {"bahnhof": _BAHNHOF, "bahnhof-dark": _BAHNHOF_DARK, "aero": _AERO, "aero-dark": _AERO_DARK}

current: dict = dict(_BAHNHOF, name=DEFAULT_THEME, tile=TILES[DEFAULT_TILE])


animations_paused = False      # set while the game is running, so the app takes no time from it


def use(name: str, tile: str = DEFAULT_TILE) -> dict:
    """Make a theme the current one. Unknown names fall back to the default."""
    global current
    name = name if name in THEMES else DEFAULT_THEME
    current = dict(THEMES[name], name=name, tile=TILES.get(tile, TILES[DEFAULT_TILE]))
    return current


def c(key: str) -> str:
    return str(current[key])


def load_fonts(resources: str) -> list:
    """Register the bundled fonts. Returns the family names that loaded."""
    families = []
    folder = os.path.join(resources, "fonts")
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return families
    for name in names:
        if name.lower().endswith(".ttf"):
            font_id = QFontDatabase.addApplicationFont(os.path.join(folder, name))
            if font_id >= 0:
                families += QFontDatabase.applicationFontFamilies(font_id)
    return sorted(set(families))


def write_arrow(folder: str) -> str:
    """The small chevron for drop-down boxes, as a file the stylesheet can point at."""
    path = os.path.join(folder, "chevron-%s.svg" % current["name"])
    try:
        os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14">'
                    '<path d="M2 4.5l5 5 5-5" fill="none" stroke="%s" stroke-width="2.4" stroke-linecap="square"/>'
                    '</svg>' % current["text"])
    except OSError:
        return ""
    return path


def write_tick(folder: str) -> str:
    """The white tick shown inside a ticked box."""
    path = os.path.join(folder, "tick.svg")
    try:
        os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">'
                    '<path d="M3.5 8.5l3 3 6-7" fill="none" stroke="#FFFFFF" stroke-width="2.4" stroke-linecap="round" '
                    'stroke-linejoin="round"/></svg>')
    except OSError:
        return ""
    return path


def stylesheet(chevron: str = "", tick: str = "") -> str:
    p = dict(current)
    p["body"] = '"%s", "Noto Sans", "DejaVu Sans", sans-serif' % p["body_font"]
    p["display"] = '"%s", "%s", sans-serif' % (p["display_font"], p["body_font"])
    p["mono"] = '"Share Tech Mono", "DejaVu Sans Mono", monospace'
    p["listf"] = '"%s", "DejaVu Sans Mono", monospace' % p["list_font"]
    p["arrow"] = ('QComboBox::down-arrow { image: url("%s"); width: 14px; height: 14px; }' % chevron.replace("\\", "/")
                  if chevron else "")
    p["tick"] = tick.replace("\\", "/")
    p["band"] = p["tile"] if p["kind"] == "bahnhof" else _grad("#8EE36E", "#3FA83A")
    p["nav_r"] = "0" if p["kind"] == "bahnhof" else "0"
    p["plate_border"] = "%dpx solid %s" % (p["bw"], p["border"])
    p["title_size"] = 24 if p["kind"] == "bahnhof" else 22
    return """
* { font-family: %(body)s; font-size: 15px; }
QMainWindow, QWidget#asphalt { background: %(win_bg)s; }
QLabel { color: %(on_bg)s; background: transparent; }
QToolTip { background: %(field_bg)s; color: %(text)s; border: 1px solid %(border)s; padding: 4px; }

QDialog, QMessageBox, QProgressDialog { background: %(dialog_bg)s; }
QDialog QLabel, QMessageBox QLabel, QProgressDialog QLabel, QFrame#wall QLabel { color: %(text)s; }
QGroupBox { color: %(text)s; font-weight: 700; border: 2px solid %(border)s; border-radius: %(radius)spx; margin-top: 14px; padding: 10px 8px 8px 8px; background: %(field_bg)s; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QRadioButton, QCheckBox { color: %(text)s; spacing: 8px; min-height: 26px; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border: 2px solid %(border)s; border-radius: 4px; background: %(field_bg)s; }
QCheckBox::indicator:checked { background: %(focus)s; border-color: %(focus)s; image: url("%(tick)s"); }
QCheckBox::indicator:disabled { border-color: %(disabled_text)s; }
QWidget#asphalt QCheckBox { color: %(on_bg)s; }
QFrame#wall QCheckBox { color: %(text)s; }
QSlider::groove:horizontal { height: 6px; background: %(scroll)s; border-radius: 3px; }
QSlider::sub-page:horizontal { background: %(focus)s; border-radius: 3px; }
QSlider::handle:horizontal { width: 18px; margin: -7px 0; background: %(btn_bg)s; border: 2px solid %(border)s; border-radius: 9px; }

QPushButton { background: %(btn_bg)s; color: %(btn_text)s; border: %(bw)spx solid %(btn_border)s; border-radius: %(radius)spx; padding: 6px 14px; font-weight: 600; min-height: 22px; }
QPushButton:hover { background: %(btn_hover)s; }
QPushButton:pressed { background: %(btn_pressed)s; }
QPushButton:disabled { color: %(disabled_text)s; border-color: %(disabled_text)s; background: %(disabled_bg)s; }
QPushButton[sign="yellow"] { background: %(primary_bg)s; color: %(primary_text)s; border-color: %(primary_border)s; font-weight: 700; }
QPushButton[sign="yellow"]:hover { background: %(primary_hover)s; }
QPushButton[sign="yellow"]:disabled { background: %(disabled_bg)s; color: %(disabled_text)s; border-color: %(disabled_text)s; }
QPushButton[sign="nav"] { border-radius: 0; border-right-width: 0; padding: 8px 18px; font-size: 16px; }
QPushButton[sign="nav"]:checked { background: %(nav_on_bg)s; color: %(nav_on_text)s; font-weight: 700; }
QPushButton[navend="first"] { border-top-left-radius: %(radius)spx; border-bottom-left-radius: %(radius)spx; }
QPushButton[navend="last"] { border-right-width: %(bw)spx; border-top-right-radius: %(radius)spx; border-bottom-right-radius: %(radius)spx; }
QPushButton[sign="board"] { background: %(chip_bg)s; color: %(chip_text)s; border: 0; border-radius: %(radius)spx; font-family: %(mono)s; font-weight: 400; padding: 8px 12px; }
QPushButton[sign="board"]:hover { background: %(chip_hover)s; }

QLineEdit, QComboBox, QSpinBox { background: %(field_bg)s; color: %(text)s; border: %(bw)spx solid %(border)s; border-radius: %(radius)spx; padding: 6px 10px; min-height: 22px; selection-background-color: %(focus)s; selection-color: #FFFFFF; }
QLineEdit:focus, QComboBox:focus { border-color: %(focus)s; }
QComboBox { combobox-popup: 0; }
QComboBox::drop-down { border: 0; width: 28px; }
%(arrow)s
QComboBox QAbstractItemView { background: %(field_bg)s; color: %(text)s; border: 1px solid %(border)s; selection-background-color: %(sel_bg)s; selection-color: %(sel_text)s; outline: 0; }

QFrame#plate { background: %(plate_bg)s; border: %(plate_border)s; border-radius: %(radius)spx; }
QFrame#plate QLabel { color: %(plate_text)s; font-family: %(display)s; font-size: 28px; font-weight: 700; }
QLabel#title { background: %(plate_bg)s; color: %(plate_text)s; border: %(plate_border)s; border-radius: %(radius)spx; padding: 8px 12px; font-family: %(display)s; font-size: %(title_size)spx; font-weight: 700; }
QLabel#heading { font-size: 13px; font-weight: 700; }
QLabel#banner { background: %(banner_bg)s; color: %(banner_text)s; border: %(bw)spx solid %(border)s; border-radius: %(radius)spx; padding: 8px 12px; font-weight: 600; }
QLabel#picture { background: %(picture_bg)s; border-radius: 4px; color: %(muted)s; }
QLabel#emptyhint { color: %(board_text)s; font-family: %(listf)s; font-size: %(list_size)spx; }
QFrame#wall { background: %(panel_bg)s; border: 1px solid %(panel_border)s; border-radius: %(panel_radius)spx; }
QFrame#stripe { background: %(band)s; border-top-left-radius: %(panel_radius)spx; border-top-right-radius: %(panel_radius)spx; }
QFrame#band { background: %(band)s; }
QFrame#boardempty { background: %(board_bg)s; border: 1px solid %(panel_border)s; border-radius: %(panel_radius)spx; }

QTreeWidget, QListWidget, QTextBrowser, QPlainTextEdit { background: %(panel_bg)s; color: %(text)s; border: %(bw)spx solid %(border)s; border-radius: %(radius)spx; outline: 0; selection-background-color: %(focus)s; selection-color: #FFFFFF; }
QFrame#wall QTreeWidget, QFrame#wall QTextBrowser { background: %(field_bg)s; border-width: 1px; border-radius: %(radius)spx; }
QTreeWidget::item { min-height: 28px; padding: 1px 4px; }
QTreeWidget::item:selected, QListWidget::item:selected { background: %(sel_bg)s; color: %(sel_text)s; }
QHeaderView::section { background: %(header_bg)s; color: %(text)s; font-weight: 700; border: 0; border-bottom: 1px solid %(border)s; padding: 5px 8px; }

QTreeWidget#board { background: %(board_bg)s; color: %(board_text)s; border: 1px solid %(panel_border)s; border-radius: %(panel_radius)spx; padding: 6px; font-family: %(listf)s; font-size: %(list_size)spx; }
QTreeWidget#board::item { min-height: 70px; padding: 0 8px; border: 0; }
QTreeWidget#board::item:selected { background: %(board_sel_bg)s; color: %(board_sel_text)s; }
QLabel#setupboard { background: %(log_bg)s; color: %(log_text)s; border-radius: %(panel_radius)spx; padding: 14px 18px; font-family: %(mono)s; font-size: 21px; }
QPlainTextEdit#log { background: %(log_bg)s; color: %(log_text)s; border: 0; border-radius: %(panel_radius)spx; padding: 6px; font-family: %(mono)s; font-size: 14px; }

QScrollBar:vertical { background: transparent; width: 14px; margin: 2px; }
QScrollBar::handle:vertical { background: %(scroll)s; border-radius: 5px; min-height: 28px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 14px; margin: 2px; }
QScrollBar::handle:horizontal { background: %(scroll)s; border-radius: 5px; min-width: 28px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QSplitter::handle { background: transparent; }
QScrollArea { background: transparent; border: 0; }
QScrollArea > QWidget > QWidget { background: transparent; }
QProgressBar { background: %(field_bg)s; border: %(bw)spx solid %(border)s; border-radius: %(radius)spx; text-align: center; color: %(text)s; min-height: 24px; }
QProgressBar::chunk { background: %(primary_bg)s; border-radius: 3px; }
""" % p


class Backdrop(QWidget):
    """The header's background. Bahnhof: a wall of large tiles. Aero: sky, bubbles and a glossy sheen."""

    TILE_W, TILE_H = 96, 48
    # x, starting y, radius, opacity, speed (widget heights per second)
    BUBBLES = ((0.42, 0.30, 26, 38, 0.030), (0.50, 0.72, 15, 46, 0.050), (0.58, 0.28, 11, 54, 0.065), (0.66, 0.62, 30, 30, 0.026),
               (0.74, 0.22, 9, 60, 0.075), (0.80, 0.70, 18, 40, 0.044), (0.36, 0.78, 8, 56, 0.080), (0.90, 0.35, 12, 44, 0.058))

    def __init__(self, parent=None):
        super().__init__(parent)
        self._clock = 0.0
        self._tick = QTimer(self)
        self._tick.setInterval(50)                 # 20 frames a second is plenty for slow bubbles
        self._tick.timeout.connect(self._advance)

    def _advance(self) -> None:
        if current["kind"] != "aero" or animations_paused or not self.isVisible() or self.window().isMinimized():
            self._tick.stop()                      # painting starts it again when it is wanted
            return
        self._clock += self._tick.interval() / 1000.0
        self.update()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._tick.stop()

    def paintEvent(self, _event):
        if current["kind"] == "aero" and not animations_paused and not self._tick.isActive():
            self._tick.start()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        if current["kind"] == "bahnhof":
            p.fillRect(self.rect(), QColor(current["wall"]))
            grout = QColor(current["grout"])
            y = self.TILE_H - 2
            while y < h:
                p.fillRect(QRect(0, y, w, 2), grout)
                y += self.TILE_H
            x = self.TILE_W - 2
            while x < w:
                p.fillRect(QRect(x, 0, 2, h), grout)
                x += self.TILE_W
        else:
            dark = current["name"].endswith("dark")
            sky = QLinearGradient(0, 0, 0, h)
            for pos, colour in ((0.0, "#04213D"), (1.0, "#0D4A72")) if dark else ((0.0, "#3FA9EC"), (1.0, "#9ADBFA")):
                sky.setColorAt(pos, QColor(colour))
            p.fillRect(self.rect(), sky)
            p.setPen(Qt.PenStyle.NoPen)
            # bubbles drift slowly upwards; positions are fractions of the size, so resizing does not make them jump
            t = self._clock
            for i, (fx, fy, r, alpha, speed) in enumerate(self.BUBBLES):
                u = (fy - t * speed) % 1.0                                # 1 = just below the bottom edge, 0 = just above the top
                y = -r + u * (h + 2 * r)
                cx = fx * w + math.sin(t * 0.7 + i * 1.9) * 6
                cy = y
                p.setBrush(QColor(255, 255, 255, alpha))
                p.drawEllipse(QPointF(cx, cy), r, r)
                p.setBrush(QColor(255, 255, 255, min(255, alpha + 70)))
                p.drawEllipse(QPointF(cx - r * 0.35, cy - r * 0.35), r * 0.28, r * 0.28)
            sheen = QPainterPath()
            sheen.moveTo(0, 0)
            sheen.lineTo(w, 0)
            sheen.lineTo(w, h * 0.30)
            sheen.quadTo(w * 0.5, h * 0.62, 0, h * 0.38)
            sheen.closeSubpath()
            p.setBrush(QColor(255, 255, 255, 26 if dark else 60))
            p.drawPath(sheen)
        p.end()


class PlatformEdge(QWidget):
    """The strip along the bottom. Bahnhof: platform-edge markers. Aero: a glossy green rise."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def sizeHint(self) -> QSize:
        return QSize(200, 28)

    def minimumSizeHint(self) -> QSize:
        return QSize(40, 28)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        if current["kind"] == "bahnhof":
            edge = QColor(current["edge"])
            x = 0
            while x < w:
                p.fillRect(QRect(x, 0, 28, 14), edge)
                x += 44
            p.fillRect(QRect(0, 20, w, 8), edge)
        else:
            grass = QLinearGradient(0, 6, 0, 28)
            grass.setColorAt(0.0, QColor("#9BE36A"))
            grass.setColorAt(0.5, QColor(current["edge"]))
            grass.setColorAt(1.0, QColor("#2E8A2A"))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(grass)
            p.drawRoundedRect(QRectF(0, 8, w, 20), 10, 10)
            p.setBrush(QColor(255, 255, 255, 80))
            p.drawRoundedRect(QRectF(6, 10, w - 12, 6), 3, 3)
        p.end()


MASCOT_NAME = "Vinny"
RAT_STATES = ("idle", "work", "done", "error")

_MARK = ('<circle cx="38" cy="34" r="15" fill="{fg}"/>'
         '<circle cx="38" cy="34" r="7" fill="{pink}"/>'
         '<circle cx="90" cy="34" r="15" fill="{fg}"/>'
         '<circle cx="90" cy="34" r="7" fill="{pink}"/>'
         '<path d="M30 98 V40 L64 78 L98 40 V98" fill="none" stroke="{fg}" stroke-width="15" stroke-linejoin="miter" stroke-linecap="butt"/>'
         '<path d="M100 93 C118 96 118 76 108 74" fill="none" stroke="{pink}" stroke-width="6" stroke-linecap="round"/>')


def mark_pixmap(size: int, scale: float = 1.0) -> QPixmap:
    """The app's mark (an M with ears and a tail) in the current theme's colours, for the name plate."""
    from PySide6.QtGui import QImage
    px = max(8, int(size * max(1.0, scale)))
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="14 12 100 100">%s</svg>' % (
        px, px, _MARK.format(fg=current["plate_text"], pink=current["rat_pink"]))
    image = QImage.fromData(svg.encode("utf-8"), "SVG")
    if image.isNull():
        return QPixmap()
    pix = QPixmap.fromImage(image)
    pix.setDevicePixelRatio(max(1.0, scale))
    return pix


RUN_FRAMES = 6            # frames in the running loop


def rat_svg(state: str = "idle", frame: int = -1) -> str:
    """The mascot: a station rat. States: idle, work (a parcel strapped to his back), done (holding
    up a tick), error (crossed-out eye, fur on end)."""
    body, pink, ink = current["rat_body"], current["rat_pink"], current["rat_ink"]
    accent, ok, bad = current["rat_accent"], current["rat_ok"], "#D8362B"
    o = []
    running = state == "work" and frame >= 0          # frame -1 is the still picture
    f = frame % RUN_FRAMES if running else 0
    bob = (-5, -6.4, -7.2, -6.4, -5, -4.2)[f] if running else 0      # lifted off the ground, and bobbing
    o.append('<g transform="translate(%.1f %.1f) rotate(%d 46 70)">' % (-3 if running else 0, bob, 9 if running else 0))   # leaning into the run
    tail = ("M24 80 C2 82 2 52 20 54", "M24 80 C0 80 0 56 16 50", "M24 80 C-2 78 2 58 12 48",
            "M24 80 C0 80 0 56 16 50", "M24 80 C2 82 2 52 20 54", "M24 80 C4 84 4 54 22 58")[f]
    o.append('<path d="%s" fill="none" stroke="%s" stroke-width="5" stroke-linecap="round"/>' % (tail, pink))
    if not running:
        o.append('<ellipse cx="38" cy="90" rx="9" ry="4" fill="%s"/><ellipse cx="60" cy="90" rx="9" ry="4" fill="%s"/>' % (pink, pink))
    o.append('<ellipse cx="46" cy="70" rx="27" ry="20" fill="%s" stroke="%s" stroke-width="2.5"/>' % (body, ink))
    if state == "work":         # the parcel on his back, held by two straps round the belly
        o.append('<path d="M31 53 C27 66 30 80 36 88 M47 51 C46 66 48 80 53 89" fill="none" stroke="%s" stroke-width="2.4" '
                 'stroke-linecap="round"/>' % ink)
        o.append('<rect x="25" y="33" width="27" height="20" rx="2" fill="%s" stroke="%s" stroke-width="2.5"/>' % (accent, ink))
        o.append('<path d="M25 43 h27 M38.5 33 v20" stroke="%s" stroke-width="2"/>' % ink)
        o.append('<rect x="34.5" y="39.5" width="8" height="7" rx="1" fill="%s" stroke="%s" stroke-width="1.6"/>' % (body, ink))
    if state == "error":
        o.append('<path d="M26 56 l5 -9 l4 8 l5 -10 l4 9 l5 -9 l4 8" fill="none" stroke="%s" stroke-width="2.5" '
                 'stroke-linejoin="round" stroke-linecap="round"/>' % ink)
    o.append('<circle cx="60" cy="38" r="11" fill="%s" stroke="%s" stroke-width="2.5"/>' % (body, ink))
    o.append('<circle cx="60" cy="38" r="6" fill="%s"/>' % pink)
    o.append('<path d="M58 44 C58 36 70 36 80 44 L96 56 C90 66 70 68 60 62 C54 58 56 50 58 44 Z" fill="%s" stroke="%s" '
             'stroke-width="2.5" stroke-linejoin="round"/>' % (body, ink))
    o.append('<circle cx="95" cy="56" r="3.2" fill="%s"/>' % pink)
    if state == "error":
        o.append('<path d="M73 47 l6 6 M79 47 l-6 6" stroke="%s" stroke-width="2.6" stroke-linecap="round"/>' % ink)
        o.append('<path d="M88 60 l9 6 M87 62 l7 8" stroke="%s" stroke-width="1.6" stroke-linecap="round"/>' % ink)
    else:
        o.append('<circle cx="76" cy="50" r="3" fill="%s"/>' % ink)
        o.append('<path d="M88 58 l10 -3 M88 60 l10 3" stroke="%s" stroke-width="1.6" stroke-linecap="round"/>' % ink)
    if state == "done":
        o.append('<path d="M66 68 L80 40" stroke="%s" stroke-width="6" stroke-linecap="round"/>' % ink)
        o.append('<path d="M66 68 L80 40" stroke="%s" stroke-width="3" stroke-linecap="round"/>' % body)
    else:
        o.append('<ellipse cx="72" cy="76" rx="6" ry="4" fill="%s" stroke="%s" stroke-width="2"/>' % (pink, ink))
    o.append('</g>')
    if running:
        # Cartoon sprint: the legs are a pink figure-of-eight blur with two feet racing round it.
        import math

        def loop(t):                      # a sideways figure of eight under the body
            d = 1 + math.sin(t) ** 2
            return 49 + 25 * math.cos(t) / d, 89 + 24 * math.sin(t) * math.cos(t) / d

        pts = [loop(i * 2 * math.pi / 40) for i in range(41)]
        track = "M" + " L".join("%.1f %.1f" % p for p in pts)
        o.append('<path d="%s" fill="none" stroke="%s" stroke-opacity="0.30" stroke-width="7.5" stroke-linecap="round" '
                 'stroke-linejoin="round"/>' % (track, pink))
        o.append('<path d="%s" fill="none" stroke="%s" stroke-opacity="0.55" stroke-width="2.6" stroke-linejoin="round"/>' % (track, pink))
        for foot in (0.0, math.pi):
            for back, alpha in ((0.75, 0.25), (0.4, 0.5), (0.0, 1.0)):       # two ghosts trail each foot
                t = f * 2 * math.pi / RUN_FRAMES + foot - back
                x, y = loop(t)
                x2, y2 = loop(t + 0.05)
                o.append('<ellipse cx="%.1f" cy="%.1f" rx="6.5" ry="3.6" fill="%s" fill-opacity="%.2f" transform="rotate(%.0f %.1f %.1f)"/>'
                         % (x, y, pink, alpha, math.degrees(math.atan2(y2 - y, x2 - x)), x, y))
    if state == "work":
        d = (0, -3, -6, -9, -5, -2)[f] if running else 0          # speed lines streaming backwards
        o.append('<path d="M%d 62 h9 M%d 70 h8 M%d 78 h7" stroke="%s" stroke-width="2.4" stroke-linecap="round"/>'
                 % (6 + d, 9 + (d * 2) % 7 - 3, 7 + d, current["on_bg"]))
    elif state == "done":
        o.append('<circle cx="82" cy="28" r="14" fill="%s" stroke="%s" stroke-width="2.5"/>' % (ok, ink))
        o.append('<path d="M75 28 l5 6 l9 -11" fill="none" stroke="#FFFFFF" stroke-width="3.6" stroke-linecap="round" '
                 'stroke-linejoin="round"/>')
    elif state == "error":
        o.append('<path d="M90 12 v16" stroke="%s" stroke-width="6" stroke-linecap="round"/>' % bad)
        o.append('<circle cx="90" cy="38" r="3.6" fill="%s"/>' % bad)
    return '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">%s</svg>' % "".join(o)


_rat_cache: dict = {}


def rat_pixmap(state: str, size: int, scale: float = 1.0, frame: int = -1) -> QPixmap:
    """The mascot as a picture, drawn sharp for the screen's scale. frame >= 0 picks a frame of the run."""
    from PySide6.QtGui import QImage
    px = max(8, int(size * max(1.0, scale)))
    key = (current["name"], state, px, frame % RUN_FRAMES if frame >= 0 else -1)
    if key in _rat_cache:
        return _rat_cache[key]
    if len(_rat_cache) > 200:
        _rat_cache.clear()
    svg = rat_svg(state if state in RAT_STATES else "idle", frame).replace('width="100" height="100"',
                                                                    'width="%d" height="%d"' % (px, px), 1)
    image = QImage.fromData(svg.encode("utf-8"), "SVG")      # drawn at the final size, so it stays crisp
    if image.isNull():
        return QPixmap()
    pix = QPixmap.fromImage(image)
    pix.setDevicePixelRatio(max(1.0, scale))
    _rat_cache[key] = pix
    return pix


class RatLabel(QLabel):
    """A label that shows the mascot. While he is working he runs: a short loop of frames."""

    def __init__(self, size: int = 92, parent=None):
        super().__init__(parent)
        self._size = size
        self._frame = 0
        self.state = "idle"
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._run = QTimer(self)
        self._run.setInterval(70)
        self._run.timeout.connect(self._step)
        self.refresh()

    def set_state(self, state: str) -> None:
        self.state = state if state in RAT_STATES else "idle"
        self.refresh()

    def _step(self) -> None:
        self._frame = (self._frame + 1) % RUN_FRAMES
        self.setPixmap(rat_pixmap("work", self._size, self.devicePixelRatioF(), self._frame))

    def refresh(self) -> None:
        moving = self.state == "work" and self.isVisible()
        if moving and not self._run.isActive():
            self._run.start()
        elif not moving and self._run.isActive():
            self._run.stop()
        self.setPixmap(rat_pixmap(self.state, self._size, self.devicePixelRatioF(), self._frame if self.state == "work" else -1))

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._run.stop()
