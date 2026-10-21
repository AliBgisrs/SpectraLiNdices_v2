"""Rebuild a file that was split into .partNN pieces, verify it, and unzip it.

    python join_parts.py                 (run it in the folder that holds the parts)
    python join_parts.py --no-extract    (only rebuild the zip)

Needs nothing but Python 3. Works on Windows, macOS and Linux.
"""
import argparse
import glob
import hashlib
import os
import re
import sys
import zipfile


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-extract", action="store_true")
    ap.add_argument("--dir", default=os.path.dirname(os.path.abspath(__file__)))
    a = ap.parse_args()
    os.chdir(a.dir)
    parts = sorted(glob.glob("*.part[0-9][0-9]"))
    if not parts:
        sys.exit("No *.partNN files found next to this script.")
    base = re.sub(r"\.part\d\d$", "", parts[0])
    expected = [f"{base}.part{i:02d}" for i in range(1, len(parts) + 1)]
    missing = [p for p in expected if p not in parts]
    if missing:
        sys.exit("Missing parts (download them first): " + ", ".join(missing))

    # optional checksum verification of the parts
    sums = {}
    if os.path.exists("SHA256SUMS.txt"):
        for line in open("SHA256SUMS.txt", encoding="utf-8"):
            if line.strip():
                h, name = line.split(None, 1)
                sums[name.strip()] = h
    print(f"Joining {len(parts)} parts -> {base}")
    with open(base, "wb") as out:
        for p in expected:
            if p in sums and sha256(p) != sums[p]:
                out.close()
                os.remove(base)
                sys.exit(f"Checksum mismatch for {p}: the download is corrupt, download that part again.")
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 22), b""):
                    out.write(chunk)
            print(f"  + {p}")
    if not zipfile.is_zipfile(base):
        sys.exit("The joined file is not a valid zip - a part is missing or damaged.")
    print(f"OK: {base} ({os.path.getsize(base) / 2**30:.2f} GB)")
    if a.no_extract:
        return
    print("Extracting ...")
    with zipfile.ZipFile(base) as z:
        z.extractall(".")
    print("Done. You can delete the .partNN files now.")


if __name__ == "__main__":
    main()
