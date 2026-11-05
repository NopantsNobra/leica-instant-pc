#!/usr/bin/env python
"""Leica-Instant-style offline renderer - personal study build.

Pipeline (scene-referred -> display-referred):
  RAW decode (LibRaw, automation disabled, AsShotNeutral WB or a
    25% daylight blend) -> exposure normalization -> linear-domain
    highlight neutralization -> filmic tone map (M9 dense / M3 soft-grey)
    -> M9 colour stage -> real 17^3 LeicaFilter LUT (lux x scene CCT)
    -> shadow-weighted denoise + isolated red-spot suppression
    -> M3: spectral monochrome + MONOPAN-style grain
    -> JPG / PNG / 16-bit TIFF

Usage:
  python render.py input.dng -o out.jpg --mode m9
  python render.py raws/ -o outs/ --mode m3 --grain 1.2
  python render.py photo.jpg -o styled.jpg --mode m9 --ev -0.3
  python render.py input.dng -o out.jpg --mode m9 --lut my_fit.cube

This is a personal study/research tool. It does not reproduce Leica or
Xiaomi branding, watermarks, or proprietary model weights.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from instant.color import apply_cube, apply_m9, load_cube, suppress_red_spots
from instant.develop import (load_linear_srgb, load_srgb_image,
                             lux_index_from_exif, scene_cct)
from instant.export import apply_vignette, denoise_ycbcr, save
from instant.grain import apply_grain
from instant.leica_lut import SCENES, apply_leica_lut, normalize_scene
from instant.tonemap import (m3_tonemap, m9_tonemap, normalize_exposure,
                             tame_clipped_highlights)

RAW_EXT = {".dng", ".arw", ".cr2", ".cr3", ".nef", ".raf", ".rw2", ".orf"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def to_monochrome(img: np.ndarray) -> np.ndarray:
    """Spectral-weighted monochrome (panchromatic film flavor).

    Slightly blue-sensitive weights (vs Rec.709 luma) so blue skies
    render denser - the classic MONOPAN/pan-F rendering difference.
    """
    return (0.30 * img[..., 0] + 0.60 * img[..., 1] + 0.10 * img[..., 2])


def render_one(inp: Path, out: Path, args) -> dict:
    """Render a single file. Returns a small info dict."""
    ext = inp.suffix.lower()
    if ext in RAW_EXT:
        img, info = load_linear_srgb(str(inp), wb=getattr(args, "wb", "warm"))
        scene_referred = True
    elif ext in IMG_EXT:
        img, info = load_srgb_image(str(inp))
        scene_referred = False  # already tone-mapped by producer; soften maps
    else:
        raise ValueError(f"unsupported file type: {ext}")

    lux_index, cct = 300.0, 5000.0
    if scene_referred:
        lux_index, _ev = lux_index_from_exif(str(inp))
        # scene CCT drives which LUT bucket the ROM table selects
        # (trigger "aiscene-lux-cct"); hard-coding 5000 pinned every render
        # to the same bucket.
        cct, cct_src = scene_cct(str(inp))
        info["lux_index"] = lux_index
        info["cct"] = cct
        info["cct_src"] = cct_src
    if getattr(args, "cct", None):
        cct = float(args.cct)

    # ---- exposure normalize (RAW only) + tone map ------------------------
    pivot_lin = None
    if scene_referred and not args.no_normalize:
        img = normalize_exposure(img)
        # The S-curve must pivot on the scene's ACTUAL median. With a fixed
        # pivot, dark/backlit scenes (where the highlight anchor caps the
        # gain) sit far below it and the subject gets crushed - measured as
        # skin losing 40% of its display luminance.
        yl = 0.2126 * img[..., 0] + 0.7152 * img[..., 1] + 0.0722 * img[..., 2]
        pivot_lin = float(np.percentile(yl, 50))
    if scene_referred:
        # clipped speculars are red before styling; neutralize in linear
        img = tame_clipped_highlights(img)

    # ---- tone map -------------------------------------------------------
    if args.mode == "m9":
        disp = m9_tonemap(img, exposure=args.ev, pivot_linear=pivot_lin)
    else:
        disp = m3_tonemap(img, exposure=args.ev, pivot_linear=pivot_lin)

    # ---- color / mono ---------------------------------------------------
    if args.mode == "m9":
        if args.lut:
            size, table = load_cube(args.lut)
            disp = apply_cube(disp, size, table)
        else:
            disp = apply_m9(disp, strength=args.strength)
    else:  # m3
        mono = to_monochrome(disp)[..., None] * np.ones(3, np.float32)
        disp = mono

    # ---- grain / vignette ------------------------------------------------
    if args.mode == "m3" and args.grain > 0:
        disp = apply_grain(disp, path_hint=str(inp), strength=args.grain)
    if args.mode == "m9":
        # real LeicaFilter: ROM 17^3 LUT + CvStyle vignette
        disp = apply_leica_lut(disp, lux_index=lux_index, cct=cct,
                               zoom=1.0, shading=True,
                               scene=getattr(args, "scene", "common"))
        # Denoise LAST: the LUT re-expands contrast in saturated areas, so
        # cleaning before it leaves grain behind. Luma+chroma, shadow
        # weighted, edge protected.
        if args.denoise > 0:
            # Low-light scenes get a stronger pass: the exposure normalization
            # lifts shadows, which is exactly where sensor noise lives. The lux
            # index already encodes scene brightness (474.9 - 23.45*EV100), so
            # scale by it instead of asking the user to remember a slider.
            # lux 400+ -> x1.0 (daylight), lux 100 -> x1.45, lux 0 -> x1.6
            boost = 1.0 + np.clip((400.0 - lux_index) / 400.0, 0.0, 1.0) * 0.6
            disp = denoise_ycbcr(disp, strength=args.denoise * boost)
        # red-eye suppression must follow denoise: blurring re-spreads the
        # dots into halos and hides them from the isolation test.
        # Fixed strength (defect correction, not style - see apply_m9).
        disp = suppress_red_spots(disp, strength=1.0)
    if args.vignette > 0:
        disp = apply_vignette(disp, amount=args.vignette)

    # ---- write ------------------------------------------------------------
    out.parent.mkdir(parents=True, exist_ok=True)
    save(disp, str(out), sixteen_bit=args.tiff)

    return {"input": str(inp), "output": str(out),
            "camera": info.get("camera", ""), "mode": args.mode,
            "lux_index": round(float(lux_index), 1),
            "cct": round(float(cct), 0),
            "scene": normalize_scene(getattr(args, "scene", "common")),
            "wb": getattr(args, "wb", "warm")}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("input", help="RAW/image file or directory")
    p.add_argument("-o", "--output", required=True,
                   help="output file (single input) or directory (batch)")
    p.add_argument("--mode", choices=["m9", "m3"], default="m9",
                   help="m9 = dense CCD color, m3 = MONOPAN-style B&W (default m9)")
    p.add_argument("--ev", type=float, default=0.0,
                   help="exposure adjustment in stops (-2..2)")
    p.add_argument("--strength", type=float, default=1.0,
                   help="M9 saturation compensation (0..2, default 1.0). "
                        "Scales ONLY the saturation stage - the LUT provides "
                        "the look, this compensates for the missing StyleTrans "
                        "network")
    p.add_argument("--grain", type=float, default=1.0,
                   help="M3 grain strength (0..2, default 1.0)")
    p.add_argument("--vignette", type=float, default=0.0,
                   help="extra vignette 0..1 (default 0: the M9 LUT already "
                        "applies its own CvStyle shading)")
    p.add_argument("--scene", default="common",
                   choices=list(SCENES) + ["portrait"],
                   help="LUT scene: common / portrait / night / plants / food "
                        "/ sunrise_sunset (portrait maps to the ROM's "
                        "'protrait' table)")
    p.add_argument("--wb", choices=["warm", "camera", "daylight", "auto"],
                   default="warm",
                   help="white balance: warm = 25%% daylight blend (default), "
                        "camera = pure AsShotNeutral, daylight = locked "
                        "daylight (strong amber cast), auto = gray-world")
    p.add_argument("--cct", type=float, default=None,
                   help="override the scene colour temperature in Kelvin "
                        "(default: derived from AsShotNeutral); selects the "
                        "LUT bucket the ROM table maps to")
    p.add_argument("--lut", default=None,
                   help="optional .cube 3D LUT replacing the M9 color chain")
    p.add_argument("--tiff", action="store_true",
                   help="write 16-bit TIFF instead of JPG")
    p.add_argument("--no-normalize", action="store_true",
                   help="skip scene-exposure normalization (RAW only)")
    p.add_argument("--denoise", type=float, default=0.75,
                   help="noise reduction strength for M9 (0..2, default 0.75, auto-boosted "
                        "in low light) "
                        "- the LUT already compresses saturated speckle")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)

    default_ext = ".tif" if args.tiff else ".jpg"
    out_suffix = f"{args.mode}{default_ext}"

    inp = Path(args.input)
    if inp.is_dir():
        files = sorted(f for f in inp.iterdir()
                       if f.suffix.lower() in RAW_EXT | IMG_EXT)
        if not files:
            print("no supported files in directory", file=sys.stderr)
            return 2
        outdir = Path(args.output)
        results = [render_one(f, outdir / f"{f.stem}_{out_suffix}", args)
                   for f in files]
        if not args.quiet:
            for r in results:
                print(f"[{r['mode']}] {r['input']} -> {r['output']}")
        return 0

    out = Path(args.output)
    if out.is_dir() or out.suffix == "":
        out = out / f"{inp.stem}_{out_suffix}"
    r = render_one(inp, out, args)
    if not args.quiet:
        print(f"[{r['mode']}] {r['input']} -> {r['output']}"
              + (f"  ({r['camera']})" if r["camera"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
