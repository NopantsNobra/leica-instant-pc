"""Real Leica M9 LeicaFilter stage (17^3 LUT + CvStyle vignette).

Data extracted from the Xiaomi 17 Ultra ROM by the OffLineM9 project
(benbaobaoshigemi/OffLineM9, MIT for its own code; params from the ROM).
This replaces every hand-guessed colour operator: the LUT *is* the look.

Behaviour measured on the real LUT:
  neutral 0.50 -> (0.518, 0.518, 0.518)  zero colour shift, pure lift
  skin         -> warmer and brighter
  sky          -> blue +0.06 (denser)
  red          -> R +0.08 / B -0.09 (reds go pure)

NOTE on scene names: the ROM's own table spells portrait as "protrait".
`LeicaFilterParams.lut()` silently falls back to "common" for unknown
names, so an un-normalised "portrait" would look like it worked while
delivering the generic look. All names are normalised through SCENES.
"""

from __future__ import annotations

import numpy as np

from .m9filter import LeicaFilter

#: canonical scene names as stored in the ROM binary
SCENES = ("common", "protrait", "night", "plants", "food", "sunrise_sunset")

#: user-facing aliases (the ROM typo must never leak into the UI/CLI)
_ALIASES = {
    "portrait": "protrait",
    "people": "protrait",
    "person": "protrait",
    "landscape": "common",
    "sunset": "sunrise_sunset",
    "sunrise": "sunrise_sunset",
    "flower": "plants",
    "plant": "plants",
    "meal": "food",
}

_FILTER: LeicaFilter | None = None


def _get() -> LeicaFilter:
    global _FILTER
    if _FILTER is None:
        _FILTER = LeicaFilter()
    return _FILTER


def normalize_scene(scene: str | None) -> str:
    """Map a user-supplied scene name onto the ROM's spelling."""
    s = (scene or "common").strip().lower()
    s = _ALIASES.get(s, s)
    return s if s in SCENES else "common"


def apply_leica_lut(img: np.ndarray, lux_index: float = 300.0,
                    cct: float = 5000.0, zoom: float = 1.0,
                    shading: bool = True, scene: str = "common") -> np.ndarray:
    """Apply the ROM's LeicaFilter: blended 17^3 LUT then CvStyle vignette.

    img : display-referred sRGB float32 [0,1] (HxWx3)
    lux_index : Qualcomm lux index from EV100 (474.9 - 23.45*EV100)
    cct : scene colour temperature (AWB is locked to the D50 point, so the
          LUT is normally queried near 5000 K)
    scene : common / protrait / night / plants / food / sunrise_sunset
    """
    if img.dtype != np.float32:
        img = img.astype(np.float32)
    return _get()(np.clip(img, 0.0, 1.0), lux_index=float(lux_index),
                  cct=float(cct), zoom=float(zoom),
                  scene=normalize_scene(scene),
                  style_trans_on=True, shading=shading)
