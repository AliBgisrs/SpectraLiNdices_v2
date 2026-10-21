"""Build a GitHub-ready release of PlotSpectra.

    python tools/make_release.py [--part-mb 90] [--no-data]

Creates dist/release_v<version>/ with
  PlotSpectra_app_v<ver>.zip               the application (small, one file)
  PlotSpectra_sampledata_v<ver>.zip.partNN  the sample Pix4D data + test plots, byte-split so that
                                            every file stays under GitHub's 100 MB per-file limit
  join_parts.py / JOIN_PARTS.bat            dependency-free tools that rebuild the data zip
  SHA256SUMS.txt                            checksums of every file
  DOWNLOAD_AND_USE.md                       instructions for the people who download it
Nothing in outputs/, tests/out_*, .venv or dist is included.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
from engine.meta import CREDIT, VERSION  # noqa: E402

APP_DIRS = ["app", "docs", "tools"]
APP_FILES = ["PlotSpectra.bat", "PlotSpectra.command", "requirements.txt", "README.md", "QUICKSTART.md", ".gitignore"]
DATA_DIRS = ["inputFolder", "tests/test_plots"]
DATA_FILES = ["tests/make_test_plots.py", "tests/run_headless.py"]
SKIP = ("__pycache__", ".pyc", "last_config.json", ".venv", "out_headless", "out_ui", "dist")
STORE_EXT = (".tif", ".tiff", ".jpg", ".png", ".zip", ".gpkg")  # already compressed: store, don't deflate


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def add_tree(z, top, arc_root):
    n = 0
    for dp, dns, fs in os.walk(top):
        dns[:] = [d for d in dns if not any(s in d for s in SKIP)]
        for f in fs:
            if any(s in f for s in SKIP):
                continue
            p = os.path.join(dp, f)
            arc = os.path.join(arc_root, os.path.relpath(p, ROOT))
            comp = zipfile.ZIP_STORED if f.lower().endswith(STORE_EXT) else zipfile.ZIP_DEFLATED
            z.write(p, arc, compress_type=comp)
            n += 1
    return n


def build_zip(out, dirs, files, arc_root="PlotSpectra"):
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        for d in dirs:
            top = os.path.join(ROOT, d)
            if os.path.isdir(top):
                n += add_tree(z, top, arc_root)
        for f in files:
            p = os.path.join(ROOT, f)
            if os.path.exists(p):
                z.write(p, os.path.join(arc_root, f))
                n += 1
        for info in z.infolist():
            if info.filename.endswith(".command"):
                info.external_attr = 0o755 << 16
    return n


def split_file(path, part_bytes):
    parts = []
    with open(path, "rb") as f:
        i = 1
        while True:
            chunk = f.read(part_bytes)
            if not chunk:
                break
            pp = f"{path}.part{i:02d}"
            with open(pp, "wb") as o:
                o.write(chunk)
            parts.append(pp)
            i += 1
    os.remove(path)
    return parts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--part-mb", type=int, default=90, help="max size of one data part (GitHub limit 100 MB)")
    ap.add_argument("--no-data", action="store_true", help="application zip only")
    a = ap.parse_args()
    rel = os.path.join(ROOT, "dist", f"release_v{VERSION}")
    shutil.rmtree(rel, ignore_errors=True)
    os.makedirs(rel)

    app_zip = os.path.join(rel, f"PlotSpectra_app_v{VERSION}.zip")
    n = build_zip(app_zip, APP_DIRS, APP_FILES)
    print(f"app  : {os.path.basename(app_zip)}  {n} files  {os.path.getsize(app_zip) / 2**20:.2f} MB")

    parts = []
    data_zip = os.path.join(rel, f"PlotSpectra_sampledata_v{VERSION}.zip")
    if not a.no_data:
        n = build_zip(data_zip, DATA_DIRS, DATA_FILES)
        size = os.path.getsize(data_zip)
        print(f"data : {os.path.basename(data_zip)}  {n} files  {size / 2**20:.1f} MB")
        if size > a.part_mb * 2**20:
            parts = split_file(data_zip, a.part_mb * 2**20)
            print(f"       split into {len(parts)} parts of <= {a.part_mb} MB")
        else:
            parts = [data_zip]

    for tool in ("join_parts.py", "JOIN_PARTS.bat"):
        shutil.copy(os.path.join(ROOT, "tools", tool), rel)
    guide = open(os.path.join(ROOT, "tools", "DOWNLOAD_AND_USE.template.md"), encoding="utf-8").read()
    guide = guide.replace("{VERSION}", VERSION).replace("{CREDIT}", CREDIT).replace("{NPARTS}", str(len(parts)))
    guide = guide.replace("{PARTLIST}", "\n".join(f"- `{os.path.basename(p)}`" for p in parts) or "- (no data parts)")
    guide = guide.replace("{DATANAME}", os.path.basename(data_zip))
    with open(os.path.join(rel, "DOWNLOAD_AND_USE.md"), "w", encoding="utf-8") as f:
        f.write(guide)

    with open(os.path.join(rel, "SHA256SUMS.txt"), "w", encoding="utf-8") as f:
        for name in sorted(os.listdir(rel)):
            if name != "SHA256SUMS.txt":
                f.write(f"{sha256(os.path.join(rel, name))}  {name}\n")
    total = sum(os.path.getsize(os.path.join(rel, x)) for x in os.listdir(rel))
    print(f"\nrelease folder: {rel}\n{len(os.listdir(rel))} files, {total / 2**30:.2f} GB total; "
          f"largest file {max(os.path.getsize(os.path.join(rel, x)) for x in os.listdir(rel)) / 2**20:.1f} MB")


if __name__ == "__main__":
    main()
