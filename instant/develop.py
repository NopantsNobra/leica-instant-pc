"""RAW decode & linearization.

Strategy: bypass every automatic correction LibRaw would apply
(auto brightness, tone curve, gamma), and implement the defining
M9 feature - **locked daylight white balance** - via LibRaw's default
color-scaling path: when camera WB / auto WB / user WB are ALL unset,
LibRaw scales the raw channels by its daylight multipliers (pre_mul),
regardless of the AWB decision made at capture time. Warm scenes
stay warm - that is the point. The coefficients actually used are
read back after postprocess for the record.

Note on rawpy 0.28 API: the old `day_mul` / `camera_mul` properties
are gone; `make` / `model` replaced `camera_make` / `camera_model`.
"""

from __future__ import annotations

import numpy as np
import rawpy


def _camera_name(path: str) -> str:
    """Best-effort camera name.

    rawpy 0.28 does not expose make/model; PIL cannot open many
    manufacturer DNGs (Xiaomi's are not recognised), so read TIFF tags
    271 (Make) / 272 (Model) directly from the file header.
    """
    import struct

    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
            if len(head) < 8:
                return ""
            if head[:2] == b"II":
                end = "<"
            elif head[:2] == b"MM":
                end = ">"
            else:
                return ""
            if struct.unpack_from(end + "H", head, 2)[0] != 42:
                return ""
            ifd = struct.unpack_from(end + "I", head, 4)[0]
            fh.seek(ifd)
            n = struct.unpack(end + "H", fh.read(2))[0]
            make = model = ""
            for _ in range(n):
                ent = fh.read(12)
                if len(ent) < 12:
                    break
                tag, typ, cnt = struct.unpack(end + "HHI", ent[:8])
                if tag not in (271, 272) or typ != 2 or cnt == 0:
                    continue
                # ASCII value: inline when <=4 bytes, else stored at an offset
                if cnt <= 4:
                    raw = ent[8:8 + cnt]
                else:
                    off = struct.unpack_from(end + "I", ent, 8)[0]
                    pos = fh.tell()
                    fh.seek(off)
                    raw = fh.read(cnt)
                    fh.seek(pos)
                val = raw.rstrip(b"\x00").decode("ascii", "replace").strip()
                if tag == 271:
                    make = val
                else:
                    model = val
        return f"{make} {model}".strip()
    except Exception:  # noqa: BLE001 - metadata is best-effort only
        return ""


def load_linear_srgb(path: str, wb: str = "warm") -> tuple[np.ndarray, dict]:
    """Decode a RAW file to 16-bit linear sRGB, un-tone-mapped.

    White balance drives the *foundation* of the look:

    wb="warm"     : (default) geometric blend of AsShotNeutral and the
                    camera's daylight multipliers, 25% daylight. This keeps
                    the scene's own colour temperature mostly intact while
                    leaning a touch warm - "a little cast", not a filter.
    wb="camera"   : pure AsShotNeutral (neutral, can go cool at dusk)
    wb="daylight" : lock daylight (strong amber cast - experiment only)
    wb="auto"     : gray-world estimate

    Rationale: locking full daylight measured R/G=1.41 (everything amber);
    pure AWB measured R/G=0.94 (a touch cool). Reviews describe the real
    M9 mode as "shadows blue, highlights slightly warm" - a split tone over
    a near-neutral base, so the WB stage must stay close to neutral and
    the warm/cool separation belongs to the colour chain.

    Returns (image float32 HxWx3 in [0, ~4+], info dict).
    """
    with rawpy.imread(path) as raw:
        info = {"camera": _camera_name(path)}
        day = None
        if wb == "daylight":
            try:
                day = [float(x) for x in raw.daylight_whitebalance[:3]]
                if min(day) <= 0:
                    day = None
            except Exception:  # noqa: BLE001
                day = None
        elif wb == "warm":
            # geometric blend: 25% daylight, 75% as-shot
            try:
                cam = [float(x) for x in raw.camera_whitebalance[:3]]
                dl = [float(x) for x in raw.daylight_whitebalance[:3]]
                if min(cam) > 0 and min(dl) > 0:
                    day = [c ** 0.75 * d ** 0.25 for c, d in zip(cam, dl)]
            except Exception:  # noqa: BLE001
                day = None
        rgb16 = raw.postprocess(
            gamma=(1, 1),                    # keep linear
            no_auto_bright=True,             # no exposure guessing
            use_camera_wb=(wb == "camera"),
            use_auto_wb=(wb == "auto"),
            user_wb=([day[0], day[1], day[2], day[1]]
                     if day else None),
            output_bps=16,
            output_color=rawpy.ColorSpace.sRGB,
            demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
            # ReconstructDefault = LibRaw's modern highlight reconstruction
            # (neighbourhood chroma), keeps highlight colour neutral.
            # Blend pulls clipped pixels toward the brightest channel (R,
            # whose WB multiplier is largest) -> red catchlights.
            highlight_mode=rawpy.HighlightMode.ReconstructDefault,
        )
        # coefficients actually applied (for the record)
        info["wb_applied"] = raw.auto_whitebalance
        info["wb_mode"] = wb
    img = rgb16.astype(np.float32) / 65535.0
    return img, info


def load_srgb_image(path: str) -> tuple[np.ndarray, dict]:
    """Decode an ordinary 8-bit sRGB (JPG/PNG/TIFF) file for style processing.

    These are already white-balanced and tone-mapped by whoever produced
    them, so we treat them as display-referred input: decode, linearize
    with the sRGB EOTF, and let the caller re-map.
    """
    from PIL import Image

    img8 = np.asarray(Image.open(path).convert("RGB"), dtype=np.float32) / 255.0
    linear = srgb_to_linear(img8)
    return linear * 4.0, {"camera": "sRGB input"}


def srgb_to_linear(x: np.ndarray) -> np.ndarray:
    """sRGB EOTF (display -> linear)."""
    return np.where(
        x <= 0.04045,
        x / 12.92,
        ((x + 0.055) / 1.055) ** 2.4,
    )


def linear_to_srgb(x: np.ndarray) -> np.ndarray:
    """Inverse sRGB EOTF (linear -> display)."""
    x = np.clip(x, 0.0, 1.0)
    return np.where(
        x <= 0.0031308,
        x * 12.92,
        1.055 * np.power(x, 1 / 2.4) - 0.055,
    )


def exif_exposure(path: str) -> tuple[float, float, float] | None:
    """(ISO, exposure_time, f_number) from DNG TIFF tags (33434/33437/34855).

    Manufacturer DNGs often break PIL's EXIF reader, so parse the IFD directly.
    """
    import struct

    vals: dict[int, float] = {}
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
            if head[:2] not in (b"II", b"MM"):
                return None
            end = "<" if head[:2] == b"II" else ">"
            fh.seek(struct.unpack_from(end + "I", head, 4)[0])
            n = struct.unpack(end + "H", fh.read(2))[0]
            for _ in range(n):
                ent = fh.read(12)
                if len(ent) < 12:
                    break
                tag, typ, cnt = struct.unpack(end + "HHI", ent[:8])
                if tag not in (33434, 33437, 34855):
                    continue
                if typ == 5:                      # rational, stored at an offset
                    off = struct.unpack_from(end + "I", ent, 8)[0]
                    pos = fh.tell()
                    fh.seek(off)
                    num, den = struct.unpack(end + "II", fh.read(8))
                    fh.seek(pos)
                    vals[tag] = num / max(den, 1)
                elif typ == 3:
                    vals[tag] = float(struct.unpack_from(end + "H", ent, 8)[0])
                elif typ == 4:
                    vals[tag] = float(struct.unpack_from(end + "I", ent, 8)[0])
    except Exception:  # noqa: BLE001
        return None
    if 33434 in vals and 33437 in vals:
        return vals.get(34855, 100.0), vals[33434], vals[33437]
    return None


def lux_index_from_exif(path: str, default: float = 300.0) -> tuple[float, float | None]:
    """Qualcomm lux index from scene EV100 (formula from the OffLineM9 reverse
    engineering: index = 474.9 - 23.45*EV100). Returns (lux_index, ev100)."""
    ex = exif_exposure(path)
    if not ex:
        return default, None
    iso, t, fnum = ex
    if t <= 0 or fnum <= 0 or iso <= 0:
        return default, None
    ev100 = float(np.log2(fnum * fnum / t) - np.log2(iso / 100.0))
    return float(np.clip(474.9 - 23.45 * ev100, 0, 999)), ev100


# --------------------------------------------------------------- scene CCT
_DNG_TAGS_CCT = (50721, 50722, 50728, 50778, 50779)


def _read_ifd_tags(path: str, wanted: tuple[int, ...]) -> dict[int, list[float]]:
    """Minimal TIFF IFD reader -> {tag: [values]} for the requested tags only.

    Manufacturer DNGs frequently defeat PIL, so tags are read directly. Handles
    BYTE/SHORT/LONG/RATIONAL/SRATIONAL, inline values and offset-stored arrays.
    """
    import struct

    out: dict[int, list[float]] = {}
    try:
        with open(path, "rb") as fh:
            head = fh.read(8)
            if head[:2] not in (b"II", b"MM"):
                return out
            end = "<" if head[:2] == b"II" else ">"
            fh.seek(struct.unpack_from(end + "I", head, 4)[0])
            n = struct.unpack(end + "H", fh.read(2))[0]
            entries = []
            for _ in range(n):
                ent = fh.read(12)
                if len(ent) < 12:
                    return out
                tag, typ, cnt = struct.unpack(end + "HHI", ent[:8])
                entries.append((tag, typ, cnt, ent[8:12]))
            for tag, typ, cnt, raw in entries:
                if tag not in wanted:
                    continue
                size = {1: 1, 3: 2, 4: 4, 5: 8, 9: 4, 10: 8}.get(typ, 0)
                if not size or cnt <= 0:
                    continue
                total = size * cnt
                pos = fh.tell()
                if total <= 4:
                    blob = raw[:total]
                else:
                    off = struct.unpack_from(end + "I", raw, 0)[0]
                    fh.seek(off)
                    blob = fh.read(total)
                    fh.seek(pos)
                if typ in (5, 10):                       # rational
                    fmt = end + ("II" if typ == 5 else "ii") * cnt
                    vals = struct.unpack(fmt, blob)
                    out[tag] = [vals[i] / (vals[i + 1] or 1)
                                for i in range(0, len(vals), 2)]
                else:
                    fmt = end + {1: "B", 3: "H", 4: "I", 9: "i"}[typ] * cnt
                    out[tag] = [float(v) for v in struct.unpack(fmt, blob)]
    except Exception:  # noqa: BLE001 - metadata is best-effort
        return out
    return out


# CCT of the DNG calibration illuminants (DNG spec presets)
_ILLUM_CCT = {1: 5500.0, 2: 4200.0, 3: 2856.0, 4: 4874.0, 5: 6774.0,
              17: 2856.0, 18: 4874.0, 19: 6774.0, 20: 5503.0, 21: 6504.0,
              22: 7504.0, 23: 5003.0, 24: 3200.0}


def _xy_to_cct(x: float, y: float) -> float:
    """McCamy's cubic approximation (adequate for LUT bucket selection).

    NOTE: the denominator goes negative above y=0.1858 (i.e. for virtually
    every real white point). An earlier `max(0.1858 - y, 1e-4)` guard clamped
    it to 1e-4 and produced CCT values in the millions - only guard against a
    true zero-crossing.
    """
    d = 0.1858 - y
    if abs(d) < 1e-4:
        d = 1e-4 if d >= 0 else -1e-4
    n = (x - 0.3320) / d
    return float(449 * n ** 3 + 3525 * n ** 2 + 6823.3 * n + 5520.33)


_SRGB2XYZ = np.array([[0.4124, 0.3576, 0.1805],
                      [0.2126, 0.7152, 0.0722],
                      [0.0193, 0.1192, 0.9505]], float)


def scene_cct(path: str, default: float = 5000.0) -> tuple[float, str]:
    """Rough scene colour temperature from AsShotNeutral.

    The LeicaFilter is queried with the *scene* CCT (the ROM trigger is named
    "aiscene-lux-cct"), so a hard-coded 5000 K pins every render to one LUT
    bucket. The fully correct route is OffLineM9's iterative DNG color model,
    but on the two reference DNGs here the ColorMatrix inverse lands the white
    point off the Planckian locus (y ~ 0.21) and McCamy diverges, so we use the
    sRGB white-point approximation instead and sanity-clamp the result.

    Accuracy is only sufficient to pick a LUT bucket (boundaries are coarse:
    2800 / 3600 / 4500 K), which is all it is used for. Users can override the
    value explicitly (`--cct` / the UI slider).

    Returns (cct, source_string).
    """
    tags = _read_ifd_tags(path, (50728,))
    neutral = tags.get(50728)
    if not neutral or len(neutral) < 3:
        return default, "default"
    n = np.asarray(neutral[:3], float)
    if min(n) <= 0:
        return default, "default"

    xyz = _SRGB2XYZ @ (1.0 / n)
    s = float(xyz.sum())
    if s <= 1e-9:
        return default, "default"
    x, y = float(xyz[0]) / s, float(xyz[1]) / s
    if not (0.25 <= y <= 0.42):        # white point off the locus -> unreliable
        return default, "default"
    cct = _xy_to_cct(x, y)
    if not (2000.0 <= cct <= 9000.0):
        return default, "default"
    return float(cct), "asn"
