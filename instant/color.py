"""Color science for the M9 look (v1: parametric operator chain).

Public information about the M9 CCD rendering converges on:
  - locked daylight WB (handled in develop.py)
  - dense, saturated color that never turns garish
  - shadows carry a blue cast; warm light is rendered with force
  - highlights desaturate toward white (chroma collapses as you clip)
  - a subtle "oily" smoothness - tiny blue-channel gamma lift

v1 implements these as an ordered operator chain on display-referred
sRGB. The chain can be entirely replaced by a fitted 3D LUT (.cube) -
same interface OffLineM9-style projects use - via load_cube() and
apply_cube(). When real matched samples become available, fit a LUT
and drop it in; nothing else changes.
"""

from __future__ import annotations

import numpy as np


# --------------------------------------------------------------------------
# parametric chain
# --------------------------------------------------------------------------

def apply_m9(img: np.ndarray, strength: float = 1.0) -> np.ndarray:
    """M9 pre-LUT stage.

    History: this used to be a fully hand-tuned chain (hue fingerprint,
    split-tone, warm oil, fade...). Those were guesses at the look and kept
    drifting into an amber filter. The actual M9 character comes from the
    real 17^3 LeicaFilter LUT extracted from the 17U ROM (see
    `apply_leica_lut`), so this stage now only does what the LUT cannot:

      * mild saturation compensation for the missing StyleTrans network
      * gentle highlight chroma roll-off (film behaviour)
      * isolated red-eye dot removal (sensor artefact, not style)

    `strength` scales ONLY the saturation compensation; 1.0 (default) lands
    on 0.85x, i.e. slightly *under* the decoded saturation, because the LUT
    that follows adds +20..25% of its own on coloured regions (measured;
    neutral greys pass through untouched at B/G=1.000).

    Measured on two night-lit DNGs against their in-camera JPEGs, 0.85x is
    the point where both wrong-direction casts disappear at once:
      amber scene (tungsten interior): R/G 1.887 -> 1.613 (camera 1.619)
      cyan scene  (dark interior):     R/G 1.249 -> 1.170 (camera 1.182)
    Anything above ~0.95 leaves the first one visibly orange; anything
    below ~0.75 goes flat.

    The highlight roll-off and red-spot suppression are fixed-strength
    defect corrections, not style, so dialling "more style" can never
    reintroduce red catchlights.

    Everything WB/hue/warmth related is intentionally left alone - the
    decoded image plus the LUT decide the colour.
    """
    sat = 0.85 + 0.15 * (strength - 1.0)        # 0->0.70, 1->0.85, 2->1.00
    img = _saturation(img, sat)
    img = _highlight_desat(img, 0.15)           # LUT handles highlights
    img = suppress_red_spots(img, 1.0)
    return np.clip(img, 0.0, 1.0)


def _luma(img: np.ndarray) -> np.ndarray:
    return 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]


def suppress_red_spots(img: np.ndarray, strength: float = 1.0) -> np.ndarray:
    """Neutralize *isolated* red-dominant pixels (real red-eye).

    A retinal catchlight arrives as e.g. (0.32, 0.05, 0.08) - R ~6x G but
    only luma 0.11, i.e. a dark red dot, not a highlight.

    Uses a *ratio* criterion (R vs max(G,B)) instead of absolute redness:
    under daylight-locked WB the whole frame is warm, so absolute measures
    lose their footing. Isolation is still the discriminator - lips and red
    clothes are large patches whose neighbourhood is equally red.
    """
    if strength <= 0.0:
        return img
    from PIL import Image, ImageFilter

    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    mx_gb = np.maximum(g, b)
    ratio = r / np.maximum(mx_gb, 1e-3)                 # 1.0 = neutral
    redness = np.clip((ratio - 3.50) / 1.00, 0.0, 1.0)  # R 3.5x+ above G/B
    # Measured on real files: red-eye dots reach R/G,B = 20-27x, lips under
    # warm WB ~2.6-2.8x. A 3.5x gate separates them with margin; anything
    # lower starts desaturating lips.

    r8 = (redness * 255.0).astype(np.uint8)
    gray = Image.fromarray(r8, "L")
    # multi-scale context: a red-eye patch is a few dozen px wide, so a
    # single small window sees only redness and misreads it as a large red
    # object. Taking the MIN across radii means "it must be red at every
    # scale to count as a red object" - catchlights fail that test, red
    # clothing and lips pass it.
    local = None
    for rad in (12, 40, 80):
        l = np.asarray(gray.filter(ImageFilter.BoxBlur(rad)),
                       dtype=np.float32) / 255.0
        local = l if local is None else np.minimum(local, l)

    # red-eye dots are DARK (luma ~0.11); bright red objects (traffic lights,
    # lanterns, signs) are legitimately saturated and must not be touched
    lum_mask = np.clip((0.40 - (0.2126 * r + 0.7152 * g + 0.0722 * b)) / 0.15,
                       0.0, 1.0)
    iso = np.clip(redness - local * 1.6 - 0.04, 0.0, 1.0) * strength * lum_mask
    # Converge toward the pixel's OWN luminance: this removes the colour
    # without darkening the dot (pulling R down to G/B leaves a dark red
    # spot, which still reads as red-eye).
    lum = (0.2126 * r + 0.7152 * g + 0.0722 * b)[..., None]
    out = img + (lum - img) * iso[..., None]
    return np.clip(out, 0.0, 1.0)


def _highlight_weight(y: np.ndarray) -> np.ndarray:
    """0 below y=0.62, ramping to 1 at y=0.90.

    Only a gentle film-style highlight roll-off: the *clipping* problem
    (red catchlights) is solved upstream in the linear domain by
    tame_clipped_highlights(). Keeping this threshold high avoids
    desaturating mid-tones such as skin.
    """
    return np.clip((y - 0.62) / 0.28, 0.0, 1.0) ** 1.2


def _saturation(img: np.ndarray, factor: float) -> np.ndarray:
    """Chroma gain with highlight AND shadow protection.

    - highlights (y>0.62): gain fades out so clipped catchlights don't
      re-saturate toward red
    - shadows (y<0.18): gain fades out so high-ISO chroma noise is not
      amplified into coloured speckle
    """
    y = _luma(img)[..., None]
    gh = (1.0 - _highlight_weight(y[..., 0]))[..., None]
    gs = np.clip(y / 0.10, 0.0, 1.0)           # only the noisiest blacks
    eff = 1.0 + (factor - 1.0) * gh * gs
    return y + (img - y) * eff


def _highlight_desat(img: np.ndarray, k: float) -> np.ndarray:
    """Gentle film-style highlight roll-off toward white.

    Deliberately mild now (was 0.95): real M9 files keep colour density
    well into the highlights, so over-neutralizing them is what made the
    output look "corrected" instead of "styled". Only true clipped
    speculars are fully neutralized, and that happens upstream in the
    linear domain (tame_clipped_highlights).
    """
    y = _luma(img)
    w = _highlight_weight(y) * k
    return y[..., None] + (img - y[..., None]) * (1.0 - 0.80 * w)[..., None]


# --------------------------------------------------------------------------
# .cube LUT support (replacement path once a fitted LUT exists)
# --------------------------------------------------------------------------

def load_cube(path: str) -> tuple[int, np.ndarray]:
    """Parse a 3D .cube LUT. Returns (size, table[size^3, 3])."""
    size = None
    rows: list[list[float]] = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.upper().startswith("LUT_3D_SIZE"):
                size = int(line.split()[-1])
                continue
            if line[0].isalpha():
                continue  # TITLE / DOMAIN_*/ other metadata
            parts = line.split()
            if len(parts) >= 3:
                rows.append([float(parts[0]), float(parts[1]), float(parts[2])])
    if size is None or len(rows) != size ** 3:
        raise ValueError(f"invalid .cube: size={size}, rows={len(rows)}")
    return size, np.asarray(rows, dtype=np.float32)


def apply_cube(img: np.ndarray, size: int, table: np.ndarray) -> np.ndarray:
    """Trilinear-interpolated 3D LUT apply. img: sRGB [0,1], HxWx3.

    Standard 8-corner trilinear interpolation on integer lattice indices.
    """
    h, w, _ = img.shape
    idx = np.clip(img, 0.0, 1.0) * (size - 1)
    i0 = np.floor(idx).astype(np.int32)
    f = (idx - i0).astype(np.float32)          # fractional part, HxWx3
    i1 = np.minimum(i0 + 1, size - 1)          # upper neighbours, int32

    # flatten lattice lookup: table is laid out r-major then g then b
    def lut(r: np.ndarray, g: np.ndarray, b: np.ndarray) -> np.ndarray:
        r = np.clip(r.ravel(), 0, size - 1)
        g = np.clip(g.ravel(), 0, size - 1)
        b = np.clip(b.ravel(), 0, size - 1)
        lin = (r.astype(np.int64) * size + g) * size + b
        return table[lin].reshape(h, w, 3).astype(np.float32)

    fr, fg, fb = f[..., 0], f[..., 1], f[..., 2]
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    r1, g1, b1 = i1[..., 0], i1[..., 1], i1[..., 2]

    # 8 corners
    c000 = lut(r0, g0, b0)
    c100 = lut(r1, g0, b0)
    c010 = lut(r0, g1, b0)
    c001 = lut(r0, g0, b1)
    c110 = lut(r1, g1, b0)
    c101 = lut(r1, g0, b1)
    c011 = lut(r0, g1, b1)
    c111 = lut(r1, g1, b1)

    def lerp(a, b, t):
        if t.ndim == 2:
            t = t[..., None]
        return a + (b - a) * t

    out = lerp(lerp(lerp(c000, c100, fr), lerp(c010, c110, fr), fg),
               lerp(lerp(c001, c101, fr), lerp(c011, c111, fr), fg), fb)
    return np.clip(out, 0.0, 1.0)
