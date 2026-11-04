#!/usr/bin/env python
"""Local web UI for the Leica-Instant-style renderer.

Security model:
  - binds to 127.0.0.1 only, single-user local tool
  - uploads land under a per-request uuid name inside a temp dir
    (client-supplied filenames are NEVER used as paths)
  - extension whitelist, hard upload size cap
  - no eval/exec, no shell; render pipeline is the audited instant/ package
"""

from __future__ import annotations

import base64
import io
import json
import sys
import tempfile
import time
import types
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

PROJECT = Path(__file__).resolve().parent.parent   # leica-instant-pc/
sys.path.insert(0, str(PROJECT))

import render as renderer                          # noqa: E402

MAX_UPLOAD = 256 * 1024 * 1024
ALLOWED_EXT = renderer.RAW_EXT | renderer.IMG_EXT
TMP_DIR = Path(tempfile.mkdtemp(prefix="instant-web-"))

INDEX_PATH = Path(__file__).resolve().parent / "index.html"


def index_html() -> bytes:
    """Read index.html per request so UI edits don't need a server restart."""
    return INDEX_PATH.read_bytes()


def baseline_preview(path: str, max_side: int = 1400) -> bytes | None:
    """Camera-like neutral preview of the source, for the "hold to compare" button.

    Browsers cannot decode DNG/ARW/CR2, so the front end has nothing to show
    for RAW inputs - the compare button used to be disabled for exactly the
    files people care about. This decodes with the camera's own white balance
    and LibRaw's auto brightness (i.e. a plain, un-styled render), scales it
    down and returns JPEG bytes.
    """
    try:
        import rawpy
        from PIL import Image

        with rawpy.imread(path) as raw:
            rgb = raw.postprocess(
                gamma=(2.222, 4.5),
                no_auto_bright=False,          # let LibRaw expose normally
                use_camera_wb=True,            # the camera's own AWB
                half_size=True,                # plenty for an on-screen compare
                output_bps=8,
                output_color=rawpy.ColorSpace.sRGB,
                demosaic_algorithm=rawpy.DemosaicAlgorithm.LINEAR,
            )
        im = Image.fromarray(rgb, "RGB")
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        return buf.getvalue()
    except Exception:  # noqa: BLE001 - preview is best-effort
        return None


def render_bytes(data: bytes, suffix: str, params: dict) -> dict:
    """Render an in-memory upload; return jpg bytes + metadata."""
    if suffix not in ALLOWED_EXT:
        raise ValueError(f"unsupported file type: {suffix or '(none)'}")

    uid = uuid.uuid4().hex
    src = TMP_DIR / f"{uid}{suffix}"
    out = TMP_DIR / f"{uid}_out.jpg"
    src.write_bytes(data)

    args = types.SimpleNamespace(
        mode=params.get("mode", "m9"),
        ev=params.get("ev", 0.0),
        strength=params.get("strength", 1.0),
        grain=params.get("grain", 1.0),
        vignette=params.get("vignette", 0.0),
        denoise=params.get("denoise", 0.60),
        wb=params.get("wb", "warm"),
        scene=params.get("scene", "common"),
        cct=params.get("cct"),        # None -> derive from AsShotNeutral
        lut=None,
        tiff=False,
        no_normalize=False,
        quiet=True,
    )
    try:
        t0 = time.perf_counter()
        info = renderer.render_one(src, out, args)
        elapsed = time.perf_counter() - t0
        jpg = out.read_bytes()
        base = baseline_preview(str(src)) if params.get("want_base") else None
    finally:
        for p in (src, out):
            p.unlink(missing_ok=True)

    result = {
        "jpg_b64": base64.b64encode(jpg).decode("ascii"),
        "camera": info.get("camera", ""),
        "mode": info.get("mode", ""),
        "lux_index": info.get("lux_index"),
        "cct": info.get("cct"),
        "scene": info.get("scene", ""),
        "elapsed_s": round(elapsed, 2),
        "size_kb": round(len(jpg) / 1024, 1),
    }
    if base:
        result["baseline_b64"] = base64.b64encode(base).decode("ascii")
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "InstantLocal/0.1"

    # -- helpers -----------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    # -- routes ------------------------------------------------------------
    def do_GET(self):  # noqa: N802
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            try:
                self._send(200, index_html(), "text/html; charset=utf-8")
            except OSError as e:
                self._send_json(500, {"error": f"index.html unreadable: {e}"})
        elif path == "/healthz":
            self._send_json(200, {"ok": True})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        path = urlsplit(self.path).path
        if path != "/render":
            self._send_json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_UPLOAD:
            self._send_json(413, {"error": f"upload must be 1B..{MAX_UPLOAD}B"})
            return

        q = parse_qs(urlsplit(self.path).query)
        params = {
            "mode": q.get("mode", ["m9"])[0],
            "ev": float(q.get("ev", ["0"])[0]),
            "strength": float(q.get("strength", ["1"])[0]),
            "grain": float(q.get("grain", ["1"])[0]),
            "vignette": float(q.get("vignette", ["0"])[0]),
            "denoise": float(q.get("denoise", ["0.6"])[0]),
            "wb": q.get("wb", ["warm"])[0],
            "scene": q.get("scene", ["common"])[0],
        }
        # empty / absent cct -> let render_one derive it from the DNG
        _cct = q.get("cct", [""])[0]
        params["cct"] = float(_cct) if _cct else None
        params["want_base"] = q.get("base", ["0"])[0] == "1"

        # client encodes the filename (non-ISO-8859-1 is illegal in headers)
        fname = unquote(self.headers.get("X-Filename", "upload"))
        suffix = Path(fname).suffix.lower()
        data = self.rfile.read(length)

        try:
            result = render_bytes(data, suffix, params)
            self._send_json(200, result)
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 - surface render errors to UI
            self._send_json(500, {"error": f"render failed: {e}"})

    def log_message(self, fmt, *args):  # quiet access log
        pass


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"instant web UI  ->  http://127.0.0.1:{port}   (Ctrl+C to stop)")
    print(f"temp dir        ->  {TMP_DIR}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
