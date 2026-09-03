# QuickWB

Open, drag or paste an image in, white-balance it, and export it or copy it to
the clipboard. A small single-window desktop app (PySide6).

![icon](src/quickwb/assets/quickwb.png)

Works on one image or on a batch, and the selection decides which: **Separately**
gives each image its own correction, **Together** computes one shared
correction for the whole selection so a set stays consistent.

## What it does

- **Drop, open, or paste** images (PNG, JPG, TIFF, BMP, WebP). Drag several
  in at once, or **paste** with **Ctrl+V** — a screenshot, a copied image, or
  copied files. A drop on a slot fills that one; a drop anywhere else in the
  gallery opens as many new slots as it needs. Opened files are named after the
  file; pasted ones become `clipboard #1`, `#2`, … so two screenshots are never
  the same label.
- **One image at a time, or a gallery.** **Single** shows the current slot at
  window size, with **◀ / ▶** (or the arrow keys) and a `3 / 7` counter
  naming the file. **Grid** lays every slot out side by side and scrolls, and
  there the arrow keys walk it the way it looks — left and right along a row,
  up and down between rows; as soon as a second image is on screen the app
  switches to it for you. Double-click any image for a full-screen view —
  scroll to zoom, drag to pan, ◀ / ▶ for the next image, Esc to close. Nothing
  is drawn on top of the picture, so it screenshots clean: stepping to another
  image, or closing and re-opening, is what brings the zoom back to a plain fit.
  There
  is no counter up there, so the same `3 / 7` line flashes over the image as
  you land on it, then fades.
- **Slots.** `+ add another slot` appends an empty drop zone and takes you
  to it; **Delete** removes any selected slot, and the last image clears back
  to a drop zone instead of leaving you stuck with it. Click to select,
  **Ctrl+click** to add, **Shift+click** for a range, **Ctrl+A** for the lot,
  and a click on the background (or **Deselect**) to select nothing. The
  slot you are on is ringed — solid when it is part of the selection, dashed
  when it is not — and the arrows walk from there; every control acts on the
  selection.
- **Ctrl+Z** undoes the last thing that changed the images: an open, a paste, a
  delete, a white balance, a slider — and says which, so you can see what came
  back. **Ctrl+Shift+Z** (or Ctrl+Y) puts it back:
  images you undo away are kept, so deleting and changing your mind costs
  nothing. Selecting, switching view and **Show original** change nothing, so
  they leave the history alone.
- **White point** holds everything that decides the neutral colour. Loading an
  image corrects nothing: you keep seeing it as it came in until you press
  **Auto** (estimate the neutral colour from the image itself) or **Pick…**
  (an eyedropper — click, or drag a rectangle over, something that *should* be
  grey or white on *any* selected image; a rectangle averages everything inside
  it). Those two are also what re-runs a balance, so there is no separate apply
  step, and everything below them stays greyed out until one has run. The
  **scope** above them is a readout, not a control: it follows what you
  selected — one image is balanced on its own, several get one shared white
  point, and a pick then covers all of them. The **De-cast** slider sets how
  hard Auto looks (brightest pixels only ↔ whole frame) and re-runs it as you
  drag. Underneath, the colour the last pass settled on: a swatch, its hex
  value, and where it came from — `auto` or `picked` on its own,
  `shared auto (1, 3-5)` or `shared picked (all)` when several images were
  balanced together, naming the images it was computed from — that is history,
  so re-balancing one of them on its own does not change what the others say.
  Slots are counted down the grid and renumbered as you move things about; an
  image that has since been deleted or replaced shows as `ex2`, the slot it came
  from. `varies` when the selection does not agree,
  and *white balance not computed yet* before the first pass.
- Fine-tune with **temperature**, **tint**, **brightness** and **contrast**;
  **Reset** puts the four of them back.
- **Show original** is an on/off switch that previews the selected images
  untouched — select one and it compares just that one, next to its corrected
  neighbours. It waits until there is a correction to compare against, and
  exports match what you see: an image you never balanced is saved as it came
  in.
- The **Image** panel names the ringed image and gives its size in pixels, or
  counts the selection when several are picked.
- **Save** (PNG/JPEG/TIFF; a folder when several) and **Copy** — **Ctrl+C**
  copies the selection: one image as a bitmap, or several as files you can
  paste into PowerPoint as separate pictures.

Every slider has an editable value box (Lightroom-style scales: de-cast
0–100, temperature / tint / brightness / contrast −100…+100) — drag for whole
numbers, or type a fraction like −2.5 the slider cannot land on.

Live edits run on a downscaled preview so sliders stay snappy; export and
clipboard copies always re-render at full resolution.

## Install (Windows)

No admin rights, no PATH or registry changes, and nothing written into your user profile.

1. On the GitHub page: **Code ▸ Download ZIP**.
2. **Right-click the ZIP ▸ Properties ▸ tick *Unblock* ▸ OK.** Windows flags everything
   that came from the internet, and without this step SmartScreen stops the launcher with
   *"Windows protected your PC"*.
3. Extract it somewhere it can stay — `C:\Users\Public\QuickWB` works for every account on the
   machine; your Documents folder is fine too.
4. Double-click **`QuickWB.bat`**. Nothing is asked: the Python runtime goes into the
   **shared** folder `C:\ProgramData\PyApps`, where every tool set up this way re-uses the
   same `uv.exe`, Python and package cache — files common to two of them are stored once
   (hard-linked), so a second tool only costs the libraries it does not already share.

   Prefer it self-contained? Run **`QuickWB.bat private`** from a terminal (or `2`) the
   *first* time, and everything lands in `C:\Users\Public\QuickWB`, a single folder that
   `uninstall.bat` deletes whole.

   Either way it downloads [`uv`](https://github.com/astral-sh/uv), a matching Python and the
   dependencies — about 260 MB on disk, a minute or two on a normal connection — and puts
   a **QuickWB** shortcut on your Desktop.
5. From now on use the Desktop shortcut. Leave the extracted folder where it is; the
   shortcut points at it.

The choice is made once: from then on the folder on disk is the memory, so the Desktop
shortcut and a plain double-click find it and start straight away — no argument needed
again. Set `QUICKWB_RUNTIME` to force some other location.

`uninstall.bat` reverses all of it: the whole folder in the private case, only QuickWB's own
virtual environment in the shared case, so anything else using that folder keeps working.

## Run it from source (any OS)

```bash
git clone https://github.com/jakubtoczek/quickwb
cd quickwb
uv run --project . quickwb
# or, with your own environment:
pip install -e .
python -m quickwb

python misc/smoke_test.py   # gallery / white-balance self-check
bash   misc/root_test.sh    # where the launcher decides to install
```

Requires Python ≥ 3.11.

## How the white balance works

Two steps, both textbook colour-constancy methods.

**1. Estimate the white point** — the colour in the frame that *should* be
neutral. QuickWB blends two classic estimators; the **De-cast** slider is the
mix:

| De-cast | Estimator | Assumption | Behaviour |
|---------|-----------|------------|-----------|
| **0** | **White-Patch** (Max-RGB, from Land's Retinex) | the brightest pixels are a white surface | subtle — highlights are often near-neutral already, so it under-corrects |
| **100** | **Gray-World** (Buchsbaum) | the whole scene averages to grey | strong — removes a heavy cast, but greys out a scene that is genuinely one colour |

The default **50** suits most photos. Both estimators use a percentile rather
than a hard max or mean, and drop near-black pixels, so one blown highlight or a
dark border can't drag the estimate. They are the two ends of one family — the
*Shades of Gray* Minkowski-norm framework, where p=∞ is White-Patch and p=1 is
Gray-World — so the slider is just moving along that axis.

**Pick…** replaces the estimate with the mean colour of what you point at — a
small patch under a click, or everything inside a rectangle you drag. The manual
version of the same thing, and the most reliable option when the shot contains a
grey card, a white wall, or a sheet of paper.

**2. Apply a von Kries diagonal transform** — multiply each channel by
`target / white_point`, so the estimated white point lands on near-white
(245, 245, 245). One gain per channel, no matrix: hues shift only as much as
removing the cast requires. This is what GIMP's white-point eyedropper does.

**Temperature** and **tint** nudge the white point (red↔blue, green↔magenta)
*before* the gain is computed, so they behave like a camera's white-balance dial
rather than a colour wash over the top. **Brightness** and **contrast** are a
plain tone curve applied afterwards.

In **Together** mode the estimators run once over the pooled pixels of the
whole selection (subsampled past 400k pixels), and every image gets that single
white point.

References:

- E. H. Land, *The Retinex Theory of Color Vision*, Scientific American 237(6), 1977 — White-Patch / Max-RGB.
- G. Buchsbaum, *A spatial processor model for object colour perception*, J. Franklin Inst. 310(1), 1980 — Gray-World.
- G. D. Finlayson & E. Trezzi, *Shades of Gray and Colour Constancy*, Color Imaging Conference, 2004 — the family both belong to.
- J. von Kries, *Chromatic adaptation* (1902) — the per-channel diagonal gain.

The maths lives in `src/quickwb/wb.py`; run it directly for a self-check:

```bash
python src/quickwb/wb.py
```
