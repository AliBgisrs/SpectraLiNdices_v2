"""PlotSpectra local server: serves the HTML app and runs analyses on this machine.

Run:  python app/server.py      (opens http://127.0.0.1:8765 in the default browser)
"""
from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import os
import secrets
import subprocess
import sys
import threading
import traceback
import urllib.parse
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

from engine import gdd as gdd_mod, indices as ix_mod, io, pipeline  # noqa: E402
from engine.gpu import get_backend  # noqa: E402
from engine.meta import APP_NAME, CREDIT, VERSION  # noqa: E402

JOBS: dict[str, pipeline.Job] = {}
ALLOWED_ROOTS: set[str] = set()
TOKEN = secrets.token_urlsafe(24)  # per-launch secret embedded in the served page; required by every API call
PORT = 8765


def _canon(p):
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def _inside_allowed(p):
    cp = _canon(p)
    for r in ALLOWED_ROOTS:
        try:
            if os.path.commonpath([cp, r]) == r:
                return True
        except ValueError:  # different drives
            continue
    return False


def _clean(o):
    """Recursively replace NaN/inf with None so the JSON is valid for the browser."""
    if isinstance(o, float):
        return o if np.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.floating):
        return float(o) if np.isfinite(o) else None
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.ndarray):
        return _clean(o.tolist())
    return o


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def _browse(kind, title, filetypes):
    """Native file/folder dialog in a child process (tkinter must own its main thread)."""
    code = (
        "import sys,json,tkinter as tk\nfrom tkinter import filedialog as fd\n"
        "r=tk.Tk();r.withdraw();r.attributes('-topmost',True)\n"
        f"k={kind!r};t={title!r};ft={filetypes!r}\n"
        "p=fd.askdirectory(title=t) if k=='folder' else (fd.asksaveasfilename(title=t) if k=='save' else fd.askopenfilename(title=t,filetypes=ft))\n"
        "print(json.dumps(p or ''))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=600)
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:
        return ""


FILETYPES = {
    "boundary": [("Vector files", "*.shp *.geojson *.json *.gpkg *.kml *.zip"), ("All files", "*.*")],
    "table": [("Tables", "*.xlsx *.xls *.csv *.txt"), ("All files", "*.*")],
    "raster": [("GeoTIFF", "*.tif *.tiff"), ("All files", "*.*")],
    "any": [("All files", "*.*")],
}


class Handler(BaseHTTPRequestHandler):
    server_version = "PlotSpectra/1.0"

    def log_message(self, fmt, *args):  # quieter console
        if "/api/job/" not in str(args[0] if args else ""):
            sys.stderr.write("%s\n" % (fmt % args))

    def _send(self, code, body, ctype="application/json"):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(_clean(body), default=_json_default, allow_nan=False).encode("utf-8") \
                if ctype == "application/json" else str(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    def _trusted_host(self):
        """Block DNS-rebinding: only accept requests addressed to this machine's loopback name."""
        host = (self.headers.get("Host") or "").lower()
        return host in (f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"[::1]:{PORT}")

    def _authorised(self, query=None):
        """Every API call must carry the per-launch token (header, or ?t= for <img>/download links)."""
        tok = self.headers.get("X-PS-Token") or ((query or {}).get("t") or [""])[0]
        return hmac.compare_digest(tok, TOKEN)

    def do_HEAD(self):
        if not self._trusted_host():
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(200 if urllib.parse.urlparse(self.path).path in ("/", "/index.html") else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if not self._trusted_host():
            return self._send(403, {"error": "forbidden host"})
        try:
            if u.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    html = f.read().replace(b"__PS_TOKEN__", TOKEN.encode())
                return self._send(200, html, "text/html; charset=utf-8")
            if u.path.startswith("/api/") and not self._authorised(q):
                return self._send(403, {"error": "missing or invalid token - reload the page"})
            if u.path == "/api/system":
                bk = get_backend(prefer_gpu=True)
                return self._send(200, {"compute": bk.info(), "python": sys.version.split()[0],
                                        "app": APP_NAME, "version": VERSION, "credit": CREDIT,
                                        "cpu_count": os.cpu_count(), "cwd": ROOT,
                                        "default_input": os.path.join(ROOT, "inputFolder"),
                                        "default_output": os.path.join(ROOT, "outputs")})
            if u.path == "/api/indices":
                return self._send(200, ix_mod.load_catalog(pipeline.DOCS_DIR))
            if u.path == "/api/crops":
                return self._send(200, gdd_mod.CROPS)
            if u.path.startswith("/api/job/"):
                jid = u.path.rsplit("/", 1)[-1]
                job = JOBS.get(jid)
                if not job:
                    return self._send(404, {"error": "unknown job"})
                since = int(q.get("since", ["0"])[0])
                return self._send(200, {"status": job.status, "progress": job.progress, "stage": job.stage,
                                        "logs": job.logs[since:], "n_logs": len(job.logs),
                                        "result": job.result, "error": job.error})
            if u.path == "/api/file":
                p = os.path.abspath(q.get("path", [""])[0])
                if not _inside_allowed(p) or not os.path.isfile(p):
                    return self._send(403, {"error": "not allowed"})
                ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
                with open(p, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                if q.get("download"):
                    self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(p)}"')
                self.end_headers()
                self.wfile.write(data)
                return
            return self._send(404, {"error": "not found"})
        except Exception as e:
            traceback.print_exc()
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})

    # ------------------------------------------------------------------ POST
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if not self._trusted_host() or not self._authorised():
            return self._send(403, {"error": "forbidden"})
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._send(415, {"error": "JSON required"})
        try:
            body = self._body()
            if u.path == "/api/browse":
                p = _browse(body.get("kind", "file"), body.get("title", "Select"),
                            FILETYPES.get(body.get("filter", "any"), FILETYPES["any"]))
                if body.get("filter") == "boundary" and p and p.lower().endswith(("gdbtable", "gdbtablx", ".gdb")):
                    p = p[: p.lower().rfind(".gdb") + 4]  # a file picked inside a .gdb -> the geodatabase
                return self._send(200, {"path": p})
            if u.path == "/api/scan":
                res = io.scan_input_folder(body["folder"])
                if res["multispectral"]:
                    try:
                        res["center_latlon"] = io.raster_center_lonlat(res["multispectral"])
                    except Exception:
                        res["center_latlon"] = None
                    d = io.image_datetime(res["multispectral"])
                    res["image_datetime"] = d.isoformat() if d else None
                return self._send(200, res)
            if u.path == "/api/boundary":
                return self._send(200, io.inspect_boundary(body["path"], body.get("layer")))
            if u.path == "/api/table":
                return self._send(200, io.inspect_table(body["path"], body.get("sheet")))
            if u.path == "/api/validate_formula":
                try:
                    return self._send(200, {"ok": True, "bands": ix_mod.validate_formula(body["formula"])})
                except Exception as e:
                    return self._send(200, {"ok": False, "error": str(e)})
            if u.path == "/api/sam_status":
                from engine import sam_count
                return self._send(200, sam_count.status())
            if u.path == "/api/sam_install":
                from engine import sam_count
                logs = []
                try:
                    st = sam_count.install(log=logs.append)
                    return self._send(200, {"ok": st["ready"], "status": st, "log": logs})
                except Exception as e:
                    return self._send(200, {"ok": False, "error": f"{type(e).__name__}: {e}", "log": logs})
            if u.path == "/api/gpu_refresh":
                bk = get_backend(prefer_gpu=True, refresh=True)
                return self._send(200, bk.info())
            if u.path == "/api/run":
                cfg = body
                if not cfg.get("output_folder"):
                    cfg["output_folder"] = os.path.join(ROOT, "outputs")
                os.makedirs(cfg["output_folder"], exist_ok=True)
                ALLOWED_ROOTS.add(_canon(cfg["output_folder"]))
                job = pipeline.Job(cfg)
                jid = uuid.uuid4().hex[:10]
                JOBS[jid] = job
                with open(os.path.join(cfg["output_folder"], "last_config.json"), "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2)

                def work():
                    job.status = "running"
                    try:
                        job.result = pipeline.run(job)
                        job.status = "done"
                    except pipeline.Cancelled:
                        job.status, job.error = "cancelled", "Cancelled by user"
                        job.log("Cancelled")
                    except Exception as e:
                        job.status, job.error = "error", f"{type(e).__name__}: {e}"
                        job.log(traceback.format_exc())
                threading.Thread(target=work, daemon=True).start()
                return self._send(200, {"job": jid})
            if u.path.startswith("/api/cancel/"):
                job = JOBS.get(u.path.rsplit("/", 1)[-1])
                if job:
                    job.cancel.set()
                return self._send(200, {"ok": True})
            if u.path == "/api/open_folder":
                p = body.get("path")
                if p and os.path.isdir(p) and _inside_allowed(p):
                    if sys.platform.startswith("win"):
                        os.startfile(p)  # noqa
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", p])
                    else:
                        subprocess.Popen(["xdg-open", p])
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            traceback.print_exc()
            return self._send(500, {"error": f"{type(e).__name__}: {e}"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8765)))
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    global PORT
    PORT = a.port
    ThreadingHTTPServer.allow_reuse_address = False  # never silently share the port with a stale instance
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    except OSError:
        sys.exit(f"Port {a.port} is already in use (another PlotSpectra running?). Close it or use --port <n>.")
    url = f"http://127.0.0.1:{a.port}/"
    print(f"PlotSpectra running at {url}  (Ctrl+C to stop)")
    if not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
