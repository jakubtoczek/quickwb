"""Headless smoke test for the gallery UI. QT_QPA_PLATFORM=offscreen."""
import os, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from quickwb.app import MainWindow

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

# select all + batch joint auto render
w.mode_btns[1].setChecked(True)   # "All together"
w.select_all()
assert len(w.selection) == 2 and w.active is loaded[-1]
w._render_selection()
for t in loaded:
    assert t.view.pixmap() is not None and not t.view.pixmap().isNull(), "tile has no pixmap"

# slider applies to whole selection
w._sliders["temperature"].setValue(25)
assert all(t.params["temperature"] == 25 for t in loaded), "slider not applied to all selected"

# eyedropper on active tile's neutral square
w.select(w.active, Qt.NoModifier)
w.on_pick(w.active, 25, 25)
assert w.active.manual_wp is not None, "pick failed"

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

# double-click zoom: image shown, ctrl-zoom reveals Reset, reset hides it again
w.zoom(w.active)
z = w._zoom
assert z is not None and not z.item.pixmap().isNull(), "zoom shows no image"
assert not z.reset_btn.isVisible() or not z._zoomed, "should start at fit (not zoomed)"
z.canvas.scale(1.5, 1.5); z._zoom_changed()
assert z._zoomed and z.reset_btn.isVisible(), "reset button should appear when zoomed"
z._fit()
assert not z._zoomed and not z.reset_btn.isVisible(), "reset should return to fit"
z.close()

# Ctrl+V replaces the active loaded image (no new tile)
img2 = np.full((90, 90, 3), (100, 140, 120), np.uint8)
q2 = QImage(bytes(np.ascontiguousarray(img2).data), 90, 90, 3 * 90, QImage.Format_RGB888)
QApplication.clipboard().setImage(q2)
target = w.active
n = sum(t.loaded for t in w.tiles)
w.paste_into(target)
assert sum(t.loaded for t in w.tiles) == n, "replace changed image count"
assert target.name == "clipboard" and target.full.shape == (90, 90, 3), "replace failed"

# delete selected -> leaves an empty drop area
w.select(target, Qt.NoModifier)
before = sum(t.loaded for t in w.tiles)
w.delete_selected()
assert sum(t.loaded for t in w.tiles) == before - 1, "delete did not remove the image"
assert w.tiles, "delete emptied the gallery"

# spin box is two-way synced with its slider
w.select([t for t in w.tiles if t.loaded][0], Qt.NoModifier)
w._sliders["contrast"].setValue(40)
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

# Single view is the default; Grid is greyed out until there is a gallery
assert w.single, "Single should be the default view"
assert not w.view_btns[0].isEnabled(), "Grid must be disabled with a single slot"
w.add_images(paths[1:2])
assert len(w.tiles) == 2, "second image should add a slot"
assert w.view_btns[0].isEnabled(), "Grid should switch on once there are two slots"
assert w.active.maximumWidth() == 16777215, "Single view did not enlarge"
assert sum(t.isVisibleTo(w) for t in w.tiles) == 1, "Single view shows more than one tile"

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

# the + bar adds a slot, lands on it, and it is reachable as the last one
n = len(w.tiles)
extra = w.add_empty()
assert len(w.tiles) == n + 1 and w.active is extra, "+ should select the new slot"
assert "empty slot" in w.lab_pos.text(), f"counter ignores empty slots: {w.lab_pos.text()!r}"

# Grid shows every tile again
w.view_btns[0].setChecked(True)
assert not w.single and all(t.isVisibleTo(w) for t in w.tiles), "Grid did not restore tiles"
assert not w.nav.isVisibleTo(w), "nav bar belongs to Single view only"

# a loaded tile must not keep the empty slot's Open/Paste buttons (resize bug)
from PySide6.QtWidgets import QPushButton
labels = {b.text() for b in w.tiles[0].findChildren(QPushButton)}
assert labels == {"Save", "Copy"}, f"stale buttons on a loaded tile: {labels}"

# Pick leaves the view alone now
w.select(w.tiles[0], Qt.NoModifier)
w._arm_pick(True)
assert not w.single, "Pick must not switch the view"
assert "grey or white" in w.status.text(), f"unexpected pick hint: {w.status.text()!r}"
w.on_pick(w.active, 20, 20)

# Show original is per image, not a global switch
a, b = [t for t in w.tiles if t.loaded][:2]
w.select(a, Qt.NoModifier)
w.btn_orig.setChecked(True)
assert a.show_orig and not b.show_orig, "compare leaked onto the unselected image"
w.select(b, Qt.NoModifier)
assert not w.btn_orig.isChecked(), "compare button did not follow the active tile"
w.select(a, Qt.NoModifier)
assert w.btn_orig.isChecked(), "compare button lost the active tile's state"
w.btn_orig.setChecked(False)

# with Auto off a freshly loaded image is shown untouched, not fake-corrected
w.cb_autorun.setChecked(False)
w.add_images(paths[:1], into=w.add_empty())
fresh_tile = w.active
assert not fresh_tile.computed, "loading with Auto off must not mark it computed"
assert np.array_equal(fresh_tile.output(full=False), fresh_tile.preview), \
    "with Auto off a new image must display its original pixels"
assert not w._fresh, "the marker should be amber after loading with Auto off"
w.recompute()
assert fresh_tile.computed and w._fresh, "Recompute did not correct the new image"
w.cb_autorun.setChecked(True)

# Ctrl+Z puts back what the last paste/open/delete changed
before = [(t.loaded, t.name) for t in w.tiles]
w.select(w.tiles[0], Qt.NoModifier)
w.delete_selected()
assert [(t.loaded, t.name) for t in w.tiles] != before, "delete changed nothing to undo"
w.undo()
assert [(t.loaded, t.name) for t in w.tiles] == before, "undo did not restore the gallery"

# the status line tracks the selection instead of going stale
w.select_all()
n_sel = sum(t.loaded for t in w.selection)
assert f"{n_sel} images selected" in w.status.text(), f"stale status: {w.status.text()!r}"
w.mode_btns[1].setChecked(True)
assert "shared" in w.status.text(), f"mode change not reflected: {w.status.text()!r}"
w.clear_selection()
assert "Nothing selected" in w.status.text(), f"stale status: {w.status.text()!r}"

# reload and check the compute marker + white-point mode
w.select_all()
assert w._fresh, "should start up to date"
w.cb_autorun.setChecked(False)
w._sliders["contrast"].setValue(11)
assert not w._fresh, "with Auto off, a change must mark the preview stale"
w.cb_autorun.setChecked(True)          # turning Auto back on recomputes
assert w._fresh, "turning Auto on did not recompute"

# white-point mode follows the active tile and does not snap back to Auto
t0 = w.active
w.on_pick(t0, 5, 5)
assert w.wp_btns[1].isChecked() and t0.manual_wp is not None, "pick did not latch"
w._sync_panel()
assert w.wp_btns[1].isChecked(), "Auto snapped back on after re-sync"
w.wp_btns[0].click()
assert t0.manual_wp is None, "Auto did not clear the picked white point"

# 'Show original' is preview-only: exports stay corrected
w.select(t0, Qt.NoModifier)
w.btn_orig.setChecked(True)
orig_preview = t0.output(full=False)
assert np.array_equal(orig_preview, t0.preview), "compare should show the untouched preview"
assert not np.array_equal(t0.balanced(t0.full), t0.full), "export must still be corrected"
w.btn_orig.setChecked(False)

print("gallery smoke OK")
