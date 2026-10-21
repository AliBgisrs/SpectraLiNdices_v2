"""Optional full-resolution GeoTIFF export of index / height / thermal layers (tiled, GPU-aware)."""
from __future__ import annotations

import os

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.windows import Window

from . import indices as ix_mod
from .meta import CREDIT_LONG

NODATA = -10000.0


def export(job, out_dir, layers, ms_info, dsm_info, band_map, sel, dtm_model, veg_thr, t_scale, t_offset, bk,
           tile=2048, t_range=(-30.0, 80.0), veg_min_h=None):
    by = {s["abbr"]: s for s in sel}
    want_idx = [l for l in layers if l in by]
    special = [l for l in layers if l in ("PlantHeight_m", "DTM_m", "Thermal_C", "VegetationMask")]
    if ("PlantHeight_m" in special or "DTM_m" in special) and (dtm_model is None or dsm_info is None):
        job.log("  height/DTM export skipped: no DSM")
        special = [s for s in special if s not in ("PlantHeight_m", "DTM_m")]
    if "Thermal_C" in special and "T" not in band_map:
        special.remove("Thermal_C")
    names = want_idx + special
    if not names:
        return []
    paths = {}
    with rasterio.open(ms_info["path"]) as ms:
        dsm_ds = rasterio.open(dsm_info["path"]) if dsm_info and dtm_model is not None else None
        prof = ms.profile.copy()
        prof.update(count=1, dtype="float32", nodata=NODATA, compress="deflate", predictor=3, tiled=True,
                    blockxsize=256, blockysize=256, BIGTIFF="IF_SAFER")
        prof.pop("photometric", None)
        dsts = {}
        for n in names:
            p = os.path.join(out_dir, f"{n}.tif")
            pr = dict(prof)
            if n == "VegetationMask":
                pr.update(dtype="uint8", nodata=255, predictor=1)
            dsts[n] = rasterio.open(p, "w", **pr)
            dsts[n].update_tags(1, DESCRIPTION=by[n]["formula"] if n in by else n)
            dsts[n].update_tags(GENERATED_BY=CREDIT_LONG, TIFFTAG_ARTIST=CREDIT_LONG)
            paths[n] = p
        used = {"R", "NIR", "A"} | {b for n in want_idx for b in by[n]["bands"]}
        if "Thermal_C" in special:
            used.add("T")
        need = [k for k in ("B", "G", "R", "RE", "NIR", "T", "A") if k in band_map and k in used]
        tiles = [(r, c) for r in range(0, ms.height, tile) for c in range(0, ms.width, tile)]
        for ti, (r0, c0) in enumerate(tiles):
            win = Window(c0, r0, min(tile, ms.width - c0), min(tile, ms.height - r0))
            h, w = int(win.height), int(win.width)
            arr = ms.read([band_map[k] for k in need], window=win).astype(np.float32)
            bands = dict(zip(need, arr))
            valid = np.ones((h, w), bool)
            for k in ("G", "R", "RE", "NIR", "B"):
                if k in bands:
                    if ms.nodata is not None:
                        bands[k][bands[k] == ms.nodata] = np.nan
                    valid &= np.isfinite(bands[k])
            if "A" in bands:
                valid &= bands["A"] > 0
            with np.errstate(all="ignore"):
                dev = {k: bk.asarray(bands[k]) for k in ("B", "G", "R", "RE", "NIR") if k in bands}
                sel_needed = [by[n] for n in want_idx]
                if "NDVI" not in want_idx and "NDVI" in by:
                    sel_needed.append(by["NDVI"])
                out = ix_mod.compute_indices(bk, dev, sel_needed)
                ndvi = bk.to_numpy(out["NDVI"]) if "NDVI" in out else (bands["NIR"] - bands["R"]) / (bands["NIR"] + bands["R"])
            for n in want_idx:
                a = bk.to_numpy(out[n]).astype(np.float32)
                a[~valid | ~np.isfinite(a)] = NODATA
                dsts[n].write(a, 1, window=win)
            veg = valid & (ndvi >= veg_thr)
            chm = None
            if dsm_ds is not None and (veg_min_h or "PlantHeight_m" in dsts):
                dwin = rasterio.windows.from_bounds(*rasterio.windows.bounds(win, ms.transform), transform=dsm_ds.transform)
                dsm = dsm_ds.read(1, window=dwin, out_shape=(h, w), boundless=True, fill_value=np.nan,
                                  out_dtype="float32", resampling=Resampling.bilinear)
                if dsm_ds.nodata is not None:
                    dsm[dsm == dsm_ds.nodata] = np.nan
                chm = dsm - dtm_model.sample_window(ms.window_transform(win), (h, w))
                if veg_min_h:
                    veg &= np.isfinite(chm) & (chm >= veg_min_h)
            if "VegetationMask" in dsts:
                m = np.where(valid, veg.astype(np.uint8), 255).astype(np.uint8)
                dsts["VegetationMask"].write(m, 1, window=win)
            if "Thermal_C" in dsts:
                t = bands["T"] * t_scale + t_offset
                t[~valid | ~np.isfinite(t) | (t < t_range[0]) | (t > t_range[1])] = NODATA
                dsts["Thermal_C"].write(t.astype(np.float32), 1, window=win)
            if dsm_ds is not None and ("PlantHeight_m" in dsts or "DTM_m" in dsts):
                wt = ms.window_transform(win)
                dtm = dtm_model.sample_window(wt, (h, w))
                if "DTM_m" in dsts:
                    dsts["DTM_m"].write(np.where(valid, dtm, NODATA).astype(np.float32), 1, window=win)
                if "PlantHeight_m" in dsts and chm is not None:
                    out_h = np.where(~valid | ~np.isfinite(chm), NODATA, np.clip(chm, 0, None))
                    dsts["PlantHeight_m"].write(out_h.astype(np.float32), 1, window=win)
            job.set(0.94 + 0.05 * (ti + 1) / len(tiles))
        for d in dsts.values():
            d.close()
        if dsm_ds is not None:
            dsm_ds.close()
    bk.free()
    job.log(f"  {len(paths)} GeoTIFF(s) written to {out_dir}")
    return list(paths.values())
