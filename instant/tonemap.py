"""Tone mapping - the "backbone" curves for both modes.

M9  : strong contrast, mid-tones pressed down, soft highlight shoulder
      (CCD clips gradually, never harshly), slightly lifted blacks.
M3  : long, gentle grey transition - the MONOPAN 50 signature is
      extended mid-tone separation, NOT a punchy contrast curve.
      Blacks float a little; highlights ease off early.

Implementation: Hable (Uncharted 2) filmic operator, re-parameterized
per mode. Monotonic by construction, C1-continuous, no banding risk.
"""

from __future__ import annotations

import numpy as np


def _hable(x: np.ndarray, A, B, C, D, E, F) -> np.ndarray:
    return ((x * (A * x + C * B) + D * E) / (x * (A * x + B) + D * F)) - E / F


def tame_clipped_highlights(img: np.ndarray, knee: float = 0.90,
                            strength: float = 1.0) -> np.ndarray:
    """Neutralize colour of clipped speculars IN THE LINEAR DOMAIN.

    A pixel whose brightest channel sits at/near the sensor ceiling has
    been hit by per-channel clipping - and because the R multiplier is the
    largest, R saturates first, so an overexposed catchlight arrives as
    (1.0, 0.75, 0.65): red, before any style op runs. That colour is an
    artifact, not scene colour.

    Detecting it here (max channel >= knee) is physically sound and cannot
    touch normal skin tones, whose linear peaks are far below 0.9.
    Pixels above the knee are pulled toward their own luminance,
    proportional to how far past the knee they are.
    """
    mx = img.max(axis=-1, keepdims=True)
    w = np.clip((mx - knee) / max(1.0 - knee, 1e-6), 0.0, 1.0) * strength
    lum = (0.2126 * img[..., 0] + 0.7152 * img[..., 1]
           + 0.0722 * img[..., 2])[..., None]
    return img + (lum - img) * w


def normalize_exposure(img: np.ndarray, target_high: float = 0.86,
                       target_mid: float = 0.155,
                       percentile: float = 99.5,
                       gain_cap: float = 12.0,
                       high_weight: float = 0.90) -> np.ndarray:
    """Robust scene-exposure normalization on linear data.

    RAW pixel values have no absolute brightness anchor. A single
    highlight anchor fails on scenes where a few bright pixels dominate:
    the body of the frame stays crushed in shadow and every luminance-gated
    operator stops firing.

    Two anchors, combined as a WEIGHTED geometric mean:
      highlight anchor : p99.5 -> target_high   (protects highlights)
      midtone anchor   : p50   -> target_mid    (puts the scene on 18% grey)
    weight = `high_weight` (default 0.90), so the highlight anchor still
    dominates but the midtone anchor gets a real say.

    Why not `min(geom, high)` (the previous behaviour): a hard highlight
    cap ignores the midtones entirely. On a night scene the bright signs
    and street lamps set p99.5, so gain collapsed to a tiny value and the
    street - the actual subject - went to pure black (measured: p50 0.20 on
    a frame where the camera's own JPEG sits at 0.37). A weighted blend
    lifts the shadows while the soft shoulder (see `filmic`) absorbs the
    highlights that the extra gain pushes up.
    """
    y = 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]
    p_hi = float(np.percentile(y, percentile))
    p_mid = float(np.percentile(y, 50))

    gain_high = (target_high / p_hi) if p_hi > 1e-6 else 1.0
    gain_mid = (target_mid / p_mid) if p_mid > 1e-6 else 1.0

    w = float(np.clip(high_weight, 0.0, 1.0))
    gain = (gain_high ** w) * (gain_mid ** (1.0 - w))
    gain = min(gain, gain_cap)
    return np.maximum(img * gain, 0.0)


def _s_curve(y: np.ndarray, strength: float, pivot: float = 0.18) -> np.ndarray:
    """Monotonic contrast S-curve anchored at `pivot`.

    Needed because the mid-grey anchor (a pure power law) flattens the
    filmic slope back to ~1.0 - without this step the tone map produces
    correct but lifeless, low-contrast images.

    strength > 1 : darker below the pivot, brighter above (keeps pivot fixed)
    strength < 1 : gentler contrast
    """
    if abs(strength - 1.0) < 1e-3:
        return y
    lo = pivot * np.power(np.clip(y / pivot, 0.0, 1.0), strength)
    hi = pivot + (1.0 - pivot) * np.power(
        np.clip((y - pivot) / (1.0 - pivot), 0.0, 1.0), 1.0 / strength)
    return np.where(y < pivot, lo, hi)


def filmic(x: np.ndarray, exposure: float = 0.0,
           contrast: float = 1.0, shoulder: float = 1.0,
           black_lift: float = 0.0, mid_gray: float | None = 0.155,
           s_curve: float = 1.0, pivot_linear: float | None = None) -> np.ndarray:
    """Map linear scene-referred values to display-referred [0, 1].

    exposure  : stops added before mapping (applies to the *linear* data)
    contrast  : >1 = stronger mid-tone slope (M9 ~1.35, M3 ~0.85)
    shoulder  : >1 = softer/earlier highlight roll-off (CCD-like)
    black_lift: 0..0.08 - raises the floor, floats the blacks
    mid_gray  : anchor 18% scene grey to this display value. The raw Hable
                operator maps 0.18 -> ~0.138 (about one stop dark), which
                makes normally-exposed photos look flat and grey; anchoring
                keeps mid-tones where a standard SDR curve puts them.
    pivot_linear : scene-linear value the S-curve should pivot around.
                Defaults to `mid_gray`, which assumes normalization placed
                the scene median exactly there. When the highlight anchor
                caps the gain (dark, backlit scenes) the real median lands
                well below `mid_gray`, and a fixed pivot then CRUSHES the
                subject: measured on a dusk portrait, skin at linear 0.015
                was mapped through 0.0333 -> 0.0092 (a 1.6x darkening) and
                read as "yellow shadowed skin". Passing the *actual*
                post-normalization median keeps the S-curve centred on the
                picture instead of on a constant.
    """
    x = np.maximum(x, 0.0) * (2.0 ** exposure)

    # Hable baseline. W is the scene value that lands at display white:
    # the stock operator uses W=11 (HDR-oriented), which maps a full-scale
    # SDR input to only ~0.59 and leaves the frame using half its range -
    # flat and grey. 1.5x shoulder suits photographic SDR content.
    W = 1.5 * shoulder
    nom = _hable(W, 0.22, 0.30, 0.10, 0.20, 0.01, 0.30)
    y = _hable(x * contrast, 0.22, 0.30, 0.10, 0.20, 0.01, 0.30) / nom

    if black_lift > 0.0:
        y = black_lift + (1.0 - black_lift) * y

    # mid-grey anchor: pure power law, keeps 0->0 and 1->1 monotonic
    if mid_gray is not None:
        v = _hable(np.float64(mid_gray) * contrast,
                   0.22, 0.30, 0.10, 0.20, 0.01, 0.30) / nom
        if black_lift > 0.0:
            v = black_lift + (1.0 - black_lift) * v
        if 1e-3 < v < 0.999:
            y = y ** (np.log(mid_gray) / np.log(v))

        pivot = float(mid_gray)
        if pivot_linear is not None and pivot_linear > 1e-6:
            # Project the scene pivot through the SAME curve the image went
            # through - including the exposure gain. The curve operates on
            # x * 2**exposure, so projecting the raw linear pivot (without
            # that factor) put the S-curve pivot far too low: most pixels
            # then sat above it and got *brightened* instead of compressed.
            # Measured: raising s_curve 1.15 -> 1.70 lifted a shadow region
            # from L*32 to L*44 - the opposite of what contrast should do.
            pv = _hable(np.float64(pivot_linear) * (2.0 ** exposure) * contrast,
                        0.22, 0.30, 0.10, 0.20, 0.01, 0.30) / nom
            if black_lift > 0.0:
                pv = black_lift + (1.0 - black_lift) * pv
            if 1e-3 < pv < 0.999 and 1e-3 < v < 0.999:
                pv = pv ** (np.log(mid_gray) / np.log(v))
            pivot = float(np.clip(pv, 0.02, 0.90))
        y = _s_curve(y, s_curve, pivot=pivot)
    return np.clip(y, 0.0, 1.0)


def m9_tonemap(x: np.ndarray, exposure: float = 0.0,
               pivot_linear: float | None = None) -> np.ndarray:
    """M9: restrained tone, dense contrast, long soft highlight shoulder.

    shoulder raised to 2.1: on high-dynamic-range scenes (backlit sunset)
    a hard shoulder drove mid-bright clouds (linear ~0.2) up to display
    0.95 and flattened their internal gradient into a white band - which
    reads as "blown highlights" even though nothing is clipped.

    Recalibrated after removing the double gamma in export.save(): the
    saved JPEG used to be gamma-encoded twice, which hid that the curve was
    rendering mid-tones at p50 0.113 instead of ~0.37. The +1.5 EV base and
    the wider shoulder (3.60) put p50/p95 back on the camera's own numbers
    (0.362/0.810 vs 0.369/0.815) while keeping clipped pixels at 0.9%.

    s_curve 1.30 -> 1.15: 1.58 assumed the subject sits at the pivot. On a
    dusk portrait the subject sat 10x below it and lost 40% of its display
    luminance, which is what "shadowed yellow skin" looked like.
    """
    return filmic(x, exposure=exposure + 1.5, contrast=1.42, shoulder=3.60,
                  black_lift=0.022, mid_gray=0.30, s_curve=1.15,
                  pivot_linear=pivot_linear)


def m3_tonemap(x: np.ndarray, exposure: float = 0.0,
               pivot_linear: float | None = None) -> np.ndarray:
    """M3/MONOPAN: extended soft greys, mild contrast, floating blacks."""
    return filmic(x, exposure=exposure + 1.6, contrast=0.92, shoulder=2.60,
                  black_lift=0.025, mid_gray=0.30, s_curve=1.08,
                  pivot_linear=pivot_linear)
