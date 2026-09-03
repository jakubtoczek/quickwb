"""White balance for arbitrary RGB images.

Estimate a *white point* — the colour the image thinks is neutral — and apply a
per-channel linear gain that maps it to near-white, so a colour cast is removed
with minimal hue shift (same as GIMP's white-point pick).

Everything works on uint8 HxWx3 RGB arrays.
"""

from __future__ import annotations

import numpy as np

NEUTRAL = np.array([245.0, 245.0, 245.0], np.float32)  # fallback white point


def white_patch(rgb: np.ndarray, bright_frac: float = 0.1) -> np.ndarray:
    """White point = mean RGB of the brightest *bright_frac* of pixels.

    Assumes the brightest thing in the frame is meant to be white/neutral. Mild:
    on a strong cast the highlights are already near-neutral, so it under-corrects.
    """
    gray = rgb.max(axis=2)
    thr = np.percentile(gray, 100.0 * (1.0 - bright_frac))
    sel = gray >= thr
    if sel.sum() < 50:
        return NEUTRAL.copy()
    return rgb[sel].astype(np.float32).mean(axis=0)


def gray_world(rgb: np.ndarray, percentile: float = 60.0) -> np.ndarray:
    """White point = per-channel *percentile* of the non-near-black pixels.

    Lands on the bulk colour of the frame, so mapping it to white removes a cast
    more aggressively than :func:`white_patch`. Near-black pixels are dropped so
    dark borders/shadows don't drag the estimate.
    """
    pix = rgb.reshape(-1, 3).astype(np.float32)
    pix = pix[pix.max(axis=1) > 30]
    if len(pix) < 100:
        return NEUTRAL.copy()
    return np.percentile(pix, percentile, axis=0).astype(np.float32)


def auto_white_point(rgb: np.ndarray, neutralize: float = 0.5,
                     bright_frac: float = 0.1, percentile: float = 60.0) -> np.ndarray:
    """Blend the mild white-patch estimate with the stronger gray-world colour.

    *neutralize* in [0,1]: 0 -> white-patch only (subtle), 1 -> gray-world only
    (strongest de-cast). The default 0.5 suits most photos.
    """
    mild = white_patch(rgb, bright_frac)
    s = float(np.clip(neutralize, 0.0, 1.0))
    if s <= 0.0:
        return mild
    return (1.0 - s) * mild + s * gray_world(rgb, percentile)


def white_point_from_rect(rgb: np.ndarray, x0: int, y0: int,
                          x1: int, y1: int) -> np.ndarray:
    """White point = mean RGB of the box (x0,y0)-(x1,y1) — the eyedropper's ROI.

    Averaging a region you dragged over beats a single click when nothing in the
    frame is a clean grey: the noise and the texture average out.
    """
    h, w = rgb.shape[:2]
    xa, xb = sorted((int(x0), int(x1)))
    ya, yb = sorted((int(y0), int(y1)))
    patch = rgb[max(0, ya):min(h, yb + 1),
                max(0, xa):min(w, xb + 1)].astype(np.float32).reshape(-1, 3)
    if len(patch) == 0:
        return NEUTRAL.copy()
    return patch.mean(axis=0)


def white_point_from_patch(rgb: np.ndarray, x: int, y: int, radius: int = 6) -> np.ndarray:
    """White point = mean RGB of a square patch around (x, y) — a single click.

    Click something that *should* be neutral grey/white; that colour becomes the
    white point. Robust to a stray pixel because it averages a small patch.
    """
    return white_point_from_rect(rgb, x - radius, y - radius, x + radius, y + radius)


def apply_temperature(wp: np.ndarray, delta: float, k: float = 0.02) -> np.ndarray:
    """Warm/cool nudge of a white point. delta>0 warms (more red, less blue).

    WB gain is target/wp, so to warm we shrink the white point's red channel
    (bigger red gain) and grow its blue channel. *k* is the per-step fraction.
    """
    if not delta:
        return np.asarray(wp, np.float32)
    f = 1.0 + k * float(delta)
    return np.asarray(wp, np.float32) * np.array([1.0 / f, 1.0, f], np.float32)


def apply_tint(wp: np.ndarray, delta: float, k: float = 0.02) -> np.ndarray:
    """Green/magenta nudge of a white point. delta>0 adds magenta (less green)."""
    if not delta:
        return np.asarray(wp, np.float32)
    f = 1.0 + k * float(delta)
    return np.asarray(wp, np.float32) * np.array([1.0, f, 1.0], np.float32)


def white_balance(rgb: np.ndarray, wp: np.ndarray, target: float = 245.0) -> np.ndarray:
    """Scale each channel so white point *wp* maps to *target* (near-white)."""
    wp = np.maximum(np.asarray(wp, np.float32), 1.0)
    out = rgb.astype(np.float32) * (target / wp)
    return out.clip(0, 255).astype(np.uint8)


def tone_curve(rgb: np.ndarray, brightness: float = 0.0, contrast: float = 0.0) -> np.ndarray:
    """Brightness/contrast on uint8 RGB. Both in ~[-1, 1]; default is a no-op."""
    if brightness == 0.0 and contrast == 0.0:
        return rgb
    x = rgb.astype(np.float32) / 255.0
    if contrast:
        x = (x - 0.5) * (1.0 + contrast) + 0.5
    if brightness:
        x = x + brightness
    return (x.clip(0.0, 1.0) * 255.0).astype(np.uint8)


def balance(rgb: np.ndarray, *, white_point: np.ndarray | None = None,
            neutralize: float = 0.5, temperature: float = 0.0, tint: float = 0.0,
            brightness: float = 0.0, contrast: float = 0.0,
            target: float = 245.0) -> np.ndarray:
    """Full pipeline: resolve white point -> temp/tint nudge -> balance -> tone.

    Pass *white_point* (from the eyedropper) to override the auto estimate.
    """
    wp = auto_white_point(rgb, neutralize) if white_point is None else np.asarray(white_point, np.float32)
    wp = apply_temperature(wp, temperature)
    wp = apply_tint(wp, tint)
    out = white_balance(rgb, wp, target)
    return tone_curve(out, brightness, contrast)


if __name__ == "__main__":  # ponytail: smallest self-check for the WB math
    # A blue-cast grey image: neutral 128 grey pushed cool (low R, high B).
    img = np.zeros((60, 60, 3), np.uint8)
    img[:] = np.array([110, 128, 150], np.uint8)   # cool cast
    out = balance(img, neutralize=1.0)
    r, g, b = out.reshape(-1, 3).mean(axis=0)
    assert abs(r - b) < abs(110 - 150), "balancing should reduce the R/B gap (remove cast)"

    assert np.allclose(apply_temperature(NEUTRAL, 0), NEUTRAL), "delta=0 is identity"
    warm = apply_temperature(NEUTRAL, +2)
    assert (245.0 / warm)[0] > (245.0 / NEUTRAL)[0], "warm raises red gain"
    assert tone_curve(img) is img, "default tone is a no-op"
    assert tone_curve(img, brightness=0.5).mean() > img.mean(), "brightness lifts"

    wp = white_point_from_patch(img, 30, 30, radius=3)
    assert np.allclose(wp, [110, 128, 150], atol=1), "eyedropper reads the local colour"
    img[0:10, 0:10] = (200, 200, 200)
    assert np.allclose(white_point_from_rect(img, 0, 0, 9, 9), [200, 200, 200], atol=1),         "a dragged ROI averages the region it covers"
    assert np.allclose(white_point_from_rect(img, 9, 9, 0, 0),
                       white_point_from_rect(img, 0, 0, 9, 9)), "drag direction must not matter"
    print("wb self-check OK")
