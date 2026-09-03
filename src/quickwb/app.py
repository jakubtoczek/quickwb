"""QuickWB GUI: drop/paste images, white-balance them, export or copy to clipboard.

A scrollable gallery of image tiles. The control panel reads top-to-bottom as the
pipeline: view -> what is selected -> where the white point comes from -> the
knobs -> compare -> what the image is -> export.
Images stay exactly as they came in until you ask for a correction: 'Auto' and
'Pick...' are the two actions that run a pass, and everything below them stays
greyed out until one has. Scope follows the selection: one image is balanced on
its own, several get one shared white point ('Together'), and the eyedropper
then applies a pick to all of them -- it is a readout, not a control. Live edits
run on a downscaled preview; export re-renders full-res.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from PySide6.QtCore import QMimeData, QPropertyAnimation, QRect, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QFrame, QGraphicsOpacityEffect,
    QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QGridLayout, QHBoxLayout,
    QLabel, QDoubleSpinBox, QMainWindow, QPushButton, QRubberBand, QScrollArea,
    QSlider, QVBoxLayout, QWidget,
)

from . import wb

APP_NAME = "QuickWB"
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp")

PREVIEW_MAX = 1400   # longest side of the live-preview copy
MIN_TILE_W = 340     # tiles shrink to fit the window down to this, then wrap/scroll
TILE_RATIO = 0.78    # 340 x 265 box -> roughly a 328 x 192 image inside it
QT_MAX = 16777215    # Qt's QWIDGETSIZE_MAX, for undoing setFixedSize()
DEFAULTS = {"neutralize": 50, "temperature": 0, "tint": 0, "brightness": 0, "contrast": 0}
ADJUSTMENTS = ("temperature", "tint", "brightness", "contrast")


def _image_paths(mime) -> list[str]:
    """The image files in a drop or a paste, if any."""
    return [f for f in (u.toLocalFile() for u in mime.urls())
            if f.lower().endswith(IMAGE_EXTS)]


def _accept_images(e):
    if _image_paths(e.mimeData()):
        e.acceptProposedAction()


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


def _safe(name: str) -> str:
    """Tile name -> filename fragment ('clipboard #2' -> 'clipboard_2')."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_") or "image"


def _hint(text: str) -> QLabel:
    """Dim caption above a control row, so two rows don't read as one choice."""
    lab = QLabel(text)
    lab.setStyleSheet("color:#7d8894; font-size:10px; border:none;")
    return lab


def _ranges(nums) -> str:
    """[1, 3, 4, 5] -> '1, 3-5'."""
    out = []
    for n in nums:
        if out and n == out[-1][1] + 1:
            out[-1][1] = n
        else:
            out.append([n, n])
    return ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in out)


class _Spin(QDoubleSpinBox):
    """Shows 0, -2.5, 0.45 -- never 0.00 -- while still taking two decimals."""

    def textFromValue(self, v):
        return f"{v:g}"


def _slider_row(label, lo, hi, val, tip=""):
    """Label + coarse integer slider + spin box. Returns (row, box).

    The box is the value that counts, so the keyboard can reach 0.45 where the
    slider only stops on whole numbers.
    """
    s = QSlider(Qt.Horizontal)
    s.setRange(lo, hi)
    s.setValue(int(val))
    box = _Spin()
    box.setRange(lo, hi)
    box.setDecimals(2)
    box.setValue(val)
    box.setFixedWidth(58)
    box.setKeyboardTracking(False)         # one recompute per edit, not per keystroke
    s.valueChanged.connect(box.setValue)

    def follow(v):                         # silent: typing 2.5 must not snap back to 2
        s.blockSignals(True)
        s.setValue(int(round(v)))
        s.blockSignals(False)

    box.valueChanged.connect(follow)
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
    return row, box


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


def _segmented(options, on_change=None):
    """Exclusive row of toggle buttons -- one is always on. Returns (row, buttons).

    Without on_change it is a readout instead of a control: flat, dimmer and deaf
    to the mouse, because something else decides which one is on."""
    row = QWidget()
    row.setStyleSheet(
        "QPushButton{padding:5px; border:1px solid #4a4a4a; border-radius:4px;}"
        "QPushButton:checked{background:#2f6ea5; color:#fff; border:1px solid #4aa3ff;}"
        if on_change is not None else
        "QPushButton{padding:5px; border:none; border-radius:4px;"
        " background:#2b2b2b; color:#6c757d;}"
        "QPushButton:checked{background:#31414e; color:#b9c6d2;}")
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(4)
    btns = []
    for i, text in enumerate(options):
        b = QPushButton(text)
        b.setCheckable(True)
        b.setAutoExclusive(True)
        b.setChecked(i == 0)
        if on_change is None:
            b.setFocusPolicy(Qt.NoFocus)
            b.setAttribute(Qt.WA_TransparentForMouseEvents)   # tooltip falls to row
        else:
            b.toggled.connect(lambda on, i=i: on and on_change(i))
        h.addWidget(b, 1)
        btns.append(b)
    return row, btns


class Switch(QCheckBox):
    """A checkbox drawn as an on/off slider, label beside it."""

    W, H = 34, 16

    def __init__(self, text=""):
        super().__init__(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(self.H + 6)
        self.setTristate(True)             # middle = the selection disagrees

    def hitButton(self, pos):
        return self.rect().contains(pos)   # the whole row toggles, not Qt's box

    def nextCheckState(self):
        """Clicking picks a side; only code puts the knob in the middle."""
        self.setChecked(self.checkState() != Qt.Checked)

    def paintEvent(self, e):
        w, h = self.W, self.H
        y = (self.height() - h) // 2
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setOpacity(1.0 if self.isEnabled() else 0.45)
        p.setPen(Qt.NoPen)
        mid = self.checkState() == Qt.PartiallyChecked
        p.setBrush(QColor("#3f6480" if mid else "#2f6ea5" if self.isChecked() else "#4a4a4a"))
        p.drawRoundedRect(0, y, w, h, h / 2, h / 2)
        p.setBrush(QColor("#eaeaea"))
        p.drawEllipse(2 + ((w - h) // 2 if mid else w - h if self.isChecked() else 0),
                      y + 2, h - 4, h - 4)
        p.setPen(QColor("#c8c8c8"))
        p.drawText(w + 8, 0, self.width() - w - 8, self.height(),
                   Qt.AlignVCenter | Qt.AlignLeft, self.text())


class TileView(QLabel):
    """Scaled-to-fit image inside a tile. Emits clicked() for selection and, in
    eyedropper mode, picked(x0, y0, x1, y1) in image pixels -- a click is the
    degenerate rectangle where both corners are the same point."""

    clicked = Signal(object)      # keyboard modifiers at click time
    picked = Signal(int, int, int, int)
    doubled = Signal()            # double-click -> zoom

    def __init__(self):
        super().__init__()
        self._pixmap: QPixmap | None = None
        self._pick = False
        self._drag = None                 # where the ROI rectangle started
        self._band = QRubberBand(QRubberBand.Rectangle, self)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(60, 45)

    def set_image(self, pixmap: QPixmap):
        self._pixmap = pixmap
        self._render()

    def set_pick(self, on: bool):
        self._pick = on
        self._drag = None
        self._band.hide()
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

    def _image_xy(self, pos):
        """Widget point -> image pixel, clamped to the drawn image (None if there
        is nothing drawn), so a drag that runs off the edge still lands."""
        r = self._drawn()
        if not r or self._pixmap is None:
            return None
        ox, oy, dw, dh = r
        x = min(max(pos.x() - ox, 0), dw - 1)
        y = min(max(pos.y() - oy, 0), dh - 1)
        return int(x / dw * self._pixmap.width()), int(y / dh * self._pixmap.height())

    def mousePressEvent(self, e):
        if self._pick and self._pixmap is not None:
            r = self._drawn()
            if r:
                ox, oy, dw, dh = r
                pos = e.position()
                if ox <= pos.x() < ox + dw and oy <= pos.y() < oy + dh:
                    self._drag = pos.toPoint()      # click or drag: the release decides
                    self._band.setGeometry(QRect(self._drag, self._drag))
                    self._band.show()
            return
        self.clicked.emit(QApplication.keyboardModifiers())

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self._band.setGeometry(QRect(self._drag, e.position().toPoint()).normalized())

    def mouseReleaseEvent(self, e):
        if self._drag is None:
            return
        a, b = self._drag, e.position().toPoint()
        self._drag = None
        self._band.hide()
        p0, p1 = self._image_xy(a), self._image_xy(b)
        if p0 and p1:
            self.picked.emit(*p0, *p1)

    def mouseDoubleClickEvent(self, e):
        if not self._pick:
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
        self.head = None          # filename caption; Single view hides it
        self.show_orig = False    # compare is per image, not a global switch
        self.applied = None       # the settings the on-screen pixels were made with
        self.serial = None        # identifies the image itself, across moves and undo
        self.mates = None         # (serial, slot) of every image the pass was computed
                                  # from, if it was a shared one
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(6, 6, 6, 6)
        self._build_empty()
        self._restyle()

    # -- state ------------------------------------------------------------
    @property
    def loaded(self) -> bool:
        return self.full is not None

    @property
    def computed(self) -> bool:
        """A pass has run, so the preview is corrected rather than untouched."""
        return self.applied is not None

    def _clear_layout(self, lay=None):
        """Recursive: a nested QHBoxLayout has no .widget(), so the old button
        row used to survive and pop back up on the next resize."""
        lay = lay or self._lay
        self.head = None          # about to be deleted with the rest
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
        self.head = QLabel(self.name)
        self.head.setStyleSheet("color:#cfcfcf; font-weight:600; border:none;")
        self._lay.addWidget(self.head)
        self.view = TileView()
        self.view.clicked.connect(lambda mods: self.win.select(self, mods))
        self.view.picked.connect(lambda *r: self.win.on_pick(self, *r))
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
        self.show_orig, self.applied = False, None
        self.serial = self.mates = None
        self._build_empty()
        self._restyle()

    def set_source(self, rgb: np.ndarray, name: str):
        self.full = rgb
        self.preview = _downscale(rgb, PREVIEW_MAX)
        self.mild = self.glass = None      # estimated when Auto first asks for them
        self.manual_wp = None
        self.auto_basis = None
        self.name = name
        self.params = dict(DEFAULTS)
        self.show_orig = False
        self.applied = self.mates = None
        self.serial = self.win.next_serial()    # a new image, even in the same slot
        self._build_image()
        self.render()

    # -- rendering --------------------------------------------------------
    def _basis(self):
        """The estimator pair for this image, computed the first time it is
        needed: loading corrects nothing, so opening a folder should not pay for
        an estimate nobody has asked for."""
        if self.mild is None:
            self.mild = wb.white_patch(self.preview)
            self.glass = wb.gray_world(self.preview)
        return self.mild, self.glass

    def white_point(self):
        if self.manual_wp is not None:
            return self.manual_wp
        mild, glass = self.auto_basis or self._basis()
        s = self.params["neutralize"] / 100.0
        return (1.0 - s) * mild + s * glass

    def commit(self):
        """Freeze the live settings as the ones the pixels were made with.

        Everything the panel reports comes from here, so it always describes the
        pixels on screen rather than a correction nobody has asked for yet.
        """
        self.applied = dict(self.params, wp=self.white_point(),
                            how="picked" if self.manual_wp is not None else "auto")

    def balanced(self, rgb: np.ndarray) -> np.ndarray:
        if self.applied is None:
            return rgb          # nothing asked for yet: export what you see
        p = self.applied
        return wb.balance(rgb, white_point=p["wp"],
                          temperature=p["temperature"], tint=p["tint"],
                          brightness=p["brightness"] / 200.0, contrast=p["contrast"] / 200.0)

    def output(self, full: bool) -> np.ndarray:
        """Preview/zoom pixels. Shows the source untouched while comparing, and
        until Auto or a pick has actually run -- a freshly loaded image must not
        pretend to be corrected. Export goes through balanced()."""
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
            border = "3px solid #4aa3ff" if self.selected else "2px dashed #4aa3ff"
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
        _accept_images(e)

    def dropEvent(self, e):
        self.win.add_images(_image_paths(e.mimeData()), into=self)

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
    """QGraphicsView gives pan (left-drag) and scale for free; we add wheel
    zoom and refit-on-resize."""

    def __init__(self, owner: "ZoomView"):
        super().__init__()
        self.owner = owner
        self.setDragMode(QGraphicsView.ScrollHandDrag)          # left-drag pans
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#111"))
        self.setRenderHints(QPainter.SmoothPixmapTransform | QPainter.Antialiasing)

    def wheelEvent(self, e):
        # the image is fitted, so there is nothing to scroll past -- the wheel
        # zooms whether or not Ctrl is held
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self.scale(f, f)
        self.owner._zoomed = True

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.owner._on_resize()


class _Flash(QLabel):
    """A caption that shows itself over the image for a moment, then fades. The
    full-screen view has no nav bar to name the image, so this stands in for it
    without leaving anything permanent on top of the picture."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setStyleSheet("color:#f0f0f0; background:rgba(0,0,0,170);"
                           "border-radius:7px; padding:6px 14px; font-weight:600;")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(fx)
        self._anim = QPropertyAnimation(fx, b"opacity", self)
        self._anim.setDuration(1500)
        for at, v in ((0.0, 1.0), (0.55, 1.0), (1.0, 0.0)):   # hold, then fade
            self._anim.setKeyValueAt(at, v)
        self._anim.finished.connect(self.hide)
        self.hide()

    def flash(self, text: str):
        self.setText(text)
        self.adjustSize()
        host = self.parentWidget()
        self.move(max(0, (host.width() - self.width()) // 2), 16)
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.start()


class ZoomView(QWidget):
    """Full-screen single-image view (double-click). Scroll zooms, left-drag
    pans, left/right walk the images, Esc closes. Nothing is drawn on top of the
    picture: Esc and another double-click is the way back to a plain fit."""

    def __init__(self, on_step=None):
        super().__init__()
        self._on_step = on_step
        self.setWindowTitle(f"{APP_NAME} - scroll to zoom - drag to pan - "
                            "left/right for the next image - Esc to close")
        icon = app_icon()
        if icon is not None:
            self.setWindowIcon(icon)
        self.scene = QGraphicsScene(self)
        self.item = QGraphicsPixmapItem()
        self.item.setTransformationMode(Qt.SmoothTransformation)
        self.scene.addItem(self.item)
        self.canvas = _Canvas(self)
        self.canvas.setScene(self.scene)
        # a QGraphicsView eats the arrow keys to scroll with; there is nothing to
        # scroll here, so keep the focus on the window and let them walk the images
        self.canvas.setFocusPolicy(Qt.NoFocus)
        self.setFocusPolicy(Qt.StrongFocus)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.canvas)
        self.flash = _Flash(self.canvas)
        self._zoomed = False      # once zoomed, a resize no longer refits

    def show_image(self, pixmap: QPixmap, caption: str = ""):
        self.item.setPixmap(pixmap)
        self.scene.setSceneRect(self.item.boundingRect())
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self._fit()
        if caption:
            self.flash.flash(caption)

    def _fit(self):
        self.canvas.resetTransform()
        self.canvas.fitInView(self.item, Qt.KeepAspectRatio)
        self._zoomed = False

    def _on_resize(self):
        if not self._zoomed:               # keep fitting until the user zooms
            self.canvas.fitInView(self.item, Qt.KeepAspectRatio)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
        elif e.key() in (Qt.Key_Left, Qt.Key_Right) and self._on_step is not None:
            self._on_step(1 if e.key() == Qt.Key_Right else -1)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} - image white balance")
        icon = app_icon()
        if icon is not None:
            self.setWindowIcon(icon)

        self.tiles: list[Tile] = []
        self.selection: list[Tile] = []
        self.active: Tile | None = None   # the slot you are ON: ring, arrows, Single view
        self._serial = 0                  # tells the images apart, whatever slot
        self.batch = False        # False: each image on its own; True: one joint correction
        self.single = True        # True: only the active tile, big; False: grid
        self._anchor = None       # where a Shift range starts (not the last click)
        self._undo = []           # snapshots for Ctrl+Z, newest last
        self._redo = []           # what Ctrl+Z took away, for Ctrl+Shift+Z
        self._loading = False     # guard: panel is being synced, ignore widget signals
        self._edit_key = None     # coalesces one slider drag into one undo step
        self._cols = 1            # grid columns, so Up/Down can move a whole row
        self._zoom = None         # the full-screen window, built on first use

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setSpacing(10)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.grid_host)
        self.grid_host.mousePressEvent = lambda e: self.clear_selection()
        # dropping in the gaps between tiles, or below them, opens slots for the
        # images rather than doing nothing at all
        self.grid_host.setAcceptDrops(True)
        self.grid_host.dragEnterEvent = _accept_images
        self.grid_host.dropEvent = lambda e: self.add_images(_image_paths(e.mimeData()))

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
        self.resize(*self._default_size())

        QShortcut(QKeySequence.Copy, self, lambda: self.copy(self.selection))
        # paste replaces the slot you are on only while it is selected; after
        # Deselect it fills a fresh one instead of overwriting something
        QShortcut(QKeySequence.Paste, self, lambda: self.paste_into(
            self.active if self.active in self.selection else None))
        QShortcut(QKeySequence.Delete, self, self.delete_selected)
        QShortcut(QKeySequence.Undo, self, self.undo)
        QShortcut(QKeySequence.SelectAll, self, self.select_all)
        QShortcut(QKeySequence.Redo, self, self.redo)              # Ctrl+Y
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, self.redo)   # and the habit
        QShortcut(QKeySequence(Qt.Key_Right), self, lambda: self._step(1))
        QShortcut(QKeySequence(Qt.Key_Left), self, lambda: self._step(-1))
        QShortcut(QKeySequence(Qt.Key_Down), self, lambda: self._step_row(1))
        QShortcut(QKeySequence(Qt.Key_Up), self, lambda: self._step_row(-1))

        self.add_empty()   # start with one big empty drop zone
        self._sync_panel()

    def _default_size(self) -> tuple[int, int]:
        """1200 x 800: a comfortable 2x2 grid, drag it wider for more. The floor
        keeps the panel off its scrollbar even with the white-point row and a
        status line wrapped to two rows. Capped to the screen."""
        grow = self.wp_info.sizeHint().height() + 2 * self.status.sizeHint().height()
        h = max(800, self._wrap.widget().sizeHint().height() + grow)
        avail = QApplication.primaryScreen().availableGeometry()
        return min(1200, avail.width()), min(h, avail.height())

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
        for t in self.tiles:
            if t.head is not None:
                t.head.setVisible(not on)   # the counter carries the name instead
        if not on:
            return
        i = self.tiles.index(self.active)
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

        # 1. how the slots are laid out (display only -- touches no pixels)
        sec, vl = _section("View")
        row, self.view_btns = _segmented(("Single", "Grid"), self._on_view)
        self.view_btns[0].setToolTip("Only the ringed image, at window size.\n"
                                     "Left / Right arrows step through the images.")
        self.view_btns[1].setToolTip("All slots side by side")
        vl.addWidget(row)
        col.addWidget(sec)

        # 2. what everything below acts on
        sec, sl = _section("Selection")
        r = QHBoxLayout()
        for text, slot, tip in (
                ("Select all", self.select_all, "Every loaded image (Ctrl+A)"),
                ("Deselect", self.clear_selection,
                 "Select nothing -- clicking the background does this too")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            r.addWidget(b)
        sl.addLayout(r)
        self.lab_sel = QLabel("no images yet")
        self.lab_sel.setStyleSheet("color:#888; border:none;")
        sl.addWidget(self.lab_sel)
        col.addWidget(sec)

        self._spins, self._rows, self._labels = {}, {}, {}

        # 3. where the white point comes from. Scope belongs here because it
        #    feeds the estimate: each image on its own, or one colour for the set.
        sec, wl = _section("White point")
        wl.addWidget(_hint("scope  ·  from the selection"))
        row, self.mode_btns = _segmented(("Separately", "Together"))
        row.setToolTip("Set by what you selected, so there is nothing to choose:"
                       "\none image gets its own white point; several share one,"
                       "\nand the eyedropper then applies to all of them.")
        wl.addWidget(row)
        wl.addWidget(_hint("source  ·  press one to balance"))
        # actions, not modes: a loaded image is left alone until one is pressed
        r = QHBoxLayout()
        r.setSpacing(4)
        self.btn_auto = QPushButton("Auto")
        self.btn_auto.setToolTip("Estimate the neutral colour from the image itself.\n"
                                 "Press again to run it over with new settings.")
        self.btn_auto.clicked.connect(self._on_auto)
        self.btn_pick = QPushButton("Pick...")
        self.btn_pick.setCheckable(True)      # stays down until the pick lands
        self.btn_pick.setToolTip("Click -- or drag a rectangle over -- something that\n"
                                 "should be grey or white on any selected image.\n"
                                 "A rectangle averages everything inside it.")
        self.btn_pick.toggled.connect(self._arm_pick)
        r.addWidget(self.btn_auto, 1)
        r.addWidget(self.btn_pick, 1)
        wl.addLayout(r)
        self._add_slider(wl, "neutralize", "De-cast", 0,
                         "How hard Auto looks: 0 = brightest pixels only (subtle), "
                         "100 = whole frame (strong de-cast). Moving it re-runs Auto.")
        self.wp_info = QWidget()          # what Auto/Pick actually came up with
        r = QHBoxLayout(self.wp_info)
        r.setContentsMargins(0, 4, 0, 0)
        self.wp_swatch = QLabel()
        self.wp_swatch.setFixedSize(14, 14)
        self.lab_wp = QLabel("")
        self.lab_wp.setStyleSheet("color:#9aa; border:none;")
        tip = ("The colour actually divided out of the image -- what the last\n"
               "pass settled on, not what the current settings would give.\n"
               "auto: computed from the picture   picked: your eyedropper\n"
               "shared: one white point, and the slots that share it -- counted\n"
               "down the grid from 1, like the Single-view counter")
        self.wp_info.setToolTip(tip)
        r.addWidget(self.wp_swatch)
        r.addWidget(self.lab_wp, 1)
        wl.addWidget(self.wp_info)
        col.addWidget(sec)

        # 4. manual tweaks on top
        self.sec_adjust, al = _section("Adjust", self._reset_adjust)
        sec = self.sec_adjust
        for attr, label, tip in (("temperature", "Temperature", "Cool <-> warm"),
                                 ("tint", "Tint", "Green <-> magenta"),
                                 ("brightness", "Brightness", ""),
                                 ("contrast", "Contrast", "")):
            self._add_slider(al, attr, label, -100, tip)
        col.addWidget(sec)

        # 5. compare (preview only; exports are always corrected)
        self.sw_orig = Switch("Show original")
        self.sw_orig.setToolTip("Preview the selected images untouched -- one at a\n"
                                "time if only one is selected. Saving and copying\n"
                                "always write the corrected version.")
        # clicked, not toggled: Qt counts the middle state as checked, so
        # toggled stays silent when a mixed selection resolves to all-on
        self.sw_orig.clicked.connect(self._on_show_orig)
        col.addWidget(self.sw_orig)

        # 6. what the selection actually is
        sec, il = _section("Image")
        self.lab_info = QLabel("no image")
        self.lab_info.setStyleSheet("color:#9aa; border:none;")
        il.addWidget(self.lab_info)
        col.addWidget(sec)

        col.addStretch(1)

        # 7. out
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
        # ponytail: scroll instead of squash -- a status line that wraps to two
        # rows used to take the height back out of the controls above it
        wrap = QScrollArea()
        wrap.setWidget(panel)
        wrap.setWidgetResizable(True)
        wrap.setFixedWidth(276 + wrap.verticalScrollBar().sizeHint().width())
        wrap.setFrameShape(QFrame.NoFrame)
        wrap.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._wrap = wrap
        return wrap

    def _add_slider(self, layout, attr, label, lo, tip):
        row, box = _slider_row(label, lo, 100, DEFAULTS[attr], tip)
        box.valueChanged.connect(lambda _=0, a=attr: self._on_value(a))
        # letting go ends the undo step, so drag-pause-drag is two of them
        row.findChild(QSlider).sliderReleased.connect(self._end_edit)
        layout.addWidget(row)
        self._spins[attr], self._rows[attr] = box, row
        self._labels[attr] = label      # what Ctrl+Z calls this knob

    def _on_show_orig(self, on: bool):
        """Compare acts on the selection like every other control, so one image
        can sit next to its own original in the grid."""
        if self._loading:
            return
        for t in self.selection:
            t.show_orig = on
            t.render()

    def recompute(self):
        """Apply the current settings to the selection. Auto, a pick, De-cast and
        the Adjust sliders all end here -- there is no separate apply step."""
        self._render_selection()
        self._sync_panel()

    def _on_view(self, i: int):
        self.single = not i
        self._reflow()

    def _step(self, d: int):
        """Walk from the slot you are on, empty ones included -- that is how you
        reach the drop zone the + bar just added. Stops at the ends, no wrap."""
        if self.active not in self.tiles:
            return
        i = self.tiles.index(self.active) + d
        if 0 <= i < len(self.tiles):
            self.select(self.tiles[i], Qt.NoModifier)

    def _step_row(self, d: int):
        """Up/Down in Grid move a whole row, so the arrows walk the layout you
        can see. In Single there is only one row, so they do nothing."""
        if not self.single:
            self._step(d * self._cols)

    def next_serial(self) -> int:
        self._serial += 1
        return self._serial

    def _picking(self) -> bool:
        return any(t.loaded and t.view._pick for t in self.tiles)

    # -- undo -------------------------------------------------------------
    def _snapshot(self):
        """Pixel arrays go in by reference, so this costs a dict per slot."""
        return [
            dict(full=t.full, preview=t.preview, mild=t.mild, glass=t.glass,
                 manual_wp=t.manual_wp, auto_basis=t.auto_basis, name=t.name,
                 serial=t.serial, mates=t.mates,
                 params=dict(t.params), show_orig=t.show_orig,
                 applied=dict(t.applied) if t.applied else None)
            for t in self.tiles]

    def _push_undo(self, label: str, key=None):
        """Called before anything that changes the pixels: an image added,
        replaced or removed, or a correction applied. *label* is what Ctrl+Z will
        say it took back, so it names the action in the user's words.

        *key* names a knob, and consecutive edits of the same one collapse into a
        single step -- dragging De-cast across the scale is one Ctrl+Z, not fifty.
        Selection, view and Show original are not history: they change nothing.
        """
        if key is not None and key == self._edit_key:
            return
        self._edit_key = key
        self._undo.append((self._snapshot(), label))
        del self._undo[:-20]
        self._redo.clear()        # a new action forks the history

    def _end_edit(self):
        """Letting a slider go closes its undo step."""
        self._edit_key = None

    def undo(self):
        if not self._undo:
            self.status.setText("Nothing to undo.")
            return
        snap, label = self._undo.pop()
        self._redo.append((self._snapshot(), label))    # redo puts back the same act
        self._restore(snap, "Undone: %s." % label)

    def redo(self):
        if not self._redo:
            self.status.setText("Nothing to redo.")
            return
        snap, label = self._redo.pop()
        self._undo.append((self._snapshot(), label))
        self._restore(snap, "Redone: %s." % label)

    def _restore(self, snap, msg):
        # positions, so undoing a slider tweak leaves the selection alone
        sel = [self.tiles.index(t) for t in self.selection if t in self.tiles]
        act = self.tiles.index(self.active) if self.active in self.tiles else -1
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
        last = len(self.tiles) - 1
        self.active = self.tiles[min(act, last) if act >= 0 else last]
        self.selection = [self.tiles[i] for i in sel if i <= last] or [self.active]
        self._edit_key = None
        self._reflow()
        self._restyle_all()
        self._sync_panel()
        self.status.setText(msg)

    # -- tiles / layout ---------------------------------------------------
    def add_empty(self) -> Tile:
        self._push_undo("add slot")
        t = Tile(self)
        self.tiles.append(t)
        self._reflow()
        if self.single and len(self.tiles) > 1:
            self.view_btns[1].setChecked(True)   # a new slot is only visible in Grid
        self.select(t, Qt.NoModifier)            # and that is where you land
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
        if self.active not in self.tiles:      # the slot you are on always exists,
            self.active = self.tiles[0] if self.tiles else None    # or Single has
        while self.grid.count():               # nothing to show
            self.grid.takeAt(0)
        gallery = len(self.tiles) > 1
        self.view_btns[1].setEnabled(gallery)      # Grid needs a gallery
        if not gallery and not self.single:        # ... and without one, Single is
            self.single = True                     # the only thing Grid could show
            for b in self.view_btns:
                b.blockSignals(True)               # no re-entrant _reflow
            self.view_btns[0].setChecked(True)
            for b in self.view_btns:
                b.blockSignals(False)
        self._update_nav()
        # one tile fills the window: always when it is the only one, and in
        # Single view. Anything else needs the fixed-size grid.
        solo = self.tiles[0] if len(self.tiles) == 1 else (
            self.active if self.single else None)
        if solo is not None:
            self._cols = 1
            for t in self.tiles:
                t.setVisible(t is solo)     # out of the layout, but still children
            solo.setMinimumSize(0, 0)
            solo.setMaximumSize(QT_MAX, QT_MAX)
            self.grid.addWidget(solo, 0, 0)
            return
        avail = self.scroll.viewport().width() - 20
        self._cols = cols = max(1, avail // MIN_TILE_W)
        tw = max(MIN_TILE_W, avail // cols - self.grid.spacing())
        for i, t in enumerate(self.tiles):
            t.setVisible(True)
            t.setFixedSize(tw, int(tw * TILE_RATIO))
            self.grid.addWidget(t, i // cols, i % cols, Qt.AlignTop)

    def resizeEvent(self, e):
        # laying the tiles out again is cheap; re-rendering them is not, and the
        # views rescale the pixmaps they already hold
        self._reflow()

    # -- loading ----------------------------------------------------------
    def _after_load(self):
        """New images come in exactly as they are: Auto or a pick is what starts
        a correction, so until then you are looking at the original."""
        if self.single and sum(t.loaded for t in self.tiles) > 1:
            self.view_btns[1].setChecked(True)   # more than one image -> show them all
        self._sync_panel()

    def add_images(self, paths: list[str], into: Tile | None = None):
        if not paths:
            return
        self._push_undo("open")
        last = None
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
        if last:
            self._reflow()
            self.select(last, Qt.NoModifier)
            self._after_load()
            self.status.setText(f"Loaded image(s). {sum(t.loaded for t in self.tiles)} total.")

    def paste_into(self, tile: Tile | None):
        # tile given (active tile, or an empty tile's Paste button) is the target:
        # a loaded target is replaced, an empty one is filled, None -> first empty.
        target = tile or self._first_empty()
        md = QApplication.clipboard().mimeData()
        self._push_undo("paste")
        if md.hasImage():
            qimg = QApplication.clipboard().image()
            if not qimg.isNull():
                replaced = target.loaded
                target.set_source(_qimage_to_rgb(qimg), self._clip_name())
                self._reflow()
                self.select(target, Qt.NoModifier)
                self._after_load()
                self.status.setText("Replaced image from clipboard." if replaced
                                    else "Pasted image from clipboard.")
                return
        urls = _image_paths(md)
        if urls:
            self.add_images(urls, into=target if not target.loaded else None)
            return
        self.status.setText("Clipboard has no image to paste.")

    def _clip_name(self) -> str:
        """clipboard #1, #2, ... -- "clipboard" on every tile told you nothing."""
        used = {t.name for t in self.tiles}
        n = 1
        while f"clipboard #{n}" in used:
            n += 1
        return f"clipboard #{n}"

    # -- selection --------------------------------------------------------
    def select(self, tile: Tile, mods):
        """Empty slots are selectable too, so Delete can remove them."""
        if mods & Qt.ShiftModifier and self._anchor in self.tiles:
            i, j = self.tiles.index(self._anchor), self.tiles.index(tile)
            lo, hi = sorted((i, j))
            self.selection = self.tiles[lo:hi + 1]   # anchor stays put, so the
        else:                                        # range grows as you re-click
            self._anchor = tile
            if not mods & Qt.ControlModifier:
                self.selection = [tile]
            elif tile in self.selection:
                self.selection.remove(tile)
            else:
                self.selection.append(tile)
        self.active = tile          # you clicked it, you are on it -- even if the
                                    # same Ctrl+click just took it out of the selection
        self._edit_key = None       # a new slot means a new undo step
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
        self._push_undo("delete")
        n_img = sum(t.loaded for t in victims)
        i = self.tiles.index(victims[0])          # land next to the hole, not at the top
        for t in victims:
            self.tiles.remove(t)
            t.setParent(None)
            t.deleteLater()
        self.selection = []
        self.active = self.tiles[min(i, len(self.tiles) - 1)] if self.tiles else None
        if not self.tiles:
            self._first_empty()       # always leave one "drag image here" area,
                                      # without an undo step of its own
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
        if self._zoom is None:
            self._zoom = ZoomView(self._zoom_step)
        # no nav bar up there, so the caption says which image this is
        loaded = [t for t in self.tiles if t.loaded]
        cap = ("%d / %d   \u2014   %s" % (loaded.index(tile) + 1, len(loaded), tile.name)
               if tile in loaded else tile.name)
        self._zoom.show_image(QPixmap.fromImage(_to_qimage(tile.output(full=True))), cap)

    def _zoom_step(self, d: int):
        """Left/Right in the full-screen view walk the loaded images, as in
        Single -- empty slots are skipped: there would be nothing to show."""
        loaded = [t for t in self.tiles if t.loaded]
        if self.active not in loaded:
            return
        i = loaded.index(self.active) + d
        if 0 <= i < len(loaded):
            self.select(loaded[i], Qt.NoModifier)
            self.zoom(loaded[i])

    def select_all(self):
        """Leaves the slot you are on where it is, so the arrows carry on from
        the image you are looking at rather than jumping to the last one."""
        self.selection = [t for t in self.tiles if t.loaded]
        self._restyle_all()
        self._sync_panel()

    def clear_selection(self):
        self.selection = []       # still on the same slot, just acting on nothing
        self._restyle_all()
        self._sync_panel()

    def _idle_status(self) -> str:
        """The resting message: what the controls would act on right now."""
        n = sum(t.loaded for t in self.selection)
        if not any(t.loaded for t in self.tiles):
            return "Drop, open, or paste (Ctrl+V) image(s) to start."
        if not n:
            return "Nothing selected - click an image."
        if not all(t.computed for t in self.selection if t.loaded):
            what = "this image." if n == 1 else f"these {n} images."
            return "Press Auto, or Pick... a grey area, to white-balance " + what
        if n == 1:
            return f"{self._lead().name}: balanced."
        shared = "one shared correction." if self.batch else "a separate correction each."
        return f"{n} images selected - " + shared

    def _lead(self):
        """The selected image the panel reports on: the one you are on when it is
        selected, else the first selected image (None if the selection is empty)."""
        sel = [t for t in self.selection if t.loaded]
        return self.active if self.active in sel else (sel[0] if sel else None)

    def _restyle_all(self):
        for t in self.tiles:
            t.set_selected(t in self.selection, t is self.active)

    # -- panel <-> selection ----------------------------------------------
    def _sync_panel(self):
        lead = self._lead()
        n = sum(t.loaded for t in self.selection)
        total = sum(t.loaded for t in self.tiles)
        self.lab_sel.setText(f"{n} of {total} image(s) selected" if total else "no images yet")
        self._auto_scope()            # before the status line, which quotes the scope
        has = n > 0
        for w in (self.btn_save, self.btn_copy, self.sw_orig,
                  self.btn_auto, self.btn_pick):
            w.setEnabled(has)
        self._sync_info()
        # De-cast is Auto's knob and Adjust refines a correction: neither means
        # anything until a pass has run, so both stay greyed until one has
        done = lead is not None and lead.computed
        picked = lead is not None and lead.manual_wp is not None
        self._rows["neutralize"].setEnabled(done and not picked)
        self.sec_adjust.setEnabled(done)
        self.sw_orig.setEnabled(done)
        if lead is None:
            self.status.setText(self._idle_status())
            return
        self._loading = True
        for attr, b in self._spins.items():
            b.setValue(lead.params[attr])
        # the switch stands for the whole selection: middle when they differ
        shown = {t.show_orig or not t.computed for t in self.selection if t.loaded}
        self.sw_orig.setCheckState(Qt.PartiallyChecked if len(shown) != 1 else
                                   Qt.Checked if shown.pop() else Qt.Unchecked)
        self._loading = False
        if self._picking():
            self._arm_pick(True)      # follow the selection: never leave the
        else:                         # eyedropper armed on a tile you left behind
            self.status.setText(self._idle_status())

    def _auto_scope(self):
        """Scope is a readout, not a question: one selected image is balanced on
        its own, several share one white point. Changing it corrects nothing on
        its own -- the next Auto or pick is what acts on the new scope."""
        n = sum(t.loaded for t in self.selection)
        if not n:
            return
        many = n > 1
        self.mode_btns[many].setChecked(True)
        if self.batch == many:
            return
        self.batch = many
        if not many:
            for t in self.tiles:
                t.auto_basis = None         # drop any shared basis left over

    def _sync_info(self):
        """What the controls act on, and (under White point) the neutral colour
        that was actually applied -- the last pass, not the pending settings."""
        sel = [t for t in self.selection if t.loaded]
        done = [t for t in sel if t.computed]
        sizes = {t.full.shape[:2] for t in sel}
        mp = sum(t.full.shape[0] * t.full.shape[1] for t in sel) / 1e6
        if not sel:
            self.lab_info.setText("no image")
        elif len(sel) == 1:
            h, w = sizes.pop()
            self.lab_info.setText(f"{sel[0].name or 'untitled'}\n"
                                  f"{w} × {h} px   —   {mp:.1f} MP")
        else:
            h, w = sizes.pop() if len(sizes) == 1 else (0, 0)
            each = f"{w} × {h} px each" if h else "mixed sizes"
            self.lab_info.setText(f"{len(sel)} images selected\n"
                                  f"{each}   —   {mp:.1f} MP total")
        # one line that always says something: no colour has been settled on
        # until a pass runs, and a disagreeing selection has no single answer
        wps = [tuple(np.clip(np.asarray(t.applied["wp"], float), 0, 255).astype(int))
               for t in done]
        one = len(done) == len(sel) and len(set(wps)) == 1
        self.wp_swatch.setVisible(one)
        if not sel:
            self.lab_wp.setText("no image selected")
        elif not done:
            self.lab_wp.setText("white balance not computed yet")
        elif not one:
            self.lab_wp.setText("varies")
        else:
            lead = self._lead()
            src = lead if lead in done else done[0]   # the image the swatch stands for
            wp = wps[done.index(src)]
            self.wp_swatch.setStyleSheet(
                "background:#%02X%02X%02X; border:1px solid #555;" % wp)
            self.lab_wp.setText("#%02X%02X%02X  ·  %s" % (*wp, self._source_text(src)))

    def _source_text(self, t: Tile) -> str:
        """Where this white point came from, and for a shared pass which images it
        was computed from -- history, not current company: re-balancing one of them
        on its own does not change where this one's white point came from.

        Slots are looked up fresh, so moving an image just updates its number; one
        that has been deleted or replaced shows as `ex4`, the slot it came from."""
        how = t.applied["how"]
        if not t.mates:
            return how
        here = {x.serial: i for i, x in enumerate(self.tiles, 1) if x.loaded}
        now = sorted(here[sid] for sid, _ in t.mates if sid in here)
        gone = sorted(was for sid, was in t.mates if sid not in here)
        if not gone and len(now) == sum(x.loaded for x in self.tiles):
            who = "all"
        else:
            who = ", ".join(["ex%d" % g for g in gone] + ([_ranges(now)] if now else []))
        return "shared %s (%s)" % (how, who)

    def _on_value(self, attr: str):
        if self._loading:
            return
        self._push_undo(self._labels[attr], key=attr)
        vals = {a: b.value() for a, b in self._spins.items()}
        for t in self.selection:
            t.params.update(vals)
            if attr == "neutralize":
                t.manual_wp = None     # De-cast is Auto's knob: moving it re-runs Auto
        self.recompute()

    def _on_auto(self):
        """Estimate the white point for the selection and apply it. A button, not
        a mode: pressing it again simply runs the pass over."""
        if not any(t.loaded for t in self.selection):
            return
        self._push_undo("Auto")
        self.btn_pick.setChecked(False)         # cancels a half-armed eyedropper
        for t in self.selection:
            t.manual_wp = None
        self.recompute()
        self.status.setText("Balanced automatically (%d image(s))."
                            % sum(t.loaded for t in self.selection))

    def _arm_pick(self, on: bool):
        """Turn the eyedropper on for every selected image, so you can aim at
        whichever one has the cleanest grey -- the pick then applies to the set.
        The button stays down until the pick lands. The view is left alone --
        switch to Single yourself if the grid tile is too small to aim."""
        sel = [t for t in self.selection if t.loaded]
        live = on and bool(sel)
        if self.btn_pick.isChecked() != live:
            self.btn_pick.blockSignals(True)
            self.btn_pick.setChecked(live)
            self.btn_pick.blockSignals(False)
        for t in self.tiles:
            if t.loaded:
                t.view.set_pick(live and t in sel)
        self.status.setText(
            "Click, or drag a rectangle over, something grey or white on %s." % (
                "the ringed image" if len(sel) == 1 else "any selected image")
            + ("" if self.single else "  (Single view makes it easier to aim.)")
            if live else self._idle_status())

    def on_pick(self, tile: Tile, x0: int, y0: int, x1: int, y1: int):
        """A click reads a small patch; a dragged rectangle averages the lot."""
        sy = tile.full.shape[0] / tile.preview.shape[0]
        sx = tile.full.shape[1] / tile.preview.shape[1]
        box = (int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy))
        area = abs(x1 - x0) > 2 and abs(y1 - y0) > 2
        point = (wb.white_point_from_rect(tile.full, *box) if area else
                 wb.white_point_from_patch(tile.full, box[0], box[1]))
        self._push_undo("Pick")
        # "Together" means one white point for the set, a picked one included.
        targets = [t for t in self.selection if t.loaded] if self.batch else [tile]
        for t in targets:
            t.manual_wp = point
        self.btn_pick.setChecked(False)
        self.recompute()
        self.status.setText("White point picked from %s (%d image(s))."
                            % ("an area" if area else "a point", len(targets)))

    def _reset_adjust(self):
        self._push_undo("Reset")
        for t in self.selection:
            for a in ADJUSTMENTS:
                t.params[a] = DEFAULTS[a]
        self.recompute()

    # -- render helpers ---------------------------------------------------
    def _joint_basis(self, tiles):
        pool = np.concatenate([t.preview.reshape(-1, 3) for t in tiles], axis=0)
        if len(pool) > 400_000:
            idx = np.random.default_rng(0).choice(len(pool), 400_000, replace=False)
            pool = pool[idx]
        pool = pool.reshape(-1, 1, 3)
        return wb.white_patch(pool), wb.gray_world(pool)

    def _unify_source(self, tiles):
        """One white point for the set means one source for the set: the pick on
        the slot you are on wins for every selected image, and with no pick
        anywhere they all go back to the estimate. Scope is the selection, not
        the whole gallery -- that is what every other control here acts on."""
        lead = self.active if self.active in tiles else tiles[0]
        wp = next((t.manual_wp for t in [lead, *tiles] if t.manual_wp is not None), None)
        for t in tiles:
            t.manual_wp = wp

    def _render_selection(self):
        tiles = [t for t in self.selection if t.loaded]
        joint = mates = None
        if self.batch and len(tiles) > 1:
            self._unify_source(tiles)
            if tiles[0].manual_wp is None:      # a shared pick needs no estimate
                joint = self._joint_basis(tiles)
            # who it was computed from, recorded once and kept: the white point on
            # this image came from that set, whatever those images do afterwards
            mates = [(t.serial, self.tiles.index(t) + 1) for t in tiles]
        for t in tiles:
            t.auto_basis, t.mates = joint, mates
            t.commit()
            t.render()

    # -- export -----------------------------------------------------------
    def save(self, tiles: list[Tile]):
        tiles = [t for t in tiles if t.loaded]
        if not tiles:
            return
        if len(tiles) == 1:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save image", f"{_safe(tiles[0].name)}_wb.png",
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
            name = f"{i + 1:02d}_{_safe(t.name)}_wb.png"   # index so same-named tiles don't collide
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
            p = d / f"{i + 1:02d}_{_safe(t.name)}_wb.png"   # index prevents same-name collisions
            Image.fromarray(t.balanced(t.full)).save(p)
            urls.append(QUrl.fromLocalFile(str(p)))
        md = QMimeData()
        md.setUrls(urls)
        QApplication.clipboard().setMimeData(md)
        self.status.setText(f"Copied {len(tiles)} images (paste as pictures in PowerPoint).")
