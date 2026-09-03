"""Headless smoke test for the gallery UI. QT_QPA_PLATFORM=offscreen."""
import os, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import (QDragEnterEvent, QDropEvent, QImage, QKeyEvent,
                          QWheelEvent)
from PySide6.QtWidgets import QApplication, QPushButton, QSlider

from quickwb.app import MIN_TILE_W, MainWindow, Tile, _ranges

app = QApplication([])
w = MainWindow()
assert len(w.tiles) == 1 and not w.tiles[0].loaded, "starts with one empty tile"

# two synthetic cast images on disk
paths = []
for i, cast in enumerate([(110, 128, 160), (150, 130, 110)]):
    img = np.full((160, 240, 3), cast, np.uint8)
    img[10:40, 10:40] = (205, 205, 205)
    p = os.path.join(tempfile.gettempdir(), f"wb_t{i}.png")
    Image.fromarray(img).save(p)
    paths.append(p)

# batch open both
w.add_images(paths, into=w.tiles[0])
loaded = [t for t in w.tiles if t.loaded]
assert len(loaded) == 2, f"expected 2 loaded, got {len(loaded)}"
assert all(t.loaded for t in w.tiles), "no spare empty slot: the add bar makes those now"
assert not w.single, "a batch of images should land in Grid"

# a freshly loaded image is the original: nothing is corrected until you ask
assert not any(t.computed for t in loaded), "loading must not white-balance anything"
assert "not computed" in w.lab_wp.text(), f"bad idle readout: {w.lab_wp.text()!r}"
assert not w._rows["neutralize"].isEnabled(), "De-cast belongs to Auto, which has not run"
assert not w.sec_adjust.isEnabled(), "nothing to fine-tune before a pass"

# select all -> Auto is the action that runs one joint pass
w.select_all()
assert len(w.selection) == 2 and w.active is loaded[-1]
w.btn_auto.click()
assert all(t.computed for t in loaded), "Auto did not balance the selection"
assert w._rows["neutralize"].isEnabled() and w.sec_adjust.isEnabled(),     "Auto should unlock De-cast and Adjust"
for t in loaded:
    assert t.view.pixmap() is not None and not t.view.pixmap().isNull(), "tile has no pixmap"

# slider applies to whole selection
w._spins["temperature"].setValue(25)
assert all(t.params["temperature"] == 25 for t in loaded), "slider not applied to all selected"

# eyedropper on active tile's neutral square
w.select(w.active, Qt.NoModifier)
w.on_pick(w.active, 25, 25, 25, 25)
assert w.active.manual_wp is not None, "pick failed"
assert not w.btn_pick.isChecked(), "Pick should pop back out once the point lands"
assert not w._rows["neutralize"].isEnabled(), "De-cast is ignored once a patch is picked"

# a dragged rectangle averages the region instead of a single patch
w.on_pick(w.active, 10, 10, 39, 39)
assert "an area" in w.status.text(), f"ROI drag not reported: {w.status.text()!r}"
assert np.allclose(w.active.manual_wp, 205, atol=2),     f"ROI should read the neutral square: {w.active.manual_wp}"

# paste bitmap -> new tile
img = np.full((100, 120, 3), (120, 120, 160), np.uint8)
qimg = QImage(bytes(np.ascontiguousarray(img).data), 120, 100, 3 * 120, QImage.Format_RGB888)
QApplication.clipboard().setImage(qimg)
n_before = sum(t.loaded for t in w.tiles)
w.paste_into(w._first_empty())
assert sum(t.loaded for t in w.tiles) == n_before + 1, "paste did not add an image"

# single-image copy (bitmap on clipboard)
w.copy([loaded[0]])
assert not QApplication.clipboard().image().isNull(), "single copy put no image"

# multi-image copy (files on clipboard) -> must be DISTINCT files, not one repeated
w.copy(loaded)
urls = QApplication.clipboard().mimeData().urls()
files = [u.toLocalFile() for u in urls]
assert len(files) == 2, f"multi copy expected 2 file urls, got {len(files)}"
assert all(os.path.exists(f) for f in files), "copied temp files missing"
assert len(set(files)) == 2, "multi copy wrote the same path twice (collision bug)"
# give the two tiles the same name and re-copy: still two distinct files
loaded[0].name = loaded[1].name = "clipboard"
w.copy(loaded)
files2 = [u.toLocalFile() for u in QApplication.clipboard().mimeData().urls()]
assert len(set(files2)) == 2, "same-named tiles collided on copy"

# save all to a folder
out = tempfile.mkdtemp(prefix="wb_out_")
from unittest.mock import patch
with patch("quickwb.app.QFileDialog.getExistingDirectory", return_value=out):
    w.save(loaded)
assert len(os.listdir(out)) == 2, "save-all did not write 2 files"

# shift-range select across all loaded tiles
loaded = [t for t in w.tiles if t.loaded]
w.select(loaded[0], Qt.NoModifier)
w.select(loaded[-1], Qt.ShiftModifier)
assert set(w.selection) == set(loaded), "shift-range did not select the whole range"

def _wheel(view, dy):
    at = QPointF(view.width() / 2, view.height() / 2)
    view.wheelEvent(QWheelEvent(at, view.mapToGlobal(at.toPoint()), QPoint(), QPoint(0, dy),
                                Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False))


# double-click zoom: image shown, fitted, and nothing painted over it
w.zoom(w.active)
z = w._zoom
assert z is not None and not z.item.pixmap().isNull(), "zoom shows no image"
assert not z._zoomed, "should start at fit (not zoomed)"
assert not any(c.isVisible() for c in z.canvas.children() if isinstance(c, QPushButton)),     "the full-screen view should have no buttons on top of the image"
_wheel(z.canvas, 120)
assert z._zoomed, "the wheel should zoom"
z._fit()                                   # what re-opening the view does
assert not z._zoomed, "re-opening should come back to a plain fit"
z.close()

# Ctrl+V replaces the active loaded image (no new tile)
img2 = np.full((90, 90, 3), (100, 140, 120), np.uint8)
q2 = QImage(bytes(np.ascontiguousarray(img2).data), 90, 90, 3 * 90, QImage.Format_RGB888)
QApplication.clipboard().setImage(q2)
target = w.active
n = sum(t.loaded for t in w.tiles)
w.paste_into(target)
assert sum(t.loaded for t in w.tiles) == n, "replace changed image count"
assert target.name.startswith("clipboard #") and target.full.shape == (90, 90, 3),     "replace failed"

# delete selected -> leaves an empty drop area
w.select(target, Qt.NoModifier)
before = sum(t.loaded for t in w.tiles)
w.delete_selected()
assert sum(t.loaded for t in w.tiles) == before - 1, "delete did not remove the image"
assert w.tiles, "delete emptied the gallery"

# spin box is two-way synced with its slider
w.select([t for t in w.tiles if t.loaded][0], Qt.NoModifier)
w._spins["contrast"].setValue(40)
assert w.active.params["contrast"] == 40, "slider value not applied"

# an EMPTY slot can be selected and deleted (the add bar makes them, Delete removes them)
n_tiles = len(w.tiles)
empty = w.add_empty()
assert len(w.tiles) == n_tiles + 1, "add bar did not append a slot"
w.select(empty, Qt.NoModifier)
w.delete_selected()
assert len(w.tiles) == n_tiles, "empty slot could not be deleted"

# Delete always works: the last image clears back to an empty drop zone
while len(w.tiles) > 1:
    w.select(w.tiles[-1], Qt.NoModifier)
    w.delete_selected()
assert w.tiles[0].loaded, "one loaded tile left to test the last-slot delete"
w.select(w.tiles[0], Qt.NoModifier)
w.delete_selected()
assert len(w.tiles) == 1, "there must always be one slot to drop onto"
assert not w.tiles[0].loaded, "deleting the last image must clear it, not refuse"

# a lone tile fills the window -- loaded too, not just the empty drop zone
w.add_images(paths[:1], into=w.tiles[0])
assert len(w.tiles) == 1 and w.tiles[0].loaded
w._reflow()
assert w.tiles[0].maximumWidth() == 16777215, "a lone loaded tile shrank to grid size"

# one slot left: Grid means nothing, so it is off and Single is forced back on
assert w.single, "a lone slot must fall back to Single"
assert not w.view_btns[1].isEnabled(), "Grid must be disabled with a single slot"
w.add_images(paths[1:2])
assert len(w.tiles) == 2, "second image should add a slot"
assert w.view_btns[1].isEnabled(), "Grid should switch on once there are two slots"
assert not w.single, "a second image on screen should switch to Grid"
w.view_btns[0].setChecked(True)          # back to Single for the nav-bar checks
assert w.active.maximumWidth() == 16777215, "Single view did not enlarge"
assert sum(t.isVisibleTo(w) for t in w.tiles) == 1, "Single view shows more than one tile"

# the panel says what the ringed image is
assert "240 × 160 px" in w.lab_info.text(), f"bad info: {w.lab_info.text()!r}"

# the nav bar counts the slots and greys out at the ends (no wrap-around)
assert w.nav.isVisibleTo(w), "nav bar should show in Single view"
assert w.lab_pos.text().startswith("2 / 2"), f"bad counter: {w.lab_pos.text()!r}"
assert not w.btn_next.isEnabled(), "Next should be dead on the last slot"
first = w.active
w._step(-1)
assert w.active is not first, "left arrow did not move"
assert w.lab_pos.text().startswith("1 / 2") and not w.btn_prev.isEnabled()
assert paths[0].endswith(w.lab_pos.text().split()[-1] + ".png"), "counter lost the file name"
w._step(-1)
assert w.lab_pos.text().startswith("1 / 2"), "stepping past the first slot wrapped around"

# the + bar adds a slot, lands on it, and shows the gallery it just grew
n = len(w.tiles)
extra = w.add_empty()
assert len(w.tiles) == n + 1 and w.active is extra, "+ should select the new slot"
assert not w.single, "+ should switch to Grid, where the new slot is visible"
w.view_btns[0].setChecked(True)
assert "empty slot" in w.lab_pos.text(), f"counter ignores empty slots: {w.lab_pos.text()!r}"

# the nav bar names the file, so the in-frame caption steps aside
assert not w.tiles[0].head.isVisibleTo(w.tiles[0]), "file name shown twice in Single view"

# Grid shows every tile again
w.view_btns[1].setChecked(True)
assert not w.single and all(t.isVisibleTo(w) for t in w.tiles), "Grid did not restore tiles"
assert not w.nav.isVisibleTo(w), "nav bar belongs to Single view only"
assert w.tiles[0].head.isVisibleTo(w.tiles[0]), "Grid tiles need their caption back"

# clicking the background is the quick way to deselect
w.select_all()
here = w.active
w.grid_host.mousePressEvent(None)
assert not w.selection, "background click should deselect"
assert w.active is here, "deselecting must not move the slot you are on"

# ... and Single still shows that slot, with nothing selected
w.view_btns[0].setChecked(True)
assert sum(x.isVisibleTo(w) for x in w.tiles) == 1,     "Single view with an empty selection showed the whole gallery"
w.view_btns[1].setChecked(True)

# Shift extends from the anchor, so re-clicking further out grows the range
w.add_empty()          # a fourth slot to drag a range across
idx = lambda: sorted(w.tiles.index(x) for x in w.selection)
w.select(w.tiles[0], Qt.NoModifier)
w.select(w.tiles[2], Qt.ShiftModifier)
assert idx() == [0, 1, 2], f"shift range wrong: {idx()}"
w.select(w.tiles[3], Qt.ShiftModifier)
assert idx() == [0, 1, 2, 3], f"shift moved the anchor instead of extending: {idx()}"
w.select(w.tiles[1], Qt.ShiftModifier)
assert idx() == [0, 1], f"shift should shrink back towards the anchor: {idx()}"

# a loaded tile must not keep the empty slot's Open/Paste buttons (resize bug)
from PySide6.QtWidgets import QPushButton
labels = {b.text() for b in w.tiles[0].findChildren(QPushButton)}
assert labels == {"Save", "Copy"}, f"stale buttons on a loaded tile: {labels}"

# Pick leaves the view alone, and stays pushed in until the pick lands
w.select(w.tiles[0], Qt.NoModifier)
w.btn_pick.click()
assert not w.single, "Pick must not switch the view"
assert w.btn_pick.isChecked(), "Pick should stay down while the eyedropper is armed"
assert "grey or white" in w.status.text(), f"unexpected pick hint: {w.status.text()!r}"
w.on_pick(w.active, 20, 20, 20, 20)
assert not w.btn_pick.isChecked(), "Pick should pop out after the pick"

# the real mouse path, not just on_pick(): press and release on the tile itself
from PySide6.QtGui import QMouseEvent
from PySide6.QtCore import QPointF, QEvent
def _mouse(view, kind, pt):
    at = QPointF(*pt)
    view.event(QMouseEvent(kind, at, view.mapToGlobal(at.toPoint()),
                           Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
w.show(); app.processEvents()
target = [t for t in w.tiles if t.loaded][0]
w.select(target, Qt.NoModifier)
target.view.set_image(target.view._pixmap)      # make sure a pixmap is laid out
app.processEvents()
r = target.view._drawn()
assert r, "the tile drew no image, cannot aim at it"
ox, oy, dw, dh = r
before = None if target.manual_wp is None else target.manual_wp.copy()
w.btn_pick.click()
_mouse(target.view, QEvent.MouseButtonPress, (ox + dw * 0.5, oy + dh * 0.5))
_mouse(target.view, QEvent.MouseButtonRelease, (ox + dw * 0.5, oy + dh * 0.5))
assert target.manual_wp is not None and (before is None or
        not np.allclose(before, target.manual_wp)),     "clicking the tile with the eyedropper armed did nothing"
assert not w.btn_pick.isChecked(), "a real click must release the Pick button"
# and the same through a drag
w.btn_pick.click()
_mouse(target.view, QEvent.MouseButtonPress, (ox + dw * 0.2, oy + dh * 0.2))
_mouse(target.view, QEvent.MouseMove, (ox + dw * 0.8, oy + dh * 0.8))
assert target.view._band.isVisible(), "the ROI rubber band never showed"
_mouse(target.view, QEvent.MouseButtonRelease, (ox + dw * 0.8, oy + dh * 0.8))
assert "an area" in w.status.text(), f"a real drag did not read as an area: {w.status.text()!r}"

# Show original is per image, not a global switch
a, b = [t for t in w.tiles if t.loaded][:2]
w.select_all()
w.btn_auto.click()          # uncomputed images have nothing to compare against
w.select(a, Qt.NoModifier)
w.sw_orig.click()
assert a.show_orig and not b.show_orig, "compare leaked onto the unselected image"
w.select(b, Qt.NoModifier)
assert not w.sw_orig.isChecked(), "compare button did not follow the active tile"
w.select(a, Qt.NoModifier)
assert w.sw_orig.isChecked(), "compare button lost the active tile's state"
w.sw_orig.click()

# a freshly loaded image is shown untouched until Auto or a pick asks otherwise
w.add_images(paths[:1], into=w.add_empty())
fresh_tile = w.active
assert not fresh_tile.computed, "loading must not mark an image computed"
assert np.array_equal(fresh_tile.output(full=False), fresh_tile.preview), \
    "a new image must display its original pixels"
assert np.array_equal(fresh_tile.balanced(fresh_tile.full), fresh_tile.full), \
    "and export what is on screen, not a correction nobody asked for"
assert w.sw_orig.isChecked() and not w.sw_orig.isEnabled(), \
    "with nothing computed the compare button has nowhere to go but original"
assert "not computed" in w.lab_wp.text(), f"stale readout: {w.lab_wp.text()!r}"
w.btn_auto.click()
assert fresh_tile.computed, "Auto did not correct the new image"
assert w.sw_orig.isEnabled(), "the compare toggle did not unlock after the first pass"
assert "auto" in w.lab_wp.text(), \
    f"white point readout missing after computing: {w.lab_wp.text()!r}"

# Ctrl+Z puts back what the last paste/open/delete changed
before = [(t.loaded, t.name) for t in w.tiles]
w.select(w.tiles[0], Qt.NoModifier)
w.delete_selected()
assert [(t.loaded, t.name) for t in w.tiles] != before, "delete changed nothing to undo"
w.undo()
assert [(t.loaded, t.name) for t in w.tiles] == before, "undo did not restore the gallery"
w.redo()
assert [(t.loaded, t.name) for t in w.tiles] != before, "redo did not replay the delete"
w.undo()
assert [(t.loaded, t.name) for t in w.tiles] == before, "second undo lost the gallery"
w.add_empty()
assert not w._redo, "a new action must fork the history"

# the status line tracks the selection instead of going stale
w.select_all()
w.btn_auto.click()
w.select_all()              # the action line gives way to the idle one
n_sel = sum(t.loaded for t in w.selection)
assert f"{n_sel} images selected" in w.status.text(), f"stale status: {w.status.text()!r}"
assert "shared" in w.status.text(), f"a plural selection is one shared pass: {w.status.text()!r}"
w.clear_selection()
assert "Nothing selected" in w.status.text(), f"stale status: {w.status.text()!r}"

# a picked white point survives a re-sync; De-cast puts the image back on Auto
w.select_all()
t0 = w._lead()
assert t0 is not None and t0.loaded, "the panel should report on a loaded image"
w.select(t0, Qt.NoModifier)
w.on_pick(t0, 5, 5, 5, 5)
assert t0.manual_wp is not None and t0.applied["how"] == "picked", "pick did not latch"
w._sync_panel()
assert t0.manual_wp is not None, "the pick was dropped on re-sync"
w._spins["neutralize"].setValue(70)
assert t0.manual_wp is None, "moving De-cast must put the image back on Auto"
assert t0.applied["how"] == "auto" and t0.applied["neutralize"] == 70, \
    "De-cast must re-run Auto straight away"

# 'Show original' is preview-only: exports stay corrected
w.select(t0, Qt.NoModifier)
w.sw_orig.click()
orig_preview = t0.output(full=False)
assert np.array_equal(orig_preview, t0.preview), "compare should show the untouched preview"
assert not np.array_equal(t0.balanced(t0.full), t0.full), "export must still be corrected"
w.sw_orig.click()

# clipboard pastes are numbered: "clipboard" on every tile told them apart from nothing
w.selection = list(w.tiles)
w.delete_selected()
QApplication.clipboard().setImage(qimg)
w.paste_into(w.tiles[0])
w.paste_into(w.add_empty())
names = [t.name for t in w.tiles if t.loaded]
assert names == ["clipboard #1", "clipboard #2"], f"clipboard names not numbered: {names}"
assert not w.single, "a second image should switch to Grid"

# the white point reported is the one that was applied, and De-cast re-runs Auto
w.selection = list(w.tiles)
w.delete_selected()
w.add_images(paths, into=w.tiles[0])      # two different casts
w.select_all()
w.btn_auto.click()
shown = w.lab_wp.text()
assert "shared auto" in shown, f"joint pass should report a shared white point: {shown!r}"
w._spins["neutralize"].setValue(100)
assert w.lab_wp.text() != shown, "De-cast did not re-run the estimate"
# balanced one at a time the two casts land on different white points
for t in [x for x in w.tiles if x.loaded]:
    w.select(t, Qt.NoModifier)
    w.btn_auto.click()
w.select_all()
assert w.lab_wp.text() == "varies", \
    f"a disagreeing selection says only 'varies': {w.lab_wp.text()!r}"
assert not w.wp_swatch.isVisibleTo(w), "no single colour to swatch when it varies"
assert "2 images selected" in w.lab_info.text(), f"bad info: {w.lab_info.text()!r}"

# a pick applies at once -- there is no separate apply step any more
w.select(w.tiles[0], Qt.NoModifier)
w.on_pick(w.active, 25, 25, 25, 25)
assert w.active.applied["how"] == "picked", "the pick was not what got applied"

# a mixed selection puts the compare switch in the middle, and a click resolves it
w.select_all()
w.btn_auto.click()
w.select(w.tiles[0], Qt.NoModifier)
w.sw_orig.click()                         # one tile on, the other off
w.select_all()
assert w.sw_orig.checkState() == Qt.PartiallyChecked, "mixed selection: knob goes middle"
w.sw_orig.click()
assert w.sw_orig.checkState() == Qt.Checked, "clicking the middle must pick a side"
assert all(t.show_orig for t in w.tiles if t.loaded), "and apply it to the whole selection"
w.sw_orig.click()
assert w.sw_orig.checkState() == Qt.Unchecked and not any(t.show_orig for t in w.tiles)

# a two-line status must not squash the controls above it
wp_btns = [w.btn_auto, w.btn_pick]
h = [b.height() for b in wp_btns]
w.status.setText("a status line long enough to wrap onto two rows in a 276 pixel panel")
app.processEvents()
assert [b.height() for b in wp_btns] == h, "the status stole height from White point"

# the arrows walk from the slot on screen; Select all / Deselect never move it
while len(w.tiles) < 3:
    w.add_empty()
w.view_btns[0].setChecked(True)                  # Single
w.select(w.tiles[1], Qt.NoModifier)
w.select_all()
assert w.active is w.tiles[1], "Select all moved the slot you are on"
w._step(1)
assert w.active is w.tiles[2], "forward arrow did nothing after Select all"
w.clear_selection()
assert w.active is w.tiles[2], "Deselect moved the slot you are on"
w._step(-1)
assert w.active is w.tiles[1], "back arrow did nothing with an empty selection"

# the scope is the selection, and the readout follows it -- there is nothing to click
w.view_btns[1].setChecked(True)
pair = [t for t in w.tiles if t.loaded][:2]
w.select(pair[0], Qt.NoModifier)
assert not w.batch, "one image is balanced on its own"
assert w.mode_btns[0].isChecked(), "the scope readout must say Separately"
w.select(pair[1], Qt.ControlModifier)
assert w.batch, "several images share one white point"
assert w.mode_btns[1].isChecked(), "the scope readout must say Together"
assert all(not b.isEnabled() or b.testAttribute(Qt.WA_TransparentForMouseEvents)
           for b in w.mode_btns), "the scope must not be clickable"

# 'Together' means one source for the selection, not one picked and one auto
w.view_btns[1].setChecked(True)
imgs = [t for t in w.tiles if t.loaded][:2]
w.select(imgs[0], Qt.NoModifier)
w.select(imgs[1], Qt.ControlModifier)
w.btn_auto.click()                               # both back to the estimate
assert all(t.manual_wp is None for t in imgs), "Auto must clear an earlier pick"
w.select(imgs[1], Qt.NoModifier)                 # one image: Separately
w.on_pick(imgs[1], 10, 10, 10, 10)
assert imgs[0].manual_wp is None, "Separately: a pick must stay on its own image"
w.select(imgs[0], Qt.NoModifier)                 # two images: Together
w.select(imgs[1], Qt.ControlModifier)
w.btn_auto.click()
assert all(t.manual_wp is None for t in imgs), "Auto over a selection clears every pick"
w.on_pick(imgs[0], 10, 10, 10, 10)
assert imgs[1].manual_wp is not None, "All together: the pick must cover the selection"
assert {t.applied["how"] for t in imgs} == {"picked"}, "one shared pass, two sources"

# ... and the info line says so, naming the slots that share it
assert _ranges([1, 3, 4, 5]) == "1, 3-5" and _ranges([2]) == "2"
mates = _ranges([i for i, x in enumerate(w.tiles, 1) if x in imgs])
want = "shared picked (" + ("all" if sum(x.loaded for x in w.tiles) == 2 else mates) + ")"
assert want in w.lab_wp.text(), f"{w.lab_wp.text()!r} should say {want!r}"
# loading one more image outside the group turns "all" into the slot numbers
spare = next(x for x in w.tiles if not x.loaded)
spare.set_source(np.full((80, 120, 3), (120, 130, 140), np.uint8), "third")
w.select(imgs[0], Qt.NoModifier)
w.select(imgs[1], Qt.ControlModifier)
assert f"shared picked ({mates})" in w.lab_wp.text(), w.lab_wp.text()

# an older grouped pick must not survive a new one, wherever you aim it
w.select_all()
w.btn_pick.click()
assert all(x.view._pick for x in w.tiles if x.loaded), "the eyedropper arms the selection"
other = next(x for x in w.tiles if x.loaded and x is not w.active)
w.on_pick(other, 3, 3, 3, 3)              # aim at an image that is not the ringed one
assert len({tuple(np.round(x.manual_wp)) for x in w.tiles if x.loaded}) == 1,     "a pick in Together mode must override every older pick in the selection"
assert "shared picked (all)" in w.lab_wp.text(), w.lab_wp.text()

# the box takes fractions the slider cannot land on, and must not be snapped back
w.select(imgs[0], Qt.NoModifier)
w._spins["temperature"].setValue(-2.5)
assert imgs[0].params["temperature"] == -2.5, "fractional adjustment lost"
assert w._spins["temperature"].text() == "-2.5", f"odd display: {w._spins['temperature'].text()!r}"
assert w._rows["temperature"].findChild(QSlider).value() == -2, "slider should take the whole number"
assert w._spins["temperature"].value() == -2.5, "the slider snapped the typed value back"

# grid tiles are big enough to judge an image, and the panel fits at opening size
w.resize(*w._default_size())
w.view_btns[1].setChecked(True)
app.processEvents()
w._reflow()
t = [x for x in w.tiles if x.loaded][0]
assert t.width() >= MIN_TILE_W and t.view.width() >= 300 and t.view.height() >= 190,     f"grid tile too small: {t.width()}x{t.height()}, image {t.view.width()}x{t.view.height()}"
w.select_all()
w.btn_auto.click()
w.status.setText("a status line long enough to wrap onto two rows inside the panel")
app.processEvents()
assert not w._wrap.verticalScrollBar().isVisible(),     "the panel should not need its scrollbar at the opening size (unless the screen is tiny)"

# pressing Auto again must reach every selected image, not just the first
w.select_all()
w._spins["neutralize"].setValue(90)
w.btn_auto.click()
lo = [t.applied["wp"].copy() for t in w.tiles if t.loaded]
w._spins["neutralize"].setValue(0)
w.btn_auto.click()
hi = [t.applied["wp"].copy() for t in w.tiles if t.loaded]
assert len(lo) > 1 and all(not np.allclose(a, b) for a, b in zip(lo, hi)),     "a repeated Auto left some selected images on the old white point"
assert all(t.applied["neutralize"] == 0 for t in w.tiles if t.loaded)

# Ctrl+Z covers the corrections too, and leaves the selection where it was
w.view_btns[1].setChecked(True)
pair = [t for t in w.tiles if t.loaded][:2]
w.select(pair[0], Qt.NoModifier)
w.btn_auto.click()
w._spins["contrast"].setValue(0)
w._end_edit()
w._spins["contrast"].setValue(33)
w._spins["contrast"].setValue(44)                 # one drag -> one undo step
assert w.active.params["contrast"] == 44
w.undo()
assert w.active.params["contrast"] == 0, \
    f"undo should drop the whole drag, got {w.active.params['contrast']}"
assert w.status.text() == "Undone: Contrast.", \
    f"undo should name what it took back: {w.status.text()!r}"
assert w.selection == [w.active], "undoing an edit must not move the selection"
w.redo()
assert w.active.params["contrast"] == 44, "redo did not replay the edit"
assert w.status.text() == "Redone: Contrast.", f"bad redo message: {w.status.text()!r}"
w.undo()
before_wp = w.active.applied["wp"].copy()
w.on_pick(w.active, 20, 20, 20, 20)
assert w.active.applied["how"] == "picked"
w.undo()
assert w.active.applied["how"] == "auto" and np.allclose(w.active.applied["wp"], before_wp), \
    "undo did not put back the white point the pick replaced"

# the grid arrows walk both ways: left/right by one, up/down by a whole row
w.view_btns[1].setChecked(True)
while len(w.tiles) < 4:
    w.add_empty()
w._reflow()
w._cols = 2                       # pin the layout so the check does not depend on width
w.select(w.tiles[0], Qt.NoModifier)
w._step_row(1)
assert w.active is w.tiles[2], "Down should move a whole row"
w._step_row(-1)
assert w.active is w.tiles[0], "Up should move back a row"
w._step_row(-1)
assert w.active is w.tiles[0], "Up on the top row must not wrap"
w.view_btns[0].setChecked(True)   # Single: one row, so up/down have nowhere to go
w.select(w.tiles[1], Qt.NoModifier)
w._step_row(1)
assert w.active is w.tiles[1], "Single view has no rows to step through"

# the full-screen view steps through the loaded images with left/right
imgs = [t for t in w.tiles if t.loaded]
assert len(imgs) >= 2, "need two images to step between"
w.select(imgs[0], Qt.NoModifier)
w.zoom(imgs[0])
app.processEvents()
assert w._zoom.isFullScreen(), "the zoom view should be full screen, not a big window"
def _key(k):        # to whatever has focus, so a child stealing the key shows up
    z = w._zoom
    app.sendEvent(z.focusWidget() or z, QKeyEvent(QEvent.KeyPress, k, Qt.NoModifier))
assert not w._zoom.canvas.focusPolicy(), "the canvas must not swallow the arrow keys"
assert w._zoom.flash.isVisibleTo(w._zoom), "no caption on opening the full-screen view"
assert imgs[0].name in w._zoom.flash.text() and "1 / " in w._zoom.flash.text(), \
    f"caption does not name the image: {w._zoom.flash.text()!r}"
w._zoom.flash.hide()
_key(Qt.Key_Right)
assert w.active is imgs[1], "right arrow did not move the full-screen view"
assert w._zoom.flash.isVisibleTo(w._zoom) and imgs[1].name in w._zoom.flash.text(), \
    f"the arrows did not re-caption: {w._zoom.flash.text()!r}"
_key(Qt.Key_Left)
assert w.active is imgs[0], "left arrow did not move it back"
_key(Qt.Key_Left)
assert w.active is imgs[0], "stepping past the first image wrapped around"
w._zoom.close()

# a shared white point keeps naming the images it was computed FROM, even after
# one of them is re-balanced on its own -- and follows them when slots move
w.selection = list(w.tiles)
w.delete_selected()
w.add_images(paths + paths[:1], into=w.tiles[0])     # three images, slots 1-3
trio = [t for t in w.tiles if t.loaded]
assert len(trio) == 3
w.select(trio[1], Qt.NoModifier)
w.select(trio[2], Qt.ControlModifier)
w.on_pick(trio[1], 20, 20, 39, 39)                   # 2 and 3 share one picked wp
w.select(trio[2], Qt.NoModifier)
assert "shared picked (2-3)" in w.lab_wp.text(), w.lab_wp.text()
w.select(trio[0], Qt.NoModifier)                     # now balance 1 and 2 together
w.select(trio[1], Qt.ControlModifier)
w.btn_auto.click()
assert "shared auto (1-2)" in w.lab_wp.text(), w.lab_wp.text()
w.select(trio[2], Qt.NoModifier)
assert "shared picked (2-3)" in w.lab_wp.text(), \
    f"image 3 forgot where its white point came from: {w.lab_wp.text()!r}"
# replacing image 2 does not erase the history, it marks the slot it came from
w.select(trio[1], Qt.NoModifier)
w.delete_selected()
w.select([t for t in w.tiles if t.loaded][-1], Qt.NoModifier)
assert "shared picked (ex2, 2)" in w.lab_wp.text(), \
    f"a lost mate should still be named: {w.lab_wp.text()!r}"

# a drop that misses every tile still lands: it opens a slot for the image
assert all(t.loaded for t in w.tiles), "no free slot, so a drop has to make one"
before = len(w.tiles)
md = QMimeData()
md.setUrls([QUrl.fromLocalFile(paths[0])])
where, act, btn = QPointF(2, 2), Qt.CopyAction, Qt.LeftButton
enter = QDragEnterEvent(where.toPoint(), act, md, btn, Qt.NoModifier)
app.sendEvent(w.grid_host, enter)
assert enter.isAccepted(), "the gallery background refused an image drag"
app.sendEvent(w.grid_host, QDropEvent(where, act, md, btn, Qt.NoModifier, QDropEvent.Drop))
assert len(w.tiles) == before + 1 and w.tiles[-1].loaded,     "a drop between the tiles should open a slot and load the image there"

# a resize only re-lays the tiles out: re-rendering them from the source arrays
# is what made dragging the window edge crawl
seen = []
real, Tile.render = Tile.render, lambda self: seen.append(self)
w._reflow()
Tile.render = real
assert not seen, f"_reflow re-rendered {len(seen)} tile(s); the views rescale themselves"

# housekeeping must not cost an undo step of its own
top = w._undo[-1]                                    # the history is capped and the
w.add_images([])                                     # last step was an open too, so
assert w._undo[-1] is top, "an empty open should not touch the history"
w.select_all()
w.delete_selected()                                  # the last image goes
assert not any(t.loaded for t in w.tiles), "delete left something behind"
w.undo()
assert any(t.loaded for t in w.tiles),     "one Ctrl+Z should bring the images back, not just the slot that replaced them"

print("gallery smoke OK")
# ponytail: Qt segfaults tearing a live QApplication down at interpreter exit,
# which turns a passing run into rc=139. Nothing left to clean up, so just go.
import sys; sys.stdout.flush(); os._exit(0)
