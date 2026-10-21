"""Create a GitHub Release and upload the files of dist/release_v<version>/ as assets.

    python tools/publish_release.py [--repo owner/name] [--tag v1.1] [--draft]

Authentication: the GitHub token is taken from Git's credential manager (the same sign-in used for
`git push`), via `git credential fill`; it is used in-process only and never printed. Set GITHUB_TOKEN
to override. Re-running updates an existing release and replaces assets of the same name.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
from engine.meta import CREDIT, VERSION  # noqa: E402

API = "https://api.github.com"


def token():
    t = os.environ.get("GITHUB_TOKEN")
    if t:
        return t
    out = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                         capture_output=True, text=True, cwd=ROOT)
    for line in out.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    sys.exit("No GitHub credential found. Run `git push` once (sign in when asked) or set GITHUB_TOKEN.")


def call(tok, method, url, data=None, ctype="application/json", raw=False):
    body = data if raw else (json.dumps(data).encode() if data is not None else None)
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "Content-Type": ctype, "User-Agent": "PlotSpectra-release"})
    try:
        with urllib.request.urlopen(req, timeout=1800) as r:
            txt = r.read().decode() or "{}"
            return r.status, json.loads(txt)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def origin_repo():
    url = subprocess.run(["git", "remote", "get-url", "origin"], capture_output=True, text=True, cwd=ROOT).stdout.strip()
    path = url.split("github.com")[-1].lstrip(":/").removesuffix(".git")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=None)
    ap.add_argument("--tag", default=f"v{VERSION}")
    ap.add_argument("--draft", action="store_true")
    a = ap.parse_args()
    repo = a.repo or origin_repo()
    rel_dir = os.path.join(ROOT, "dist", f"release_v{VERSION}")
    if not os.path.isdir(rel_dir):
        sys.exit(f"{rel_dir} not found - run tools/make_release.py first")
    tok = token()
    notes = open(os.path.join(rel_dir, "DOWNLOAD_AND_USE.md"), encoding="utf-8").read()

    st, rel = call(tok, "GET", f"{API}/repos/{repo}/releases/tags/{a.tag}")
    if st == 404:
        st, rel = call(tok, "POST", f"{API}/repos/{repo}/releases", {
            "tag_name": a.tag, "target_commitish": "main", "name": f"PlotSpectra {a.tag}",
            "body": notes, "draft": a.draft, "prerelease": False})
        if st not in (200, 201):
            sys.exit(f"Could not create release: {st} {rel.get('message')}")
        print(f"created release {a.tag}: {rel['html_url']}")
    else:
        call(tok, "PATCH", f"{API}/repos/{repo}/releases/{rel['id']}", {"body": notes, "name": f"PlotSpectra {a.tag}"})
        print(f"updating existing release {a.tag}: {rel['html_url']}")
    existing = {x["name"]: x["id"] for x in rel.get("assets", [])}
    upload_url = rel["upload_url"].split("{")[0]

    files = sorted(os.listdir(rel_dir))
    for i, name in enumerate(files, 1):
        p = os.path.join(rel_dir, name)
        if name in existing:
            call(tok, "DELETE", f"{API}/repos/{repo}/releases/assets/{existing[name]}")
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        with open(p, "rb") as f:
            data = f.read()
        for attempt in range(3):
            st, res = call(tok, "POST", upload_url + "?" + urllib.parse.urlencode({"name": name}), data, ctype, raw=True)
            if st in (200, 201):
                print(f"[{i}/{len(files)}] {name}  {len(data) / 2**20:.1f} MB  ok", flush=True)
                break
            print(f"[{i}/{len(files)}] {name}  attempt {attempt + 1} failed: {st} {res.get('message')}", flush=True)
            if st == 422:  # asset already exists (half-finished earlier upload) - remove and retry
                st2, rel2 = call(tok, "GET", f"{API}/repos/{repo}/releases/{rel['id']}")
                for x in rel2.get("assets", []):
                    if x["name"] == name:
                        call(tok, "DELETE", f"{API}/repos/{repo}/releases/assets/{x['id']}")
        else:
            sys.exit(f"giving up on {name}")
    print("done:", rel["html_url"])


if __name__ == "__main__":
    main()
