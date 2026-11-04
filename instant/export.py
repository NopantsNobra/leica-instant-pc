"""Optical vignette (CvStyle-flavored) and export helpers."""

from __future__ import annotations

import numpy as np
from PIL import Image

from .develop import linear_to_srgb


def apply_vignette(img: np.ndarray, amount: float = 0.35) -> np.ndarray:
    """Radial falloff with a faint warm-mid / cool-corner split."""
    h, w = img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    nx = (xx / max(w - 1, 1)) * 2 - 1
    ny = (yy / max(h - 1, 1)) * 2 - 1
    r2 = (nx * 1.02) ** 2 + (ny * 1.02) ** 2
    fall = 1.0 - amount * np.clip(r2, 0.0, 1.0) ** 1.4

    # corners go a touch cool (blue up, red down) - classic lens cast
    corner_w = np.clip(r2 - 0.35, 0.0, 1.0) * amount
    out = img * fall[..., None]
    out[..., 0] *= (1.0 - 0.10 * corner_w)
    out[..., 2] *= (1.0 + 0.06 * corner_w)
    return np.clip(out, 0.0, 1.0)


def denoise_chroma(img: np.ndarray, strength: float = 1.0) -> np.ndarray:
    """Chroma-only denoise (kept for API compatibility) - see denoise_ycbcr."""
    return denoise_ycbcr(img, strength, luma_share=0.0)


def denoise_ycbcr(img: np.ndarray, strength: float = 1.0,
                  luma_share: float = 1.0) -> np.ndarray:
    """Shadow-weighted, edge-aware denoise in YCbCr.

    Measured on a 14 Ultra dusk portrait (ISO 296): skin high-pass std was
    6.8/255 on LUMA but only 1.0-1.5 on chroma. Chroma-only denoising (the
    previous behaviour) therefore did nothing for the visible grain - and
    because the grain sat on warm skin, it read as blotchy *yellow* patches
    rather than as noise.

    So:
      * chroma  : strong blur, radius scales with strength
      * luma    : mild blur, applied ONLY where it is safe:
                  - shadow-weighted (grain energy is highest in the dark),
                  - edge-protected (Sobel gradient gates the mix), so eyes,
                    lashes and fabric texture survive
      * the gating map itself is blurred, otherwise the noise in the
        gradient estimate would punch holes in the smoothing.
    """
    if strength <= 0.0:
        return img
    from PIL import Image, ImageFilter

    im8 = Image.fromarray((np.clip(img, 0.0, 1.0) * 255).round().astype(np.uint8),
                          "RGB")
    y_img, cb, cr = im8.convert("YCbCr").split()

    r_c = 0.8 + 1.4 * float(strength)
    cb = cb.filter(ImageFilter.GaussianBlur(r_c))
    cr = cr.filter(ImageFilter.GaussianBlur(r_c))

    out_y = y_img
    if luma_share > 0.0:
        y = np.asarray(y_img, np.float32)
        r_y = 0.35 + 0.55 * float(strength) * luma_share
        y_blur = np.asarray(y_img.filter(ImageFilter.GaussianBlur(r_y)), np.float32)

        # shadow weight: full below Y~95, none above Y~165
        shadow = np.clip((165.0 - y) / 70.0, 0.0, 1.0)
        # edge weight from a blurred gradient (raw gradient is noise-dominated)
        g = np.asarray(y_img.filter(ImageFilter.FIND_EDGES), np.float32)
        g = np.asarray(Image.fromarray(g.clip(0, 255).astype(np.uint8), "L")
                       .filter(ImageFilter.GaussianBlur(1.6)), np.float32)
        edge = np.clip(1.0 - g / 28.0, 0.0, 1.0)
        w = np.clip(shadow * edge, 0.0, 1.0) * min(luma_share, 1.0)
        y2 = y * (1.0 - w) + y_blur * w
        out_y = Image.fromarray(np.clip(y2, 0, 255).round().astype(np.uint8), "L")

    out = Image.merge("YCbCr", (out_y, cb, cr)).convert("RGB")
    return np.asarray(out, dtype=np.float32) / 255.0


def save(img: np.ndarray, path: str, sixteen_bit: bool = False) -> None:
    """Write display-referred sRGB output.

    NO gamma encoding here: `filmic()` already returns display-referred
    sRGB in [0,1]. An earlier version called `linear_to_srgb()` on top,
    i.e. gamma-encoded twice, which silently brightened every saved file
    (skin read L*38 in the JPEG but L*14 inside the pipeline) and hid how
    hard the tone curve was crushing the subject.

    Container is decided by the extension: .tif/.tiff -> TIFF (16-bit when
    requested, via tifffile since PIL cannot write RGB16), .png -> PNG,
    anything else -> JPG.
    """
    ext = path.lower().rsplit(".", 1)[-1]
    disp = np.clip(img, 0.0, 1.0)
    if ext in ("tif", "tiff"):
        if sixteen_bit:
            import tifffile
            arr16 = (disp * 65535.0).round().astype(np.uint16)
            tifffile.imwrite(path, arr16, photometric="rgb")
        else:
            arr8 = (disp * 255.0).round().astype(np.uint8)
            Image.fromarray(arr8, "RGB").save(path, format="TIFF")
        return
    if ext == "png":
        arr8 = (disp * 255.0).round().astype(np.uint8)
        Image.fromarray(arr8, "RGB").save(path, format="PNG")
        return
    arr8 = (disp * 255.0).round().astype(np.uint8)
    Image.fromarray(arr8, "RGB").save(path, quality=95, subsampling=0)
