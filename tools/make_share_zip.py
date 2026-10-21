"""Build a share-ready zip of PlotSpectra (app + docs + launchers, no data, no outputs, no venv).

    python tools/make_share_zip.py        ->  dist/PlotSpectra_v<version>.zip
"""
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
from engine.meta import VERSION  # noqa: E402

INCLUDE_DIRS = ["app", "docs"]
INCLUDE_FILES = ["PlotSpectra.bat", "PlotSpectra.command", "requirements.txt", "README.md", "QUICKSTART.md"]
SKIP = ("__pycache__", ".pyc", "last_config.json")


def main():
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    out = os.path.join(ROOT, "dist", f"PlotSpectra_v{VERSION}.zip")
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for d in INCLUDE_DIRS:
            for dp, _, fs in os.walk(os.path.join(ROOT, d)):
                if any(s in dp for s in SKIP):
                    continue
                for f in fs:
                    if any(s in f for s in SKIP):
                        continue
                    p = os.path.join(dp, f)
                    z.write(p, os.path.join("PlotSpectra", os.path.relpath(p, ROOT)))
                    n += 1
        for f in INCLUDE_FILES:
            p = os.path.join(ROOT, f)
            if os.path.exists(p):
                z.write(p, os.path.join("PlotSpectra", f))
                n += 1
        # the .command file must stay executable on macOS
        for info in z.infolist():
            if info.filename.endswith(".command"):
                info.external_attr = 0o755 << 16
    print(f"{out}  ({n} files, {os.path.getsize(out) / 2**20:.2f} MB)")


if __name__ == "__main__":
    main()
