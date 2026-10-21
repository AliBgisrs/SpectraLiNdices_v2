"""Publication figures (PNG, 350 dpi by default).

Uses matplotlib's object-oriented API (Figure + Agg canvas) so it is safe to call from a
worker thread. Every function returns a gallery record {path, title, group, var}.
"""
from __future__ import annotations

import math
import os

import matplotlib

matplotlib.use("Agg")
import numpy as np
import pandas as pd
import rasterio
from matplotlib import colors as mcolors
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import FancyArrow, Rectangle
import matplotlib.patheffects  # noqa: E402,F401  (registers path effects in worker processes)
from rasterio.enums import Resampling

from . import indices as ix_mod
from .meta import APP_NAME, AUTHOR, CREDIT, CREDIT_LONG, EMAIL, FIG_FOLDERS, VERSION

RC = {"font.size": 9, "axes.titlesize": 10, "axes.labelsize": 9, "xtick.labelsize": 8, "ytick.labelsize": 8,
      "legend.fontsize": 8, "font.family": "DejaVu Sans", "axes.spines.top": False, "axes.spines.right": False}
matplotlib.rcParams.update(RC)

DIVERGING_LIKE = {"NDVI", "NDRE", "GNDVI", "OSAVI", "SAVI", "MSAVI2", "EVI2", "WDRVI", "NGRDI", "RDVI", "MTVI2",
                  "MCARI2", "NLI", "NDREI_RE_R"}
UNITS = {"PlantHeight_m": "Plant height (m)", "Thermal_C": "Temperature (°C)", "Tcanopy_C": "Canopy temperature (°C)",
         "Tsoil_C": "Soil temperature (°C)", "DSM_m": "Elevation (m)", "DTM_m": "Elevation (m)"}
MAX_BOX = 40   # plots per distribution page
MAX_RANK = 50  # plots per ranking page
ALPHA = 0.05
PALETTE = "viridis"  # "viridis" (colour-blind safe, default) | "cividis" | "classic" (RdYlGn for greenness indices)


def cmap_for(var):
    if var in ("Thermal_C", "Tcanopy_C", "Tsoil_C") or var.startswith(("Tcanopy", "Tsoil")):
        return "inferno"
    if var in ("DTM_m", "DSM_m"):
        return "terrain"
    if var.startswith("PlantHeight") or var.startswith("Canopy"):
        return "YlGn"
    if var.startswith(("CWSI", "NRCT")):
        return "YlOrRd"
    if PALETTE == "classic" and var in DIVERGING_LIKE:
        return "RdYlGn"
    return "cividis" if PALETTE == "cividis" else "viridis"


def _fixed_limits(var):
    return (0.0, 1.0) if var.startswith(("CWSI", "NRCT")) else None


def _fig(w, h):
    f = Figure(figsize=(w, h), constrained_layout=True)
    FigureCanvasAgg(f)
    return f


def _save(fig, path, dpi):
    fig.text(0.004, 0.002, CREDIT_LONG, fontsize=4.5, color="0.5", ha="left", va="bottom")
    fig.savefig(path, dpi=dpi, facecolor="white",
                metadata={"Author": f"{AUTHOR} <{EMAIL}>", "Software": f"{APP_NAME} {VERSION}", "Comment": CREDIT})
    return path


def _nice(x):
    e = 10 ** math.floor(math.log10(x))
    for m in (1, 2, 5, 10):
        if m * e >= x:
            return m * e
    return 10 * e


def _scalebar_north(ax, extent):
    l, r, b, t = extent
    width = r - l
    L = _nice(width / 5)
    x0, y0 = l + 0.05 * width, b + 0.04 * (t - b)
    h = 0.008 * (t - b)
    ax.add_patch(Rectangle((x0, y0), L / 2, h, fc="black", ec="black", lw=0.6, zorder=10))
    ax.add_patch(Rectangle((x0 + L / 2, y0), L / 2, h, fc="white", ec="black", lw=0.6, zorder=10))
    ax.text(x0 + L / 2, y0 + 2.2 * h, f"{L:g} m", ha="center", va="bottom", fontsize=7, zorder=10,
            bbox=dict(fc="white", ec="none", alpha=0.7, pad=0.5))
    xa, ya = r - 0.08 * width, t - 0.10 * (t - b)
    ax.add_patch(FancyArrow(xa, ya, 0, 0.05 * (t - b), width=0.012 * width, head_width=0.035 * width,
                            head_length=0.02 * (t - b), length_includes_head=True, fc="black", ec="white", lw=0.5,
                            zorder=10))
    ax.text(xa, ya - 0.012 * (t - b), "N", ha="center", va="top", fontsize=9, weight="bold", zorder=10,
            bbox=dict(fc="white", ec="none", alpha=0.7, pad=0.5))


def _map_axes_fmt(ax):
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_visible(True)
        s.set_linewidth(0.6)


def _robust(a, lo=2, hi=98):
    v = a[np.isfinite(a)]
    if v.size == 0:
        return 0, 1
    p = np.percentile(v, [lo, hi])
    return (float(p[0]), float(p[1])) if p[1] > p[0] else (float(v.min()), float(v.max()) + 1e-6)


# ============================================================================ display rasters
class Display:
    """Field-wide layers at reduced resolution for mapping."""

    def __init__(self, ms_info, rgb_info, band_map, sel, dtm_model, aux, veg_thr, t_scale, t_offset, bk, max_px=2400,
                 t_range=(-30.0, 80.0), veg_min_h=None):
        with rasterio.open(ms_info["path"]) as ms:
            f = max(1, int(math.ceil(max(ms.height, ms.width) / max_px)))
            h, w = ms.height // f, ms.width // f
            self.extent = (ms.bounds.left, ms.bounds.right, ms.bounds.bottom, ms.bounds.top)
            self.shape = (h, w)
            need = [k for k in ("B", "G", "R", "RE", "NIR", "T", "A") if k in band_map]
            arr = ms.read([band_map[k] for k in need], out_shape=(len(need), h, w), resampling=Resampling.average)
            nod = ms.nodata
        bands = dict(zip(need, arr.astype(np.float32)))
        valid = np.ones((h, w), bool)
        for k in ("B", "G", "R", "RE", "NIR"):
            if k in bands:
                if nod is not None:
                    bands[k][bands[k] == nod] = np.nan
                valid &= np.isfinite(bands[k]) & (bands[k] > -1)
        self.valid = valid
        self.layers = {}
        with np.errstate(all="ignore"):
            dev = {k: bk.asarray(bands[k]) for k in ("B", "G", "R", "RE", "NIR") if k in bands}
            for k, v in ix_mod.compute_indices(bk, dev, sel).items():
                a = bk.to_numpy(v).astype(np.float32)
                a[~valid | ~np.isfinite(a)] = np.nan
                self.layers[k] = a
        if "T" in bands:
            t = bands["T"] * t_scale + t_offset
            t[~valid | (t < t_range[0]) | (t > t_range[1])] = np.nan
            self.layers["Thermal_C"] = t
        self.veg = valid & (self.layers.get("NDVI", np.zeros((h, w))) >= veg_thr)
        if dtm_model is not None and aux is not None:
            from rasterio.transform import Affine
            tr = Affine((self.extent[1] - self.extent[0]) / w, 0, self.extent[0], 0,
                        -(self.extent[3] - self.extent[2]) / h, self.extent[3])
            dtm = dtm_model.sample_window(tr, (h, w))
            from scipy import ndimage
            dsm_w = aux["dsm_work"]
            cols, rows = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
            xs, ys = tr * (cols, rows)
            wc, wr = ~aux["work_transform"] * (xs, ys)
            dsm = ndimage.map_coordinates(np.nan_to_num(dsm_w, nan=np.nanmedian(dsm_w)), [wr - 0.5, wc - 0.5],
                                          order=1, mode="nearest")
            inside_w = (wr >= 0) & (wc >= 0) & (wr < dsm_w.shape[0]) & (wc < dsm_w.shape[1])
            chm = np.where(valid & inside_w, dsm - dtm, np.nan)
            if veg_min_h:
                self.veg &= np.isfinite(chm) & (chm >= veg_min_h)
            self.layers["DTM_m"] = np.where(valid, dtm, np.nan)
            self.layers["DSM_m"] = np.where(valid & inside_w, dsm, np.nan)
            self.layers["PlantHeight_m"] = np.where(self.veg, np.clip(chm, 0, None), np.nan)
            self.ground_cells = (dtm_model, aux)
        self.rgb = None
        if rgb_info:
            with rasterio.open(rgb_info["path"]) as r:
                a = r.read([1, 2, 3], out_shape=(3, h, w), resampling=Resampling.average)
                self.rgb = np.moveaxis(a, 0, -1)
                if r.count >= 4:
                    alpha = r.read(4, out_shape=(h, w)) > 0
                    self.rgb = np.dstack([self.rgb, (alpha * 255).astype(np.uint8)])
        else:
            if all(k in bands for k in ("R", "G")):
                base = np.dstack([bands.get("NIR", bands["R"]), bands["R"], bands["G"]])
                lo, hi = np.nanpercentile(base, [2, 98])
                self.rgb = (np.clip((base - lo) / (hi - lo), 0, 1) * 255).astype(np.uint8)


# ============================================================================ figures
def fig_layout(d, plots, out, dpi):
    ar = (d.extent[3] - d.extent[2]) / (d.extent[1] - d.extent[0])
    W = 7.0 if ar < 1.4 else 5.5
    f = _fig(W, min(11, max(4, W * ar * 0.95)))
    ax = f.add_subplot(111)
    if d.rgb is not None:
        ax.imshow(d.rgb, extent=d.extent, interpolation="bilinear")
    plots.boundary.plot(ax=ax, color="yellow", lw=0.5)
    if len(plots) <= 400:
        fs = max(3, min(7, 300 / max(1, len(plots)) ** 0.8))
        for pid, g in zip(plots.PLOTID, plots.geometry):
            c = g.representative_point()
            ax.text(c.x, c.y, str(pid), ha="center", va="center", fontsize=fs, color="white",
                    path_effects=[matplotlib.patheffects.withStroke(linewidth=1.2, foreground="black")])
    ax.set_xlim(d.extent[:2])
    ax.set_ylim(d.extent[2:])
    _scalebar_north(ax, d.extent)
    _map_axes_fmt(ax)
    ax.set_title(f"Plot layout ({len(plots)} plots)")
    return _save(f, os.path.join(out, "00_plot_layout.png"), dpi)


def fig_raster_map(d, var, plots, out, dpi, title=None, fname=None):
    a = d.layers[var]
    ar = (d.extent[3] - d.extent[2]) / (d.extent[1] - d.extent[0])
    W = 6.5 if ar < 1.4 else 5.2
    f = _fig(W, min(11, max(4, (W - 1.0) * ar)))
    ax = f.add_subplot(111)
    if d.rgb is not None:
        gray = np.mean(d.rgb[..., :3], axis=-1)
        ax.imshow(gray, extent=d.extent, cmap="gray", alpha=0.55, interpolation="bilinear")
    vmin, vmax = _fixed_limits(var) or _robust(a)
    cm = cmap_for(var)
    im = ax.imshow(a, extent=d.extent, cmap=cm, vmin=vmin, vmax=vmax, interpolation="nearest")
    if plots is not None:
        plots.boundary.plot(ax=ax, color="black", lw=0.35)
    ax.set_xlim(d.extent[:2])
    ax.set_ylim(d.extent[2:])
    _scalebar_north(ax, d.extent)
    _map_axes_fmt(ax)
    cb = f.colorbar(im, ax=ax, shrink=0.6, pad=0.02, extend="both")
    cb.set_label(UNITS.get(var, var))
    ax.set_title(title or f"{var} map (2–98 % stretch)")
    p = os.path.join(out, fname or f"map_{var}.png")
    return _save(f, p, dpi)


def fig_plot_choropleth(d, plots, values: pd.Series, var, stat, out, dpi, letters=None):
    g = plots.copy()
    g["val"] = g["PLOTID"].map(values)
    ar = (d.extent[3] - d.extent[2]) / (d.extent[1] - d.extent[0])
    W = 6.5 if ar < 1.4 else 5.2
    f = _fig(W, min(11, max(4, (W - 1.0) * ar)))
    ax = f.add_subplot(111)
    if d.rgb is not None:
        ax.imshow(np.mean(d.rgb[..., :3], axis=-1), extent=d.extent, cmap="gray", alpha=0.6)
    vmin, vmax = _fixed_limits(var) or _robust(g["val"].to_numpy(dtype=float), 0, 100)
    norm = mcolors.Normalize(vmin, vmax)
    g.plot(column="val", ax=ax, cmap=cmap_for(var), norm=norm, edgecolor="black", linewidth=0.3,
           missing_kwds={"color": "lightgrey", "hatch": "///"})
    sm = matplotlib.cm.ScalarMappable(norm=norm, cmap=cmap_for(var))
    cb = f.colorbar(sm, ax=ax, shrink=0.6, pad=0.02)
    cb.set_label(f"{UNITS.get(var, var)} – plot {stat}")
    if len(g) <= 150:
        for pid, geom, v in zip(g.PLOTID, g.geometry, g.val):
            c = geom.representative_point()
            lab = f"{pid}" + (f"\n{letters[pid]}" if letters and pid in letters and len(letters[pid]) <= 4 else "")
            ax.text(c.x, c.y, lab, ha="center", va="center", fontsize=4.5, color="black",
                    path_effects=[matplotlib.patheffects.withStroke(linewidth=1.0, foreground="white")])
    ax.set_xlim(d.extent[:2])
    ax.set_ylim(d.extent[2:])
    _scalebar_north(ax, d.extent)
    _map_axes_fmt(ax)
    ax.set_title(f"Plot {stat} of {var}")
    return _save(f, os.path.join(out, f"plotmap_{var}_{stat}.png"), dpi)


def fig_distributions(results, var, stats_res, pair, out, dpi):
    data = {r["PLOTID"]: r["reps"].get(var) for r in results if r["reps"].get(var) is not None and len(r["reps"][var]) > 0}
    if len(data) < 2:
        return []
    order = sorted(data, key=lambda k: np.median(data[k]))
    paths = []
    pages = [order[i:i + MAX_BOX] for i in range(0, len(order), MAX_BOX)]
    for pi, page in enumerate(pages, 1):
        n = len(page)
        f = _fig(max(6.5, min(16, 0.22 * n + 2)), 4.2)
        ax = f.add_subplot(111)
        bp = ax.boxplot([data[k] for k in page], showfliers=False,
                        patch_artist=True, widths=0.65, medianprops=dict(color="black", lw=1))
        meds = np.array([np.median(data[k]) for k in page])
        normv = mcolors.Normalize(meds.min(), meds.max() if meds.max() > meds.min() else meds.min() + 1)
        cm = matplotlib.colormaps[cmap_for(var)]
        for patch, m in zip(bp["boxes"], meds):
            patch.set_facecolor(cm(normv(m)))
            patch.set_alpha(0.85)
            patch.set_linewidth(0.5)
        if pair and pair.get("letters"):
            top = ax.get_ylim()[1]
            for i, k in enumerate(page, 1):
                lt = pair["letters"].get(k, "")
                if lt:
                    ax.text(i, top, lt if len(lt) <= 6 else lt[:5] + "…", ha="center", va="bottom", fontsize=5.5,
                            rotation=90 if len(lt) > 2 else 0)
        ax.set_xticks(range(1, n + 1))
        ax.set_xticklabels([str(k) for k in page])
        ax.set_ylabel(UNITS.get(var, var))
        ax.set_xlabel("Plot (ordered by median)")
        ax.tick_params(axis="x", rotation=90, labelsize=max(4, min(8, 400 / max(n, 1))))
        sr = stats_res or {}
        sub = []
        if sr.get("welch_p") is not None:
            sub.append(f"Welch F p={_fmtp(sr['welch_p'])}")
        if sr.get("kruskal_p") is not None:
            sub.append(f"Kruskal–Wallis p={_fmtp(sr['kruskal_p'])}")
        if sr.get("epsilon_squared") is not None:
            sub.append(f"ε²={sr['epsilon_squared']:.2f}")
        ttl = f"{var}: within-plot distribution" + (f" (page {pi}/{len(pages)})" if len(pages) > 1 else "")
        ax.set_title(ttl + ("\n" + ", ".join(sub) if sub else ""), fontsize=9)
        if pair and pair.get("letters"):
            f.text(0.99, 0.005, f"Letters: {pair['posthoc']}, α={ALPHA:g}; plots sharing a letter do not differ",
                   ha="right", va="bottom", fontsize=6, color="0.3")
        paths.append(_save(f, os.path.join(out, f"box_{var}" + (f"_p{pi}" if len(pages) > 1 else "") + ".png"), dpi))
    return paths


def _fmtp(p):
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def fig_rank(center, lo, hi, var, out, dpi, letters=None, err_label="± SD", fname=None):
    """Dot chart of ranked plot values with error bars (indices have no meaningful zero, so no bars)."""
    s = center.dropna().sort_values(ascending=False)
    if s.empty:
        return []
    lo = lo.reindex(s.index) if lo is not None else None
    hi = hi.reindex(s.index) if hi is not None else None
    n = len(s)
    pages = [list(s.index)] if n > 150 else [list(s.index[i:i + MAX_RANK]) for i in range(0, n, MAX_RANK)]
    vmin, vmax = float(np.nanmin(s.values)), float(np.nanmax(s.values))
    nv = mcolors.Normalize(vmin, vmax if vmax > vmin else vmin + 1)
    cm = matplotlib.colormaps[cmap_for(var)]
    paths = []
    for pi, page in enumerate(pages, 1):
        k = len(page)
        f = _fig(7.2 if k > 30 else 5.5, 3.8)
        ax = f.add_subplot(111)
        x = np.arange(k)
        y = s.loc[page].to_numpy(float)
        if lo is not None and hi is not None:
            yerr = np.vstack([y - lo.loc[page].to_numpy(float), hi.loc[page].to_numpy(float) - y])
            yerr = np.where(np.isfinite(yerr), np.clip(yerr, 0, None), 0)
            ax.errorbar(x, y, yerr=yerr, fmt="none", ecolor="0.55", lw=0.6, capsize=1.2 if k <= 60 else 0)
        ax.scatter(x, y, c=[cm(nv(v)) for v in y], s=16 if k <= 60 else 5, edgecolor="black", lw=0.3, zorder=3)
        if k <= 60:
            ax.set_xticks(x)
            ax.set_xticklabels(page, rotation=90, fontsize=max(5, min(8, 360 / k)))
            if letters:
                top = hi.loc[page].to_numpy(float) if hi is not None else y
                for i, pid in enumerate(page):
                    lt = letters.get(pid, "")
                    if lt:
                        yy = top[i] if np.isfinite(top[i]) else y[i]
                        ax.annotate(lt if len(lt) <= 6 else lt[:5] + "…", (i, yy), xytext=(0, 2),
                                    textcoords="offset points", ha="center", va="bottom", fontsize=6,
                                    rotation=90 if len(lt) > 3 else 0)
            ax.set_xlabel("Plot")
        else:
            ax.set_xlabel("Plot rank")
        ax.set_ylabel(UNITS.get(var, var))
        ax.grid(axis="y", lw=0.3, color="0.85")
        ax.set_title(f"{var}: plot values {err_label} (ranked)" + (f" – page {pi}/{len(pages)}" if len(pages) > 1 else ""))
        fn = (fname or f"rank_{var}") + (f"_p{pi}" if len(pages) > 1 else "") + ".png"
        paths.append(_save(f, os.path.join(out, fn), dpi))
    return paths


def fig_heatmap(tab, out, dpi):
    from scipy.cluster.hierarchy import leaves_list, linkage
    t = tab.dropna(axis=1, how="all").dropna(axis=0, how="all")
    t = t.loc[:, t.std() > 0]
    if t.shape[0] < 2 or t.shape[1] < 2:
        return None
    z = (t - t.mean()) / t.std()
    z = z.fillna(0)
    try:
        ro = leaves_list(linkage(z.values, "ward")) if len(z) > 2 else np.arange(len(z))
        co = leaves_list(linkage(z.values.T, "average", metric="correlation"))
        z = z.iloc[ro, co]
    except Exception:
        pass
    n, m = z.shape
    f = _fig(max(6, 0.32 * m + 2.5), min(14, max(4, 0.12 * n + 1.8)))
    ax = f.add_subplot(111)
    im = ax.imshow(z.values, aspect="auto", cmap="RdBu_r", vmin=-2.5, vmax=2.5, interpolation="nearest")
    ax.set_xticks(range(m))
    ax.set_xticklabels(z.columns, rotation=90)
    if n <= 120:
        ax.set_yticks(range(n))
        ax.set_yticklabels(z.index, fontsize=max(3.5, min(7, 500 / n)))
    else:
        ax.set_yticks([])
        ax.set_ylabel(f"{n} plots (clustered)")
    cb = f.colorbar(im, ax=ax, shrink=0.6)
    cb.set_label("z-score of plot mean")
    ax.set_title("Plots × variables (z-scored means, hierarchical clustering)")
    return _save(f, os.path.join(out, "heatmap_plots_variables.png"), dpi)


def fig_corr(c, p, title, fname, out, dpi):
    from scipy.cluster.hierarchy import leaves_list, linkage
    from scipy.spatial.distance import squareform
    c = c.dropna(how="all").dropna(axis=1, how="all")
    if c.shape[0] < 2:
        return None
    try:
        dist = np.clip(1 - np.abs(c.fillna(0).values), 0, 2)
        np.fill_diagonal(dist, 0)
        o = leaves_list(linkage(squareform(dist, checks=False), "average"))
        c = c.iloc[o, o]
        if p is not None:
            p = p.loc[c.index, c.columns]
    except Exception:
        pass
    m = c.shape[0]
    f = _fig(max(5.5, 0.38 * m + 2), max(5, 0.38 * m + 1.6))
    ax = f.add_subplot(111)
    mask = np.triu(np.ones_like(c.values, bool), 1)
    im = ax.imshow(np.where(mask, np.nan, c.values), cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(m))
    ax.set_yticks(range(m))
    ax.set_xticklabels(c.columns, rotation=90)
    ax.set_yticklabels(c.index)
    fs = max(3.5, min(7, 120 / m))
    for i in range(m):
        for j in range(i + 1):
            v = c.values[i, j]
            if not np.isfinite(v):
                continue
            star = ""
            if p is not None and i != j:
                pv = p.values[i, j]
                star = "***" if pv < 0.001 else "**" if pv < 0.01 else "*" if pv < 0.05 else ""
            ax.text(j, i, f"{v:.2f}{star}" if m <= 30 else f"{v:.1f}", ha="center", va="center", fontsize=fs,
                    color="white" if abs(v) > 0.6 else "black")
    for s in ax.spines.values():
        s.set_visible(False)
    cb = f.colorbar(im, ax=ax, shrink=0.7)
    cb.set_label("Correlation coefficient")
    ax.set_title(title)
    return _save(f, os.path.join(out, fname), dpi)


def fig_high_corr_pairs(c, out, dpi, thr=0.9):
    m = c.where(np.triu(np.ones(c.shape, bool), 1)).stack().sort_values(key=np.abs, ascending=False)
    m = m[np.abs(m) >= 0.5].head(30)
    if m.empty:
        return None
    f = _fig(6.5, max(3, 0.18 * len(m) + 1))
    ax = f.add_subplot(111)
    labels = [f"{a} – {b}" for a, b in m.index]
    cols = ["#b2182b" if abs(v) >= thr else "#4393c3" for v in m.values]
    ax.barh(range(len(m))[::-1], m.values, color=cols)
    ax.set_yticks(range(len(m))[::-1])
    ax.set_yticklabels(labels, fontsize=7)
    ax.axvline(0, color="black", lw=0.5)
    for x in (thr, -thr):
        ax.axvline(x, color="#b2182b", lw=0.5, ls="--")
    ax.set_xlim(-1, 1)
    ax.set_xlabel("r (across plots)")
    ax.set_title(f"Most correlated variable pairs (red: |r| ≥ {thr}, redundant)")
    return _save(f, os.path.join(out, "corr_top_pairs.png"), dpi)


def fig_scatter(x, y, xl, yl, fname, out, dpi, title=None, color=None, clabel=None):
    ok = x.notna() & y.notna()
    if ok.sum() < 3:
        return None
    f = _fig(4.8, 4.0)
    ax = f.add_subplot(111)
    sc = ax.scatter(x[ok], y[ok], c=None if color is None else color[ok], cmap="viridis", s=18, edgecolor="black", lw=0.3)
    if color is not None:
        f.colorbar(sc, ax=ax, shrink=0.8).set_label(clabel or "")
    xv, yv = x[ok].to_numpy(float), y[ok].to_numpy(float)
    if np.std(xv) > 0:
        from scipy import stats as ss
        r = ss.linregress(xv, yv)
        xx = np.linspace(xv.min(), xv.max(), 50)
        ax.plot(xx, r.intercept + r.slope * xx, color="#b2182b", lw=1)
        ax.text(0.02, 0.97, f"R² = {r.rvalue ** 2:.3f}, p = {_fmtp(r.pvalue)}, n = {ok.sum()}",
                transform=ax.transAxes, va="top", fontsize=7.5, bbox=dict(fc="white", ec="0.8", pad=2))
    ax.set_xlabel(xl)
    ax.set_ylabel(yl)
    ax.set_title(title or f"{yl} vs {xl}")
    return _save(f, os.path.join(out, fname), dpi)


def fig_height_hist(d, results, out, dpi):
    if "PlantHeight_m" not in d.layers:
        return None
    a = d.layers["PlantHeight_m"]
    v = a[np.isfinite(a)]
    if v.size < 10:
        return None
    f = _fig(6, 3.6)
    ax = f.add_subplot(111)
    ax.hist(v, bins=80, color="#5aae61", edgecolor="white", lw=0.2)
    for q, ls in ((50, "-"), (95, "--")):
        val = np.percentile(v, q)
        ax.axvline(val, color="black", ls=ls, lw=0.8)
        ax.text(val, ax.get_ylim()[1] * 0.95, f" p{q}={val:.2f} m", fontsize=7, va="top")
    ax.set_xlabel("Plant height (m) – vegetation pixels")
    ax.set_ylabel("Pixel count")
    ax.set_title("Field-wide canopy height distribution (DSM − generated DTM)")
    return _save(f, os.path.join(out, "height_histogram.png"), dpi)


def fig_dtm_qc(d, out, dpi):
    if "DTM_m" not in d.layers or not hasattr(d, "ground_cells"):
        return None
    model, aux = d.ground_cells
    f = _fig(10, 5.5)
    axs = [f.add_subplot(1, 3, i) for i in (1, 2, 3)]
    a = d.layers["DTM_m"]
    vmin, vmax = _robust(a, 1, 99)
    im = axs[0].imshow(a, extent=d.extent, cmap="terrain", vmin=vmin, vmax=vmax)
    f.colorbar(im, ax=axs[0], shrink=0.6).set_label("DTM elevation (m)")
    axs[0].set_title("Generated DTM")
    axs[1].imshow(aux["soil"], extent=d.extent, cmap="Greys_r")
    axs[1].set_title(f"Bare-soil pixels (NDVI < {model.info['ndvi_soil_threshold']:.2f})")
    ch = d.layers.get("PlantHeight_m")
    if ch is not None:
        im2 = axs[2].imshow(ch, extent=d.extent, cmap="YlGn", vmin=0, vmax=_robust(ch, 1, 99)[1])
        f.colorbar(im2, ax=axs[2], shrink=0.6).set_label("Plant height (m)")
        axs[2].set_title("Canopy height model")
    for ax in axs:
        _map_axes_fmt(ax)
    f.suptitle(f"DTM quality control – {model.info['ground_cells']} ground cells "
               f"({model.info['ground_cell_fraction']:.0%}) at {model.info['cell_m']:.2f} m", fontsize=10)
    return _save(f, os.path.join(out, "qc_dtm.png"), dpi)


def _hillshade(z, extent, az=315, alt=45):
    from matplotlib.colors import LightSource
    h, w = z.shape
    dx = (extent[1] - extent[0]) / w
    dy = (extent[3] - extent[2]) / h
    zz = np.nan_to_num(z, nan=np.nanmedian(z))
    return LightSource(azdeg=az, altdeg=alt).hillshade(zz, vert_exag=2, dx=dx, dy=dy)


def fig_height_panel(d, plots, out, dpi):
    """Plant-height derivation in one view: DSM (what the drone saw) -> DTM (bare earth) -> CHM = DSM - DTM."""
    if "DSM_m" not in d.layers or "DTM_m" not in d.layers:
        return None
    dsm, dtm, chm = d.layers["DSM_m"], d.layers["DTM_m"], d.layers.get("PlantHeight_m")
    both = np.concatenate([dsm[np.isfinite(dsm)], dtm[np.isfinite(dtm)]])
    vmin, vmax = np.percentile(both, [1, 99])
    ar = (d.extent[3] - d.extent[2]) / (d.extent[1] - d.extent[0])
    W = 10.5
    f = _fig(W, min(11, max(4.5, (W / 3 - 0.6) * ar + 0.9)))
    axs = [f.add_subplot(1, 3, i) for i in (1, 2, 3)]
    for ax, z, ttl in ((axs[0], dsm, "DSM – surface model (Pix4D)"), (axs[1], dtm, "DTM – generated bare earth")):
        ax.imshow(_hillshade(z, d.extent), extent=d.extent, cmap="gray", interpolation="bilinear")
        im = ax.imshow(z, extent=d.extent, cmap="terrain", vmin=vmin, vmax=vmax, alpha=0.75, interpolation="nearest")
        ax.set_title(ttl)
    cb = f.colorbar(im, ax=axs[:2], shrink=0.55, pad=0.02, location="bottom")
    cb.set_label("Elevation (m, shared scale)")
    if chm is not None:
        im2 = axs[2].imshow(chm, extent=d.extent, cmap="YlGn", vmin=0, vmax=_robust(chm, 1, 99)[1], interpolation="nearest")
        f.colorbar(im2, ax=axs[2], shrink=0.55, pad=0.02, location="bottom").set_label("Plant height (m)")
        axs[2].set_title("CHM = DSM − DTM (vegetation pixels)")
    for ax in axs:
        if plots is not None:
            plots.boundary.plot(ax=ax, color="black", lw=0.25)
        ax.set_xlim(d.extent[:2])
        ax.set_ylim(d.extent[2:])
        _map_axes_fmt(ax)
    _scalebar_north(axs[0], d.extent)
    f.suptitle("Plant height analysis: surface model → terrain model → canopy height", fontsize=10)
    return _save(f, os.path.join(out, "height_panel_dsm_dtm_chm.png"), dpi)


def fig_gdd(weather, gdd_df, crop_info, out, dpi):
    if weather is None or gdd_df is None or gdd_df.empty or "Sowing_date" not in gdd_df:
        return None
    from . import gdd as g
    sd = pd.to_datetime(gdd_df["Sowing_date"]).min()
    img = pd.to_datetime(gdd_df["Image_date"].iloc[0])
    dates = pd.to_datetime(weather["dates"])
    tb, tu = gdd_df["Tbase_C"].iloc[0], gdd_df["Tupper_C"].iloc[0]
    daily = g.daily_gdd(weather["tmax"], weather["tmin"], tb, tu, gdd_df["Method"].iloc[0])
    sel = (dates >= sd) & (dates <= img)
    f = _fig(7, 4)
    ax = f.add_subplot(111)
    ax2 = ax.twinx()
    ax2.spines["right"].set_visible(True)
    ax.fill_between(dates[sel], np.array(weather["tmin"])[sel], np.array(weather["tmax"])[sel], color="#fdae61", alpha=0.4,
                    label="Daily Tmin–Tmax")
    ax.axhline(tb, color="#2166ac", lw=0.7, ls="--", label=f"Tbase {tb:g} °C")
    ax2.plot(dates[sel], np.nancumsum(daily[sel]), color="#1b7837", lw=1.6, label="Cumulative GDD")
    ax.axvline(img, color="black", lw=0.8)
    ax2.annotate(f"Image: {img:%Y-%m-%d}\nGDD = {np.nansum(daily[sel]):.0f} °C·d", xy=(img, np.nansum(daily[sel])),
                 xytext=(-110, -10), textcoords="offset points", fontsize=7.5, arrowprops=dict(arrowstyle="->", lw=0.6))
    ax.set_ylabel("Air temperature (°C)")
    ax2.set_ylabel("Cumulative GDD (°C·d)")
    ax.set_title(f"Growing degree days – {gdd_df['Crop'].iloc[0]} (sown {sd:%Y-%m-%d})")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", frameon=False)
    f.text(0.99, 0.005, weather["source"], ha="right", fontsize=6, color="0.4")
    return _save(f, os.path.join(out, "gdd_curve.png"), dpi)


def fig_field_corr(merged, metric_cols, trait_cols, out, dpi):
    paths = []
    m = merged[merged.get("Match_status", "matched") == "matched"] if "Match_status" in merged else merged
    traits = [t for t in trait_cols if t in m and pd.api.types.is_numeric_dtype(m[t]) and m[t].notna().sum() >= 4]
    min_n = max(4, int(0.5 * len(m)))  # ignore sparsely populated metrics (e.g. stand count only in open plots)
    mets = [c for c in metric_cols if c in m and pd.api.types.is_numeric_dtype(m[c]) and m[c].notna().sum() >= min_n
            and m[c].std() > 0 and not c.startswith(("Plot", "Objects", "Tair", "VPD", "Idso", "SinglePlant"))]
    if not traits or not mets:
        return paths
    R = pd.DataFrame({t: [m[[t, c]].dropna().corr().iloc[0, 1] if m[[t, c]].dropna().shape[0] > 3 else np.nan
                          for c in mets] for t in traits}, index=mets)
    top = R.abs().max(axis=1).sort_values(ascending=False).head(25).index
    Rt = R.loc[top]
    f = _fig(max(5, 0.6 * len(traits) + 3.5), max(4, 0.22 * len(top) + 1.5))
    ax = f.add_subplot(111)
    im = ax.imshow(Rt.values, cmap="RdBu_r", vmin=-1, vmax=1, aspect="auto")
    ax.set_xticks(range(len(traits)))
    ax.set_xticklabels(traits, rotation=45, ha="right")
    ax.set_yticks(range(len(top)))
    ax.set_yticklabels(top, fontsize=7)
    for i in range(Rt.shape[0]):
        for j in range(Rt.shape[1]):
            v = Rt.values[i, j]
            if np.isfinite(v):
                ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=6, color="white" if abs(v) > 0.6 else "black")
    f.colorbar(im, ax=ax, shrink=0.7).set_label("Pearson r")
    ax.set_title("Field traits vs image-derived plot metrics (top 25)")
    paths.append(_save(f, os.path.join(out, "field_traits_correlation.png"), dpi))
    for t in traits[:8]:
        best = R[t].abs().idxmax()
        p = fig_scatter(m[best], m[t], best, t, f"field_{_slug(t)}_vs_{_slug(best)}.png", out, dpi,
                        title=f"{t} vs best image predictor ({best})")
        if p:
            paths.append(p)
    return paths


def _slug(s):
    return "".join(ch if ch.isalnum() else "_" for ch in str(s))[:40]


# ============================================================================ orchestrator
def _thumb(path, root, width=600):
    """Gallery thumbnail under <root>/_thumbs/, mirroring the figure's sub-folder."""
    try:
        from PIL import Image
        rel = os.path.relpath(path, root)
        tp = os.path.join(root, "_thumbs", os.path.splitext(rel)[0] + ".jpg")
        os.makedirs(os.path.dirname(tp), exist_ok=True)
        im = Image.open(path)
        im.thumbnail((width, width * 3))
        im.convert("RGB").save(tp, "JPEG", quality=82)
        return tp
    except Exception:
        return None


def _task(fn_name, args, kwargs, palette, alpha=0.05, root=None, subdir=None):
    """Worker entry point (also used in-process). Renders into <root>, then files the PNG(s) under
    <root>/<subdir>/. Returns ([(png, thumb), ...], error)."""
    global PALETTE, ALPHA
    PALETTE, ALPHA = palette, alpha
    try:
        r = globals()[fn_name](*args, **kwargs)
        paths = [p for p in (r if isinstance(r, list) else [r]) if p]
        out = []
        for p in paths:
            if root and subdir:
                dest = os.path.join(root, subdir, os.path.relpath(p, root))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                os.replace(p, dest)
                p = dest
            out.append((p, _thumb(p, root or os.path.dirname(p))))
        return out, None
    except Exception as e:
        return [], f"{fn_name}: {type(e).__name__}: {e}"


GROUP_ORDER = ["Overview", "Maps", "Plot maps", "Distributions & tests", "Rankings", "Plant height", "Thermal",
               "Canopy structure", "Correlation", "Correlation (per plot)", "GDD", "Field data"]


def make_all(job, out, plots, tables, var_names, sel, stat_res, pair_res, corr, adv_df, thermal_df, gdd_df, merged,
             ms_info, rgb_info, band_map, dtm_model, aux, veg_thr, t_scale, t_offset, bk, cfg, results, group_series,
             dpi=350, t_range=(-30.0, 80.0)):
    import json
    import types
    global PALETTE
    PALETTE = cfg.get("palette", "viridis")
    groups = set(cfg.get("groups") or ["layout", "maps", "plotmaps", "distributions", "ranking", "heatmap",
                                       "correlation", "height", "thermal", "canopy", "gdd", "field", "qc"])
    d = Display(ms_info, rgb_info, band_map, sel, dtm_model, aux, veg_thr, t_scale, t_offset, bk, t_range=t_range,
                veg_min_h=cfg.get("veg_min_h"))
    global ALPHA
    ALPHA = float(cfg.get("alpha") or 0.05)
    gray = None if d.rgb is None else np.mean(d.rgb[..., :3], axis=-1, keepdims=True).astype(np.uint8)

    def lite(var=None):  # small picklable stand-in for Display (one layer + grey backdrop)
        return types.SimpleNamespace(extent=d.extent, rgb=gray, layers={var: d.layers[var]} if var in d.layers else {})

    plots_l = plots[["PLOTID", "geometry"]].copy()
    map_vars = cfg.get("map_vars") or ([s["abbr"] for s in sel if s["abbr"] in d.layers and not s.get("hidden")] +
                                       [v for v in ("Thermal_C",) if v in d.layers])  # height layers go to the Plant height group
    fig_vars = cfg.get("fig_vars")
    mean, sd = tables["mean"], tables["std"]
    key_vars = [v for v in var_names if v in mean and mean[v].notna().any() and v not in ("DSM_m", "DTM_m", "Tsoil_C")
                and (not fig_vars or v in fig_vars)]
    recs, tasks = [], []

    def collect(paths, err, title, group, var):
        if err:
            job.log(f"  figure failed: {err}")
        for p, t in paths:
            ttl = title
            if len(paths) > 1:
                ttl = f"{title} – {os.path.basename(p).rsplit('_', 1)[-1].replace('.png', '')}"
            if group == "Field data":
                ttl = os.path.basename(p).replace(".png", "").replace("_", " ")
            recs.append({"path": p, "thumb": t, "title": ttl, "group": group, "var": var})

    def T(fn, args, title, group, var=None, **kw):
        tasks.append((fn.__name__, args, kw, title, group, var))

    # figures needing the full display object: render here
    if "layout" in groups:
        collect(*_task("fig_layout", (d, plots_l, out, dpi), {}, PALETTE, ALPHA, out, FIG_FOLDERS["Overview"]),
                "Plot layout", "Overview", None)
    if "qc" in groups:
        collect(*_task("fig_dtm_qc", (d, out, dpi), {}, PALETTE, ALPHA, out, FIG_FOLDERS["Overview"]),
                "DTM quality control", "Overview", None)
    if "height" in groups:
        collect(*_task("fig_height_hist", (d, None, out, dpi), {}, PALETTE, ALPHA, out, FIG_FOLDERS["Plant height"]),
                "Canopy height histogram", "Plant height", None)
        collect(*_task("fig_height_panel", (d, plots_l, out, dpi), {}, PALETTE, ALPHA, out, FIG_FOLDERS["Plant height"]),
                "DSM → DTM → plant height", "Plant height", None)
        for v in ("DSM_m", "DTM_m", "PlantHeight_m"):
            if v in d.layers:
                T(fig_raster_map, (lite(v), v, plots_l, out, dpi), f"{v} – map", "Plant height", v)

    if "maps" in groups:
        for v in map_vars:
            if v in d.layers:
                T(fig_raster_map, (lite(v), v, plots_l, out, dpi), f"{v} – raster map", "Maps", v)
    for v in key_vars:
        p = pair_res.get(v) if pair_res else None
        letters = p.get("letters") if p else None
        if "plotmaps" in groups:
            T(fig_plot_choropleth, (lite(), plots_l, mean[v], v, "mean", out, dpi, letters), f"{v} – plot means map",
              "Plot maps", v)
        if "distributions" in groups and results and v in (results[0].get("reps") or {}):
            rl = [{"PLOTID": r["PLOTID"], "reps": {v: r["reps"].get(v)}} for r in results]
            T(fig_distributions, (rl, v, stat_res.get(v), p, out, dpi), f"{v} – within-plot distributions",
              "Distributions & tests", v)
        if "ranking" in groups:
            summ = p.get("summary") if p else None
            if summ is not None and "CI95_low" in summ:
                sm = summ.set_index("Unit")
                c, lo, hi, lab = sm["mean"], sm["CI95_low"], sm["CI95_high"], "mean ± 95 % CI of sub-samples"
            else:
                c, lo, hi, lab = mean[v], mean[v] - sd[v], mean[v] + sd[v], "mean ± SD of pixels"
            T(fig_rank, (c, lo, hi, v, out, dpi, letters, lab), f"{v} – ranked plots", "Rankings", v)
    if "heatmap" in groups:
        T(fig_heatmap, (mean[key_vars], out, dpi), "Plots × variables heatmap", "Overview")
    if "correlation" in groups and corr:
        T(fig_corr, (corr["across"], corr["across_p"], "Correlation across plots (plot means)", "corr_across_plots.png",
                     out, dpi), "Correlation – across plots", "Correlation")
        T(fig_high_corr_pairs, (corr["across"], out, dpi), "Most correlated pairs", "Correlation")
        if corr.get("within_mean") is not None:
            T(fig_corr, (corr["within_mean"], None, "Mean within-plot pixel correlation (Fisher-z average)",
                         "corr_within_plots_mean.png", out, dpi), "Correlation – within plots (mean)", "Correlation")
        if cfg.get("per_plot_corr") and corr.get("within"):
            for pid, m in list(corr["within"].items())[:300]:
                T(fig_corr, (m, None, f"Within-plot correlation – plot {pid}", f"corr_plot_{_slug(pid)}.png", out, dpi),
                  f"Correlation – plot {pid}", "Correlation (per plot)")
    if "thermal" in groups and "Tcanopy_C" in mean and "NDVI" in mean:
        T(fig_scatter, (mean["NDVI"], mean["Tcanopy_C"], "NDVI (plot mean)", "Canopy temperature (°C)",
                        "thermal_vs_ndvi.png", out, dpi), "Canopy temperature vs NDVI", "Thermal",
          color=mean["PlantHeight_m"] if "PlantHeight_m" in mean else None, clabel="Plant height (m)")
        if thermal_df is not None:
            for col in ("CWSI_empirical_mean", "CTD_C", "NRCT_mean", "CWSI_Idso"):
                if col in thermal_df and thermal_df[col].notna().any():
                    T(fig_rank, (thermal_df[col], None, None, col, out, dpi), f"{col} – ranked plots", "Thermal", col)
                    T(fig_plot_choropleth, (lite(), plots_l, thermal_df[col], col, "value", out, dpi),
                      f"{col} – plot map", "Thermal", col)
    if "canopy" in groups and adv_df is not None and not adv_df.empty:
        for col in ("CanopyCover_pct", "CanopyVolume_m3", "HarvestableArea_ratio", "StandCount", "PlantDensity_per_m2",
                    "CanopyLength_m", "RowCoverage_pct", "MeanPlantLength_m"):
            if col in adv_df and pd.api.types.is_numeric_dtype(adv_df[col]) and adv_df[col].notna().any():
                T(fig_plot_choropleth, (lite(), plots_l, adv_df[col], col, "value", out, dpi), f"{col} – plot map",
                  "Canopy structure", col)
        if "CanopyCover_pct" in adv_df and "PlantHeight_m" in mean:
            T(fig_scatter, (adv_df["CanopyCover_pct"], mean["PlantHeight_m"], "Canopy cover (%)", "Plant height (m)",
                            "cover_vs_height.png", out, dpi), "Canopy cover vs height", "Canopy structure")
    qc_dir = os.path.join(out, FIG_FOLDERS["Canopy structure"], "sam_qc")
    if os.path.isdir(qc_dir):
        for fn in sorted(os.listdir(qc_dir)):
            if fn.endswith(".png"):
                p = os.path.join(qc_dir, fn)
                recs.append({"path": p, "thumb": _thumb(p, out), "title": fn[:-4].replace("_", " ") + " (MobileSAM QC)",
                             "group": "Canopy structure", "var": "StandCount"})
    if "gdd" in groups and getattr(job, "weather", None) is not None and gdd_df is not None:
        T(fig_gdd, (job.weather, gdd_df, None, out, dpi), "Growing degree days", "GDD")
    if "field" in groups and merged is not None:
        metric_cols = [c for c in merged.columns if c.endswith("_mean")] + \
            (list(adv_df.columns) if adv_df is not None else []) + ["PlantHeight_p95_m"]
        if thermal_df is not None:
            metric_cols += list(thermal_df.columns)
        T(fig_field_corr, (merged, metric_cols, merged.attrs.get("field_cols") or [], out, dpi), "Field data", "Field data")

    # matplotlib rendering is GIL-bound -> a process pool gives real parallelism; sequential fallback
    n_workers = int(cfg.get("workers") or max(1, min(6, (os.cpu_count() or 2) - 1)))
    pending = list(tasks)
    if n_workers > 1 and len(tasks) > 4:
        try:
            import multiprocessing as mp
            from concurrent.futures import ProcessPoolExecutor, as_completed
            with ProcessPoolExecutor(max_workers=n_workers, mp_context=mp.get_context("spawn")) as ex:
                futs = {ex.submit(_task, fn, a, k, PALETTE, ALPHA, out, FIG_FOLDERS.get(g, "99_other")): i
                        for i, (fn, a, k, t, g, v) in enumerate(tasks)}
                finished = set()
                for f in as_completed(futs):
                    if job.cancel.is_set():
                        ex.shutdown(cancel_futures=True)
                        break
                    i = futs[f]
                    collect(*f.result(), *tasks[i][3:])
                    finished.add(i)
                    job.set(0.82 + 0.12 * len(finished) / len(tasks))
            pending = [t for i, t in enumerate(tasks) if i not in finished]
            job.log(f"  rendered {len(finished)} figure jobs on {n_workers} processes")
        except Exception as e:
            job.log(f"  parallel rendering unavailable ({type(e).__name__}: {e}); continuing sequentially")
            pending = [t for i, t in enumerate(tasks) if i not in locals().get("finished", set())]
    for n_done, (fn, a, k, t, g, v) in enumerate(pending, 1):
        collect(*_task(fn, a, k, PALETTE, ALPHA, out, FIG_FOLDERS.get(g, "99_other")), t, g, v)
        job.set(0.82 + 0.12 * n_done / len(pending))

    recs.sort(key=lambda r: (GROUP_ORDER.index(r["group"]) if r["group"] in GROUP_ORDER else 99, r["var"] or "", r["path"]))
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"generated_by": CREDIT_LONG, "dpi": dpi, "folders": FIG_FOLDERS, "figures": recs}, fh, indent=1)
    by_folder = {}
    for r in recs:
        by_folder[os.path.relpath(os.path.dirname(r["path"]), out)] = by_folder.get(os.path.relpath(os.path.dirname(r["path"]), out), 0) + 1
    job.log(f"  {len(recs)} figures saved at {dpi} dpi in {len(by_folder)} sub-folders: " +
            ", ".join(f"{k} ({n})" for k, n in sorted(by_folder.items())))
    return recs
