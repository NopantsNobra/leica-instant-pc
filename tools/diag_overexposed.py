"""Overexposed catchlight test - the real mechanism behind red-eye highlights.

A specular catchlight in a portrait is typically 2-4 stops over white.
In RAW, after WB the R channel saturates first (its multiplier is the
largest), so an overexposed pixel arrives as e.g. (1.0, 0.75, 0.65):
already red before any style op runs. Then saturation gain amplifies it,
and highlight desaturation (starting at y=0.72 with a squared ramp) is
too weak/too late to recover.
"""
import sys
sys.path.insert(0, ".")

import numpy as np

from instant.color import apply_m9
from instant.develop import srgb_to_linear
from instant.tonemap import m9_tonemap, tame_clipped_highlights

h, w = 240, 320
yy, xx = np.mgrid[0:h, 0:w]
spec = ((xx-155)**2 + (yy-115)**2) < 9**2
iris = ((xx-160)**2 + (yy-120)**2) < 55**2


def build(scene_linear):
    img = np.zeros((h, w, 3), np.float32)
    img[:] = srgb_to_linear(np.array([0.70, 0.53, 0.44])) * 4.0
    img[iris] = srgb_to_linear(np.array([0.10, 0.08, 0.07])) * 4.0
    img[spec] = scene_linear
    return img


def run(tag, lin):
    lin = tame_clipped_highlights(lin)     # linear-domain fix (production)
    d = m9_tonemap(lin, 0.0)
    R, G, B = (d[..., i][spec].mean() for i in range(3))
    print(f"  {tag:26s} R={R:.3f} G={G:.3f} B={B:.3f}  R/G={R/G:.3f}  "
          f"R-B={R-B:+.3f}")
    d = apply_m9(d, strength=1.0)          # the real production chain
    R, G, B = (d[..., i][spec].mean() for i in range(3))
    print(f"  {'  + apply_m9 (CURRENT)':26s} R={R:.3f} G={G:.3f} B={B:.3f}  "
          f"R/G={R/G:.3f}  R-B={R-B:+.3f}   <-- what the user sees")


print("=== overexposed catchlight, scene-linear values ===")
print("(R saturates first because its WB multiplier is the largest)\n")

# 2 stops over: R clipped at 1.0, G/B still rising
run("2 stops over (1.0,0.75,0.65)", build(np.array([1.0, 0.75, 0.65], np.float32)))
# 4 stops over: even G hits ceiling, B trails
run("4 stops over (1.0,1.0,0.80)", build(np.array([1.0, 1.0, 0.80], np.float32)))
# severe: pure red clipping (R only channel above ceiling)
run("severe R-only  (1.0,0.60,0.50)", build(np.array([1.0, 0.60, 0.50], np.float32)))
