"""Diagnose (a) shadow yellowness on skin, (b) noise level, per pipeline stage.

Yellowness is measured in CIE Lab b* (positive = yellow), per luminance band.
Noise is measured as high-pass std inside each band (chroma and luma separately).
"""
import sys
sys.path.insert(0, ".")

import numpy as np
from PIL import Image, ImageFilter

from instant.color import apply_m9, suppress_red_spots
from instant.develop import (linear_to_srgb, load_linear_srgb,
                             lux_index_from_exif, scene_cct)
from instant.export import denoise_ycbcr
from instant.leica_lut import apply_leica_lut
from instant.tonemap import (m9_tonemap, normalize_exposure,
                             tame_clipped_highlights)

PATH = "samples/user/IMG_20251207_165744.dng"


def to_lab(rgb):
    """sRGB display [0,1] -> CIE Lab (D65)."""
    x = np.clip(rgb, 0, 1)
    lin = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = lin @ m.T
    wp = np.array([0.95047, 1.0, 1.08883])
    t = xyz / wp
    d = 6 / 29
    f = np.where(t > d ** 3, np.cbrt(t), t / (3 * d * d) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16,
                     500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], -1)


def hp_std(img, radius=1.5):
    """High-pass std per channel -> crude noise energy."""
    out = []
    for c in range(3):
        ch = img[..., c]
        blur = np.asarray(Image.fromarray((ch * 255).astype(np.uint8), "L")
                          .filter(ImageFilter.GaussianBlur(radius)),
                          np.float32) / 255.0
        out.append((ch - blur).std())
    return float(np.mean(out)), float(np.std(np.array(out)))


def report(name, disp):
    lab = to_lab(disp)
    y = 0.2126 * disp[..., 0] + 0.7152 * disp[..., 1] + 0.0722 * disp[..., 2]
    print(f"\n--- {name} ---")
    print(f"{'band':10s} {'share':>6s} {'L*':>5s} {'a*':>6s} {'b*':>6s} "
          f"{'R/G':>6s} {'B/G':>6s}")
    for lo, hi, tag in [(0.03, 0.15, "deep"), (0.15, 0.32, "shadow"),
                        (0.32, 0.52, "low-mid"), (0.52, 0.75, "mid"),
                        (0.75, 1.01, "high")]:
        m = (y >= lo) & (y < hi)
        if m.sum() < 2000:
            continue
        L = lab[..., 0][m].mean()
        a = lab[..., 1][m].mean()
        b = lab[..., 2][m].mean()
        r, g, bl = (disp[..., i][m].mean() for i in range(3))
        print(f"{tag:10s} {m.mean()*100:5.1f}% {L:5.1f} {a:+6.1f} {b:+6.1f} "
              f"{r/g:6.3f} {bl/g:6.3f}")


def face_roi(disp, cx, cy, half, tag):
    h, w = disp.shape[:2]
    x, y = int(w * cx), int(h * cy)
    roi = disp[max(0, y - half):y + half, max(0, x - half):x + half]
    lab = to_lab(roi)
    yl = 0.2126 * roi[..., 0] + 0.7152 * roi[..., 1] + 0.0722 * roi[..., 2]
    sh = yl < np.percentile(yl, 35)          # shadow part of the face
    r, g, b = (roi[..., i][sh].mean() for i in range(3))
    print(f"  {tag:12s} face-shadow L*={lab[...,0][sh].mean():5.1f} "
          f"b*={lab[...,2][sh].mean():+6.1f} R/G={r/g:.3f} B/G={b/g:.3f} "
          f"yellowness={(r+g)/2-b:+.4f}")


lin, info = load_linear_srgb(PATH, wb="warm")
lux, ev = lux_index_from_exif(PATH)
cct, src = scene_cct(PATH)
print(f"wb={info.get('wb_applied')} lux={lux:.0f} cct={cct:.0f}({src})")

stages = {}
stages["1. decode(linear->disp)"] = np.clip(linear_to_srgb(lin), 0, 1)
a = tame_clipped_highlights(normalize_exposure(lin))
stages["2. +exposure norm"] = np.clip(linear_to_srgb(a), 0, 1)
b = m9_tonemap(a, 0.0)
stages["3. +tonemap"] = np.clip(b, 0, 1)
c = apply_m9(b, 1.0)
stages["4. +apply_m9"] = np.clip(c, 0, 1)
d = denoise_ycbcr(c, 0.6)
stages["5. +denoise .6"] = np.clip(d, 0, 1)
e = suppress_red_spots(d, 1.0)
f = apply_leica_lut(e, lux, cct)
stages["6. +LUT"] = np.clip(f, 0, 1)

for name, disp in stages.items():
    report(name, disp)

print("\n=== face shadow regions (yellowness) ===")
for name in ("1. decode(linear->disp)", "3. +tonemap", "4. +apply_m9",
             "5. +denoise .6", "6. +LUT"):
    print(f"[{name}]")
    face_roi(stages[name], 0.532, 0.262, 80, "standing")
    face_roi(stages[name], 0.455, 0.395, 70, "seated")

print("\n=== noise (high-pass std), full frame per band ===")
for name in ("3. +tonemap", "5. +denoise .6", "6. +LUT"):
    disp = stages[name]
    y = 0.2126 * disp[..., 0] + 0.7152 * disp[..., 1] + 0.0722 * disp[..., 2]
    print(f"[{name}]")
    for lo, hi, tag in [(0.10, 0.25, "shadow"), (0.25, 0.45, "lowmid"),
                        (0.45, 0.65, "mid")]:
        m = (y >= lo) & (y < hi)
        if m.sum() < 5000:
            continue
        sub = disp[m].reshape(1, -1, 3)
        img = sub.reshape(-1, 1, 3)
        lum_n, chr_n = hp_std(img.reshape(1, img.shape[0], 3))
        print(f"  {tag:8s} n={m.mean()*100:4.1f}%  high-pass std={lum_n:.4f}")
