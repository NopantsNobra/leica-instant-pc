"""Find which operator turns the lips grey-purple.

Samples a lip patch and reports RGB + saturation through the chain.
"""
import sys
sys.path.insert(0, ".")

import numpy as np

from instant.color import (_fade, _highlight_desat, _m9_color_signature,
                           _saturation, _shadow_blue, _split_tone, _warm_oil,
                           suppress_red_spots)
from instant.develop import load_linear_srgb
from instant.tonemap import m9_tonemap, normalize_exposure, tame_clipped_highlights

P = "samples/user/IMG_20251207_165744.dng"
lin, _ = load_linear_srgb(P)
img = tame_clipped_highlights(normalize_exposure(lin))
d = m9_tonemap(img, 0.0)

H, W = d.shape[:2]
# lip patch (standing girl), measured from the wide face crop
cx, cy = int(W * 0.537), int(H * 0.288)
px, py = int(W * 0.012), int(H * 0.006)
patch = np.s_[cy - py:cy + py, cx - px:cx + px]


def report(tag, a):
    p = a[patch]
    r, g, b = (float(p[..., i].mean()) for i in range(3))
    mx, mn = max(r, g, b), min(r, g, b)
    sat = (mx - mn) / max(mx, 1e-6)
    print(f"  {tag:22s} R={r:.3f} G={g:.3f} B={b:.3f} | sat={sat:.3f} | R/B={r/max(b,1e-6):.2f}")


print("lip patch through the chain:")
report("tonemap", d)
cur = d
for name, fn in [
    ("_m9_color_signature", lambda x: _m9_color_signature(x, 1.0)),
    ("_saturation(1.35)", lambda x: _saturation(x, 1.35)),
    ("_split_tone", lambda x: _split_tone(x, 1.0)),
    ("_shadow_blue(0.55)", lambda x: _shadow_blue(x, 0.55)),
    ("_highlight_desat(0.65)", lambda x: _highlight_desat(x, 0.65)),
    ("_warm_oil", lambda x: _warm_oil(x, 1.0)),
    ("_fade(0.5)", lambda x: _fade(x, 0.5)),
    ("suppress_red_spots", lambda x: suppress_red_spots(x, 1.0)),
]:
    cur = np.clip(fn(cur), 0, 1)
    report(name, cur)

# what does the ORIGINAL raw look like at the lips (after WB, before tone)?
report("raw linear->tonemap (ref)", d)
