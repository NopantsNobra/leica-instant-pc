"""Parse DNG TIFF IFD tags directly - is ColorMatrix really absent?

Relevant DNG tags:
  50721 ColorMatrix1      50722 ColorMatrix2
  50728 AsShotNeutral     50729 AsShotWhiteXY
  50778 CalibrationIlluminant1   50779 CalibrationIlluminant2
  50708 UniqueCameraModel 50715 CFAPlaneColor
  50710 CFAPlaneColor, 50711 CFALayout, 50717 WhiteLevel
"""
import struct
import sys

TAGS = {
    254: "NewSubfileType", 256: "ImageWidth", 257: "ImageLength",
    258: "BitsPerSample", 259: "Compression", 262: "PhotometricInterpretation",
    271: "Make", 272: "Model", 273: "StripOffsets", 277: "SamplesPerPixel",
    278: "RowsPerStrip", 279: "StripByteCounts", 284: "PlanarConfiguration",
    50706: "DNGVersion", 50707: "DNGBackwardVersion",
    50708: "UniqueCameraModel", 50710: "CFAPlaneColor", 50711: "CFALayout",
    50712: "LinearizationTable", 50713: "BlackLevelRepeatDim",
    50714: "BlackLevel", 50717: "WhiteLevel",
    50721: "ColorMatrix1", 50722: "ColorMatrix2",
    50723: "CameraCalibration1", 50724: "CameraCalibration2",
    50728: "AsShotNeutral", 50729: "AsShotWhiteXY",
    50778: "CalibrationIlluminant1", 50779: "CalibrationIlluminant2",
    50781: "RawDataUniqueID", 50827: "OriginalRawFileName",
    50931: "CameraCalibrationSignature", 50932: "ProfileCalibrationSignature",
    50940: "PreviewApplicationName",
    52525: "ProfileGainTableMap", 52526: "SemanticName",
    # Xiaomi private tags often live in 0xC6xx / 0xEAxx ranges
}

TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}


def read_ifd(data: bytes, offset: int, depth: int = 0) -> None:
    if offset + 2 > len(data):
        return
    n = struct.unpack_from("<H", data, offset)[0]
    print(f"{'  '*depth}IFD @ {offset} entries={n}")
    subs = []
    for i in range(n):
        e = offset + 2 + i * 12
        tag, typ, cnt = struct.unpack_from("<HHI", data, e)
        val_off = e + 8
        size = TYPE_SIZE.get(typ, 1) * cnt
        if size <= 4:
            raw = data[val_off:val_off + size]
        else:
            off = struct.unpack_from("<I", data, val_off)[0]
            if tag in (273, 324) or (typ in (3, 4) and cnt > 1000):
                raw = b""
            else:
                raw = data[off:off + size]
        if tag in TAGS or (tag >= 50000):
            name = TAGS.get(tag, f"0x{tag:04X}")
            if typ in (1, 2, 7) and len(raw) < 64:
                v = raw.rstrip(b"\x00")[:40]
            elif typ in (3, 4) and raw:
                fmt = "H" if typ == 3 else "I"
                k = len(raw) // TYPE_SIZE[typ]
                v = struct.unpack_from(f"<{k}{fmt}", raw) if k <= 12 else f"({k} values)"
            elif typ in (5, 10) and raw:
                fmt = "I" if typ == 5 else "i"
                k = min(len(raw) // 4, 24)
                vals = struct.unpack_from(f"<{k}{fmt}", raw)
                v = [f"{vals[j]}/{vals[j+1]}" if typ == 5 and j+1 < k else str(vals[j])
                     for j in range(0, k, 2)] if typ == 5 else list(vals)
            else:
                v = f"type={typ} count={cnt} size={size}"
            print(f"{'  '*depth}  {tag:5d} {name:24s} {v}")
        if tag in (330, 50718):  # SubIFDs / PreviewIFD
            fmt = "I"
            if size <= 4:
                subs.append(struct.unpack_from("<I", data, val_off)[0])
            else:
                off = struct.unpack_from("<I", data, val_off)[0]
                k = size // 4
                subs.extend(struct.unpack_from(f"<{k}I", data, off))
    nxt = offset + 2 + n * 12
    if nxt + 4 <= len(data):
        nxt_off = struct.unpack_from("<I", data, nxt)[0]
        if nxt_off and depth < 2:
            read_ifd(data, nxt_off, depth + 1)
    for s in subs:
        if s > 8 and depth < 2:
            read_ifd(data, s, depth + 1)


path = sys.argv[1]
with open(path, "rb") as f:
    data = f.read()
bo = data[:2]
endian = "<" if bo == b"II" else ">"
magic = struct.unpack_from(endian + "H", data, 2)[0]
first = struct.unpack_from(endian + "I", data, 4)[0]
print(f"{path}  endian={'LE' if endian=='<' else 'BE'} magic={magic} IFD0={first}")
read_ifd(data, first)
