"""Per-plot canopy-structure metrics computed on full-resolution plot windows.

All functions take NumPy arrays already clipped to one plot window:
  plot_mask  - pixels inside the polygon and with valid data
  veg        - vegetation pixels (subset of plot_mask)
  chm        - canopy height model (m), NaN where unknown
  ndvi       - NDVI (for peak-based counting)
  px         - pixel size in metres
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage


def canopy_metrics(plot_mask, veg, chm, px, harvest_min_h=0.0, plot_area_m2=None):
    pa = px * px
    n_plot = int(plot_mask.sum())
    area_plot = plot_area_m2 if plot_area_m2 else n_plot * pa
    n_veg = int(veg.sum())
    canopy_area = n_veg * pa
    h = chm[veg]
    h = h[np.isfinite(h)]
    h_pos = np.clip(h, 0, None)
    has_h = bool(np.isfinite(chm).any())
    volume = float(h_pos.sum() * pa) if has_h else np.nan
    if has_h:
        harvest = veg & np.isfinite(chm) & (chm >= harvest_min_h)
        harvest_area = float(harvest.sum() * pa)
    else:  # no DSM: harvestable area falls back to canopy area (no height criterion possible)
        harvest_area = canopy_area if harvest_min_h <= 0 else np.nan
    return {
        "PlotArea_m2": float(area_plot),
        "CanopyArea_m2": float(canopy_area),
        "CanopyCover_pct": 100.0 * n_veg / n_plot if n_plot else np.nan,
        "CanopyVolume_m3": volume,
        "CanopyVolume_m3_per_m2": volume / area_plot if area_plot else np.nan,
        "HarvestableArea_m2": harvest_area,
        "HarvestableArea_ratio": harvest_area / area_plot if area_plot else np.nan,
    }


def _clean(veg, px, min_area_cm2):
    min_px = max(1, int(round(min_area_cm2 / 1e4 / (px * px))))
    m = ndimage.binary_opening(veg, structure=np.ones((2, 2), bool))
    lab, n = ndimage.label(m, structure=np.ones((3, 3), bool))
    if n == 0:
        return lab, n, np.array([])
    areas = ndimage.sum_labels(np.ones_like(lab), lab, index=np.arange(1, n + 1))
    keep = np.zeros(n + 1, bool)
    keep[1:] = areas >= min_px
    lab = np.where(keep[lab], lab, 0)
    lab, n = ndimage.label(lab > 0, structure=np.ones((3, 3), bool))
    areas = ndimage.sum_labels(np.ones_like(lab), lab, index=np.arange(1, n + 1)) if n else np.array([])
    return lab, n, areas


def stand_objects(veg, ndvi, px, method="blob", min_area_cm2=20.0, min_spacing_cm=5.0):
    """Per-plot pass: plant objects (connected components) and, for 'peaks', NDVI peak count."""
    lab, n, areas = _clean(veg, px, min_area_cm2)
    n_peaks = None
    if method == "peaks" and n:
        from skimage.feature import peak_local_max
        sig = max(1.0, (min_spacing_cm / 100 / px) / 4)
        sm = ndimage.gaussian_filter(np.where(np.isfinite(ndvi), ndvi, 0), sig)
        md = max(1, int(round(min_spacing_cm / 100 / px)))
        n_peaks = int(len(peak_local_max(sm, min_distance=md, labels=(lab > 0).astype(int), exclude_border=False)))
    return lab, np.asarray(areas, np.float32), n_peaks


def single_plant_area_px(stand_records, px, user_cm2=None, open_cover=35.0):
    """Field-wide single-plant area: user value, else the median object area in open plots
    (cover < open_cover %), else the median of the smaller half of all objects."""
    if user_cm2:
        return float(user_cm2) / 1e4 / (px * px), "user"
    open_areas = [r["areas_px"] for r in stand_records if r and r["cover"] < open_cover and len(r["areas_px"])]
    if open_areas:
        a = np.concatenate(open_areas)
        return float(np.median(a)), f"median object in {len(open_areas)} open plots (<{open_cover:.0f}% cover)"
    allv = np.concatenate([r["areas_px"] for r in stand_records if r and len(r["areas_px"])] or [np.array([1.0])])
    small = np.sort(allv)[: max(1, len(allv) // 2)]
    return float(np.median(small)), "median of smaller half of objects (no open plots - low confidence)"


def stand_count(rec, px, single_px, method="blob", closed_cover=60.0, force=False, sam_split=True):
    """Second pass: count plants of one plot from its objects and the field single-plant area."""
    pa_cm2 = px * px * 1e4
    areas, cover, area_m2 = rec["areas_px"], rec["cover"], rec["area_m2"]
    out = {"StandCount": np.nan, "PlantDensity_per_m2": np.nan, "Objects_n": int(len(areas)),
           "MeanObjectArea_cm2": float(np.mean(areas) * pa_cm2) if len(areas) else np.nan,
           "StandCount_reliability": "ok"}
    if cover > closed_cover and not force:
        out["StandCount_reliability"] = f"not estimable: canopy closed ({cover:.0f}% cover)"
        return out
    if method == "sam" and rec.get("n_sam") is not None:
        info = rec.get("sam_info") or {}
        out["SAM_instances"] = int(rec["n_sam"])
        out["SAM_prompts"] = info.get("prompts")
        out["SAM_mean_mask_area_cm2"] = info.get("mean_mask_area_cm2")
        if sam_split and len(areas):  # a mask much larger than a single plant is a clump -> split by area
            count = int(np.sum(np.where(areas > 1.8 * single_px, np.round(areas / max(single_px, 1.0)), 1)))
        else:
            count = int(rec["n_sam"])
    elif method == "peaks" and rec.get("n_peaks") is not None:
        count = rec["n_peaks"]
    else:
        count = int(np.sum(np.maximum(1, np.round(areas / max(single_px, 1.0))))) if len(areas) else 0
    out["StandCount"] = count
    out["PlantDensity_per_m2"] = count / area_m2 if area_m2 else np.nan
    if cover > closed_cover:
        out["StandCount_reliability"] = "low: canopy closed (forced)"
    elif cover > 35:
        out["StandCount_reliability"] = "moderate: plants touching" + (" (SAM separates touching plants)" if method == "sam" else "")
    return out


def plot_axis(geom):
    """Unit vector of the long side of the polygon's minimum rotated rectangle."""
    rect = geom.minimum_rotated_rectangle
    xs, ys = rect.exterior.coords.xy
    e1 = np.array([xs[1] - xs[0], ys[1] - ys[0]])
    e2 = np.array([xs[2] - xs[1], ys[2] - ys[1]])
    long_e, short_len = (e1, np.hypot(*e2)) if np.hypot(*e1) >= np.hypot(*e2) else (e2, np.hypot(*e1))
    return long_e / (np.hypot(*long_e) + 1e-12), float(np.hypot(*long_e)), float(short_len)


def length_metrics(veg, lab, win_transform, geom, px, bin_cm=5.0, gap_cm=30.0):
    """Canopy/plant length along the plot's long axis (row direction for row plots).

    CanopyLength_m      - extent of vegetation along the plot axis (1st-99th percentile)
    RowCoverage_pct     - share of the plot length (in 5 cm bins) that contains canopy
    Gaps_n / GapLength_m- canopy gaps longer than `gap_cm` along the axis
    MeanPlantLength_m   - mean major-axis length of individual plant objects
    MaxPlantLength_m    - longest plant object
    """
    out = {"CanopyLength_m": np.nan, "RowCoverage_pct": np.nan, "Gaps_n": 0, "GapLength_m": 0.0,
           "MeanPlantLength_m": np.nan, "MaxPlantLength_m": np.nan, "PlotLength_m": np.nan, "PlotWidth_m": np.nan}
    axis, L, W = plot_axis(geom)
    out["PlotLength_m"], out["PlotWidth_m"] = L, W
    rr, cc = np.nonzero(veg)
    if rr.size < 5:
        return out
    xs, ys = win_transform * (cc + 0.5, rr + 0.5)
    proj = np.asarray(xs) * axis[0] + np.asarray(ys) * axis[1]
    # plot extent along the axis from the polygon itself
    gx, gy = np.asarray(geom.exterior.coords.xy) if geom.geom_type == "Polygon" else np.asarray(geom.convex_hull.exterior.coords.xy)
    gproj = gx * axis[0] + gy * axis[1]
    lo, hi = float(gproj.min()), float(gproj.max())
    p1, p99 = np.percentile(proj, [1, 99])
    out["CanopyLength_m"] = float(p99 - p1)
    b = bin_cm / 100.0
    nb = max(1, int(np.ceil((hi - lo) / b)))
    hist = np.bincount(np.clip(((proj - lo) / b).astype(int), 0, nb - 1), minlength=nb)
    occupied = hist >= 2
    out["RowCoverage_pct"] = 100.0 * occupied.mean()
    # gaps (runs of empty bins) longer than gap_cm, ignoring the plot ends
    runs, cur = [], 0
    first, last = np.argmax(occupied), nb - 1 - np.argmax(occupied[::-1])
    for v in occupied[first:last + 1]:
        if not v:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    gaps = [r * b for r in runs if r * b >= gap_cm / 100.0]
    out["Gaps_n"], out["GapLength_m"] = len(gaps), float(sum(gaps))
    if lab is not None and lab.max() > 0:
        from skimage.measure import regionprops
        lens = [r.major_axis_length * px for r in regionprops(lab) if r.area >= 3]
        if lens:
            out["MeanPlantLength_m"] = float(np.mean(lens))
            out["MaxPlantLength_m"] = float(np.max(lens))
    return out
