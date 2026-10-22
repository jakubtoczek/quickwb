# QuickWB

Open, drag or paste an image in, white-balance it, and export it or copy it to
the clipboard. A small single-window desktop app (PySide6).

![icon](src/quickwb/assets/quickwb.png)

Works on one image or on a batch, and the selection decides which: **Separately**
gives each image its own correction, **Together** computes one shared
correction for the whole selection so a set stays consistent.

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
   To pin it to the taskbar: right-click its taskbar button while it runs (or its Start
   menu entry, which it keeps up to date) ▸ *Pin to taskbar*.

The choice is made once: from then on the folder on disk is the memory, so the Desktop
shortcut and a plain double-click find it and start straight away — no argument needed
again. Set `QUICKWB_RUNTIME` to force some other location.

`uninstall.bat` reverses all of it: the whole folder in the private case, only QuickWB's own
virtual environment in the shared case, so anything else using that folder keeps working.

## Use

1. **Drop, open or paste** images (PNG, JPG, TIFF, BMP, WebP; Ctrl+V a screenshot or copied
   files). **Single** shows one at a time, **Grid** them all.
2. Select the images to correct — **Separately** gives each its own correction, **Together** one
   shared correction for the selection, so a set stays consistent.
3. **White point**: **Auto**, or **Pick…** a neutral spot; then de-cast, temperature, tint,
   brightness, contrast if needed. **Show original** compares; **Reset** undoes it; Ctrl+Z
   anything.
4. **Save** the result, or **Copy** it (Ctrl+C) to paste elsewhere.

Every control, and how the correction is computed: **[GUIDE.md](GUIDE.md)**.

## Files

- **Reads** PNG, JPG, TIFF, BMP, WebP, and images or files from the clipboard.
- **Writes** the corrected image as `<name>_wb.png` (PNG, JPEG or TIFF; a folder of them for
  several), where you choose — beside the original, never over it unless you pick its name.

## Development

```bash
git clone https://github.com/jakubtoczek/quickwb
cd quickwb
uv run --project . quickwb
# or, with your own environment:
pip install -e .
python -m quickwb

python misc/bump.py         # stamp today's date as the version
python misc/smoke_test.py   # gallery / white-balance self-check
bash   misc/root_test.sh    # where the launcher decides to install
```

Requires Python ≥ 3.11.

## Licence

MIT — see [LICENSE](LICENSE).
