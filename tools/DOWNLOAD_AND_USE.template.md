# PlotSpectra v{VERSION} – download and use

*{CREDIT}*

This release is published in pieces because GitHub does not accept single files larger than 100 MB.
You need **either** just the application, **or** the application plus the sample data.

## A. The application only (most people) – 1 file, < 1 MB

1. Download **`PlotSpectra_app_v{VERSION}.zip`** (from the repository's *Releases* page, or *Code → Download ZIP*).
2. Unzip it anywhere, e.g. `Documents\PlotSpectra`.
3. Install Python 3.10+ from https://www.python.org/downloads/ (tick *Add python.exe to PATH* on Windows).
4. Double-click **`PlotSpectra.bat`** (Windows) or **`PlotSpectra.command`** (macOS/Linux). The first start installs
   the packages (a few minutes, internet needed); the app then opens at http://127.0.0.1:8765.
5. In the app: choose your Pix4Dfields export folder, your plot-boundary file, click **Run analysis**.
   `QUICKSTART.md` inside the zip has the details.

## B. Application + sample data (1.3 GB in {NPARTS} parts)

The sample data is one zip, `{DATANAME}`, cut into equal pieces:

{PARTLIST}

1. Create an empty folder and download **every** part into it, plus `join_parts.py`, `JOIN_PARTS.bat` and
   `SHA256SUMS.txt`. All parts must be present; a single missing part makes the zip unreadable.
2. Rebuild and extract:
   - **Windows:** double-click `JOIN_PARTS.bat`.
   - **macOS / Linux:** `python3 join_parts.py`
   - without Python, on Windows: `copy /b {DATANAME}.part* {DATANAME}` then *Extract All*;
     on macOS/Linux: `cat {DATANAME}.part* > {DATANAME}` then `unzip {DATANAME}`.
   The script checks every part against `SHA256SUMS.txt` and tells you which one to re-download if a
   download was corrupted.
3. Unzip `PlotSpectra_app_v{VERSION}.zip` into the **same** `PlotSpectra` folder (merge the folders), so that you have
   `PlotSpectra\app`, `PlotSpectra\inputFolder`, `PlotSpectra\tests\test_plots`.
4. Start the app (step A-4). The sample field is pre-filled as the input folder; the test boundaries are
   `tests\test_plots\plots.gpkg` (PLOTID column `Plot_ID`, treatment column `Treatment`).

## Checking a download

`SHA256SUMS.txt` lists the SHA-256 of every file. On Windows: `certutil -hashfile <file> SHA256`;
on macOS/Linux: `shasum -a 256 <file>`.

## For the maintainer: publishing on GitHub

- Put the **source** (the content of the app zip) in the repository itself; the included `.gitignore` keeps data,
  outputs and `.venv` out.
- Attach the files of this folder to a **GitHub Release** (Releases → Draft a new release → drag the files in).
  Release assets may be up to 2 GB each, but the 90 MB parts also work with the web uploader and with slow
  connections; keep them as they are.
- Do **not** commit the data parts into the repository: they would bloat every clone forever. If you prefer the data
  under version control, use Git LFS (`git lfs track "inputFolder/*.tif"`) instead of parts.
