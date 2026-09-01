"""QuickWB GUI: drop/paste images, white-balance them, export or copy to clipboard.

A scrollable gallery of image tiles. The control panel reads top-to-bottom as the
pipeline: what is selected -> how it is computed -> the knobs -> compare -> export.
'All together' computes one shared white point for the whole selection; the
eyedropper then applies that pick to the whole selection too. Live edits run on a
downscaled preview; export re-renders full-res and always writes the corrected
image (the compare button is preview-only).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from PySide6.QtCore import QMimeData, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QFrame, QGraphicsPixmapItem,
    QGraphicsScene, QGraphicsView, QGridLayout, QHBoxLayout, QLabel,
    QMainWindow, QPushButton, QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from . import wb

APP_NAME = "QuickWB"
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp")
PREVIEW_MAX = 1400   # longest side of the live-preview copy
MIN_TILE_W = 240     # tiles shrink to fit the window down to this, then wrap/scroll
QT_MAX = 16777215    # Qt's QWIDGETSIZE_MAX, for undoing setFixedSize()
DEFAULTS = {"neutralize": 50, "temperature": 0, "tint": 0, "brightness": 0, "contrast": 0}
ADJUSTMENTS = ("temperature", "tint", "brightness", "contrast")

FRESH, STALE, BUSY = "#4caf50", "#e0a020", "#eaeaea"   # recompute marker colours


def app_icon():
    p = Path(__file__).with_name("assets") / "quickwb.ico"
    return QIcon(str(p)) if p.exists() else None


def _to_qimage(rgb: np.ndarray) -> QImage:
    """uint8 HxWx3 RGB -> owned QImage (copy so it survives the numpy buffer)."""
    h, w = rgb.shape[:2]
    arr = np.ascontiguousarray(rgb)
    return QImage(arr.data, w, h, 3 * w, QImage.Format_RGB888).copy()


def _load_rgb(path: str) -> np.ndarray:
    """Load any image as uint8 RGB, honouring EXIF orientation."""
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return np.asarray(img, np.uint8)


def _qimage_to_rgb(qimg: QImage) -> np.ndarray:
    """QImage (e.g. from the clipboard) -> uint8 HxWx3 RGB, dropping row padding."""
    qimg = qimg.convertToFormat(QImage.Format_RGB888)
    w, h = qimg.width(), qimg.height()
    buf = np.frombuffer(bytes(qimg.constBits()), np.uint8).reshape(h, qimg.bytesPerLine())
    return np.ascontiguousarray(buf[:, : w * 3].reshape(h, w, 3))


def _downscale(rgb: np.ndarray, longest: int) -> np.ndarray:
    h, w = rgb.shape[:2]
    if max(h, w) <= longest:
        return rgb
    im = Image.fromarray(rgb)
    im.thumbnail((longest, longest), Image.BILINEAR)
    return np.asarray(im, np.uint8)


def _dot(color: str) -> QIcon:
    """Small filled circle used as the recompute button's freshness marker."""
    pm = QPixmap(12, 12)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(1, 1, 10, 10)
    p.end()
    return QIcon(pm)


def _slider_row(label, lo, hi, val, tip=""):
    """Label + slider + editable spin box, two-way synced. Returns (row, slider)."""
    s = QSlider(Qt.Horizontal)
    s.setRange(lo, hi)
    s.setValue(val)
    box = QSpinBox()
    box.setRange(lo, hi)
    box.setValue(val)
    box.setFixedWidth(52)
    s.valueChanged.connect(box.setValue)   # Qt drops no-op setValue, so no infinite loop
    box.valueChanged.connect(s.setValue)
    name = QLabel(label)
    name.setFixedWidth(74)
    name.setStyleSheet("color:#c8c8c8; border:none;")
    row = QWidget()
    row.setToolTip(tip)
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(name)
    h.addWidget(s, 1)
    h.addWidget(box)
    return row, s


def _section(title, on_reset=None):
    """Titled bordered block with an optional small Reset in its header.

    Returns (frame, body_layout) -- add your widgets to the body layout.
    """
    frame = QFrame()
    frame.setObjectName("sec")
    frame.setStyleSheet("QFrame#sec{border:1px solid #3a3a3a; border-radius:6px;}")
    col = QVBoxLayout(frame)
    col.setContentsMargins(8, 6, 8, 8)
    col.setSpacing(5)
    head = QHBoxLayout()
    lab = QLabel(title.upper())
    lab.setStyleSheet("color:#8b98a5; font-weight:600; font-size:10px; border:none;")
    head.addWidget(lab)
    head.addStretch(1)
    if on_reset is not None:
        b = QPushButton("Reset")
        b.setFlat(True)
        b.setFixedHeight(18)
        b.setStyleSheet("color:#7fa8d0; border:none; font-size:11px;")
        b.clicked.connect(on_reset)
        head.addWidget(b)
    col.addLayout(head)
    return frame, col


def _segmented(options, on_change):
    """Exclusive row of toggle buttons -- one is always on. Returns (row, buttons)."""
    row = QWidget()
    row.setStyleSheet(
        "QPushButton{padding:5px; border:1px solid #4a4a4a; border-radius:4px;}"
        "QPushButton:checked{background:#2f6ea5; color:#fff; border:1px solid #4aa3ff;}")
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(4)
    btns = []
    for i, text in enumerate(options):
        b = QPushButton(text)
        b.setCheckable(True)
        b.setAutoExclusive(True)
        b.setChecked(i == 0)
        b.toggled.connect(lambda on, i=i: on and on_change(i))
        h.addWidget(b, 1)
        btns.append(b)
    return row, btns


class TileView(QLabel):
    """Scaled-to-fit image inside a tile. Emits clicked() for selection and,
    when in eyedropper mode, picked(x, y) in image pixels."""

    clicked = Signal(object)      # keyboard modifiers at click time
    picked = Signal(int, int)
    doubled = Signal()            # double-click -> zoom

    def __init__(self):
        super().__init__()
        self._pixmap: QPixmap | None = None
        self._pick = False
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(60, 45)

    def set_image(self, pixmap: QPixmap):
        self._pixmap = pixmap
        self._render()

    def set_pick(self, on: bool):
        self._pick = on
        self.setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)

    def resizeEvent(self, e):
        self._render()

    def _render(self):
        if self._pixmap is not None:
            self.setPixmap(self._pixmap.scaled(self.size(), Qt.KeepAspectRatio,
                                               Qt.SmoothTransformation))

    def _drawn(self):
        pm = self.pixmap()
        if pm is None or pm.isNull():
            return None
        dw, dh = pm.width(), pm.height()
        return (self.width() - dw) // 2, (self.height() - dh) // 2, dw, dh

    def mousePressEvent(self, e):
        if self._pick and self._pixmap is not None:
            r = self._drawn()
            if r:
                ox, oy, dw, dh = r
                x, y = e.position().x() - ox, e.position().y() - oy
                if 0 <= x < dw and 0 <= y < dh:
                    self.picked.emit(int(x / dw * self._pixmap.width()),
                                     int(y / dh * self._pixmap.height()))
            return
        self.clicked.emit(QApplication.keyboardModifiers())

    def mouseDoubleClickEvent(self, e):
        self.doubled.emit()


class Tile(QFrame):
    """One gallery slot: an empty drop zone (Open/Paste) or a loaded image
    (Save/Copy). Holds its own source pixels, white point and adjustments."""

    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.setAcceptDrops(True)
        self.full = self.preview = self.mild = self.glass = None
        self.manual_wp = None
        self.auto_basis = None          # shared (mild, glass) when batch-rendered
        self.name = ""
        self.params = dict(DEFAULTS)
        self.selected = self.active = False
        self.show_orig = False    # compare is per image, not a global switch
        self.computed = False     # untouched pixels until a pass has run
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(6, 6, 6, 6)
        self._build_empty()
        self._restyle()

    # -- state ------------------------------------------------------------
    @property
    def loaded(self) -> bool:
        return self.full is not None

    def _clear_layout(self, lay=None):
        """Recursive: a nested QHBoxLayout has no .widget(), so the old button
        row used to survive and pop back up on the next resize."""
        lay = lay or self._lay
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)     # deleteLater alone leaves it on screen
                w.deleteLater()       # until the event loop next turns
            elif item.layout():
                self._clear_layout(item.layout())
                item.layout().deleteLater()

    def _build_empty(self):
        self._clear_layout()
        lab = QLabel("Drag image here")
        lab.setAlignment(Qt.AlignCenter)
        lab.setStyleSheet("color:#888; border:none;")
        lab.mousePressEvent = lambda e: self.win.select(self, QApplication.keyboardModifiers())
        self._lay.addWidget(lab, 1)
        row = QHBoxLayout()
        for text, slot in (("Open", self._open), ("Paste", lambda: self.win.paste_into(self))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        self._lay.addLayout(row)

    def _build_image(self):
        self._clear_layout()
        head = QLabel(self.name)
        head.setStyleSheet("color:#cfcfcf; font-weight:600; border:none;")
        self._lay.addWidget(head)
        self.view = TileView()
        self.view.clicked.connect(lambda mods: self.win.select(self, mods))
        self.view.picked.connect(lambda x, y: self.win.on_pick(self, x, y))
        self.view.doubled.connect(lambda: self.win.zoom(self))
        self._lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        for text, slot in (("Save", lambda: self.win.save([self])),
                           ("Copy", lambda: self.win.copy([self]))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        self._lay.addLayout(row)

    def clear(self):
        """Back to an empty drop zone -- what Delete does to the last slot."""
        self.full = self.preview = self.mild = self.glass = None
        self.manual_wp = self.auto_basis = None
        self.name = ""
        self.params = dict(DEFAULTS)
        self.show_orig = self.computed = False
        self._build_empty()
        self._restyle()

    def set_source(self, rgb: np.ndarray, name: str):
        self.full = rgb
        self.preview = _downscale(rgb, PREVIEW_MAX)
        self.mild = wb.white_patch(self.preview)
        self.glass = wb.gray_world(self.preview)
        self.manual_wp = None
        self.auto_basis = None
        self.name = name
        self.params = dict(DEFAULTS)
        self.show_orig = False
        self.computed = False
        self._build_image()
        self.render()

    # -- rendering --------------------------------------------------------
    def white_point(self):
        if self.manual_wp is not None:
            return self.manual_wp
        mild, glass = self.auto_basis or (self.mild, self.glass)
        s = self.params["neutralize"] / 100.0
        return (1.0 - s) * mild + s * glass

    def balanced(self, rgb: np.ndarray) -> np.ndarray:
        p = self.params
        return wb.balance(rgb, white_point=self.white_point(),
                          temperature=p["temperature"], tint=p["tint"],
                          brightness=p["brightness"] / 200.0, contrast=p["contrast"] / 200.0)

    def output(self, full: bool) -> np.ndarray:
        """Preview/zoom pixels. Shows the source untouched while comparing, and
        until a pass has actually run -- with Auto off, a freshly loaded image
        must not pretend to be corrected. Export goes through balanced()."""
        rgb = self.full if full else self.preview
        return rgb if self.show_orig or not self.computed else self.balanced(rgb)

    def render(self):
        if self.loaded:
            self.view.set_image(QPixmap.fromImage(_to_qimage(self.output(full=False))))

    # -- selection styling ------------------------------------------------
    def set_selected(self, selected: bool, active: bool):
        self.selected, self.active = selected, active
        self._restyle()

    def _restyle(self):
        if self.active:
            border = "3px solid #4aa3ff"
        elif self.selected:
            border = "2px solid #3d6ea5"
        elif self.loaded:
            border = "1px solid #555"
        else:
            border = "1px dashed #555"
        self.setStyleSheet(f"Tile{{border:{border}; border-radius:8px; background:#262626;}}")

    # -- input ------------------------------------------------------------
    def mousePressEvent(self, e):
        self.win.select(self, QApplication.keyboardModifiers())

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and any(u.toLocalFile().lower().endswith(IMAGE_EXTS)
                                          for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls()
                 if u.toLocalFile().lower().endswith(IMAGE_EXTS)]
        self.win.add_images(paths, into=self)

    def _open(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open image(s)", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp)")
        if paths:
            self.win.add_images(paths, into=self)


class AddBar(QFrame):
    """Flat add-a-slot strip under the gallery. Replaces the old + tile, which ate
    a whole grid cell that could have shown an image."""

    def __init__(self, on_click):
        super().__init__()
        self.setFixedHeight(30)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("AddBar{border:1px dashed #555; border-radius:6px; background:#242424;}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lab = QLabel("+   add another slot")
        lab.setAlignment(Qt.AlignCenter)
        lab.setStyleSheet("color:#888; border:none;")
        lay.addWidget(lab)
        self._on_click = on_click

    def mousePressEvent(self, e):
        self._on_click()


class _Canvas(QGraphicsView):
    """QGraphicsView gives pan (left-drag) and scale for free; we add Ctrl+scroll
    zoom and refit-on-resize."""

    def __init__(self, owner: "ZoomView"):
        super().__init__()
        self.owner = owner
        self.setDragMode(QGraphicsView.ScrollHandDrag)          # left-drag pans
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#111"))
        self.setRenderHints(QPainter.SmoothPixmapTransform | QPainter.Antialiasing)

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
            self.scale(f, f)
            self.owner._zoom_changed()
        else:
            super().wheelEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.owner._on_resize()


class ZoomView(QWidget):
    """Maximised single-image view (double-click). Ctrl+scroll zooms, left-drag
    pans, a Reset button shows when zoomed, Esc closes."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} - Ctrl+scroll zoom - drag to pan - Esc to close")
        icon = app_icon()
        if icon is not None:
            self.setWindowIcon(icon)
        self.scene = QGraphicsScene(self)
        self.item = QGraphicsPixmapItem()
        self.item.setTransformationMode(Qt.SmoothTransformation)
        self.scene.addItem(self.item)
        self.canvas = _Canvas(self)
        self.canvas.setScene(self.scene)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.canvas)
        self.reset_btn = QPushButton("Reset zoom", self.canvas)
        self.reset_btn.clicked.connect(self._fit)
        self.reset_btn.hide()
        self._zoomed = False

    def show_image(self, pixmap: QPixmap):
        self.item.setPixmap(pixmap)
        self.scene.setSceneRect(self.item.boundingRect())
        self.showMaximized()
        self.raise_()
        self.activateWindow()
        self._fit()

    def _fit(self):
        self.canvas.resetTransform()
        self.canvas.fitInView(self.item, Qt.KeepAspectRatio)
        self._zoomed = False
        self.reset_btn.hide()

    def _zoom_changed(self):
        self._zoomed = True
        self.reset_btn.show()
        self._place_reset()

    def _on_resize(self):
        self._place_reset()
        if not self._zoomed:               # keep fitting until the user zooms
            self.canvas.fitInView(self.item, Qt.KeepAspectRatio)

    def _place_reset(self):
        self.reset_btn.adjustSize()
        self.reset_btn.move(self.canvas.width() - self.reset_btn.width() - 12, 12)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} - image white balance")
        icon = app_icon()
        if icon is not None:
            self.setWindowIcon(icon)

        self.tiles: list[Tile] = []
        self.selection: list[Tile] = []
        self.active: Tile | None = None
        self.batch = False        # False: each image on its own; True: one joint correction
        self.single = True        # True: only the active tile, big; False: grid
        self._undo = []           # snapshots for Ctrl+Z, newest last
        self._loading = False     # guard: panel is being synced, ignore widget signals
        self._fresh = True

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setSpacing(10)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.grid_host)

        gallery = QWidget()
        gcol = QVBoxLayout(gallery)
        gcol.setContentsMargins(0, 0, 0, 0)
        gcol.setSpacing(6)
        gcol.addWidget(self._nav_bar())
        gcol.addWidget(self.scroll, 1)
        gcol.addWidget(AddBar(self.add_empty))

        root = QWidget()
        row = QHBoxLayout(root)
        row.addWidget(gallery, 1)
        row.addWidget(self._panel())
        self.setCentralWidget(root)
        self.resize(1120, 720)

        QShortcut(QKeySequence.Copy, self, lambda: self.copy(self.selection))
        QShortcut(QKeySequence.Paste, self, lambda: self.paste_into(self.active))
        QShortcut(QKeySequence.Delete, self, self.delete_selected)
        QShortcut(QKeySequence.Undo, self, self.undo)
        QShortcut(QKeySequence(Qt.Key_Right), self, lambda: self._step(1))
        QShortcut(QKeySequence(Qt.Key_Left), self, lambda: self._step(-1))

        self.add_empty()   # start with one big empty drop zone
        self._sync_panel()

    def _nav_bar(self) -> QWidget:
        """Which slot you are on, and how to walk them. Single view only -- in
        Grid the tiles are all on screen and this would say nothing new."""
        self.nav = QWidget()
        h = QHBoxLayout(self.nav)
        h.setContentsMargins(2, 0, 2, 0)
        self.btn_prev = QPushButton("\u25c0")
        self.btn_next = QPushButton("\u25b6")
        for b, d in ((self.btn_prev, -1), (self.btn_next, 1)):
            b.setFixedWidth(34)
            b.setToolTip("Previous slot" if d < 0 else "Next slot")
            b.clicked.connect(lambda _=False, d=d: self._step(d))
        self.lab_pos = QLabel("")
        self.lab_pos.setAlignment(Qt.AlignCenter)
        self.lab_pos.setStyleSheet("color:#cfcfcf; font-weight:600;")
        h.addWidget(self.btn_prev)
        h.addWidget(self.lab_pos, 1)
        h.addWidget(self.btn_next)
        return self.nav

    def _update_nav(self):
        on = self.single and len(self.tiles) > 1
        self.nav.setVisible(on)
        if not on:
            return
        i = self.tiles.index(self.active) if self.active in self.tiles else 0
        t = self.tiles[i]
        self.lab_pos.setText(f"{i + 1} / {len(self.tiles)}"
                             + (f"   \u2014   {t.name}" if t.name else "   \u2014   empty slot"))
        self.btn_prev.setEnabled(i > 0)          # no wrap-around, so the greying
        self.btn_next.setEnabled(i < len(self.tiles) - 1)   # tells the truth

    # -- control panel ----------------------------------------------------
    def _panel(self) -> QWidget:
        """Top-to-bottom is the pipeline: scope -> compute -> knobs -> compare -> out."""
        panel = QWidget()
        panel.setFixedWidth(276)
        col = QVBoxLayout(panel)
        col.setSpacing(8)

        # 1. what everything below acts on
        sec, sl = _section("Selection")
        row, self.view_btns = _segmented(("Grid", "Single"), self._on_view)
        self.view_btns[0].setToolTip("All slots side by side")
        self.view_btns[1].setToolTip("Only the ringed image, at window size.\n"
                                     "Left / Right arrows step through the images.")
        self.view_btns[1].setChecked(True)      # the widget must agree with self.single
        sl.addWidget(row)
        r = QHBoxLayout()
        for text, slot in (("Select all", self.select_all), ("Clear", self.clear_selection)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            r.addWidget(b)
        sl.addLayout(r)
        self.lab_sel = QLabel("no images yet")
        self.lab_sel.setStyleSheet("color:#888; border:none;")
        sl.addWidget(self.lab_sel)
        col.addWidget(sec)

        # 2. how, and when, the correction is computed
        sec, pl = _section("Process")
        row, self.mode_btns = _segmented(("Separately", "All together"), self._on_mode)
        self.mode_btns[0].setToolTip("Every selected image gets its own white point")
        self.mode_btns[1].setToolTip("One shared white point for the whole selection:\n"
                                     "keeps a set consistent, and the eyedropper applies to all")
        pl.addWidget(row)
        r = QHBoxLayout()
        self.btn_compute = QPushButton(" Recompute")
        self.btn_compute.setToolTip("Green marker: the preview matches the settings.\n"
                                    "Amber: settings changed, press to update.")
        self.btn_compute.clicked.connect(self.recompute)
        self.cb_autorun = QCheckBox("Auto")
        self.cb_autorun.setChecked(True)
        self.cb_autorun.setToolTip("Recompute on every change (turn off for big batches)")
        self.cb_autorun.toggled.connect(self._on_autorun)
        r.addWidget(self.btn_compute, 1)
        r.addWidget(self.cb_autorun)
        pl.addLayout(r)
        col.addWidget(sec)

        self._sliders, self._rows = {}, {}

        # 3a. where the white point comes from
        sec, wl = _section("White point", self._reset_wp)
        row, self.wp_btns = _segmented(("Auto", "Pick..."), self._on_wp_mode)
        self.wp_btns[0].setToolTip("Estimate the neutral colour from the image itself")
        self.wp_btns[1].setToolTip("Click something that should be grey or white "
                                   "(press again to re-pick)")
        self.wp_btns[1].clicked.connect(lambda: self._arm_pick(True))
        wl.addWidget(row)
        self._add_slider(wl, "neutralize", "De-cast", 0,
                         "Which estimator to trust: 0 = brightest pixels only (subtle), "
                         "100 = whole frame (strong de-cast)")
        col.addWidget(sec)

        # 3b. manual tweaks on top
        sec, al = _section("Adjust", self._reset_adjust)
        for attr, label, tip in (("temperature", "Temperature", "Cool <-> warm"),
                                 ("tint", "Tint", "Green <-> magenta"),
                                 ("brightness", "Brightness", ""),
                                 ("contrast", "Contrast", "")):
            self._add_slider(al, attr, label, -100, tip)
        col.addWidget(sec)

        # 4. compare (preview only; exports are always corrected)
        self.btn_orig = QPushButton("Show original")
        self.btn_orig.setCheckable(True)
        self.btn_orig.setToolTip("Preview the selected images untouched -- one at a\n"
                                 "time if only one is selected. Saving and copying\n"
                                 "always write the corrected version.")
        self.btn_orig.toggled.connect(self._on_show_orig)
        col.addWidget(self.btn_orig)

        col.addStretch(1)

        # 5. out
        self.btn_save = QPushButton("Save selected...")
        self.btn_save.clicked.connect(lambda: self.save(self.selection))
        col.addWidget(self.btn_save)
        self.btn_copy = QPushButton("Copy selected (Ctrl+C)")
        self.btn_copy.clicked.connect(lambda: self.copy(self.selection))
        col.addWidget(self.btn_copy)

        self.status = QLabel("Drop, open, or paste (Ctrl+V) image(s) to start.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#9aa;")
        col.addWidget(self.status)
        self._set_fresh(True)
        return panel

    def _add_slider(self, layout, attr, label, lo, tip):
        row, s = _slider_row(label, lo, 100, DEFAULTS[attr], tip)
        s.valueChanged.connect(self._on_slider)
        layout.addWidget(row)
        self._sliders[attr], self._rows[attr] = s, row

    def _on_show_orig(self, on: bool):
        """Compare acts on the selection like every other control, so one image
        can sit next to its own original in the grid."""
        if self._loading:
            return
        for t in self.selection:
            t.show_orig = on
            t.render()

    # -- compute freshness ------------------------------------------------
    def _set_fresh(self, fresh: bool):
        self._fresh = fresh
        self.btn_compute.setIcon(_dot(FRESH if fresh else STALE))

    def request_render(self):
        """A setting changed: render now, or flag the preview stale if Auto is off."""
        if self.cb_autorun.isChecked():
            self.recompute()
        else:
            self._set_fresh(False)

    def recompute(self):
        self._render_selection()
        self._fresh = True                      # state is immediate; only the icon blinks
        self.btn_compute.setIcon(_dot(BUSY))    # brief flash so a fast pass is still visible
        QTimer.singleShot(120, lambda: self._set_fresh(self._fresh))

    def _on_autorun(self, on: bool):
        if on and not self._fresh:
            self.recompute()

    def _on_view(self, i: int):
        self.single = bool(i)
        self._reflow()

    def _step(self, d: int):
        """Walk every slot, empty ones included -- that is how you reach the
        drop zone the + bar just added. Stops at the ends; no wrap-around."""
        if not self.tiles:
            return
        i = (self.tiles.index(self.active) if self.active in self.tiles else 0) + d
        if 0 <= i < len(self.tiles):
            self.select(self.tiles[i], Qt.NoModifier)

    def _picking(self) -> bool:
        return any(t.loaded and t.view._pick for t in self.tiles)

    def _on_mode(self, i: int):
        self.batch = bool(i)
        if not self.batch:
            for t in self.tiles:
                t.auto_basis = None             # drop any shared basis left over
        self.status.setText(self._idle_status())
        self.request_render()

    # -- undo -------------------------------------------------------------
    def _push_undo(self):
        """Snapshot before anything that adds, replaces or removes an image.
        Pixel arrays go in by reference, so this costs a dict per slot."""
        self._undo.append([
            dict(full=t.full, preview=t.preview, mild=t.mild, glass=t.glass,
                 manual_wp=t.manual_wp, auto_basis=t.auto_basis, name=t.name,
                 params=dict(t.params), show_orig=t.show_orig, computed=t.computed)
            for t in self.tiles])
        del self._undo[:-10]

    def undo(self):
        if not self._undo:
            self.status.setText("Nothing to undo.")
            return
        snap = self._undo.pop()
        for t in self.tiles:
            t.setParent(None)
            t.deleteLater()
        self.tiles = []
        for st in snap:
            t = Tile(self)
            if st["full"] is not None:
                t.__dict__.update(st)
                t._build_image()
                t.render()
            self.tiles.append(t)
        if not self.tiles:
            self.tiles.append(Tile(self))     # always something to drop onto
        self.active = self.tiles[-1]
        self.selection = [self.active]
        self._reflow()
        self._restyle_all()
        self._sync_panel()
        self.status.setText("Undone.")

    # -- tiles / layout ---------------------------------------------------
    def add_empty(self) -> Tile:
        self._push_undo()
        t = Tile(self)
        self.tiles.append(t)
        self._reflow()
        self.select(t, Qt.NoModifier)    # land on the new slot, Single view too
        return t

    def _first_empty(self) -> Tile:
        for t in self.tiles:
            if not t.loaded:
                return t
        t = Tile(self)              # not add_empty(): the caller owns the undo step
        self.tiles.append(t)
        self._reflow()
        return t

    def _reflow(self):
        while self.grid.count():
            self.grid.takeAt(0)
        self.view_btns[0].setEnabled(len(self.tiles) > 1)   # Grid needs a gallery
        self._update_nav()
        # one tile fills the window: always when it is the only one, and in
        # Single view. Anything else needs the fixed-size grid.
        solo = self.tiles[0] if len(self.tiles) == 1 else (
            self.active if self.single and self.active in self.tiles else None)
        if solo is not None:
            for t in self.tiles:
                t.setVisible(t is solo)     # out of the layout, but still children
            solo.setMinimumSize(0, 0)
            solo.setMaximumSize(QT_MAX, QT_MAX)
            self.grid.addWidget(solo, 0, 0)
            solo.render()
            return
        avail = self.scroll.viewport().width() - 20
        cols = max(1, avail // MIN_TILE_W)
        tw = max(MIN_TILE_W, avail // cols - self.grid.spacing())
        for i, t in enumerate(self.tiles):
            t.setVisible(True)
            t.setFixedSize(tw, int(tw * 0.78))
            self.grid.addWidget(t, i // cols, i % cols, Qt.AlignTop)
            t.render()

    def resizeEvent(self, e):
        self._reflow()

    # -- loading ----------------------------------------------------------
    def _after_load(self, added: list["Tile"]):
        """With Auto on the new images are corrected immediately; with Auto off
        they stay as they came in and the marker goes amber."""
        if self.cb_autorun.isChecked():
            for t in added:
                t.computed = True
                t.render()
            self._set_fresh(True)
        else:
            self._set_fresh(False)

    def add_images(self, paths: list[str], into: Tile | None = None):
        self._push_undo()
        last = None
        added = []
        for p in paths:
            try:
                rgb = _load_rgb(p)
            except Exception as exc:  # noqa: BLE001
                self.status.setText(f"Could not open {Path(p).name}: {exc}")
                continue
            if into is not None and not into.loaded:
                tile, into = into, None      # fill the drop target once, then spill over
            else:
                tile = self._first_empty()
            tile.set_source(rgb, Path(p).stem)
            last = tile
            added.append(tile)
        if last:
            self._reflow()
            self.select(last, Qt.NoModifier)
            self._after_load(added)
            self.status.setText(f"Loaded image(s). {sum(t.loaded for t in self.tiles)} total.")

    def paste_into(self, tile: Tile | None):
        # tile given (active tile, or an empty tile's Paste button) is the target:
        # a loaded target is replaced, an empty one is filled, None -> first empty.
        target = tile or self._first_empty()
        md = QApplication.clipboard().mimeData()
        self._push_undo()
        if md.hasImage():
            qimg = QApplication.clipboard().image()
            if not qimg.isNull():
                replaced = target.loaded
                target.set_source(_qimage_to_rgb(qimg), "clipboard")
                self._reflow()
                self.select(target, Qt.NoModifier)
                self._after_load([target])
                self.status.setText("Replaced image from clipboard." if replaced
                                    else "Pasted image from clipboard.")
                return
        urls = [u.toLocalFile() for u in md.urls() if u.toLocalFile().lower().endswith(IMAGE_EXTS)]
        if urls:
            self.add_images(urls, into=target if not target.loaded else None)
            return
        self.status.setText("Clipboard has no image to paste.")

    # -- selection --------------------------------------------------------
    def select(self, tile: Tile, mods):
        """Empty slots are selectable too, so Delete can remove them."""
        if mods & Qt.ShiftModifier and self.active is not None and self.active in self.tiles:
            i, j = self.tiles.index(self.active), self.tiles.index(tile)
            lo, hi = sorted((i, j))
            self.selection = self.tiles[lo:hi + 1]
        elif mods & Qt.ControlModifier:
            if tile in self.selection:
                self.selection.remove(tile)
            else:
                self.selection.append(tile)
        else:
            self.selection = [tile]
        self.active = tile if tile in self.selection else (
            self.selection[-1] if self.selection else None)
        if self.single:
            self._reflow()
        self._update_nav()
        self._restyle_all()
        self._sync_panel()

    def delete_selected(self):
        """Remove the selected slots, loaded or empty. Never removes the last one."""
        victims = [t for t in self.selection if t in self.tiles]
        if not victims:
            return
        self._push_undo()
        n_img = sum(t.loaded for t in victims)
        for t in victims:
            self.tiles.remove(t)
            t.setParent(None)
            t.deleteLater()
        self.selection = []
        self.active = None
        if not self.tiles:
            self.add_empty()          # always leave one "drag image here" area
        else:
            self._reflow()
        self._restyle_all()
        self._sync_panel()
        word = "image" if n_img == 1 else "images"
        self.status.setText(f"Removed {n_img} {word}." if n_img else
                            f"Removed {len(victims)} empty slot(s).")

    def zoom(self, tile: Tile):
        """Double-click: show the tile large; Esc closes it."""
        if not tile.loaded:
            return
        if getattr(self, "_zoom", None) is None:
            self._zoom = ZoomView()
        self._zoom.show_image(QPixmap.fromImage(_to_qimage(tile.output(full=True))))

    def select_all(self):
        self.selection = [t for t in self.tiles if t.loaded]
        self.active = self.selection[-1] if self.selection else None
        self._restyle_all()
        self._sync_panel()

    def clear_selection(self):
        self.selection = []
        self.active = None
        self._restyle_all()
        self._sync_panel()

    def _idle_status(self) -> str:
        """The resting message: what the controls would act on right now."""
        n = sum(t.loaded for t in self.selection)
        if not any(t.loaded for t in self.tiles):
            return "Drop, open, or paste (Ctrl+V) image(s) to start."
        if not n:
            return "Nothing selected - click an image."
        if n == 1:
            return f"{self.active.name}: ready."
        shared = "one shared correction." if self.batch else "a separate correction each."
        return f"{n} images selected - " + shared

    def _restyle_all(self):
        for t in self.tiles:
            t.set_selected(t in self.selection, t is self.active)

    # -- panel <-> selection ----------------------------------------------
    def _sync_panel(self):
        n = sum(t.loaded for t in self.selection)
        total = sum(t.loaded for t in self.tiles)
        self.lab_sel.setText(f"{n} of {total} image(s) selected" if total else "no images yet")
        has = n > 0
        for w in (self.btn_save, self.btn_copy, self.btn_compute, self.btn_orig,
                  *self.mode_btns, *self.wp_btns, *self._rows.values()):
            w.setEnabled(has)
        self.wp_btns[1].setEnabled(self.active is not None and self.active.loaded)
        if not self.active or not self.active.loaded:
            self.status.setText(self._idle_status())
            return
        self._loading = True
        for attr, s in self._sliders.items():
            s.setValue(self.active.params[attr])
        self.btn_orig.setChecked(self.active.show_orig)
        picked = self.active.manual_wp is not None
        self.wp_btns[1 if picked else 0].setChecked(True)
        self._rows["neutralize"].setEnabled(not picked)   # ignored once a patch is picked
        self._loading = False
        if not self._picking():
            self.status.setText(self._idle_status())

    def _on_slider(self):
        if self._loading:
            return
        vals = {a: s.value() for a, s in self._sliders.items()}
        for t in self.selection:
            t.params.update(vals)
        self.request_render()

    def _on_wp_mode(self, i: int):
        if self._loading:
            return
        if i == 0:                                  # back to the automatic estimate
            for t in self.selection:
                t.manual_wp = None
            self._arm_pick(False)
            self._rows["neutralize"].setEnabled(True)
            self.request_render()

    def _arm_pick(self, on: bool):
        """Turn the eyedropper cursor on for the active tile. The view is left
        alone -- switch to Single yourself if the grid tile is too small to aim."""
        live = on and self.active is not None and self.active.loaded
        for t in self.tiles:
            if t.loaded:
                t.view.set_pick(live and t is self.active)
        self.status.setText(
            "Click something grey or white on the ringed image."
            + ("" if self.single else "  (Single view makes it easier to aim.)")
            if live else self._idle_status())

    def on_pick(self, tile: Tile, x: int, y: int):
        sy = tile.full.shape[0] / tile.preview.shape[0]
        sx = tile.full.shape[1] / tile.preview.shape[1]
        point = wb.white_point_from_patch(tile.full, int(x * sx), int(y * sy))
        # "All together" means one white point for the set, a picked one included.
        targets = [t for t in self.selection if t.loaded] if self.batch else [tile]
        for t in targets:
            t.manual_wp = point
        self._arm_pick(False)
        self._loading = True
        self.wp_btns[1].setChecked(True)
        self._loading = False
        self._rows["neutralize"].setEnabled(False)
        self.status.setText(f"White point picked ({len(targets)} image(s)).")
        self.request_render()

    def _reset_wp(self):
        for t in self.selection:
            t.manual_wp = None
            t.params["neutralize"] = DEFAULTS["neutralize"]
        self._loading = True
        self.wp_btns[0].setChecked(True)
        self._loading = False
        self._sync_panel()
        self.request_render()

    def _reset_adjust(self):
        for t in self.selection:
            for a in ADJUSTMENTS:
                t.params[a] = DEFAULTS[a]
        self._sync_panel()
        self.request_render()

    # -- render helpers ---------------------------------------------------
    def _joint_basis(self, tiles):
        pool = np.concatenate([t.preview.reshape(-1, 3) for t in tiles], axis=0)
        if len(pool) > 400_000:
            idx = np.random.default_rng(0).choice(len(pool), 400_000, replace=False)
            pool = pool[idx]
        pool = pool.reshape(-1, 1, 3)
        return wb.white_patch(pool), wb.gray_world(pool)

    def _render_selection(self):
        tiles = [t for t in self.selection if t.loaded]
        joint = self._joint_basis(tiles) if self.batch and len(tiles) > 1 else None
        for t in tiles:
            t.auto_basis = joint
            t.computed = True
            t.render()

    def _render_all(self):
        for t in self.tiles:
            if t.loaded:
                t.render()

    # -- export -----------------------------------------------------------
    def save(self, tiles: list[Tile]):
        tiles = [t for t in tiles if t.loaded]
        if not tiles:
            return
        if len(tiles) == 1:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save image", f"{tiles[0].name}_wb.png",
                "PNG (*.png);;JPEG (*.jpg);;TIFF (*.tif)")
            if not path:
                return
            Image.fromarray(tiles[0].balanced(tiles[0].full)).save(path)
            self.status.setText(f"Saved {Path(path).name}")
            return
        folder = QFileDialog.getExistingDirectory(self, "Save all to folder")
        if not folder:
            return
        for i, t in enumerate(tiles):
            name = f"{i + 1:02d}_{t.name}_wb.png"   # index so same-named tiles don't collide
            Image.fromarray(t.balanced(t.full)).save(Path(folder) / name)
        self.status.setText(f"Saved {len(tiles)} images to {Path(folder).name}")

    def copy(self, tiles: list[Tile]):
        tiles = [t for t in tiles if t.loaded]
        if not tiles:
            return
        if len(tiles) == 1:
            QApplication.clipboard().setImage(_to_qimage(tiles[0].balanced(tiles[0].full)))
            self.status.setText("Copied 1 image to clipboard.")
            return
        # multiple: write temp PNGs and put them on the clipboard as files, so a
        # single paste in PowerPoint inserts them all as separate pictures (CF_HDROP)
        # ponytail: temp dir per copy; OS cleans %TEMP%, no bookkeeping needed
        d = Path(tempfile.mkdtemp(prefix="quickwb_"))
        urls = []
        for i, t in enumerate(tiles):
            p = d / f"{i + 1:02d}_{t.name}_wb.png"   # index prevents same-name collisions
            Image.fromarray(t.balanced(t.full)).save(p)
            urls.append(QUrl.fromLocalFile(str(p)))
        md = QMimeData()
        md.setUrls(urls)
        QApplication.clipboard().setMimeData(md)
        self.status.setText(f"Copied {len(tiles)} images (paste as pictures in PowerPoint).")
