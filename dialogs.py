"""Smaller windows of the Installed tab: conflicts, save check, tools, profiles."""
from __future__ import annotations

import datetime
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

import profiles
import savefile
import theme


class ConflictDialog(QDialog):
    """Every file more than one enabled mod provides: who wins, who is overridden."""

    def __init__(self, parent, mgr, focus_mod_id: str = ""):
        super().__init__(parent)
        self.mgr = mgr
        self.setWindowTitle("Conflicts")
        self.resize(980, 600)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.mod_box = QComboBox()
        self.mod_box.setMaxVisibleItems(14)
        self.mod_box.addItem("All mods", "")
        for m in mgr.mods:
            if m.enabled:
                self.mod_box.addItem(m.name, m.id)
        self.mod_box.setCurrentIndex(max(0, self.mod_box.findData(focus_mod_id)))
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter by file name or folder, for example textures or .nif")
        self.filter.setAccessibleName("Filter files")
        top.addWidget(QLabel("Show"))
        top.addWidget(self.mod_box)
        top.addWidget(self.filter, 1)
        lay.addLayout(top)
        self.summary = QLabel()
        lay.addWidget(self.summary)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["File", "Used from (wins)", "Overridden"])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 430)
        self.tree.setColumnWidth(1, 220)
        lay.addWidget(self.tree, 1)
        lay.addWidget(QLabel("The mod lower in the Installed list wins. Move a mod down to make its files the ones used."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        names = {m.id: m.name for m in mgr.mods}
        owners: dict = {}
        for mod in mgr.mods:
            if mod.enabled:
                for _path, parts in mgr._walk(mod):
                    owners.setdefault("/".join(parts).lower(), ("/".join(parts), []))[1].append(mod.id)
        self.rows = [(shown, ids[-1], ids[:-1], names) for _k, (shown, ids) in sorted(owners.items()) if len(ids) > 1]
        self.mod_box.currentIndexChanged.connect(lambda _i: self.fill())
        self.filter.textChanged.connect(lambda _t: self.fill())
        self.fill()

    def fill(self) -> None:
        wanted, text = self.mod_box.currentData() or "", self.filter.text().strip().lower()
        self.tree.clear()
        shown = wins = loses = 0
        for path, winner, losers, names in self.rows:
            if wanted and wanted != winner and wanted not in losers:
                continue
            if text and text not in path.lower():
                continue
            shown += 1
            if shown <= 5000:
                item = QTreeWidgetItem([path, names.get(winner, "?"), ", ".join(names.get(i, "?") for i in losers)])
                if wanted and wanted in losers:
                    item.setForeground(2, QBrush(QColor(theme.c("problem"))))
                self.tree.addTopLevelItem(item)
            wins += wanted == winner
            loses += wanted in losers
        if not self.rows:
            self.summary.setText("No two enabled mods provide the same file.")
        elif wanted:
            self.summary.setText("%d files shared with other mods: this mod wins %d and is overridden on %d.%s" % (
                shown, wins, loses, " Showing the first 5000." if shown > 5000 else ""))
        else:
            self.summary.setText("%d files are provided by more than one mod.%s" % (shown, " Showing the first 5000." if shown > 5000 else ""))


class SaveCheckDialog(QDialog):
    """Pick a save; see which of its plugins are missing or switched off now."""

    def __init__(self, parent, ini_dir, present: dict, added: dict):
        super().__init__(parent)
        self.present, self.added = present, added
        self.setWindowTitle("Check a save")
        self.resize(820, 600)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.box = QComboBox()
        self.box.setMaxVisibleItems(14)
        self.box.setAccessibleName("Save to check")
        for path, when in savefile.list_saves(ini_dir)[:300]:
            self.box.addItem("%s   (%s)" % (os.path.basename(path), datetime.datetime.fromtimestamp(when).strftime("%Y-%m-%d %H:%M")), path)
        browse = QPushButton("Other file…")
        browse.clicked.connect(self._browse)
        top.addWidget(QLabel("Save"))
        top.addWidget(self.box, 1)
        top.addWidget(browse)
        lay.addLayout(top)
        self.verdict = QLabel()
        self.verdict.setWordWrap(True)
        self.verdict.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.verdict)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Plugin", "In this save", "Now"])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(0, 400)
        self.tree.setColumnWidth(1, 150)
        lay.addWidget(self.tree, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.box.currentIndexChanged.connect(lambda _i: self.check(self.box.currentData()))
        if self.box.count():
            self.check(self.box.currentData())
        else:
            self.verdict.setText("No saves were found in the game's Saves folder. Use \"Other file…\" to pick one.")

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a save", os.path.expanduser("~"), "Fallout 4 saves (*.fos);;All files (*)")
        if path:
            self.check(path)

    def check(self, path: str) -> None:
        self.tree.clear()
        if not path:
            return
        try:
            info = savefile.read(path)
        except savefile.SaveError as e:
            self.verdict.setText('<span style="color: %s; font-weight: 600">%s</span>' % (theme.c("problem"), e))
            return
        result = savefile.compare(info, self.present, self.added)
        who = ", ".join(x for x in (info.player, ("level %d" % info.level) if info.level else "", info.location,
                                    ("game %s" % info.game_version) if info.game_version else "") if x)
        bad = len(result["missing"]) + len(result["disabled"])
        colour = theme.c("problem") if bad else theme.c("text")
        self.verdict.setText("%s<span style=\"color: %s; font-weight: 600\">%s</span>%s" % (
            (who + "<br>") if who else "", colour,
            "This save expects %d plugins. %d are missing and %d are switched off." % (
                len(info.plugins) + len(info.light), len(result["missing"]), len(result["disabled"])) if bad else
            "All %d plugins this save expects are installed and switched on." % (len(info.plugins) + len(info.light)),
            " Loading it without them can lose items, quests or settlements they added." if bad else ""))
        missing, disabled = {n.lower() for n in result["missing"]}, {n.lower() for n in result["disabled"]}
        for name in info.plugins + info.light:
            state = "MISSING" if name.lower() in missing else ("switched off" if name.lower() in disabled else "present")
            item = QTreeWidgetItem([name, "light plugin" if name in info.light else "plugin", state])
            if state != "present":
                for col in range(3):
                    item.setForeground(col, QBrush(QColor(theme.c("problem"))))
            self.tree.addTopLevelItem(item)
        for name in result["extra"]:
            self.tree.addTopLevelItem(QTreeWidgetItem([name, "not in this save", "switched on (added since)"]))
        self.result = result


class ToolsDialog(QDialog):
    """A short list of Windows modding programs to run inside the game's Proton prefix."""

    def __init__(self, parent, tools_list: list):
        super().__init__(parent)
        self.tools = [dict(t) for t in tools_list]
        self.run_index = -1
        self.setWindowTitle("Tools")
        self.resize(720, 420)
        lay = QVBoxLayout(self)
        note = QLabel("Windows modding programs such as FO4Edit or BodySlide, run in the same Proton setup as the game. "
                      "They see the game with your mods in place. New files they leave in the game's Data folder are "
                      "collected into a mod called \"Overwrite\" afterwards, so they do not get lost or mixed into other mods.")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.list = QListWidget()
        self.list.setAccessibleName("Tools")
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        add = QPushButton("Add program…")
        add.clicked.connect(self._add)
        args = QPushButton("Options…")
        args.setToolTip("Extra words passed to the program when it starts")
        args.clicked.connect(self._args)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove)
        self.run_btn = QPushButton("Run")
        self.run_btn.setProperty("sign", "yellow")
        self.run_btn.clicked.connect(self._run)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        for b in (add, args, remove):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.run_btn)
        row.addWidget(close)
        lay.addLayout(row)
        self._fill()

    def _fill(self) -> None:
        self.list.clear()
        for t in self.tools:
            self.list.addItem("%s   %s%s" % (t["name"], t["exe"].replace(os.path.expanduser("~"), "~"),
                                            ("   " + t["args"]) if t.get("args") else ""))
        if self.tools:
            self.list.setCurrentRow(0)

    def _add(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose the program", os.path.expanduser("~"), "Windows programs (*.exe);;All files (*)")
        if not path:
            return
        name, ok = QInputDialog.getText(self, "Add program", "Name to show:", text=os.path.splitext(os.path.basename(path))[0])
        if ok and name.strip():
            self.tools.append({"name": name.strip()[:60], "exe": path, "args": ""})
            self._fill()
            self.list.setCurrentRow(len(self.tools) - 1)

    def _args(self) -> None:
        i = self.list.currentRow()
        if 0 <= i < len(self.tools):
            text, ok = QInputDialog.getText(self, "Options", "Options for %s:" % self.tools[i]["name"], text=self.tools[i].get("args", ""))
            if ok:
                self.tools[i]["args"] = text.strip()[:400]
                self._fill()
                self.list.setCurrentRow(i)

    def _remove(self) -> None:
        i = self.list.currentRow()
        if 0 <= i < len(self.tools):
            del self.tools[i]
            self._fill()

    def _run(self) -> None:
        self.run_index = self.list.currentRow()
        if self.run_index >= 0:
            self.accept()


def ask_profile_name(parent, settings: dict, title: str, text: str = "") -> str:
    """Ask for a new profile name until it is usable or the user gives up ('' then)."""
    while True:
        name, ok = QInputDialog.getText(parent, title, "Profile name:", text=text)
        if not ok:
            return ""
        problem = profiles.valid_name(name, settings)
        if not problem:
            return name.strip()
        QMessageBox.information(parent, title, problem)
        text = name


def _words(text: str) -> list:
    import re
    text = re.sub(r"\.(esp|esm|esl)$", "", text, flags=re.I)
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return [w for w in re.split(r"[^A-Za-z0-9]+", text.lower()) if w]


def resemblance(plugin_file: str, mod_name: str) -> float:
    """How much a plugin file name looks like a mod's name, 0 to 1. 'GreaseRatGarbs.esp' and
    'Grease Rat Garbs' are the same thing; 'HellcatPA.esp' fits 'Hellcat Power Armor'."""
    a, b = _words(plugin_file), _words(mod_name)
    if not a or not b:
        return 0.0
    ja, jb = "".join(a), "".join(b)
    if ja == jb:
        return 1.0
    if len(ja) >= 4 and (ja in jb or jb in ja):
        return 0.9
    hits = 0.0
    for w in a:
        if w in b:
            hits += 1
        elif len(w) >= 2 and any("".join(x[0] for x in b[i:i + len(w)]) == w for i in range(len(b) - len(w) + 1)):
            hits += 1                               # initials: "pa" for "power armor"
        elif len(w) >= 4 and any(x.startswith(w) or w.startswith(x) for x in b if len(x) >= 4):
            hits += 0.7
    return 0.8 * hits / len(a)


class PickDialog(QDialog):
    """A list of things to tick. rows: (text, ticked, can be changed)."""

    def __init__(self, parent, title: str, intro: str, rows: list, footer: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(620)
        lay = QVBoxLayout(self)
        top = QLabel(intro)
        top.setWordWrap(True)
        lay.addWidget(top)
        self.boxes = []
        for text, ticked, enabled in rows:
            box = QCheckBox(text)
            box.setChecked(ticked)
            box.setEnabled(enabled)
            lay.addWidget(box)
            self.boxes.append(box)
        if footer:
            note = QLabel(footer)
            note.setWordWrap(True)
            lay.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Install ticked")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def chosen(self) -> list:
        return [i for i, b in enumerate(self.boxes) if b.isChecked() and b.isEnabled()]
