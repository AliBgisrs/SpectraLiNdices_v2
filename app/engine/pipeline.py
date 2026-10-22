"""End-to-end plot analytics pipeline."""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
import traceback
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import rasterio
from scipy import ndimage
from rasterio.enums import Resampling
from rasterio.features import geometry_mask, geometry_window
from rasterio.transform import Affine

from . import advanced, dtm as dtm_mod, gdd as gdd_mod, indices as ix_mod, io, report as report_mod, stats as st, \
    thermal as th
from .gpu import get_backend
from .meta import CREDIT_LONG

STAT_KEYS = ["min", "mean", "median", "max", "std", "range"]
STAT_SHEETS = {"min": "Minimum", "mean": "Mean", "median": "Median", "max": "Maximum",
               "std": "StDev", "range": "Range"}
BAND_VARS = {"G": "Green", "R": "Red", "RE": "RedEdge", "NIR": "NIR", "B": "Blue"}
DOCS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "docs")


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, cfg):
        self.cfg = cfg
        self.progress = 0.0
        self.stage = "queued"
        self.logs = []
        self.status = "queued"
        self.result = None
        self.error = None
        self.cancel = threading.Event()
        self.t0 = time.time()
        self._lock = threading.Lock()

    def log(self, msg):
        line = f"[{time.time() - self.t0:7.1f}s] {msg}"
        with self._lock:
            self.logs.append(line)
        print(line, flush=True)

    def set(self, p=None, stage=None):
        if self.cancel.is_set():
            raise Cancelled()
        if p is not None:
            self.progress = float(p)
        if stage:
            self.stage = stage
            self.log(stage)


def _dget(d, path, default=None):
    cur = d
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur if cur is not None else default


# ============================================================================ per plot
class PlotProcessor:
    def __init__(self, ctx):
        self.ctx = ctx
        self.local = threading.local()

    def _ds(self):
        if not hasattr(self.local, "ms"):
            c = self.ctx
            self.local.ms = rasterio.open(c["ms_path"])
            self.local.dsm = rasterio.open(c["dsm_path"]) if c["dsm_path"] else None
            self.local.rgb = rasterio.open(c["rgb_path"]) if c.get("rgb_path") else None
        return self.local.ms, self.local.dsm

    def _rgb_chip(self, ms, win, h, w, bands_np):
        """uint8 RGB chip of the plot window: the RGB orthomosaic if present, else NIR/R/G false colour."""
        rgb_ds = getattr(self.local, "rgb", None)
        if rgb_ds is not None:
            rwin = rasterio.windows.from_bounds(*rasterio.windows.bounds(win, ms.transform), transform=rgb_ds.transform)
            a = rgb_ds.read([1, 2, 3], window=rwin, out_shape=(3, h, w), boundless=True, fill_value=0,
                            resampling=Resampling.bilinear)
            return np.ascontiguousarray(np.moveaxis(a, 0, -1)).astype(np.uint8)
        chans = [bands_np.get(k) for k in ("NIR", "R", "G")]
        out = np.zeros((h, w, 3), np.uint8)
        for i, ch in enumerate(chans):
            if ch is None:
                continue
            v = np.where(np.isfinite(ch), ch, 0)
            lo, hi = np.percentile(v[v > 0], [2, 98]) if (v > 0).any() else (0, 1)
            out[..., i] = (np.clip((v - lo) / max(hi - lo, 1e-6), 0, 1) * 255).astype(np.uint8)
        return out

    def close(self):
        pass

    def __call__(self, row):
        with self.ctx["bk"].device_ctx():
            return self._process(row)

    def _process(self, row):
        c = self.ctx
        bk = c["bk"]
        ms, dsm_ds = self._ds()
        geom = row.geometry
        try:
            win = geometry_window(ms, [geom], pad_x=1, pad_y=1)
        except Exception:
            return {"PLOTID": row.PLOTID, "error": "outside raster"}
        win = win.round_offsets().round_lengths()
        h, w = int(win.height), int(win.width)
        if h < 2 or w < 2:
            return {"PLOTID": row.PLOTID, "error": "plot smaller than 2 pixels"}
        wt = ms.window_transform(win)
        inside = geometry_mask([geom], out_shape=(h, w), transform=wt, invert=True, all_touched=False)
        bm = c["band_map"]
        need = [k for k in ("B", "G", "R", "RE", "NIR", "T", "A") if k in bm]
        arr = ms.read([bm[k] for k in need], window=win, boundless=True, fill_value=np.nan,
                      out_dtype="float32")  # NaN outside the raster, whatever the nodata value
        bands_np = dict(zip(need, arr))
        nod = ms.nodata
        valid = inside.copy()
        for k in ("G", "R", "RE", "NIR", "B"):
            if k in bands_np:
                b = bands_np[k]
                if nod is not None:
                    b[b == nod] = np.nan
                valid &= np.isfinite(b)
        if "A" in bands_np:
            valid &= bands_np["A"] > 0
        if valid.sum() < 4:
            return {"PLOTID": row.PLOTID, "error": "no valid pixels"}

        # ---------------- spectral indices on the compute backend
        with np.errstate(divide="ignore", invalid="ignore"):
            dev = {k: bk.asarray(bands_np[k]) for k in ("B", "G", "R", "RE", "NIR") if k in bands_np}
            idx_dev = ix_mod.compute_indices(bk, dev, c["indices"])
            ndvi_np = bk.to_numpy(idx_dev["NDVI"]) if "NDVI" in idx_dev else \
                (bands_np["NIR"] - bands_np["R"]) / (bands_np["NIR"] + bands_np["R"])
        layers = {}  # name -> (numpy array, pixel scope)
        for k, v in BAND_VARS.items():
            if k in bands_np:
                layers[v] = bands_np[k]
        for name, a in idx_dev.items():
            layers[name] = bk.to_numpy(a)
        if "T" in bands_np:
            t = bands_np["T"]
            if nod is not None:
                t = np.where(t == nod, np.nan, t)
            tc = t * c["t_scale"] + c["t_offset"]
            tc[(tc < c["t_lo"]) | (tc > c["t_hi"])] = np.nan  # physical range + field 6*MAD outlier filter
            layers["Thermal_C"] = tc

        veg = valid & np.isfinite(ndvi_np) & (ndvi_np >= c["veg_thr"])
        chm = None
        if dsm_ds is not None and c["dtm"] is not None:
            # DSM shares the orthomosaic grid in Pix4D outputs; use bounds-based window to be safe
            dwin = rasterio.windows.from_bounds(*rasterio.windows.bounds(win, ms.transform), transform=dsm_ds.transform)
            dsm = dsm_ds.read(1, window=dwin, out_shape=(h, w), boundless=True, fill_value=np.nan, out_dtype="float32",
                              resampling=Resampling.bilinear).astype(np.float32)
            if dsm_ds.nodata is not None:
                dsm[dsm == dsm_ds.nodata] = np.nan
            dtm = c["dtm"].sample_window(wt, (h, w))
            chm = dsm - dtm
            layers["DSM_m"], layers["DTM_m"] = dsm, dtm
            if c["veg_min_h"] is not None:
                veg &= np.isfinite(chm) & (chm >= c["veg_min_h"])
            layers["PlantHeight_m"] = np.clip(chm, 0, None)  # same convention as maps and GeoTIFF
        soil = valid & ~veg & np.isfinite(ndvi_np) & (ndvi_np < c["veg_thr"])
        masks = {"veg": veg, "soil": soil, "all": valid}
        if "Thermal_C" in layers:
            # stricter, eroded masks so mixed canopy/soil thermal pixels are excluded
            e = c["t_erode_px"]
            tveg = veg & (ndvi_np >= c["t_canopy_ndvi"])
            tsoil = valid & np.isfinite(ndvi_np) & (ndvi_np <= c["t_soil_ndvi"])
            if e > 0:
                tveg = ndimage.binary_erosion(tveg, iterations=e)
                tsoil = ndimage.binary_erosion(tsoil, iterations=e)
            masks["tveg"], masks["tsoil"] = tveg, tsoil
            layers["Tcanopy_C"] = layers["Thermal_C"]
            layers["Tsoil_C"] = layers["Thermal_C"]

        scope = {"PlantHeight_m": "veg", "Tcanopy_C": "tveg", "Tsoil_C": "tsoil"}
        spectral_scope = "veg" if c["spectral_pixels"] == "vegetation" else "all"
        res_stats, extra, reps, corr_sample = {}, {}, {}, {}
        k_cell = c["cell_px"]
        rr, cc = np.indices((h, w))
        cell_id = (rr // k_cell) * (w // k_cell + 1) + (cc // k_cell)
        rng = np.random.default_rng(zlib.crc32(str(row.PLOTID).encode()))
        if c["rep_unit"] == "pixels":
            # spatially thinned lattice (spacing = cell size), random offset, shared by all variables
            oy, ox = rng.integers(0, k_cell, 2)
            lattice = ((rr - oy) % k_cell == 0) & ((cc - ox) % k_cell == 0) & ndimage.binary_erosion(valid)
        for name, a in layers.items():
            sc = scope.get(name, spectral_scope if name not in ("DSM_m", "DTM_m", "Thermal_C") else "all")
            m = masks[sc] & np.isfinite(a)
            if m.sum() == 0:
                res_stats[name] = {k: np.nan for k in STAT_KEYS + ["count"]}
                continue
            v_dev = bk.compress(bk.asarray(a), bk.asarray(m.astype(np.float32)) > 0) if bk.is_gpu else a[m]
            res_stats[name] = bk.stats1d(v_dev)
            if name == "PlantHeight_m":
                p = bk.percentile(v_dev, [90, 95, 99])
                extra.update({"PlantHeight_p90_m": p[0], "PlantHeight_p95_m": p[1], "PlantHeight_p99_m": p[2]})
            # replicates for significance tests
            if c["stats_on"] and name in c["stat_vars"]:
                if c["rep_unit"] == "pixels":
                    vv = a[m & lattice]
                    if vv.size > c["n_pixels"]:
                        vv = vv[np.random.default_rng(zlib.crc32(str(row.PLOTID).encode())).permutation(vv.size)[:c["n_pixels"]]]
                    reps[name] = vv.astype(np.float32)
                else:
                    ids = cell_id[m]
                    s = np.bincount(ids, weights=a[m])
                    n = np.bincount(ids)
                    ok = n >= max(3, int(0.25 * k_cell * k_cell))
                    reps[name] = (s[ok] / n[ok]).astype(np.float32)
        # pixel sample for within-plot correlation (same pixels for every variable)
        if c["corr_on"]:
            pix = np.flatnonzero(valid.ravel())
            if pix.size > 3:
                sel = rng.choice(pix, size=min(3000, pix.size), replace=False)
                for name, a in layers.items():
                    if name in ("DSM_m", "DTM_m", "Tcanopy_C", "Tsoil_C"):
                        continue
                    corr_sample[name] = a.ravel()[sel]
        out = {"PLOTID": row.PLOTID, "stats": res_stats, "extra": extra, "reps": reps,
               "n_pixels": int(valid.sum()), "pixel_size": c["px"]}
        # within-plot correlation matrix
        if corr_sample:
            df = pd.DataFrame(corr_sample)
            out["corr"] = df.corr(method=c["corr_method"], min_periods=30)
        # thermal samples for field references
        if "Thermal_C" in layers:
            tcv = layers["Thermal_C"][masks["tveg"] & np.isfinite(layers["Thermal_C"])]
            tsv = layers["Thermal_C"][masks["tsoil"] & np.isfinite(layers["Thermal_C"])]
            out["tc_sample"] = rng.choice(tcv, min(4000, tcv.size), replace=False) if tcv.size else tcv
            out["ts_sample"] = rng.choice(tsv, min(2000, tsv.size), replace=False) if tsv.size else tsv
        # advanced structure metrics
        px = c["px"]
        area_m2 = float(geom.area)
        out["plot_area_m2"] = area_m2
        adv = {}
        if c["canopy_on"]:
            adv.update(advanced.canopy_metrics(valid, veg, chm if chm is not None else np.full((h, w), np.nan),
                                               px, c["harvest_min_h"], area_m2))
        lab = None
        if c["stand_on"]:
            cover = 100.0 * veg.sum() / max(valid.sum(), 1)
            lab, areas, n_peaks = advanced.stand_objects(veg, ndvi_np, px, c["stand_method"], c["min_area_cm2"],
                                                         c["min_spacing_cm"])
            out["stand"] = {"areas_px": areas, "n_peaks": n_peaks, "cover": cover, "area_m2": area_m2}
            if c["stand_method"] == "sam" and (cover <= c["closed_cover"] or c["stand_force"]):
                from . import sam_count
                chip = self._rgb_chip(ms, win, h, w, bands_np)
                n_sam, lab_sam, info = sam_count.count_plot(
                    chip, veg & inside, ndvi_np, px, c["sam_mode"], c["min_spacing_cm"], c["min_area_cm2"],
                    c["sam_max_area_cm2"], device_pref="cuda" if c["bk"].is_gpu or c["sam_gpu"] else "auto")
                out["stand"].update(n_sam=n_sam, sam_info=info, areas_px=info["areas_px"])  # SAM masks define plant size
                lab = lab_sam  # SAM instances also feed the plant-length metrics
                # the segmentation layer itself: canopy area from the masks, polygons, per-plot GeoTIFF
                sam_canopy = (lab_sam > 0) & inside
                out["stand"]["sam_canopy_m2"] = float(sam_canopy.sum() * px * px)
                if c["sam_layers_dir"]:
                    out["sam_polys"] = sam_count.plant_polygons(lab_sam, wt, ms.crs, row.PLOTID, px)
                    out["sam_tif"] = sam_count.write_label_tif(
                        lab_sam, wt, ms.crs, os.path.join(c["sam_layers_dir"], "plots", f"plants_plot_{row.PLOTID}.tif"))
                if c["sam_qc_dir"] and self.ctx["sam_qc_count"][0] < c["sam_qc_max"]:
                    with self.ctx["sam_qc_lock"]:
                        if self.ctx["sam_qc_count"][0] < c["sam_qc_max"]:
                            self.ctx["sam_qc_count"][0] += 1
                            qc = os.path.join(c["sam_qc_dir"], f"sam_plot_{row.PLOTID}.png")
                            sam_count.overlay(chip, lab_sam, qc, f"Plot {row.PLOTID}: {n_sam} plants (MobileSAM, "
                                                                 f"{info.get('prompts') or 'auto'} prompts)")
                            out["sam_qc"] = qc
        if c["length_on"]:
            if lab is None:
                lab, _, _ = advanced._clean(veg, px, c["min_area_cm2"])
            g = geom if geom.geom_type == "Polygon" else max(geom.geoms, key=lambda x: x.area)
            adv.update(advanced.length_metrics(veg, lab, wt, g, px, gap_cm=c["gap_cm"]))
        out["advanced"] = adv
        return out


# ============================================================================ main
def run(job: Job):
    cfg = job.cfg
    t_start = time.time()
    out_dir = cfg["output_folder"]
    os.makedirs(out_dir, exist_ok=True)
    fig_dir = os.path.join(out_dir, "figures")
    ras_dir = os.path.join(out_dir, "rasters")
    os.makedirs(fig_dir, exist_ok=True)

    job.set(0.01, "Detecting compute devices")
    bk = get_backend(prefer_gpu=_dget(cfg, "gpu.prefer", True), device_index=int(_dget(cfg, "gpu.device", 0)))
    job.log(f"Compute backend: {bk.kind} - {bk.device_name}")
    for n in bk.notes:
        job.log(f"  note: {n}")

    job.set(0.02, "Scanning input folder")
    scan = io.scan_input_folder(cfg["input_folder"])
    ms_info = scan["multispectral"]
    if _dget(cfg, "files.ms"):
        ms_info = io.raster_info(cfg["files"]["ms"])
    if ms_info is None:
        raise RuntimeError("No multispectral orthomosaic found")
    dsm_info = io.raster_info(cfg["files"]["dsm"]) if _dget(cfg, "files.dsm") else scan["dsm"]
    rgb_info = io.raster_info(cfg["files"]["rgb"]) if _dget(cfg, "files.rgb") else scan["rgb"]
    band_map = {k: int(v) for k, v in (_dget(cfg, "band_map") or scan["band_map"]).items() if v}
    job.log(f"Orthomosaic: {ms_info['name']}  bands={band_map}  GSD={ms_info['res'][0] * 100:.2f} cm")
    for wmsg in scan["warnings"]:
        job.log("  warning: " + wmsg)

    # ------------------------------------------------ indices
    catalog = ix_mod.load_catalog(DOCS_DIR)
    by = {c["abbr"]: c for c in catalog}
    chosen = cfg.get("indices") or [c["abbr"] for c in catalog if c["default"]]
    sel = []
    for a in chosen:
        if a in by:
            if all(b in band_map for b in by[a]["bands"]):
                sel.append(by[a])
            else:
                job.log(f"  skip {a}: needs bands {by[a]['bands']}")
    reserved = {s["abbr"] for s in sel} | set(by) | set(BAND_VARS.values()) | {
        "Thermal_C", "Tcanopy_C", "Tsoil_C", "PlantHeight_m", "DSM_m", "DTM_m"}
    for ci in cfg.get("custom_indices") or []:
        bands = ix_mod.validate_formula(ci["formula"])
        if ci["abbr"] in reserved:
            job.log(f"  custom index '{ci['abbr']}' renamed to '{ci['abbr']}_custom' (name already used)")
            ci = dict(ci, abbr=ci["abbr"] + "_custom")
        reserved.add(ci["abbr"])
        sel.append({"abbr": ci["abbr"], "name": ci.get("name", ci["abbr"]), "formula": ci["formula"],
                    "bands": bands, "sensitivity": "custom", "reference": "user-defined", "doi": ""})
    if not any(s["abbr"] == "NDVI" for s in sel) and "NDVI" in by:
        sel.append(dict(by["NDVI"], hidden=True))  # needed internally for vegetation masking, not reported
    job.log(f"Vegetation indices: {', '.join(s['abbr'] for s in sel if not s.get('hidden'))}")

    # ------------------------------------------------ boundaries
    job.set(0.04, "Reading plot boundaries")
    b = cfg["boundary"]
    extra_cols = [x for x in (b.get("group_col"), b.get("block_col"), b.get("sowing_col")) if x]
    plots, dups = io.read_boundaries(b["path"], b["id_col"], ms_info["crs"], b.get("layer"),
                                     float(b.get("buffer_m") or 0), extra_cols)
    if dups:
        job.log(f"  {len(dups)} duplicated plot IDs were dissolved into multipolygons")
    with rasterio.open(ms_info["path"]) as ms:
        from shapely.geometry import box
        rb = box(*ms.bounds)
    inside = plots.geometry.intersects(rb)
    if (~inside).any():
        job.log(f"  {int((~inside).sum())} plots outside the orthomosaic were skipped")
    plots = plots[inside].reset_index(drop=True)
    if len(plots) == 0:
        raise RuntimeError("No plot polygons overlap the orthomosaic (check CRS / file).")
    job.log(f"{len(plots)} plots, ID column '{b['id_col']}'")

    # ------------------------------------------------ thermal unit
    t_scale, t_offset, t_mode = 1.0, 0.0, None
    t_lo, t_hi, thermal_qc = th.VALID_C[0], th.VALID_C[1], {}
    if "T" in band_map and _dget(cfg, "thermal.enabled", True):
        with rasterio.open(ms_info["path"]) as ms:
            ov = ms.read(band_map["T"], out_shape=(max(1, ms.height // 16), max(1, ms.width // 16)))
            if ms.nodata is not None:
                ov = ov[ov != ms.nodata]
        mode = _dget(cfg, "thermal.unit", "auto")
        probe = np.array([0.0, 1.0], np.float32)
        if mode == "auto":
            _, mode = io.thermal_to_celsius(ov, "auto")
        conv, t_mode = io.thermal_to_celsius(probe, mode, _dget(cfg, "thermal.scale"), _dget(cfg, "thermal.offset"))
        t_offset, t_scale = float(conv[0]), float(conv[1] - conv[0])
        job.log(f"Thermal band unit: {t_mode} (T_C = {t_scale:g} * v + {t_offset:g})")
        tc_ov = ov.astype(np.float64) * t_scale + t_offset
        tc_ov = tc_ov[np.isfinite(tc_ov) & (tc_ov >= th.VALID_C[0]) & (tc_ov <= th.VALID_C[1])]
        if tc_ov.size:
            med = float(np.median(tc_ov))
            mad = 1.4826 * float(np.median(np.abs(tc_ov - med)))
            t_lo, t_hi = max(th.VALID_C[0], med - 6 * mad), min(th.VALID_C[1], med + 6 * mad)
            thermal_qc = {"field_median_C": med, "field_MAD_C": mad, "valid_range_C": (t_lo, t_hi),
                          "p1_C": float(np.percentile(tc_ov, 1)), "p99_C": float(np.percentile(tc_ov, 99))}
            job.log(f"Thermal outlier filter: keep {t_lo:.1f} to {t_hi:.1f} C (median {med:.1f}, 6xMAD)")
    elif "T" in band_map:
        band_map.pop("T")

    # ------------------------------------------------ DTM
    dtm_model, aux = None, None
    veg_thr = _dget(cfg, "veg.ndvi_threshold")
    if dsm_info:
        job.set(0.06, "Generating DTM from DSM + orthomosaic (bare-soil filtering)")
        dtm_model, aux = dtm_mod.build_dtm(ms_info["path"], dsm_info["path"], band_map,
                                           work_res=float(_dget(cfg, "dtm.work_res", 0.10)),
                                           cell=float(_dget(cfg, "dtm.cell", 1.0)),
                                           pct=float(_dget(cfg, "dtm.percentile", 25)),
                                           ndvi_thr=_dget(cfg, "dtm.ndvi_threshold"), log=job.log)
        if veg_thr is None:
            veg_thr = dtm_model.info["otsu_threshold"]
    if veg_thr is None:
        with rasterio.open(ms_info["path"]) as ms:
            shp = (max(1, ms.height // 8), max(1, ms.width // 8))
            r = ms.read(band_map["R"], out_shape=shp).astype(float)
            n = ms.read(band_map["NIR"], out_shape=shp).astype(float)
            bad = np.zeros(shp, bool)
            if ms.nodata is not None:
                bad |= (r == ms.nodata) | (n == ms.nodata)
            if "A" in band_map:
                bad |= ms.read(band_map["A"], out_shape=shp) <= 0
        with np.errstate(all="ignore"):
            nd = np.where(bad, np.nan, (n - r) / (n + r))
        veg_thr = float(np.clip(dtm_mod.otsu_threshold(nd[np.isfinite(nd) & (np.abs(nd) <= 1)]), 0.15, 0.45))
    veg_thr = float(veg_thr)
    job.log(f"Vegetation mask: NDVI >= {veg_thr:.3f}" +
            (f" and height >= {_dget(cfg, 'veg.min_height')} m" if _dget(cfg, "veg.min_height") else ""))

    px = float(ms_info["res"][0])
    adv = cfg.get("advanced") or {}
    stats_cfg = cfg.get("stats") or {}
    stat_vars_default = None  # filled after we know the layer names
    ctx = {
        "bk": bk, "ms_path": ms_info["path"], "dsm_path": dsm_info["path"] if dsm_info else None,
        "band_map": band_map, "indices": sel, "t_scale": t_scale, "t_offset": t_offset,
        "dtm": dtm_model, "veg_thr": veg_thr, "veg_min_h": _dget(cfg, "veg.min_height"),
        "spectral_pixels": _dget(cfg, "spectral_pixels", "all"), "px": px,
        "stats_on": _dget(stats_cfg, "enabled", True), "rep_unit": _dget(stats_cfg, "unit", "cells"),
        "cell_px": max(2, int(round(float(_dget(stats_cfg, "cell_m", 0.5)) / px))),
        "n_pixels": int(_dget(stats_cfg, "n_pixels", 200)), "stat_vars": set(),
        "corr_on": _dget(adv, "correlation.enabled", False),
        "corr_method": _dget(adv, "correlation.method", "pearson"),
        "canopy_on": _dget(adv, "canopy.enabled", False) or _dget(adv, "harvest.enabled", False),
        "harvest_min_h": float(_dget(adv, "harvest.min_height", 0.0)),
        "stand_on": _dget(adv, "stand_count.enabled", False),
        "stand_method": _dget(adv, "stand_count.method", "blob"),
        "rgb_path": rgb_info["path"] if rgb_info else None,
        "sam_mode": _dget(adv, "stand_count.sam_mode", "prompted"),
        "sam_max_area_cm2": _dget(adv, "stand_count.max_area_cm2"),
        "sam_gpu": bool(_dget(adv, "stand_count.sam_gpu", True)),
        "closed_cover": float(_dget(adv, "stand_count.closed_cover", 60.0)),
        "stand_force": bool(_dget(adv, "stand_count.force", False)),
        "sam_qc_dir": None, "sam_qc_max": int(_dget(adv, "stand_count.qc_plots") or 10**6),  # blank = every plot
        "sam_split": bool(_dget(adv, "stand_count.sam_split", True)),
        "sam_layers_dir": None, "sam_save_layers": bool(_dget(adv, "stand_count.save_layers", True)),
        "sam_qc_count": [0], "sam_qc_lock": threading.Lock(),
        "min_area_cm2": float(_dget(adv, "stand_count.min_area_cm2", 20.0)),
        "min_spacing_cm": float(_dget(adv, "stand_count.min_spacing_cm", 5.0)),
        "single_plant_cm2": _dget(adv, "stand_count.single_plant_area_cm2"),
        "length_on": _dget(adv, "length.enabled", False), "gap_cm": float(_dget(adv, "length.gap_cm", 30.0)),
        "t_lo": t_lo, "t_hi": t_hi,
        "t_canopy_ndvi": max(veg_thr, float(_dget(adv, "thermal.canopy_ndvi", 0.60))),
        "t_soil_ndvi": float(_dget(adv, "thermal.soil_ndvi", 0.20)),
        "t_erode_px": int(round(float(_dget(adv, "thermal.erode_m", 0.05)) / px)),
    }
    if ctx["single_plant_cm2"]:
        ctx["single_plant_cm2"] = float(ctx["single_plant_cm2"])
    if ctx["sam_max_area_cm2"]:
        ctx["sam_max_area_cm2"] = float(ctx["sam_max_area_cm2"])
    if ctx["stand_on"] and ctx["stand_method"] == "sam":
        from . import sam_count
        sam_st = sam_count.status()
        if not sam_st["ready"]:
            job.log("MobileSAM not installed - falling back to the object-area method (install it in the app).")
            ctx["stand_method"] = "blob"
        else:
            _, dev = sam_count.load("cuda" if bk.is_gpu or ctx["sam_gpu"] else "auto")
            job.log(f"MobileSAM loaded on {dev} ({ctx['sam_mode']} mode)")
            ctx["sam_qc_dir"] = os.path.join(fig_dir, "08_canopy_structure", "sam_qc")
            os.makedirs(ctx["sam_qc_dir"], exist_ok=True)
            if ctx["sam_save_layers"]:
                ctx["sam_layers_dir"] = os.path.join(out_dir, "segmentation")
                os.makedirs(os.path.join(ctx["sam_layers_dir"], "plots"), exist_ok=True)
    var_names = [BAND_VARS[k] for k in BAND_VARS if k in band_map] + [s["abbr"] for s in sel if not s.get("hidden")]
    if "T" in band_map:
        var_names += ["Thermal_C", "Tcanopy_C", "Tsoil_C"]
    if dtm_model is not None:
        var_names += ["PlantHeight_m", "DSM_m", "DTM_m"]
    ctx["stat_vars"] = set(stats_cfg.get("variables") or
                           [s["abbr"] for s in sel] + [v for v in ("PlantHeight_m", "Tcanopy_C", "Thermal_C") if v in var_names])

    # ------------------------------------------------ per-plot zonal processing
    job.set(0.12, f"Zonal statistics for {len(plots)} plots")
    proc = PlotProcessor(ctx)
    workers = int(cfg.get("workers") or max(1, min(8, (os.cpu_count() or 2) - 1)))
    if bk.is_gpu:
        workers = min(workers, 4)
    results, errors = [], []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(proc, r): r.PLOTID for r in plots.itertuples()}
        done = 0
        for f in as_completed(futs):
            if job.cancel.is_set():
                ex.shutdown(cancel_futures=True)
                raise Cancelled()
            try:
                r = f.result()
                if "error" in r:
                    errors.append((r["PLOTID"], r["error"]))
                else:
                    results.append(r)
            except Exception as e:
                errors.append((futs[f], f"{type(e).__name__}: {e}"))
                job.log(traceback.format_exc(limit=3))
            done += 1
            if done % max(1, len(futs) // 20) == 0 or done == len(futs):
                job.set(0.12 + 0.50 * done / len(futs))
                job.log(f"  {done}/{len(futs)} plots processed")
    bk.free()
    for pid, e in errors:
        job.log(f"  plot {pid}: {e}")
    if not results:
        raise RuntimeError("No plot produced results.")
    order = {p: i for i, p in enumerate(plots["PLOTID"])}
    results.sort(key=lambda r: order.get(r["PLOTID"], 0))
    ids = [r["PLOTID"] for r in results]

    # ------------------------------------------------ assemble tables
    job.set(0.63, "Assembling tables")
    tables = {}
    for k in STAT_KEYS + ["count"]:
        tables[k] = pd.DataFrame({v: [r["stats"].get(v, {}).get(k, np.nan) for r in results] for v in var_names},
                                 index=pd.Index(ids, name="PLOTID"))
    height_pct = pd.DataFrame([r["extra"] for r in results], index=pd.Index(ids, name="PLOTID")) \
        if any(r["extra"] for r in results) else None
    if ctx["stand_on"]:
        recs = [r.get("stand") for r in results]
        single, how = advanced.single_plant_area_px(recs, px, ctx["single_plant_cm2"])
        job.log(f"Stand count: single-plant area {single * px * px * 1e4:.1f} cm2 ({how})")
        for r, rec in zip(results, recs):
            if rec:
                r["advanced"].update(advanced.stand_count(rec, px, single, ctx["stand_method"],
                                                          ctx["closed_cover"], ctx["stand_force"], ctx["sam_split"]))
                r["advanced"]["SinglePlantArea_cm2"] = single * px * px * 1e4
                r["advanced"]["StandCount_method"] = {"sam": "MobileSAM " + ctx["sam_mode"], "peaks": "NDVI peaks",
                                                      "blob": "object area"}[ctx["stand_method"]]
                if rec.get("sam_canopy_m2") is not None:  # SAM-based canopy / harvestable area (plant masks only)
                    a_plot = rec["area_m2"] or np.nan
                    r["advanced"]["SAM_CanopyArea_m2"] = rec["sam_canopy_m2"]
                    r["advanced"]["SAM_CanopyCover_pct"] = 100.0 * rec["sam_canopy_m2"] / a_plot
                    r["advanced"]["SAM_HarvestableArea_ratio"] = rec["sam_canopy_m2"] / a_plot
    sam_layers = {}
    if ctx.get("sam_layers_dir"):
        polys = [r["sam_polys"] for r in results if r.get("sam_polys") is not None]
        if polys:
            import geopandas as gpd
            allp = gpd.GeoDataFrame(pd.concat(polys, ignore_index=True), crs=polys[0].crs)
            gpkg = os.path.join(ctx["sam_layers_dir"], "sam_plants.gpkg")
            allp.to_file(gpkg, layer="plants", driver="GPKG")
            per_plot = allp.dissolve(by="PLOTID", aggfunc={"plant_id": "count", "area_cm2": "sum"}).reset_index() \
                .rename(columns={"plant_id": "n_plants", "area_cm2": "canopy_cm2"})
            per_plot.to_file(gpkg, layer="plant_canopy_by_plot", driver="GPKG")
            sam_layers = {"gpkg": gpkg, "n_plants": int(len(allp)), "n_plots": int(allp.PLOTID.nunique()),
                          "tifs": sorted(r["sam_tif"] for r in results if r.get("sam_tif"))}
            job.log(f"Segmentation layers: {sam_layers['n_plants']} plant polygons in {sam_layers['n_plots']} plots -> {gpkg}")
            job.sam_polys = allp
    adv_df = pd.DataFrame([r["advanced"] for r in results], index=pd.Index(ids, name="PLOTID"))
    group_series, block_series = None, None
    if b.get("group_col"):
        gmap = dict(zip(plots["PLOTID"], plots[b["group_col"]].astype(str)))
        group_series = pd.Series([gmap.get(i) for i in ids], index=ids, name=b["group_col"])
    if b.get("block_col"):
        bmap = dict(zip(plots["PLOTID"], plots[b["block_col"]].astype(str)))
        block_series = pd.Series([bmap.get(i) for i in ids], index=ids, name=b["block_col"])

    # ------------------------------------------------ thermal advanced
    thermal_df, thermal_refs, weather, gdd_df = None, None, None, None
    ta = _dget(adv, "thermal.tair")
    rh = _dget(adv, "thermal.rh")
    img_dt = None
    if _dget(adv, "gdd.image_datetime"):
        try:
            img_dt = dt.datetime.fromisoformat(adv["gdd"]["image_datetime"])
        except ValueError:
            img_dt = None
    img_dt = img_dt or io.image_datetime(ms_info)
    lat, lon = _dget(adv, "gdd.lat"), _dget(adv, "gdd.lon")
    if lat in (None, "") or lon in (None, ""):
        lat, lon = io.raster_center_lonlat(ms_info)
    lat, lon = float(lat), float(lon)

    want_weather = (_dget(adv, "gdd.enabled", False) or
                    (_dget(adv, "thermal.enabled", False) and _dget(adv, "thermal.use_weather", True) and ta in (None, "")))
    if want_weather and img_dt:
        job.set(0.65, "Downloading weather data (Open-Meteo / NASA POWER)")
        sow = _dget(adv, "gdd.sowing_date")
        start = dt.date.fromisoformat(sow) if sow else img_dt.date() - dt.timedelta(days=1)
        if b.get("sowing_col"):
            try:
                sd = pd.to_datetime(plots[b["sowing_col"]], errors="coerce").dropna()
                if len(sd):
                    start = min(start, sd.min().date())
            except Exception:
                pass
        try:
            weather = gdd_mod.fetch_weather(lat, lon, start, img_dt.date(), log=job.log)
            job.log(f"Weather source: {weather['source']} ({len(weather['dates'])} days)")
        except Exception as e:
            job.log(f"Weather download failed: {e}")

    if _dget(adv, "thermal.enabled", False) and "T" in band_map:
        job.set(0.67, "Advanced thermal analysis")
        if (ta in (None, "")) and weather and img_dt:
            ta, rh2 = gdd_mod.air_at_time(weather, img_dt)
            rh = rh if rh not in (None, "") else rh2
            job.log(f"Air temperature at image time from weather data: Ta={ta} C, RH={rh} %")
        ta = None if ta in (None, "") else float(ta)
        rh = None if rh in (None, "") else float(rh)
        allc = np.concatenate([r.get("tc_sample", np.array([])) for r in results])
        thermal_refs = th.field_references(allc, ta, _dget(adv, "thermal.twet"), _dget(adv, "thermal.tdry"))
        crop = _dget(adv, "thermal.crop") or (_dget(adv, "gdd.crop") if _dget(adv, "gdd.enabled", False) else None)
        rows = []
        for r in results:
            d = th.plot_thermal(r.get("tc_sample", np.array([])), r.get("ts_sample"), thermal_refs, ta, rh, crop)
            rows.append(d)
        thermal_df = pd.DataFrame(rows, index=pd.Index(ids, name="PLOTID"))
        if thermal_refs:
            thermal_refs.update({f"QC_{k}": v for k, v in thermal_qc.items() if not isinstance(v, tuple)})
            flags = []
            if ta is not None and thermal_qc.get("p1_C", 99) < ta - 15:
                flags.append("thermal_cold_tail")
            if "CWSI_Idso_raw" in thermal_df and thermal_df["CWSI_Idso_raw"].median() < -0.1:
                flags.append("thermal_offset_suspect (Idso CWSI < 0: check sensor calibration / flight time)")
            thermal_refs["QC_flags"] = "; ".join(flags) or "none"
            for f_ in flags:
                job.log(f"  thermal QC flag: {f_}")
            job.log(f"Thermal references: Twet={thermal_refs['Twet_C']:.2f} C, Tdry={thermal_refs['Tdry_C']:.2f} C")

    if _dget(adv, "gdd.enabled", False):
        job.set(0.69, "Growing degree days")
        crop = _dget(adv, "gdd.crop", "Custom")
        sow = _dget(adv, "gdd.sowing_date")
        if weather is None or not img_dt:
            job.log("GDD skipped: weather or image date unavailable")
        elif not sow and not b.get("sowing_col"):
            job.log("GDD skipped: no sowing date given")
        else:
            rows = []
            sow_map = {}
            if b.get("sowing_col"):
                sm = pd.to_datetime(plots[b["sowing_col"]], errors="coerce")
                sow_map = {p: (d.date() if pd.notna(d) else None) for p, d in zip(plots["PLOTID"], sm)}
            for pid in ids:
                sd = sow_map.get(pid) or (dt.date.fromisoformat(sow) if sow else None)
                if sd is None:
                    rows.append({})
                    continue
                g, _ = gdd_mod.gdd_between(weather, sd, img_dt.date(), crop, _dget(adv, "gdd.tbase"), _dget(adv, "gdd.tupper"))
                g["Sowing_date"] = sd.isoformat()
                rows.append(g)
            gdd_df = pd.DataFrame(rows, index=pd.Index(ids, name="PLOTID"))
            if "GDD_cum_C" not in gdd_df or gdd_df["GDD_cum_C"].isna().all():
                job.log("GDD skipped: no valid sowing date (check the sowing-date column format or set a date)")
                gdd_df = None
        if gdd_df is not None:
            gdd_df["Crop"] = crop
            gdd_df["Image_date"] = img_dt.date().isoformat()
            gdd_df["Latitude"], gdd_df["Longitude"] = lat, lon
            gdd_df["Weather_source"] = weather["source"]
            job.log(f"GDD ({crop}): {gdd_df['GDD_cum_C'].min():.0f}-{gdd_df['GDD_cum_C'].max():.0f} C-days")

    # ------------------------------------------------ significance tests
    stat_res, pair_res = {}, {}
    if ctx["stats_on"]:
        job.set(0.71, "Statistical tests between plots")
        alpha = float(_dget(stats_cfg, "alpha", 0.05))
        padj = _dget(stats_cfg, "p_adjust", "auto")
        import warnings
        warnings.filterwarnings("ignore", category=RuntimeWarning, module=r"(scipy|numpy|engine)\..*")
        tvars = [x for x in var_names if x in ctx["stat_vars"]]
        for iv, v in enumerate(tvars):
            job.set(0.71 + 0.03 * iv / max(1, len(tvars)))
            samples = {r["PLOTID"]: r["reps"].get(v) for r in results}
            stat_res[v], pair_res[v] = st.test_plots(samples, v, alpha, padj)
            if group_series is not None:
                stat_res[v + "|groups"], pair_res[v + "|groups"] = st.test_groups(
                    tables["mean"][v], group_series, block_series, v, alpha)
        if group_series is not None:  # plot-level scalars can only be tested between treatments
            for d in (adv_df, thermal_df):
                if d is None or d.empty:
                    continue
                for col in d.select_dtypes("number").columns:
                    if d[col].notna().sum() >= 4 and d[col].std() > 0 and not col.startswith(("Plot", "Tair", "VPD", "Idso")):
                        stat_res[col + "|groups"], pair_res[col + "|groups"] = st.test_groups(
                            d[col], group_series, block_series, col, alpha)
        st.adjust_across_variables(list(stat_res.values()), alpha)
        n_sig = sum(1 for r in stat_res.values() if r.get("significant"))
        job.log(f"  {len(stat_res)} tests, {n_sig} significant at alpha={alpha}")

    # ------------------------------------------------ correlation
    corr = None
    if ctx["corr_on"]:
        job.set(0.74, "Correlation analysis")
        mats = {r["PLOTID"]: r["corr"] for r in results if r.get("corr") is not None}
        meanv = tables["mean"][[v for v in var_names if v not in ("DSM_m", "DTM_m", "Tcanopy_C", "Tsoil_C")]]
        meanv = meanv.dropna(axis=1, how="all")
        across = meanv.corr(method=ctx["corr_method"])
        from scipy import stats as ss
        pmat = pd.DataFrame(np.ones(across.shape), index=across.index, columns=across.columns)
        for i, a in enumerate(across.columns):
            for j, bcol in enumerate(across.columns):
                if j <= i:
                    continue
                x, y = meanv[a], meanv[bcol]
                ok = x.notna() & y.notna()
                if ok.sum() > 3 and x[ok].std() > 0 and y[ok].std() > 0:
                    fn = ss.spearmanr if ctx["corr_method"] == "spearman" else ss.pearsonr
                    p = fn(x[ok], y[ok])[1]
                    pmat.iloc[i, j] = pmat.iloc[j, i] = p
        # Fisher-z average of within-plot matrices
        within = None
        if mats:
            zs = [np.arctanh(np.clip(m.values, -0.999999, 0.999999)) for m in mats.values()]
            cols = next(iter(mats.values())).columns
            within = pd.DataFrame(np.tanh(np.nanmean(np.stack(zs), axis=0)), index=cols, columns=cols)
        corr = {"across": across, "across_p": pmat, "within_mean": within, "within": mats}

    # ------------------------------------------------ all predictors per plot (one wide table)
    pred_parts, pred_dict = [], []
    for k in STAT_KEYS + ["count"]:
        t = tables[k].add_suffix(f"_{k}")
        pred_parts.append(t)
        pred_dict += [(c, STAT_SHEETS.get(k, "PixelCount"), f"{report_mod.PRED_DOC[k]} of {c[: -len(k) - 1]} within the plot")
                      for c in t.columns]
    for d, sheet in ((height_pct, "PlantHeight_Pctl"), (adv_df if not adv_df.empty else None, "Canopy_Structure"),
                     (thermal_df, "Thermal_Advanced"), (gdd_df, "GDD")):
        if d is not None and not d.empty:
            dn = d.select_dtypes("number")
            pred_parts.append(dn)
            pred_dict += [(c, sheet, "") for c in dn.columns]
    predictors = pd.concat(pred_parts, axis=1)
    predictors = predictors.loc[:, ~predictors.columns.duplicated()]
    for sname, ser in (("block", block_series), ("group", group_series)):
        if ser is not None:
            predictors.insert(0, ser.name, ser.reindex(predictors.index).values)
            pred_dict.insert(0, (ser.name, "Boundary file", f"{sname} column from the plot boundaries"))
    pred_dictionary = pd.DataFrame(pred_dict, columns=["Column", "Source sheet", "Definition"]).drop_duplicates("Column")
    job.log(f"Predictor table: {predictors.shape[1]} columns for {predictors.shape[0]} plots")

    # ------------------------------------------------ field data
    merged = None
    fd = adv.get("field_data") or {}
    if fd.get("enabled") and fd.get("path"):
        job.set(0.76, "Integrating field data")
        fdf = io.read_table(fd["path"], fd.get("sheet"))
        jc = fd.get("join_col")
        if jc not in fdf.columns:
            job.log(f"Field data: join column '{jc}' not found")
        else:
            fdf[jc] = io.norm_id(fdf[jc])
            allspec = predictors.copy()
            allspec.index = allspec.index.astype(str).str.strip()
            merged = fdf.merge(allspec, left_on=jc, right_index=True, how="outer", indicator=True)
            n_match = int((merged["_merge"] == "both").sum())
            merged["_merge"] = merged["_merge"].map({"both": "matched", "left_only": "field data only",
                                                     "right_only": "image data only"})
            merged = merged.rename(columns={"_merge": "Match_status"})
            merged.attrs["field_cols"] = [c for c in fdf.select_dtypes("number").columns if c != jc]
            job.log(f"Field data: {len(fdf)} rows, {n_match} matched plots")

    # ------------------------------------------------ save excel
    job.set(0.78, "Writing Excel workbook")
    stamp = f"{dt.datetime.now():%Y%m%d_%H%M%S}"
    xlsx = os.path.join(out_dir, f"PlotStats_{stamp}.xlsx")
    meta = {
        "Generated by": CREDIT_LONG,
        "Input folder": cfg["input_folder"], "Orthomosaic": ms_info["path"],
        "DSM": dsm_info["path"] if dsm_info else "", "Boundary file": b["path"], "Plot ID column": b["id_col"],
        "Plots analysed": len(results), "Plots failed": len(errors), "Bands": json.dumps(band_map),
        "GSD (m)": px, "CRS": ms_info["crs"], "Image date/time": str(img_dt) if img_dt else "",
        "Compute backend": f"{bk.kind} - {bk.device_name}", "Vegetation NDVI threshold": veg_thr,
        "Spectral pixels used": ctx["spectral_pixels"], "Thermal unit": t_mode or "n/a",
        "DTM": json.dumps(dtm_model.info) if dtm_model else "n/a",
        "Replicate unit (stats)": f"{ctx['rep_unit']} ({_dget(stats_cfg, 'cell_m', 0.5)} m cells)" if ctx["rep_unit"] == "cells" else f"{ctx['n_pixels']} random pixels",
        "Alpha": float(_dget(stats_cfg, "alpha", 0.05)),
        "Thermal QC": json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in thermal_qc.items()}, default=str),
        "Created": dt.datetime.now().isoformat(timespec="seconds"),
    }
    report_mod.write_workbook(xlsx, tables, STAT_SHEETS, height_pct, adv_df, thermal_df, thermal_refs, gdd_df, weather,
                              stat_res, pair_res, corr, merged, sel, meta, errors, group_series, predictors)
    job.log(f"Excel saved: {xlsx}")
    pred_xlsx = os.path.join(out_dir, f"PlotPredictors_{stamp}.xlsx")
    if merged is not None:  # the predictor workbook also carries the field data when it was supplied
        fcols = [c for c in merged.columns if c not in predictors.columns and c != "Match_status"]
        jc = (adv.get("field_data") or {}).get("join_col")
        extra = merged[[jc] + [c for c in fcols if c != jc]].set_index(jc) if jc in merged else None
        if extra is not None:
            extra.index = extra.index.astype(str)
            predictors_out = predictors.copy()
            predictors_out.index = predictors_out.index.astype(str)
            predictors_out = predictors_out.join(extra.add_prefix("Field_"), how="left")
            pred_dictionary = pd.concat([pred_dictionary, pd.DataFrame(
                [("Field_" + c, "Field data file", "supplied field measurement") for c in extra.columns],
                columns=pred_dictionary.columns)], ignore_index=True)
            report_mod.write_predictors(pred_xlsx, predictors_out, pred_dictionary, meta)
        else:
            report_mod.write_predictors(pred_xlsx, predictors, pred_dictionary, meta)
    else:
        report_mod.write_predictors(pred_xlsx, predictors, pred_dictionary, meta)
    job.log(f"Predictor workbook saved: {pred_xlsx}")

    # ------------------------------------------------ figures + rasters
    job.weather = weather
    figs = []
    viz_cfg = dict(cfg.get("viz") or {}, veg_min_h=ctx["veg_min_h"], alpha=_dget(stats_cfg, "alpha", 0.05))
    if _dget(viz_cfg, "enabled", True):
        job.set(0.82, "Rendering figures (350 dpi)")
        from . import viz
        figs = viz.make_all(job, fig_dir, plots[plots.PLOTID.isin(ids)], tables, var_names, sel, stat_res, pair_res,
                            corr, adv_df, thermal_df, gdd_df, merged, ms_info, rgb_info, band_map, dtm_model, aux,
                            veg_thr, t_scale, t_offset, bk, viz_cfg, results, group_series,
                            dpi=int(_dget(viz_cfg, "dpi", 350)), t_range=(t_lo, t_hi))
    ras = []
    rcfg = cfg.get("rasters") or {}
    if rcfg.get("enabled") and rcfg.get("layers"):
        job.set(0.94, "Exporting GeoTIFF rasters")
        os.makedirs(ras_dir, exist_ok=True)
        from . import rasters
        ras = rasters.export(job, ras_dir, rcfg["layers"], ms_info, dsm_info, band_map, sel, dtm_model,
                             veg_thr, t_scale, t_offset, bk, t_range=(t_lo, t_hi), veg_min_h=ctx["veg_min_h"])

    job.result_partial = None
    job.set(1.0, f"Done in {time.time() - t_start:.0f} s")
    return {"excel": xlsx, "predictors_excel": pred_xlsx, "figures": figs, "rasters": ras, "n_plots": len(results), "errors": errors,
            "segmentation": sam_layers,
            "backend": f"{bk.kind} - {bk.device_name}", "output_folder": out_dir}
