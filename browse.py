"""The built-in mod browser: one screen shared by every provider."""
from __future__ import annotations

import re
import hashlib
import html
import os
from typing import Callable, Optional

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThread, QThreadPool, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFrame, QMessageBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QSplitter,
    QStackedLayout, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

import net
import gamever
import providers as prov
import theme


def human_size(n: int) -> str:
    if n <= 0:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return ""


class _Job(QThread):
    done = Signal(int, object, object)        # token, value, error

    def __init__(self, token: int, fn: Callable):
        super().__init__()
        self.token = token
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.token, self.fn(), None)
        except Exception as e:                # shown to the user by the receiver
            self.done.emit(self.token, None, e)


class _ThumbSignals(QObject):
    loaded = Signal(str, bytes)


class _ThumbTask(QRunnable):
    def __init__(self, url: str, cache_dir: str, signals: _ThumbSignals):
        super().__init__()
        self.url, self.cache_dir, self.signals = url, cache_dir, signals

    def run(self):
        path = os.path.join(self.cache_dir, hashlib.sha1(self.url.encode("utf-8")).hexdigest())
        data = b""
        try:
            if os.path.exists(path):
                with open(path, "rb") as f:
                    data = f.read()
            else:
                data = net.get_bytes(self.url, {"Accept": "image/*"}, limit=6 << 20)
                os.makedirs(self.cache_dir, exist_ok=True)
                with open(path + ".tmp", "wb") as f:
                    f.write(data)
                os.replace(path + ".tmp", path)
        except (net.NetError, OSError):
            data = b""
        try:
            self.signals.loaded.emit(self.url, data)
        except RuntimeError:                  # window closed while loading
            pass


class BrowseTab(QWidget):
    logLine = Signal(str)
    queueRequested = Signal(object, object)                  # provider, [ModSummary]: several mods to install
    requirementsRequested = Signal(object, object, object)   # provider, the mod they belong to, [Requirement]
    installRequested = Signal(object, object, object)     # provider, ModSummary, ModFile
    message = Signal(str)

    def __init__(self, provider_list: list, cache_dir: str, parent=None):
        super().__init__(parent)
        self.providers = provider_list
        self.cache_dir = cache_dir
        self.results: list = []
        self.page = 0
        self.mode = ("feed", "")              # ("feed", key) or ("search", term)
        self.current: Optional[prov.ModDetails] = None
        self.game_version: Optional[tuple] = None            # set by the main window; None = unknown
        self.installed_lookup = lambda: (set(), set())       # -> (origins like "nexus:12", lower-case mod names)
        self._trail: list = []                                # mods to go back to after following a requirement
        self._token = 0
        self._detail_token = 0
        self._jobs: list = []
        self._pixmaps: dict = {}
        self._thumb_items: dict = {}          # picture address -> rows waiting for it
        self._signals = _ThumbSignals()
        self._signals.loaded.connect(self._thumb_loaded)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(4)
        self._picture_url = ""

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.provider_box = QComboBox()
        for p in self.providers:
            self.provider_box.addItem(p.name, p.id)
        self.provider_box.currentIndexChanged.connect(self._provider_changed)
        self.feed_box = QComboBox()
        self.feed_box.activated.connect(lambda _i: self.load_feed())
        self.category_box = QComboBox()
        self.category_box.setMinimumContentsLength(16)
        self.category_box.setMaxVisibleItems(14)          # a scrolling list, not one as tall as the screen
        self.category_clear = QPushButton("All")
        self.category_clear.setToolTip("Back to all categories")
        self.category_clear.clicked.connect(self._clear_category)
        self.category_clear.hide()
        self.category_box.addItem("All categories", "")
        self.category_box.activated.connect(lambda _i: self._category_changed())
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search mods, or paste a mod page address")
        self.search_edit.returnPressed.connect(self.start_search)
        go = QPushButton("Search")
        go.clicked.connect(self.start_search)
        self.adult = QCheckBox("Show adult content")
        self.adult.toggled.connect(lambda _on: self._adult_toggled())
        top.addWidget(QLabel("Source"))
        top.addWidget(self.provider_box)
        top.addWidget(self.feed_box)
        top.addWidget(self.category_box)
        top.addWidget(self.category_clear)
        top.addWidget(self.search_edit, 1)
        top.addWidget(go)
        top.addWidget(self.adult)
        lay.addLayout(top)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.hide()
        lay.addWidget(self.note)

        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        # The results list is a departure board: amber text on black, one mod per row.
        self.list = QTreeWidget()
        self.list.setObjectName("board")
        self.list.setHeaderHidden(True)
        self.list.setColumnCount(1)
        self.list.setRootIsDecorated(False)
        self.list.setUniformRowHeights(True)
        self.list.setIconSize(QSize(104, 58))
        self.list.setAllColumnsShowFocus(True)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentItemChanged.connect(lambda item, _old: self._selected(item))
        # When there is nothing to list, the mascot keeps the board company.
        self.empty = QFrame()
        self.empty.setObjectName("boardempty")
        el = QVBoxLayout(self.empty)
        el.addStretch(1)
        self.empty_rat = theme.RatLabel(120)
        self.empty_text = QLabel("")
        self.empty_text.setObjectName("emptyhint")
        self.empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_text.setWordWrap(True)
        el.addWidget(self.empty_rat)
        el.addWidget(self.empty_text)
        el.addStretch(1)
        self._empty_state = "idle"
        self.board_stack = QStackedLayout()
        self.board_stack.addWidget(self.list)
        self.board_stack.addWidget(self.empty)
        ll.addLayout(self.board_stack, 1)
        self.more = QPushButton("Load more")
        self.more.clicked.connect(self.load_more)
        self.more.setEnabled(False)
        self.list.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.list.itemSelectionChanged.connect(self._selection_changed)
        self.many_btn = QPushButton("Install selected")
        self.many_btn.setProperty("sign", "yellow")
        self.many_btn.setToolTip("Hold Ctrl or Shift and click to select several mods, then install them in one go.\n"
                                 "Each gets its main file for your game version.")
        self.many_btn.clicked.connect(self.install_many)
        self.many_btn.hide()
        row = QHBoxLayout()
        row.addWidget(self.more)
        row.addWidget(self.many_btn)
        self.many_hint = QLabel("Ctrl-click or Shift-click to select several")
        row.addWidget(self.many_hint)
        row.addStretch(1)
        ll.addLayout(row)
        split.addWidget(left)

        # The details side is a light wall panel with a tile-coloured stripe on top.
        right = QFrame()
        right.setObjectName("wall")
        outer = QVBoxLayout(right)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        stripe = QFrame()
        stripe.setObjectName("stripe")
        stripe.setFixedHeight(10)
        outer.addWidget(stripe)
        inner = QWidget()
        rl = QVBoxLayout(inner)
        rl.setContentsMargins(16, 14, 16, 16)
        rl.setSpacing(8)
        outer.addWidget(inner, 1)
        head_row = QHBoxLayout()
        head_row.setSpacing(14)
        self.picture = QLabel()
        self.picture.setObjectName("picture")
        self.picture.setFixedSize(288, 162)
        self.picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head_row.addWidget(self.picture, 0, Qt.AlignmentFlag.AlignTop)
        info = QVBoxLayout()
        info.setSpacing(8)
        self.title = QLabel("Pick a mod on the board")
        self.title.setObjectName("title")
        self.title.setWordWrap(True)
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.facts = QLabel()
        self.facts.setTextFormat(Qt.TextFormat.RichText)
        self.facts.setWordWrap(True)
        self.facts.linkActivated.connect(lambda _href: self.show_author())
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        info.addWidget(self.title)
        info.addWidget(self.facts)
        info.addWidget(self.summary, 1)
        head_row.addLayout(info, 1)
        rl.addLayout(head_row)

        def heading(text: str) -> QLabel:
            label = QLabel(text.upper())
            label.setObjectName("heading")
            return label

        rl.addWidget(heading("Requirements"))
        self.requirements = QTextBrowser()
        self.requirements.document().setDefaultStyleSheet("a { color: %s; }" % theme.c("link"))
        self.requirements.setOpenLinks(False)                 # links to other mods open here, not in the web browser
        self.requirements.anchorClicked.connect(self._requirement_clicked)
        self.requirements.setMaximumHeight(96)
        rl.addWidget(self.requirements)

        rl.addWidget(heading("Files"))
        self.files = QTreeWidget()
        self.files.setHeaderLabels(["File", "Version", "Type", "Size", "Date", "Made for"])
        self.files.setRootIsDecorated(False)
        self.files.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.files.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        fh = self.files.header()
        fh.setStretchLastSection(False)
        fh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3, 4, 5):
            fh.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.files.setMaximumHeight(150)
        self.files.itemDoubleClicked.connect(lambda _i, _c: self.install_selected())
        rl.addWidget(self.files)
        row = QHBoxLayout()
        self.install_btn = QPushButton("Install selected file")
        self.install_btn.setProperty("sign", "yellow")
        self.install_btn.clicked.connect(self.install_selected)
        self.install_btn.setEnabled(False)
        self.page_btn = QPushButton("Open mod page in web browser")
        self.page_btn.clicked.connect(self._open_page)
        self.page_btn.setEnabled(False)
        self.req_btn = QPushButton("Install requirements…")
        self.req_btn.setToolTip("Pick which of the listed requirements to download and install")
        self.req_btn.clicked.connect(self._ask_requirements)
        self.req_btn.setEnabled(False)
        self.back_btn = QPushButton("Back")
        self.back_btn.clicked.connect(self._go_back)
        self.back_btn.hide()
        row.addWidget(self.back_btn)
        row.addWidget(self.install_btn)
        row.addWidget(self.req_btn)
        row.addWidget(self.page_btn)
        row.addStretch(1)
        rl.addLayout(row)

        rl.addWidget(heading("Description"))
        self.description = QTextBrowser()
        self.description.document().setDefaultStyleSheet("a { color: %s; }" % theme.c("link"))
        self.description.setOpenExternalLinks(True)
        rl.addWidget(self.description, 1)
        split.addWidget(right)
        split.setSizes([600, 660])
        split.setHandleWidth(18)
        lay.addWidget(split, 1)
        self._loaded_once = False
        self._provider_changed()

    # -- helpers ------------------------------------------------------------

    @property
    def provider(self) -> prov.Provider:
        return self.providers[max(0, self.provider_box.currentIndex())]

    def set_providers(self, provider_list: list) -> None:
        """Swap in freshly configured providers (after the API key changes)."""
        index = self.provider_box.currentIndex()
        self.providers = provider_list
        self._loaded_once = False
        self.provider_box.setCurrentIndex(max(0, index))
        self._clear_details()
        self.results = []
        self._fill_results()

    def _show_empty(self, state: str, text: str = "") -> None:
        """Swap the board for the mascot with a line of text, or back ('' = show the list)."""
        if not state:
            self.board_stack.setCurrentWidget(self.list)
            return
        self._empty_state = state
        self.empty_rat.set_state(state)
        self.empty_text.setText(text)
        self.board_stack.setCurrentWidget(self.empty)

    def restyle(self) -> None:
        """Called after the theme changes: refresh the parts the stylesheet cannot reach."""
        for doc in (self.requirements.document(), self.description.document()):
            doc.setDefaultStyleSheet("a { color: %s; }" % theme.c("link"))
        if self.board_stack.currentWidget() is self.empty:
            self._show_empty(self._empty_state, self.empty_text.text())

    def _adult_toggled(self) -> None:
        self._fill_results()
        if self.results:
            if self.list.topLevelItemCount():
                self._show_empty("")
            else:
                self._show_empty("idle", "Everything in this list is marked as adult content.\n"
                                         "Tick the adult content box to see it.")

    def _note(self, text: str, error: bool = False) -> None:
        self.note.setText(text)
        self.note.setStyleSheet("QLabel { color: %s; font-weight: 600; }" % theme.c("note_error") if error else "")
        self.note.setVisible(bool(text))

    def _run(self, fn: Callable, on_done: Callable, detail: bool = False) -> None:
        if detail:
            self._detail_token += 1
            token = self._detail_token
        else:
            self._token += 1
            token = self._token
        job = _Job(token, fn)

        def finished(tok, value, error, job=job):
            if job in self._jobs:
                self._jobs.remove(job)
            job.wait()
            if tok != (self._detail_token if detail else self._token):
                return                        # a newer request replaced this one
            on_done(value, error)

        job.done.connect(finished)
        self._jobs.append(job)
        job.start()

    def wait_idle(self, timeout_ms: int = 8000) -> None:
        """For tests: wait for background work to finish."""
        for job in list(self._jobs):
            job.wait(timeout_ms)
        self._pool.waitForDone(timeout_ms)

    def ensure_loaded(self) -> None:
        """Load the first list the first time the tab is shown."""
        if not self._loaded_once:
            self._loaded_once = True
            self.load_feed()

    # -- lists --------------------------------------------------------------

    def _provider_changed(self, *_a) -> None:
        self.feed_box.blockSignals(True)
        self.feed_box.clear()
        for key, label in self.provider.feeds():
            self.feed_box.addItem(label, key)
        self.feed_box.blockSignals(False)
        self.adult.setVisible(self.provider.id == "nexus")
        self.category_box.blockSignals(True)
        self.category_box.clear()
        self.category_box.addItem("All categories", "")
        self.category_box.blockSignals(False)
        self.category_box.setVisible(self.provider.id == "nexus")
        self._categories_for = None
        self.results = []
        self._fill_results()
        self._clear_details()
        self._note("")
        if self._loaded_once:
            self.load_feed()

    def show_author(self) -> None:
        """List the other mods by the author of the mod being shown."""
        if self.current is None:
            return
        mod = self.current.summary
        if not (mod.author or mod.uploader):
            return
        self.mode = ("author", mod)
        self.page = 0
        self.category_box.setCurrentIndex(0)
        self.search_edit.clear()
        self._fetch(replace=True)

    def _clear_category(self) -> None:
        if self.category_box.currentIndex() != 0:
            self.category_box.setCurrentIndex(0)
            self._category_changed()

    def _category_changed(self) -> None:
        """Re-run whatever is showing (a list or a search) inside the chosen category."""
        if self.mode[0] == "author":
            self.mode = ("feed", self.feed_box.currentData() or "")
        self.page = 0
        self._fetch(replace=True)

    def _load_categories(self) -> None:
        """Fetch the provider's category list once, in the background."""
        provider = self.provider
        if self._categories_for is provider:
            return
        self._categories_for = provider
        job = _Job(0, provider.categories)

        def finished(_tok, value, error, job=job):
            if job in self._jobs:
                self._jobs.remove(job)
            job.wait()
            if provider is not self.provider:
                return
            if error is not None or not value:
                self._categories_for = None if error is not None else provider
                return
            chosen = self.category_box.currentData() or ""
            self.category_box.blockSignals(True)
            self.category_box.clear()
            self.category_box.addItem("All categories", "")
            for cid, name in value:
                self.category_box.addItem(name, cid)
            self.category_box.setCurrentIndex(max(0, self.category_box.findData(chosen)))
            self.category_box.blockSignals(False)

        job.done.connect(finished)
        self._jobs.append(job)
        job.start()

    def load_feed(self) -> None:
        key = self.feed_box.currentData() or ""
        self.mode = ("feed", key)
        self.page = 0
        self._fetch(replace=True)

    def start_search(self) -> None:
        term = self.search_edit.text().strip()
        if not term:
            self.load_feed()
            return
        self.mode = ("search", term)
        self.page = 0
        self._fetch(replace=True)

    def load_more(self) -> None:
        self.page += 1
        self._fetch(replace=False)

    def _fetch(self, replace: bool) -> None:
        provider, (kind, value), page = self.provider, self.mode, self.page
        category = self.category_box.currentData() or ""
        self.category_clear.setVisible(bool(category) and self.category_box.isVisible())
        self._loaded_once = True
        self._load_categories()
        self._note("Loading…")
        self.more.setEnabled(False)
        if replace:
            self._show_empty("work", "%s is fetching the list…" % theme.MASCOT_NAME)

        def work():
            if kind == "author":
                return provider.by_author(value, page)
            if kind == "search":
                return provider.search(value, page, category)
            return provider.browse(value, page, category)

        def done(value_, error):
            if error is not None:
                if isinstance(error, prov.NeedsKey):
                    self._note("%s %s" % (error, provider.key_help), True)
                elif isinstance(error, prov.ProviderError):
                    self._note(str(error), True)
                else:
                    self._note("Something went wrong: %s" % error, True)
                if replace:
                    self.results = []
                    self._fill_results()
                    self._show_empty("error", "%s could not get the list. The reason is written above." % theme.MASCOT_NAME)
                return
            items = value_ or []
            if kind == "search" and replace:
                self.logLine.emit("Search \"%s\"%s on %s: %d found.%s" % (
                    value, " in %s" % self.category_box.currentText() if category else "", provider.name, len(items),
                    "  [tried: %s]" % provider.search_trace if getattr(provider, "search_trace", "") else ""))
            self.results = items if replace else self.results + items
            self._fill_results()
            self.more.setEnabled(len(items) >= 10)
            note = getattr(provider, "search_note", "")
            if kind == "author":
                note = "Mods by %s. Pick a list or search to go back." % (value.uploader or value.author)
            if not self.results:
                note = (note + " " if note else "") + "Nothing found."
            self._note(note)
            if self.list.topLevelItemCount():
                self._show_empty("")
            elif self.results:
                self._adult_toggled()
            else:
                where = " in %s" % self.category_box.currentText() if category else ""
                if kind == "search":
                    self._show_empty("idle", "%s found nothing for \"%%s\"%%s.\nTry fewer words%%s." % theme.MASCOT_NAME % (
                        value, where, ", or set the category back to all" if where else ""))
                else:
                    self._show_empty("idle", "Nothing to show here%s." % where)
            # technical detail of a fallback, for reporting problems
            self.note.setToolTip(getattr(provider, "last_error", "") if "unavailable" in note else "")

        self._run(work, done)

    def _fill_results(self) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        self._thumb_items = {}
        unit = "endorsements" if self.provider.id == "nexus" else "stars"
        for mod in self.results:
            if mod.adult and not self.adult.isChecked():
                continue
            facts = [mod.author or mod.uploader] if (mod.author or mod.uploader) else []
            if mod.endorsements:
                facts.append("%s %s" % ("{:,}".format(mod.endorsements), unit))
            # picture on the left, name on the first line, author and count on the second
            item = QTreeWidgetItem(["%s\n%s" % (mod.name, "  /  ".join(facts))])
            item.setData(0, Qt.ItemDataRole.UserRole, mod)
            item.setToolTip(0, mod.summary)
            self.list.addTopLevelItem(item)
            if mod.thumbnail:
                if mod.thumbnail in self._pixmaps:
                    item.setIcon(0, self._row_icon(self._pixmaps[mod.thumbnail]))
                else:
                    first = mod.thumbnail not in self._thumb_items
                    self._thumb_items.setdefault(mod.thumbnail, []).append(item)
                    if first:
                        self._pool.start(_ThumbTask(mod.thumbnail, self.cache_dir, self._signals))
        self.list.blockSignals(False)

    def result_names(self) -> list:
        return [self.list.topLevelItem(i).text(0).split("\n")[0] for i in range(self.list.topLevelItemCount())]

    def select_row(self, row: int) -> None:
        self.list.setCurrentItem(self.list.topLevelItem(row))

    def _thumb_for_row(self, pix: QPixmap) -> QPixmap:
        """Crop a picture to the row's 16:9 thumbnail so every row lines up."""
        size = self.list.iconSize()
        scaled = pix.scaled(size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                            Qt.TransformationMode.SmoothTransformation)
        x = max(0, (scaled.width() - size.width()) // 2)
        y = max(0, (scaled.height() - size.height()) // 2)
        return scaled.copy(x, y, size.width(), size.height())

    def _row_icon(self, pix: QPixmap) -> QIcon:
        thumb = self._thumb_for_row(pix)
        icon = QIcon(thumb)
        icon.addPixmap(thumb, QIcon.Mode.Selected)      # no tint on the selected row
        return icon

    def _thumb_loaded(self, url: str, data: bytes) -> None:
        pix = QPixmap()
        if data:
            pix.loadFromData(data)
        waiting = self._thumb_items.pop(url, [])
        if not pix.isNull():
            self._pixmaps[url] = pix
            icon = self._row_icon(pix)
            for item in waiting:
                try:
                    item.setIcon(0, icon)
                except RuntimeError:          # the list was refilled meanwhile
                    pass
            if url == self._picture_url:
                self._show_picture(pix)

    # -- one mod ------------------------------------------------------------

    def _clear_details(self) -> None:
        self.current = None
        self._picture_url = ""
        self.picture.clear()
        self.title.setText("Pick a mod on the board")
        self.facts.setText("")
        self.summary.setText("")
        self.requirements.clear()
        self.files.clear()
        self.description.clear()
        self.install_btn.setEnabled(False)
        self.req_btn.setEnabled(False)
        self.page_btn.setEnabled(False)

    def _show_picture(self, pix: QPixmap) -> None:
        self.picture.setPixmap(pix.scaled(self.picture.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))

    def _selected(self, item) -> None:
        if item is None:
            return
        self._trail = []
        self.back_btn.hide()
        self._open(item.data(0, Qt.ItemDataRole.UserRole))

    def _open(self, mod: prov.ModSummary) -> None:
        """Show one mod in the details panel (from the board, or by following a requirement)."""
        provider = self.provider
        self._clear_details()
        self.title.setText(mod.name)
        self.summary.setText(mod.summary)
        self.facts.setText("Loading details…")
        self.facts.setToolTip("")
        self._set_picture(mod.picture or mod.thumbnail)

        def done(details, error):
            if error is not None:
                text = str(error)
                if isinstance(error, prov.NeedsKey):
                    text = "%s %s" % (error, provider.key_help)
                self.facts.setText(html.escape(text))
                self.page_btn.setEnabled(bool(mod.url))
                self.current = prov.ModDetails(mod)
                return
            self.show_details(details)

        self._run(lambda: provider.details(mod), done, detail=True)

    def _set_picture(self, url: str) -> None:
        self._picture_url = url
        if not url:
            return
        if url in self._pixmaps:
            self._show_picture(self._pixmaps[url])
        else:
            self._pool.start(_ThumbTask(url, self.cache_dir, self._signals))

    def show_details(self, details: prov.ModDetails) -> None:
        self.current = details
        mod = details.summary
        self.title.setText(mod.name)
        name = mod.author or mod.uploader
        facts = ['by <a href="author:" style="color: %s; font-weight: 600">%s</a>' % (theme.c("link"), html.escape(name))] if name else []
        facts += ["%s: %s" % (html.escape(label), html.escape(str(value))) for label, value in details.extra if value]
        self.facts.setText("&nbsp;&nbsp;&nbsp;".join(facts))
        self.facts.setToolTip("Click the author's name to see their other mods." if name else "")
        self.summary.setText(mod.summary)
        self._set_picture(mod.picture or mod.thumbnail)
        if details.requirements:
            rows = []
            for r in details.requirements:
                name = html.escape(r.name)
                if r.mod_id and r.kind == "mod":
                    name = '<a href="mod:%s">%s</a>' % (r.mod_id, name)
                elif r.url.lower().startswith(("http://", "https://")):
                    name = '<a href="%s">%s</a> (other site)' % (html.escape(r.url, quote=True), name)
                tags = []
                if r.kind != "dlc":
                    tags.append("optional" if r.optional else "required")
                if r.kind == "mod" and self.is_installed(r):
                    tags.append("installed")
                rows.append("&bull; %s%s%s" % (name, " <i>[%s]</i>" % ", ".join(tags) if tags else "",
                                               (" &mdash; " + html.escape(r.notes)) if r.notes else ""))
            self.requirements.setHtml("<br>".join(rows))
        else:
            self.requirements.setPlainText(details.requirements_note or "None listed.")
        self.req_btn.setEnabled(any(r.kind == "mod" and r.mod_id for r in details.requirements))
        self.files.clear()
        gen = gamever.generation(self.game_version)
        best = gamever.best_file(details.files, gen)
        for f in details.files:
            said, fit = gamever.file_mentions(f), gamever.fit(f, gen)
            item = QTreeWidgetItem([f.name, f.version, f.category, human_size(f.size), f.uploaded, gamever.names(said)])
            item.setData(0, Qt.ItemDataRole.UserRole, f)
            tip = f.description or f.file_name
            if fit == "no":
                tip = "This file says it is for %s. Your game is %s.\n\n%s" % (
                    gamever.names(said), gamever.describe(self.game_version), tip)
                for col in range(6):
                    item.setForeground(col, QBrush(QColor(theme.c("problem"))))
            for col in range(6):
                item.setToolTip(col, tip)
            self.files.addTopLevelItem(item)
            if f is best:
                self.files.setCurrentItem(item)
        # What the page as a whole says about game versions, next to what is installed.
        if self.game_version:
            page_says = gamever.mentions(mod.name + " \n " + mod.summary + " \n " +
                                         re.sub(r"<[^>]+>", " ", details.description_html or ""))
            line = "Your game: %s." % html.escape(gamever.describe(self.game_version))
            if page_says:
                line += " The description mentions: %s." % html.escape(gamever.names(page_says))
                if gen not in page_says and not any(gamever.fit(f, gen) == "yes" for f in details.files):
                    line = '<span style="color: %s; font-weight: 600">%s It does not mention yours.</span>' % (
                        theme.c("problem"), line)
            self.facts.setText(self.facts.text() + "<br>" + line)
        self.install_btn.setEnabled(bool(details.files))
        self.page_btn.setEnabled(bool(mod.url))
        self.description.setHtml(details.description_html or "<i>No description.</i>")

    def install_selected(self) -> None:
        item = self.files.currentItem()
        if item is None or self.current is None:
            return
        file = item.data(0, Qt.ItemDataRole.UserRole)
        gen = gamever.generation(self.game_version)
        if gamever.fit(file, gen) == "no":
            better = [f for f in self.current.files if gamever.fit(f, gen) == "yes"]
            text = ("\"%s\" says it is for %s.\nYour game is %s.\n\n" % (
                file.name, gamever.names(gamever.file_mentions(file)), gamever.describe(self.game_version)))
            text += ("These files say they are for your version:\n%s\n\n" % "\n".join("  " + f.name for f in better[:6])
                     if better else "No file on this page says it is for your version.\n\n")
            text += "Install the selected file anyway?"
            if QMessageBox.question(self, "Game version", text) != QMessageBox.StandardButton.Yes:
                return
        self.installRequested.emit(self.provider, self.current.summary, file)

    # -- requirements -------------------------------------------------------

    def selected_mods(self) -> list:
        return [i.data(0, Qt.ItemDataRole.UserRole) for i in self.list.selectedItems()]

    def _selection_changed(self) -> None:
        n = len(self.list.selectedItems())
        self.many_btn.setVisible(n > 1)
        self.many_hint.setVisible(n <= 1)
        self.many_btn.setText("Install %d selected" % n)

    def install_many(self) -> None:
        mods = self.selected_mods()
        if len(mods) > 1:
            self.queueRequested.emit(self.provider, mods)

    def refresh_marks(self) -> None:
        """Redraw the open mod so 'installed' marks are current."""
        if self.current is not None and (self.current.files or self.current.requirements):
            self.show_details(self.current)

    def is_installed(self, r: prov.Requirement) -> bool:
        origins, names = self.installed_lookup()
        return ("%s:%s" % (self.provider.id, r.mod_id)) in origins or r.name.strip().lower() in names

    def _requirement_clicked(self, url: QUrl) -> None:
        text = url.toString()
        if text.startswith("mod:"):
            self.open_mod(text[4:])
        elif text.lower().startswith(("http://", "https://")):
            QDesktopServices.openUrl(url)

    def open_mod(self, mod_id: str, name: str = "") -> None:
        """Show another mod of the same site here, remembering where we came from."""
        if self.current is not None:
            self._trail.append(self.current.summary)
            self.back_btn.setText("Back to %s" % self.current.summary.name[:28])
            self.back_btn.show()
        if not name and self.current is not None:
            name = next((r.name for r in self.current.requirements if r.mod_id == mod_id), "")
        site = getattr(self.provider, "site", "")
        self._open(prov.ModSummary(provider=self.provider.id, id=str(mod_id), name=name or "Mod %s" % mod_id,
                                   url="%s/%s/mods/%s" % (site, prov.GAME_DOMAIN, mod_id) if site else ""))

    def _go_back(self) -> None:
        if not self._trail:
            return
        mod = self._trail.pop()
        if self._trail:
            self.back_btn.setText("Back to %s" % self._trail[-1].name[:28])
        else:
            self.back_btn.hide()
        self._open(mod)

    def _ask_requirements(self) -> None:
        """Let the user tick which requirements to fetch. Required ones start ticked, optional ones do not."""
        if self.current is None:
            return
        dlg = QDialog(self.window())
        dlg.setWindowTitle("Requirements of %s" % self.current.summary.name)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Ticked mods are downloaded and installed one after another, main file each.\n"
                             "\"Optional\" is read from the author's note, so check the note if unsure."))
        boxes = []
        for r in self.current.requirements:
            note = (" — " + r.notes) if r.notes else ""
            if r.kind == "mod" and r.mod_id:
                done = self.is_installed(r)
                box = QCheckBox("%s  [%s]%s" % (r.name, "already installed" if done else
                                                "optional" if r.optional else "required", note))
                box.setChecked(not done and not r.optional)
                box.setEnabled(not done)
                lay.addWidget(box)
                boxes.append((box, r))
            else:
                label = QLabel("%s  [%s]%s" % (r.name, "comes with the game or its add-ons" if r.kind == "dlc"
                                               else "on another site: get it yourself", note))
                label.setWordWrap(True)
                lay.addWidget(label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Install ticked")
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        self._req_dialog = (dlg, boxes)                       # for tests
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = [r for box, r in boxes if box.isChecked() and box.isEnabled()]
        if chosen:
            self.requirementsRequested.emit(self.provider, self.current.summary, chosen)

    def _open_page(self) -> None:
        if self.current is not None and self.current.summary.url.lower().startswith(("http://", "https://")):
            QDesktopServices.openUrl(QUrl(self.current.summary.url))
