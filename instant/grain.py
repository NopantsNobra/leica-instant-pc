"""MONOPAN 50-style silver-halide grain (for the M3 monochrome mode).

Public descriptions of the official implementation:
  - grain is laid down per-pixel, dynamically
  - visibility *increases* in shadows (ISO-50 film: fine grain in
    highlights, emerging grain as density builds)
  - extremely fine structure (not "digital noise")

Implementation: two-scale monochrome noise -
  fine layer  : per-pixel gaussian, amplitude grows in shadows
  clump layer : low-frequency gaussian modulating the fine amplitude,
                mimicking crystal clustering on the emulsion
Deterministic per input file (seed derived from path) so renders are
reproducible.
"""

from __future__ import annotations

import hashlib

import numpy as np


def apply_grain(img: np.ndarray, path_hint: str = "",
                strength: float = 1.0) -> np.ndarray:
    """Monochrome film grain. img: display-referred [0,1], any channel count."""
    h, w = img.shape[:2]
    seed = int.from_bytes(
        hashlib.sha256(path_hint.encode("utf-8")).digest()[:8], "little")
    rng = np.random.default_rng(seed)

    y = img.mean(axis=-1)

    # fine grain: sigma peaks in shadows (~3x), fades to ~0.6x in highlights
    shadow_w = (1.0 - np.clip(y, 0.0, 1.0)) ** 1.8
    sigma_map = (0.006 + 0.022 * shadow_w) * strength
    fine = rng.standard_normal((h, w), dtype=np.float32)

    # clumps: ~3px blobs modulating local grain amplitude
    low = rng.standard_normal((max(2, h // 4), max(2, w // 4)), dtype=np.float32)
    low = _resize_smooth(low, h, w)
    clump = 1.0 + 0.55 * low  # 0.45 .. 1.55 amplitude modulation

    grain = fine * clump * sigma_map
    return np.clip(img + grain[..., None], 0.0, 1.0)


def _resize_smooth(arr: np.ndarray, h: int, w: int) -> np.ndarray:
    """Bilinear upscale of the clump map to full resolution."""
    from PIL import Image
    im = Image.fromarray(((arr - arr.min()) /
                          max(float(np.ptp(arr)), 1e-9) * 255).astype(np.uint8), "L")
    im = im.resize((w, h), Image.BILINEAR)
    out = np.asarray(im, dtype=np.float32) / 255.0
    return (out - out.mean()) / max(out.std(), 1e-9)  # renormalize to N(0,1)
