"""Bare-earth DTM generation from the existing DSM + the multispectral orthomosaic.

Method (spectral ground filtering + surface interpolation):
  1. Read DSM and Red/NIR at a working resolution (default 0.10 m) with area averaging.
  2. Classify bare-soil pixels: NDVI below an Otsu threshold (clamped to [0.15, 0.45]),
     eroded by one pixel to drop mixed canopy-edge pixels.
  3. Aggregate soil elevations into ground cells (default 1.0 m) using a low percentile
     (default 25th) - robust against residues, clods and leftover leaves.
  4. Two-pass outlier rejection on the cell surface (residual > 2.5 robust sigma removed).
  5. Fill cells without soil (closed canopy) by Delaunay-linear interpolation, nearest
     neighbour outside the convex hull, then Gaussian smoothing.
  6. The coarse DTM is sampled bilinearly at full resolution on demand (per plot window).
Plant height (CHM) = DSM - DTM, evaluated on vegetation pixels only.
"""
from __future__ import annotations

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import Affine
from scipy import interpolate, ndimage


def otsu_threshold(values, bins=256):
    v = values[np.isfinite(values)]
    if v.size == 0:
        return np.nan
    hist, edges = np.histogram(v, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    m0 = np.cumsum(hist * centers) / np.maximum(w0, 1)
    m1 = (np.sum(hist * centers) - np.cumsum(hist * centers)) / np.maximum(w1, 1)
    between = w0 * w1 * (m0 - m1) ** 2
    return float(centers[np.argmax(between)])


class DTMModel:
    def __init__(self, grid, transform, crs, info):
        self.grid = grid.astype(np.float32)      # coarse DTM (rows, cols)
        self.transform = transform               # affine of the coarse grid
        self.crs = crs
        self.info = info

    def sample(self, xs, ys):
        """Bilinear DTM at map coordinates (arrays of equal shape)."""
        inv = ~self.transform
        cols, rows = inv * (xs, ys)
        # pixel-is-area: value at cell centre -> shift by 0.5
        return ndimage.map_coordinates(self.grid, [rows - 0.5, cols - 0.5], order=1, mode="nearest").astype(np.float32)

    def sample_window(self, win_transform, shape):
        h, w = shape
        cols, rows = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
        xs, ys = win_transform * (cols, rows)
        return self.sample(xs, ys)


def build_dtm(ms_path, dsm_path, band_map, work_res=0.10, cell=1.0, pct=25.0,
              ndvi_thr=None, log=print):
    with rasterio.open(dsm_path) as dsm_ds, rasterio.open(ms_path) as ms:
        f = max(1, int(round(work_res / dsm_ds.res[0])))
        h, w = dsm_ds.height // f, dsm_ds.width // f
        dsm = dsm_ds.read(1, out_shape=(h, w), resampling=Resampling.average, masked=True).filled(np.nan)
        nod = ms.nodata
        same_grid = (ms.crs == dsm_ds.crs and ms.transform.almost_equals(dsm_ds.transform)
                     and ms.width == dsm_ds.width and ms.height == dsm_ds.height)
        if same_grid:
            src = ms
        else:  # different extent / grid / CRS: resample the orthomosaic onto the DSM grid first
            from rasterio.vrt import WarpedVRT
            src = WarpedVRT(ms, crs=dsm_ds.crs, transform=dsm_ds.transform, width=dsm_ds.width,
                            height=dsm_ds.height, resampling=Resampling.bilinear, src_nodata=nod, nodata=nod)
            log("DTM: orthomosaic and DSM grids differ - orthomosaic resampled onto the DSM grid")
        red = src.read(band_map["R"], out_shape=(h, w), resampling=Resampling.average).astype(np.float32)
        nir = src.read(band_map["NIR"], out_shape=(h, w), resampling=Resampling.average).astype(np.float32)
        if nod is not None:
            red[red == nod] = np.nan
            nir[nir == nod] = np.nan
        if "A" in band_map:
            alpha = src.read(band_map["A"], out_shape=(h, w), resampling=Resampling.nearest)
            red[alpha <= 0] = np.nan
        if src is not ms:
            src.close()
        dsm_nod = dsm_ds.nodata
        if dsm_nod is not None:
            dsm[dsm == dsm_nod] = np.nan
        wt = dsm_ds.transform * Affine.scale(dsm_ds.width / w, dsm_ds.height / h)
        crs = dsm_ds.crs
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = (nir - red) / (nir + red)
    valid = np.isfinite(ndvi) & np.isfinite(dsm)
    otsu = float(np.clip(otsu_threshold(ndvi[valid]), 0.15, 0.45))
    thr_used = otsu if ndvi_thr is None else float(ndvi_thr)
    soil = valid & (ndvi < thr_used)
    soil = ndimage.binary_erosion(soil, iterations=1) & valid
    log(f"DTM: working res {abs(wt.a):.3f} m, NDVI soil threshold {thr_used:.3f}, "
        f"soil fraction {soil.sum() / max(valid.sum(), 1):.1%}")

    # aggregate to ground cells
    k = max(1, int(round(cell / abs(wt.a))))
    H, W = int(np.ceil(h / k)), int(np.ceil(w / k))
    pad = np.full((H * k, W * k), np.nan, np.float32)
    pad[:h, :w] = np.where(soil, dsm, np.nan)
    blocks = pad.reshape(H, k, W, k).transpose(0, 2, 1, 3).reshape(H, W, k * k)
    counts = np.isfinite(blocks).sum(-1)
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            ground = np.nanpercentile(blocks, pct, axis=-1)
    min_n = max(3, int(0.05 * k * k))
    ground[counts < min_n] = np.nan
    gt = wt * Affine.scale(k, k)

    # outlier rejection against a smooth trend
    for _ in range(2):
        filled = _fill(ground)
        if filled is None:
            break
        trend = ndimage.median_filter(filled, size=5)
        res = ground - trend
        r = res[np.isfinite(res)]
        if r.size < 10:
            break
        mad = 1.4826 * np.median(np.abs(r - np.median(r))) + 1e-6
        bad = np.abs(res) > 2.5 * mad + 0.05
        ground[bad] = np.nan
    filled = _fill(ground)
    if filled is None:
        raise RuntimeError("DTM: not enough bare-soil pixels to build a terrain model. "
                           "Set the NDVI soil threshold manually.")
    dtm = ndimage.gaussian_filter(filled, sigma=1.0)
    n_ground = int(np.isfinite(ground).sum())
    info = {"work_res_m": float(abs(wt.a)), "cell_m": float(abs(gt.a)), "ndvi_soil_threshold": thr_used,
            "otsu_threshold": otsu,
            "ground_cells": n_ground, "total_cells": int(H * W),
            "ground_cell_fraction": n_ground / float(H * W), "percentile": pct}
    log(f"DTM: {n_ground}/{H * W} ground cells ({info['ground_cell_fraction']:.1%}), grid {H}x{W} @ {abs(gt.a):.2f} m")
    return DTMModel(dtm, gt, crs, info), {"ndvi_work": ndvi, "dsm_work": dsm, "work_transform": wt, "soil": soil}


def _fill(grid):
    ok = np.isfinite(grid)
    if ok.sum() < 3:
        return None
    rr, cc = np.nonzero(ok)
    pts = np.column_stack([rr, cc]).astype(np.float64)
    vals = grid[ok].astype(np.float64)
    R, C = np.mgrid[0:grid.shape[0], 0:grid.shape[1]]
    try:
        lin = interpolate.griddata(pts, vals, (R, C), method="linear")
    except Exception:
        lin = np.full(grid.shape, np.nan)
    near = interpolate.griddata(pts, vals, (R, C), method="nearest")
    out = np.where(np.isfinite(lin), lin, near)
    return out.astype(np.float32)


def write_dtm_fullres(model: DTMModel, ref_path, out_path, block=1024):
    """Write the DTM at the DSM's full resolution (block-wise, memory-safe)."""
    with rasterio.open(ref_path) as ref:
        prof = ref.profile.copy()
        prof.update(count=1, dtype="float32", nodata=-10000.0, compress="deflate",
                    tiled=True, blockxsize=256, blockysize=256, BIGTIFF="IF_SAFER")
        with rasterio.open(out_path, "w", **prof) as dst:
            for r0 in range(0, ref.height, block):
                for c0 in range(0, ref.width, block):
                    win = rasterio.windows.Window(c0, r0, min(block, ref.width - c0), min(block, ref.height - r0))
                    wt = ref.window_transform(win)
                    arr = model.sample_window(wt, (int(win.height), int(win.width)))
                    dst.write(arr, 1, window=win)
