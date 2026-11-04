"""Diagnostic: verify the purple cast is gone after the WB + operator fixes."""
import sys
sys.path.insert(0, ".")

import numpy as np
import rawpy

from instant.color import (_highlight_desat, _saturation, _shadow_blue,
                           _warm_boost, _warm_oil)
from instant.develop import load_linear_srgb
from instant.tonemap import m9_tonemap, normalize_exposure

SRC = "samples/mi8.dng"


def stats(tag, img):
    y = 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]
    mid = (y > 0.20) & (y < 0.60)
    r, g, b = (img[..., i].mean() for i in range(3))
    mr, mg, mb = (img[..., i][mid].mean() for i in range(3))
    purple = (img[..., 0] + img[..., 2] - 2 * img[..., 1]).mean()
    print(f"{tag:20s} R={r:.4f} G={g:.4f} B={b:.4f} | R/G={r/g:.3f} B/G={b/g:.3f} "
          f"| purple={purple:+.4f} | mid R/G={mr/mg:.3f} B/G={mb/mg:.3f}")


print("=== 1. Decode paths (control vs fixed) ===")
with rawpy.imread(SRC) as raw:
    rgb16 = raw.postprocess(gamma=(1, 1), no_auto_bright=True,
                            use_camera_wb=False, use_auto_wb=False, user_wb=None,
                            output_bps=16, output_color=rawpy.ColorSpace.sRGB,
                            demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
                            highlight_mode=rawpy.HighlightMode.Blend)
stats("NO-WB (old bug)", rgb16.astype(np.float32) / 65535.0)

lin, info = load_linear_srgb(SRC)           # default: camera WB
stats("camera WB (fixed)", lin)
print("wb_applied =", info.get("wb_applied"))

print("\n=== 2. Operator chain on fixed decode ===")
disp = m9_tonemap(normalize_exposure(lin), exposure=0.0)
stats("tonemap only", disp)
cur = disp
for name, fn in [
    ("+shadow_blue", lambda x: _shadow_blue(x, 1.0)),
    ("+warm_boost", lambda x: _warm_boost(x, 1.0)),
    ("+saturation", lambda x: _saturation(x, 1.16)),
    ("+highlight_desat", lambda x: _highlight_desat(x, 1.0)),
    ("+warm_oil", lambda x: _warm_oil(x, 1.0)),
]:
    cur = np.clip(fn(cur), 0.0, 1.0)
    stats(name, cur)

print("\n=== 3. shadow_blue coverage after narrowing ===")
y = 0.2126 * disp[..., 0] + 0.7152 * disp[..., 1] + 0.0722 * disp[..., 2]
w = np.clip(1.0 - y / 0.20, 0, 1) ** 2.0
print(f"  weight>0.1 in {float((w>0.1).mean())*100:.1f}% of frame (was 87.1%)")
print(f"  mean weight {w.mean():.4f} (was 0.5482)")
