# Visualization & Statistics Specification — UAV Plot Analysis

Panel: scientific data-visualization designer + biostatistician. Target: Python backend
(numpy, scipy ≥ 1.11, pandas, matplotlib ≥ 3.8, openpyxl, rasterio, geopandas) plus an HTML front-end
that displays saved PNGs. No extra packages are required. `statsmodels` is optional and only
needed for unbalanced RCBD models (§1.5).

---

## 0. Inputs, conventions, global settings

| Item | Rule |
|---|---|
| Plot key | `PLOTID` (user-chosen column), cast to `str`, sorted naturally (`P2` < `P10`). |
| Optional design columns | `GROUP` (treatment/genotype), `BLOCK` (rep). Either may be absent. |
| Variable kinds | **Pixel variables**: indices, bands, thermal, CHM, CWSI. A per-pixel value exists for these. **Plot scalars**: stand count, canopy cover %, canopy volume, harvestable-area ratio, canopy length. **Field constants**: GDD (one value per flight, so it is excluded from all tests). |
| Plot statistics | min, mean, median, max, stdev, range per pixel variable (zonal stats). |
| Missing data | Pixels with NaN or nodata are dropped. A plot with fewer than 30 valid pixels is flagged and excluded from tests. A plot with less than 50 % valid coverage is flagged `low_coverage` in Excel. |
| Reproducibility | A single `rng = np.random.default_rng(seed)` with `seed=42` by default (user-settable). The seed and all parameters are written to the Excel `Design` sheet. |
| Output | `out/figures/*.png` (350 dpi), `out/figures/thumbs/*.webp`, `out/figures/manifest.json`, `out/stats/statistics.xlsx`, optional `out/rasters/*.tif`. |

---

## 1. STATISTICS

### 1.1 The design problem (read first)

The question "do plots differ?" can be tested two ways, and they mean different things.

* **No grouping column.** One polygon per plot, so each plot is its own "treatment" with n = 1
  experimental unit. The only replicates are **pixels within a plot**, which makes them pseudo-replicates
  (Hurlbert 1984):
  1. They are spatially autocorrelated, so neighbouring pixels are nearly duplicates.
  2. n is huge (10³–10⁵ per plot), which makes almost any difference "significant".
  3. Inference covers only *these* pixels in *this* flight. A plot difference is confounded
     with field gradients (soil, drainage, edge effects), so the test **cannot be attributed to a treatment**.
  The app therefore runs a **descriptive heterogeneity test** (Mode A). It reduces dependence by
  spatial thinning and puts effect sizes and a practical-difference threshold ahead of p-values.
* **Grouping column present.** Plots are the experimental units and plot means are the response.
  This is the **scientifically correct** analysis (Mode B), and the UI recommends it whenever a
  `GROUP` column exists.

The UI shows this sentence verbatim in Mode A:
> "Plot-vs-plot tests use within-plot pixels as samples (pseudo-replication). They describe whether pixel
> distributions differ, not whether treatments differ. Use a treatment column for inferential comparisons."

### 1.2 Mode selection

```
if GROUP column given and every group has >= 2 plots:   Mode B (plot means)   [default]
                                                        Mode A also available as "within-plot detail"
elif variable is a pixel variable:                      Mode A (thinned pixels)
else (plot scalars without grouping):                   no test; descriptives + ranking only
```

### 1.3 Mode A — thinned pixel samples, plots as groups

**Step A1 — spatial thinning (per plot, per variable).**
1. Rasterize the plot polygon (`rasterio.features.geometry_mask`, `all_touched=False`), then erode by
   1 pixel. This removes mixed edge pixels.
2. **Choose the lag `s` in pixels.** For `s="auto"` (the default), compute the pooled within-plot lag
   correlation for each variable. First centre each plot's pixels on the plot mean. Then
   `rho(h) = corr(z[:, :-h], z[:, h:])` combined with the same along rows, for h = 1…50, pooled over up
   to 50 random plots. Set `s = min{h : rho(h) <= 0.10}`, clamped to [2, 50]. As a fallback with no
   data, use `s = ceil(0.5 m / GSD)`.
3. Draw a random offset `(oy, ox)` in [0, s). Keep pixels with `(row-oy) % s == 0 and (col-ox) % s == 0`.
   This gives a regular lattice, so retained pixels are about s·GSD apart.
4. If more than `n_max` lattice pixels remain (default **n_max = 200**, user range 50–500), take a simple
   random sample of `n_max` without replacement. The same lattice and indices are reused for every
   variable, so the samples are paired across variables.
5. Store `n_raw`, `n_lattice`, `n_used`, `s`, and `rho(s)` per plot in the Excel output.

> Why 200 rather than 500? Power grows with n. With n = 500, NDVI differences of about 0.005 come out
> "significant" but are agronomically meaningless. A cap of 200 after thinning keeps the tests interpretable.
> Effect sizes (below) remain the primary readout.

**Step A2 — omnibus tests.** For k plots with samples `y_ij`, always compute both:

| Test | Implementation | Effect size | Magnitude labels |
|---|---|---|---|
| Welch ANOVA | `welch_anova()` (§3) | ω² = (df1·(F−1)) / (df1·(F−1) + N), truncated at 0. Classic η² = SS_between/SS_total is also reported. | 0.01 small, 0.06 medium, 0.14 large |
| Kruskal–Wallis | `scipy.stats.kruskal(*groups)` (tie-corrected) | ε² = H / (N − 1)  (= H/((N²−1)/(N+1))) | 0.01 small, 0.08 medium, 0.26 large |

**Primary test rule:**
1. Compute the residuals `r_ij = y_ij − mean_j`.
2. If the median |skewness| across plots is ≤ 1 and the median excess kurtosis is ≤ 2,
   the primary test is **Welch + Games–Howell**.
3. Otherwise, the primary test is **Kruskal–Wallis + Dunn**. This is common for CHM (soil spikes at 0) and thermal.

Diagnostics are reported but do not gate the choice when N > 500, because Shapiro rejects trivially at that size:
* Shapiro–Wilk on pooled residuals (`scipy.stats.shapiro`). If there are more than 5000 residuals, use a
  random subsample of 5000.
* Brown–Forsythe/Levene: `scipy.stats.levene(*groups, center='median')`.

**Step A3 — practical relevance.** Each variable has a default smallest effect of interest δ. These are
editable in the UI:

| Variable | δ |
|---|---|
| NDVI / GNDVI / OSAVI | 0.02 |
| NDRE | 0.015 |
| Thermal | 0.5 °C |
| CHM | 0.05 m |
| CWSI | 0.05 |
| Raw bands | 2 % of the field median |

A pair is reported as **"different"** only when two conditions both hold: p_adj < α, and |Δ median| (or |Δ mean| for Welch) ≥ δ.
The Excel output keeps `sig_stat` and `sig_practical` as separate columns. The CLD uses `sig_stat` by
default, and a toggle switches it to "statistical AND practical".

**Step A4 — post-hoc test.**
* Welch path: **Games–Howell** (`games_howell()`, exact studentized-range p; family-wise error is built in).
* KW path: **Dunn** with tie correction (`dunn_test()`). Use Holm when k ≤ 20 and BH-FDR when k > 20.
  The user can override this.
* **Limits.** There are k(k−1)/2 pairs, which is 124,750 at k = 500.
  - k ≤ 60: full pairwise table, p-matrix heatmap, and CLD. Measured cost: Games–Howell takes about
    6 ms per pair (studentized-range integration), which is about 11 s per variable at k = 60. Run variables in a `ProcessPoolExecutor`.
  - 60 < k ≤ 150: Dunn only (vectorized and fast), long table written, no CLD. Each plot gets
    `n_sig_partners`, the number of plots it differs from.
  - k > 150: no pairwise test. Each plot is compared with the **field** using a one-vs-rest
    Mann–Whitney U on thinned pixels, BH-adjusted. This is labelled "above / below / typical of field".
  - Do **not** approximate Games–Howell with df = ∞. At df ≈ 400 the error is about +10 % on p near 0.05.

### 1.4 Mode B — plot means with a treatment column (correct inference)

The response per plot is the plot **mean** of the variable. The user can switch this to median. Plot scalars
are used as they are.

1. **Model.**
   * CRD: `y = μ + τ_g + ε`, fitted with `scipy.stats.f_oneway`.
   * RCBD (`BLOCK` present): `y = μ + τ_g + β_b + ε`, additive. For a balanced design, use the closed form:
     - SS_T = Σ(y−ȳ)²
     - SS_G = b·Σ(ȳ_g−ȳ)²
     - SS_B = t·Σ(ȳ_b−ȳ)²
     - SS_E = SS_T−SS_G−SS_B, with df_E = (t−1)(b−1)
     - F_G = (SS_G/(t−1))/(SS_E/df_E)

     For an unbalanced design, use `statsmodels` `ols('y ~ C(g)+C(b)')` with `anova_lm(typ=2)`.
     This is the single justified extra dependency.
2. **Assumptions.**
   * Shapiro–Wilk on model residuals (valid for 3 ≤ n ≤ 5000).
   * Levene (`center='median'`) across groups.
   * Two diagnostic plots (F14).
3. **Decision.**

   | Condition | Omnibus | Post-hoc |
   |---|---|---|
   | Shapiro p ≥ 0.05 and Levene p ≥ 0.05 | ANOVA (or RCBD ANOVA) | **Tukey HSD**: `scipy.stats.tukey_hsd` for CRD; `tukey_from_mse()` with MSE_E and df_E for RCBD |
   | Levene p < 0.05, residuals ≈ normal | Welch ANOVA (CRD only) | Games–Howell |
   | Shapiro p < 0.05 and \|skew\| > 1 | Kruskal–Wallis (CRD) or Friedman on block×group means (RCBD, `scipy.stats.friedmanchisquare`) | Dunn–Holm |

   Effect size: η² = SS_G/SS_T, partial η²_G = SS_G/(SS_G+SS_E), and ω².
4. Run the post-hoc test only if the omnibus p < α. Otherwise all groups get the letter "a" and the
   chart notes "n.s.".
5. CLD from the post-hoc p-matrix (§1.6).
6. Report each group mean ± SE, where SE = √(MSE/n_g) for model-based output, plus the 95 % CI.

### 1.5 Multiplicity across variables

There are about 100 variables. Each variable's omnibus p-value is tested as its own family. In addition,
`p_omnibus_BH` (BH across all variables in the run) appears in the `Omnibus` sheet, and the UI flags
results that survive only the per-variable test.

### 1.6 Compact letter display — insert-and-absorb (Piepho 2004)

Input: a symmetric adjusted-p matrix P (k×k) and group means.

1. Order the groups by mean, descending. Start with one letter column containing every group.
2. For each significant pair (i, j) with P_ij < α, take every column that contains both i and j.
   **Insert:** duplicate the column, then remove i from the original and j from the copy.
   **Absorb:** delete any column that is a subset of another column (duplicates keep the first).
3. **Sweep:** for each column c and member r, remove r from c if every partner of r in c also shares
   another column with r.
4. Sort the columns by their highest-mean member and label them a, b, c, … (A–Z after z,
   then aa, ab, …). "a" marks the highest mean.
5. Verify the result: two groups share a letter ⇔ not significant (`cld_check`; unit-tested on 200 random matrices).
   Measured cost: 0.9 s at k = 60, which is why the hard cap is k ≤ 60.

### 1.7 Excel output — `out/stats/statistics.xlsx` (openpyxl)

Sheet names are limited to 31 characters. Strip `[]:*?/\` and abbreviate as `<var>_<suffix>`
(e.g. `NDRE_sum`). Keep a lookup in the `Index` sheet. Freeze the header row, turn on autofilter,
use number format `0.0000` for values, and show p-values as `0.000E+00` when < 0.001.

| Sheet | Contents (one row per …) |
|---|---|
| `Design` | Mode, α, seed, n_max, thinning lag s (per variable), post-hoc method, p-adjust, δ table, software versions, and the pseudo-replication disclaimer. |
| `Index` | The variable → sheet-name map and the links to each figure file. |
| `Omnibus` | Variable: mode, primary test, k, N, statistic, df1, df2, p, p_omnibus_BH, effect size (+label), secondary test (stat, p, effect), Shapiro p, Levene p, median skew, flags. |
| `<var>_sum` | Plot (Mode A) or group (Mode B): n_raw, n_used, mean, SD, SE, 95 % CI low/high, median, Q1, Q3, min, max, CV %, rank (by primary location: median for KW, mean for Welch/ANOVA), CLD letters, n_sig_partners, field_flag (k > 150). |
| `<var>_pairs` | Pair, long format: A, B, diff (A−B), CI low/high (Tukey or Games–Howell when computed), statistic (q or z), df, p_raw, p_adj, sig_stat, sig_practical. Sorted by p_adj. |
| `<var>_pmat` | k ≤ 60 only. Square p_adj matrix with groups ordered by mean. Conditional formatting: green fill p < 0.001, light green < 0.01, pale < 0.05, blank otherwise; diagonal "—". |
| `Plot_metrics` | The full zonal-statistics table (PLOTID × every variable_stat) with plot scalars appended. |

---

## 2. VISUALIZATION

### 2.1 Global matplotlib style (apply once with `mpl.rcParams.update`)

```python
STYLE = {
  "figure.dpi": 100, "savefig.dpi": 350, "savefig.bbox": "tight", "savefig.pad_inches": 0.03,
  "font.family": "DejaVu Sans", "font.size": 8, "axes.titlesize": 9, "axes.labelsize": 8,
  "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 7, "legend.frameon": False,
  "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
  "lines.linewidth": 1.0, "axes.spines.top": False, "axes.spines.right": False,
  "image.interpolation": "antialiased", "pdf.fonttype": 42, "svg.fonttype": "none"}
```
* **Widths** (journal standard): single column **3.5 in**, 1.5 column **5.5 in**, double column **7.2 in**.
  Maximum height is **9.0 in**. At 350 dpi, a 7.2 in figure is 2520 px wide.
* Text must be at least 6 pt after saving. Ticks are 7 pt and labels 8 pt. In-figure annotations
  (letters, IDs) use 6–7 pt.
* Always call `fig.savefig(path, dpi=350)` and then `plt.close(fig)`. Use the `Agg` backend. Generate figures in a process pool.
* Use `rasterized=True` for imshow, pcolormesh, and dense scatter, so vector exports stay small if they are added later.
* **Colour rules (colourblind-safe):**

  | Use | Colormap |
  |---|---|
  | Sequential vegetation indices | `viridis` (default) |
  | Grayscale-print alternative | `cividis` |
  | Thermal | `inferno` |
  | CHM | `YlGn` |
  | CWSI | `YlOrRd` (0–1, fixed limits) |
  | Diverging (z-scores, correlations, deviation from field median) | `RdBu_r`, centred with `TwoSlopeNorm(vcenter=0)` |
  | Categorical (groups ≤ 8) | Okabe–Ito: `#000000 #E69F00 #56B4E9 #009E73 #F0E442 #0072B2 #D55E00 #CC79A7` |
  | Categorical (groups > 8) | `tab20` with marker shapes as redundant encoding |

  `RdYlGn` is available only as an opt-in "agronomic convention" preset, with a tooltip warning that it is
  not safe for red–green colour vision deficiency.
* Colour limits are robust: the 2nd–98th percentile of valid values across the field, the same for every
  map of a variable so maps are comparable. Use `extend='both'` on the colourbar when values are clipped.
  Fixed physical ranges override this: NDVI-type indices are clipped to [−0.2, 1], CWSI to [0, 1].
* Units always appear in the colourbar or axis label: "NDVI (–)", "Canopy temperature (°C)",
  "Plant height (m)", "Canopy cover (%)".

### 2.2 Map furniture (shared helper `add_map_furniture(ax, crs)`)
* **CRS.** Maps need a projected CRS in metres. If the source is geographic (EPSG:4326), reproject vectors
  and rasters to the local UTM zone (`geopandas.estimate_utm_crs()`) before plotting.
* **Scale bar.** Choose a length L from {1, 2, 5}·10ⁿ m closest to 20 % of the map width. Draw two
  alternating black/white `Rectangle`s, each L/2 wide and 1.2 % of the map height tall, at the lower-left
  with a 4 % inset. Label "0", "L/2", and "L m" at 7 pt, each with a white halo
  (`patheffects.withStroke(linewidth=2, foreground='w')`).
* **North arrow.** `ax.annotate('N', xy=(0.95,0.95), xytext=(0.95,0.85), xycoords='axes fraction',
  arrowprops=dict(arrowstyle='-|>', lw=1, color='k'), ha='center', fontsize=8, fontweight='bold')`.
  The arrow is valid because UTM grid north is up. If the convergence angle is > 1°, add "grid north" to the caption.
* Axes are equal-aspect (`ax.set_aspect('equal')`). Ticks are hidden, or shown as easting/northing in m with
  a 7 pt "E (m)"/"N (m)" label. Keep a thin neat line (spines at 0.6 pt).
* Every map's caption gives the CRS (EPSG code), GSD, flight date, and source sensor.

### 2.3 Figure catalogue

`k` is the number of plots and `p` is the number of variables. **Pagination rule:** a page holds at most
P plots. Pages are numbered `_p01`, `_p02`, … and plots are ordered by the same ranking on every page, so
page 1 always holds the top plots. For each figure below: purpose, implementation, size, and large-k handling.

**F01 — Plot choropleth map (one per variable, statistic = mean by default; other stats on request).**
* *Purpose:* show where values are high or low across the field, and reveal gradients.
* *Backdrop:* the RGB orthomosaic, downsampled with `rasterio` `out_shape` resampling `average` so the long
  side is at most `width_in·350` px and percentile-stretched 2–98 %. If there is no RGB, use a DSM hillshade
  (`matplotlib.colors.LightSource(azdeg=315, altdeg=45).hillshade(dsm, vert_exag=2, dx=gsd, dy=gsd)`,
  `cmap='gray'`). Draw the backdrop at alpha 0.6–0.8.
* *Plots:* `geopandas.GeoDataFrame.plot(column=var, cmap=..., norm=..., edgecolor='k', linewidth=0.3, ax=ax)`.
  Plots with missing values get a `#BDBDBD` fill with `hatch='///'` and a legend entry "no data".
* *Labels:* show PLOTID at each polygon's `representative_point()` when k ≤ 150 and the polygon is at least
  12 pt tall on paper. Font size is 7 pt (6 pt when k > 80), with a white halo. For k > 150, show no labels and
  put a companion lookup map F01b (IDs only, grey) on its own page.
* *Colourbar:* vertical on the right, `fig.colorbar(sm, ax=ax, fraction=0.035, pad=0.02, shrink=0.8)`.
* *Size:* 7.2 in wide, with height set by the field aspect (clamped 3–9 in). Elongated fields can rotate
  the layout so the long axis is horizontal (only when the map is not rotated relative to north; otherwise keep north up).

**F02 — Full-resolution index raster map (per index).**
* *Purpose:* show within-plot patterns such as gaps, lodging, and weeds.
* Use `ax.imshow(arr, extent=rasterio.plot.plotting_extent(src), cmap, norm, interpolation='antialiased')`.
  Downsample first to the output pixel budget (block mean via `rasterio` read with `out_shape`), because at
  2520 px wide, 20k px of source is wasted. Mask nodata to transparent with `np.ma.masked_invalid`.
* Overlay the plot outlines (`facecolor='none'`, edgecolor white at 0.4 pt with a black 0.8 pt underlay for
  contrast), then add the scale bar, north arrow, and colourbar as in §2.2.
* An optional zoom-inset panel (`inset_axes`) of a user-chosen plot at native resolution.
* *Size:* 7.2 in wide. *GeoTIFF* is optional (toggle): write the full-resolution array, not the downsampled one,
  with `rasterio`, `dtype=float32`, `nodata=np.nan`, `compress='deflate'`, `predictor=3`, `tiled=True`,
  `blockxsize=blockysize=512`, and the source CRS and transform, plus a `BIGTIFF='IF_SAFER'` tag and a band
  description set to the index name.

**F03 — Small-multiples map panel (key metrics).**
* Shows 4–6 metrics (NDVI, NDRE, CHM, Thermal, Canopy cover, CWSI) on a shared geometry, each panel with
  its own colourbar. Layout `plt.subplots(2, 3, figsize=(7.2, ~6), layout='constrained')`, panel letters
  "(a)"… at 9 pt bold. There is one scale bar and north arrow, in the last panel only.

**F04 — Per-plot distributions (box + violin), ordered by median.**
* *Data:* the thinned pixel samples from Mode A, which are the same data that was tested.
* `ax.violinplot(..., showextrema=False, widths=0.8)` (face colour `#56B4E9`, alpha 0.4), overlaid with
  `ax.boxplot(..., widths=0.25, showfliers=False, medianprops=dict(color='k', lw=1))`. The field median is
  a dashed grey line. CLD letters sit above the whiskers at 6 pt when k ≤ 60.
* Vertical orientation when P ≤ 30 per page (x tick labels rotated 90°). Horizontal (`vert=False`) when
  P is 31–60: plot IDs on y, one row per 0.13 in, figure height `0.13·P + 0.8` in.
* *Pagination:* P = 40 plots per page, 7.2 × (≤ 6.0) in. For k > 60 this figure is optional, defaults to
  the top and bottom 20 plots only, and points to F05.

**F05 — Caterpillar (ranked median ± IQR), all plots in one figure.**
* *Purpose:* the overview of all plots, scaling to k = 500. x is rank and y is the value; the median is a
  dot (s = 4) with a vertical IQR bar (lw 0.6) and a 5–95 % bar (lw 0.3, alpha 0.5). Dots are coloured by
  `GROUP` (Okabe–Ito) when present, and by k > 150 field flags (above/typical/below) otherwise. The field
  median is a horizontal line, and the ±δ band is shaded grey (alpha 0.15).
* Show x tick labels (plot IDs) only when k ≤ 80. Otherwise annotate the top and bottom 5 IDs.
* *Size:* 7.2 × 3.0 in.

**F06 — Ranking chart with error bars and CLD letters.**
* Horizontal dot-and-whisker chart. Do not use bars, because index values often have no meaningful zero.
  Plot the mean ± 95 % CI (Mode A: t-interval on thinned pixels; Mode B: model SE), sorted descending, with
  CLD letters right-aligned in a fixed text column (`ax.text(1.01, y, letters, transform=blended)`) at 6.5 pt.
* Plot scalars (stand count, canopy cover %) are ratio-scaled with a true zero, so for those use `ax.barh`
  with bars starting at 0.
* *Pagination:* P = 50 rows per page. Row height 0.14 in, so a full page is 7.2 × 7.8 in.

**F07 — Clustered heatmap, plots × metrics (z-scored).**
* *Data:* plot means of 10–30 selected variables (all indices plus CHM, thermal, and scalars). For each
  variable, z = (x − mean)/SD across plots.
* *Clustering:* `scipy.cluster.hierarchy.linkage(Z, method='ward')` on rows (plots) and
  `linkage(1−|corr|, 'average')` on columns, via `squareform`. Add dendrograms on the left and top
  (`dendrogram(..., no_labels=True, color_threshold=0, above_threshold_color='k')`). Cut the rows into
  `n_clusters` (default chosen by maximum mean silhouette over 2–8) and draw a cluster colour strip using Okabe–Ito.
* `imshow(Z, cmap='RdBu_r', norm=TwoSlopeNorm(0, -3, 3), aspect='auto')`.
* Show row labels only when k ≤ 100 (6 pt). Variable labels are rotated 90° at 7 pt.
* *Size:* 7.2 in wide, height `clip(0.09·k + 1.5, 4, 9)` in. For k > 100, rows compress, labels are
  dropped, and the cluster membership is written to Excel `Clusters` and CSV.

**F08 — Correlation matrix heatmap with significance stars.**
* Variables: plot means (n = k plots, which is legitimate here because plots are the units). Spearman
  by default, Pearson optional. The p-values come from `scipy.stats.spearmanr` / `pearsonr`, then are
  BH-adjusted across the m(m−1)/2 tests. Stars: `*` < 0.05, `**` < 0.01, `***` < 0.001 (adjusted).
* Order the variables by `linkage(squareform(1-|r|), 'average')` leaf order. Show the lower triangle only
  (mask with `np.triu(ones, 1)`), `RdBu_r` with vmin −1 and vmax 1, a cell outline in white at 0.5 pt, and
  the r value written in each cell when m ≤ 15 (5.5–6 pt, white text if |r| > 0.6).
* Caption note: neighbouring plots are spatially autocorrelated, so p-values are optimistic. Moran's I of
  each variable on plot centroids (k-nearest-neighbour weights, k = 4) is reported in Excel.
* *Size:* m ≤ 15 → 5.5 × 5.0 in; m ≤ 30 → 7.2 × 6.8 in. For m > 30, use plot means of indices only (≈ 15).

**F09 — Scatter matrix of the top indices.**
* Select 4–6 indices by **highest Mode A ε²** (or Mode B η²). Skip any index with |r| > 0.95 to an
  already-selected one, because it is redundant (e.g. NDVI vs OSAVI).
* Lower triangle: scatter of plot means (s = 6, alpha 0.7, Okabe–Ito by group). Diagonal: histogram
  (`bins='fd'`). Upper triangle: Spearman ρ and stars at 8 pt.
* *Size:* 7.2 × 7.2 in.

**F10 — Thermal vs NDVI (and CWSI vs NDVI) scatter.**
* *Plot-level:* plot means with an OLS line (`scipy.stats.linregress`) and a 95 % confidence band computed
  from `t·SE_fit`. Annotate `r`, `R²`, `p`, `n`, and the slope in °C per 0.1 NDVI. Plots with
  |studentized residual| > 2.5 are labelled with their PLOTID; these are water-stress candidates.
* *Pixel-level panel (b):* `hexbin(ndvi, temp, gridsize=60, mincnt=1, bins='log', cmap='viridis')` on
  thinned pixels pooled across plots, giving the "triangle/trapezoid" (temperature–vegetation dryness) shape.
  Add a colourbar labelled "pixel count (log)".
* *Size:* 7.2 × 3.2 in (two panels).

**F11 — CHM (plant height) histogram.**
* Pooled canopy pixels: CHM > h_min (default 0.05 m, which removes ground). Use `bins='fd'`, capped at 100
  bins, `histtype='stepfilled'` with alpha 0.6 in `#009E73`. Draw vertical lines for the mean (solid),
  median (dashed), and P95 (dotted) with a legend. Write the share of ground pixels in the corner.
* Optional panel (b): a ridgeline of per-plot height distributions for the top and bottom 10 plots by P95.
* *Size:* 3.5 × 2.6 in (single), or 7.2 × 3.0 in with the ridgeline.

**F12 — Post-hoc p-value matrix heatmap (k ≤ 60).**
* Lower triangle of −log10(p_adj) in `viridis`, with groups ordered by mean. Cells with p < α get a black
  dot. Labels are the plot IDs at 6 pt.
* *Size:* `min(7.2, 0.12·k + 1.2)` in square.

**F13 — Group means with letters (Mode B).**
* Plot the group mean ± 95 % CI as a dot-and-whisker, overlaid with plot-level points (jittered by
  ±0.15, s = 8, alpha 0.6) so the replicates stay visible. Letters sit above at 7 pt, and the omnibus
  result ("Welch F(5, 12.3) = 8.1, p = 0.002, ω² = 0.61") appears in the subtitle at 7 pt. Groups are
  ordered by mean.
* With more than 25 groups (genotype trials), switch to a horizontal layout with 0.15 in per group, and
  paginate at 50 groups.
* *Size:* 3.5 × 2.8 in (≤ 8 groups), otherwise 7.2 in wide.

**F14 — Model diagnostics (Mode B).** Two panels: a residuals-vs-fitted plot with a LOWESS-free running
median, and a normal Q–Q plot (`scipy.stats.probplot`) with a 95 % simulation envelope from 200 normal
draws. The Shapiro and Levene p-values appear in the title. *Size:* 7.2 × 3.0 in.

**F15 — Field-gradient check map.** This is a choropleth of plot means minus a smooth trend fitted on
centroid x, y (second-order polynomial by `numpy.linalg.lstsq`), in `RdBu_r` centred on 0, with Moran's I
and its permutation p (999 permutations) in the title. It warns users when "plot differences" are just
field trends. This is essential in Mode A.

**F16 — Advanced-metric panel.** Stand count, canopy cover %, canopy volume, harvestable-area ratio, and
canopy length as F06-style bar rankings (true zero) or F01 choropleths. GDD appears only as text in the
caption (one value per flight). For multi-date projects, add a time series of field mean ± SD per index
against GDD (x axis), with one faint line per plot (alpha 0.15) and plots highlighted by group.

### 2.4 Volume control (default figure set)

The default figure set is: F01 (means of all pixel variables), F02 (indices ≤ 15), F03, F05 and F06 per
variable, F07, F08, F09, F10, F11, F12/F04 (only when k ≤ 60), F13–F14 (Mode B), and F15 and F16.
"Generate all statistics" (min/max/sd/range maps) is opt-in. A hard ceiling of 400 PNGs per run, with
a UI warning above that.

### 2.5 File naming & manifest

`{figid}__{variable}__{stat}__p{page:02d}.png`, for example `F01__NDRE__mean__p01.png`. Use ASCII only;
non-alphanumeric characters become `-`. A thumbnail is written alongside each figure at 600 px wide as
WebP quality 80 (Pillow, which matplotlib already depends on), as `thumbs/<same>.webp`.

`manifest.json` (written last, atomically):
```json
{"run_id": "2026-10-20T14:03Z", "mode": "A", "k": 212, "dpi": 350,
 "figures": [{"id": "F01__NDRE__mean__p01", "file": "F01__NDRE__mean__p01.png",
   "thumb": "thumbs/F01__NDRE__mean__p01.webp", "category": "Maps", "figtype": "F01",
   "variable": "NDRE", "stat": "mean", "page": 1, "n_pages": 1, "width_in": 7.2,
   "height_in": 5.1, "title": "NDRE plot means", "caption": "...", "alt": "...",
   "geotiff": "../rasters/NDRE.tif", "stats_ref": "NDRE_sum"}]}
```
The categories (front-end tabs) are **Maps** (F01–F03, F15), **Distributions** (F04, F05, F11),
**Rankings** (F06, F13, F16), **Relationships** (F07–F10), and **Statistics** (F12, F14 plus the Excel link).

### 2.6 HTML front-end presentation

* **Gallery.** Tabs follow the categories above, each with a count badge. A filter bar holds a variable
  multi-select, a stat select, and free-text search over title, caption, and PLOTID. The grid is a responsive
  CSS grid (`repeat(auto-fill, minmax(240px, 1fr))`) of cards showing the thumbnail (`loading="lazy"`,
  `alt` from the manifest), the title, and a "page i/n" chip. Paginated figures are grouped into one card
  with page arrows.
* **Lightbox.** Clicking a card opens the full 350 dpi PNG, fitted to the viewport. It supports wheel and
  pinch zoom and drag-to-pan (CSS transform, so no library is needed). Keyboard: ← → for previous/next in
  the current filter, PgUp/PgDn for figure pages, and Esc to close. A side panel shows the caption,
  dimensions (in and px), and a mini table from the Excel `<var>_sum` (top 5 and bottom 5 plots plus the
  omnibus line) supplied by a JSON endpoint.
* **Downloads.** Each card and the lightbox carry a PNG link (`<a download>`), a GeoTIFF link when
  `geotiff` is set, and an Excel sheet deep link. The page header offers "Download all (ZIP)" through a
  backend endpoint that streams a zip of the figures, `manifest.json`, and `statistics.xlsx`.
* **Statistics tab header.** A summary table from `Omnibus` sorted by effect size, with effect-size chips
  coloured by magnitude: grey for negligible, then three steps of `#56B4E9` → `#0072B2`. A text label always
  accompanies the colour. In Mode A, the pseudo-replication disclaimer appears as a persistent info banner.
* **Accessibility.** All controls are keyboard reachable. The lightbox has a focus trap. Colour is never
  the sole carrier of meaning.

---

## 3. Python reference (validated)

Validated against `pingouin` 0.7.0 (Welch ANOVA, Games–Howell: identical p and diff), `scikit-posthocs` 0.17.1
(Dunn Holm/BH: |Δp| < 1e-15), and `scipy.stats.tukey_hsd` (`tukey_from_mse` with one-way MSE: identical).
The CLD passed `cld_check` on 200 random and mean-structured p-matrices (k = 3–14) and at k = 20/40/60.

```python
import itertools
import numpy as np
import pandas as pd
from scipy import stats


def welch_anova(groups):
    """Welch (1951) one-way ANOVA. groups: list of 1-D arrays. Returns F, df1, df2, p."""
    n = np.array([len(g) for g in groups], float)
    m = np.array([np.mean(g) for g in groups])
    v = np.array([np.var(g, ddof=1) for g in groups])
    k = len(groups)
    w = n / v
    sw = w.sum()
    mw = (w * m).sum() / sw
    a = (w * (m - mw) ** 2).sum() / (k - 1)
    tmp = (((1 - w / sw) ** 2) / (n - 1)).sum()
    b = 1 + 2 * (k - 2) / (k ** 2 - 1) * tmp
    F = a / b
    df1, df2 = k - 1, (k ** 2 - 1) / (3 * tmp)
    return F, df1, df2, stats.f.sf(F, df1, df2)


def games_howell(df, value, group, ci=False, alpha=0.05):
    """Games-Howell pairwise test, one row per pair. ci=True adds simultaneous
    CIs (ppf is ~10x slower than sf; use only for k <= ~20)."""
    g = df.groupby(group, sort=False)[value]
    names = list(g.groups.keys())
    n = g.count().to_numpy(float)
    m = g.mean().to_numpy()
    v = g.var(ddof=1).to_numpy()
    k = len(names)
    i, j = np.triu_indices(k, 1)
    vi, vj = v[i] / n[i], v[j] / n[j]
    se = np.sqrt(vi + vj)
    diff = m[i] - m[j]
    dfw = (vi + vj) ** 2 / (vi ** 2 / (n[i] - 1) + vj ** 2 / (n[j] - 1))
    q = np.abs(diff) / se * np.sqrt(2)
    p = stats.studentized_range.sf(q, k, dfw)
    out = pd.DataFrame({"A": np.array(names)[i], "B": np.array(names)[j],
                        "diff": diff, "se": se, "df": dfw, "q": q,
                        "p_adj": np.clip(p, 0, 1)})
    if ci:
        half = stats.studentized_range.ppf(1 - alpha, k, dfw) * se / np.sqrt(2)
        out["ci_low"], out["ci_high"] = diff - half, diff + half
    return out


def dunn_test(df, value, group, method="holm"):
    """Dunn (1964) test with tie correction; p adjusted by Holm or BH."""
    x = df[value].to_numpy()
    r = stats.rankdata(x)
    N = len(x)
    tmp = pd.DataFrame({"r": r, "g": df[group].to_numpy()})
    gg = tmp.groupby("g", sort=False)["r"]
    names = list(gg.groups.keys())
    rbar = gg.mean().to_numpy()
    n = gg.count().to_numpy(float)
    _, t = np.unique(x, return_counts=True)
    tie = (t ** 3 - t).sum() / (12 * (N - 1))
    i, j = np.triu_indices(len(names), 1)
    se = np.sqrt((N * (N + 1) / 12 - tie) * (1 / n[i] + 1 / n[j]))
    z = (rbar[i] - rbar[j]) / se
    p = 2 * stats.norm.sf(np.abs(z))
    return pd.DataFrame({"A": np.array(names)[i], "B": np.array(names)[j],
                         "z": z, "p_raw": p, "p_adj": p_adjust(p, method)})


def p_adjust(p, method="holm"):
    p = np.asarray(p, float)
    m = len(p)
    o = np.argsort(p)
    if method == "holm":
        adj = np.maximum.accumulate((m - np.arange(m)) * p[o])
    elif method == "bh":
        adj = np.minimum.accumulate((m / np.arange(m, 0, -1)) * p[o][::-1])[::-1]
    else:
        raise ValueError(method)
    out = np.empty(m)
    out[o] = np.minimum(adj, 1)
    return out


def tukey_from_mse(means, ns, mse, df_resid, names):
    """Tukey-Kramer using an external MSE (e.g. from an RCBD model)."""
    means, ns = np.asarray(means, float), np.asarray(ns, float)
    k = len(means)
    i, j = np.triu_indices(k, 1)
    se = np.sqrt(mse / 2 * (1 / ns[i] + 1 / ns[j]))
    q = np.abs(means[i] - means[j]) / se
    p = stats.studentized_range.sf(q, k, df_resid)
    return pd.DataFrame({"A": np.array(names)[i], "B": np.array(names)[j],
                         "diff": means[i] - means[j], "q": q, "p_adj": p})


def pairs_to_matrix(pairs, names, pcol="p_adj"):
    P = pd.DataFrame(1.0, index=names, columns=names)
    for a, b, p in pairs[["A", "B", pcol]].itertuples(index=False):
        P.loc[a, b] = P.loc[b, a] = p
    return P


def cld(pmat, means, alpha=0.05):
    """Compact letter display, insert-and-absorb (Piepho 2004).
    pmat: symmetric DataFrame of adjusted p-values (index == columns).
    means: Series indexed like pmat; letter 'a' goes to the highest mean.
    Returns Series of letter strings in the order of means (descending)."""
    order = means.sort_values(ascending=False).index.tolist()
    P = pmat.loc[order, order].to_numpy()
    k = len(order)
    cols = [np.ones(k, bool)]                       # one letter shared by all
    for a, b in zip(*np.where(np.triu(P < alpha, 1))):
        # insert: split every column that still joins a and b
        new = []
        for c in cols:
            if c[a] and c[b]:
                c1, c2 = c.copy(), c.copy()
                c1[a] = False
                c2[b] = False
                new += [c1, c2]
            else:
                new.append(c)
        # absorb: drop columns that are subsets of another column
        new = [c for c in new if c.any()]
        keep = []
        for idx, c in enumerate(new):
            redundant = any((idx != jdx) and np.all(c <= d) and
                            (not np.array_equal(c, d) or jdx < idx)
                            for jdx, d in enumerate(new))
            if not redundant:
                keep.append(c)
        cols = keep
    # sweep: drop letter c from member r if every partner of r in c shares
    # another letter with r (removes redundant letters, Piepho 2004 step 3)
    M = np.array(cols)                              # letters x groups
    for ci in range(len(M)):
        for r in np.flatnonzero(M[ci]):
            partners = np.flatnonzero(M[ci])
            partners = partners[partners != r]
            others = np.delete(M, ci, axis=0)
            if all((others[:, r] & others[:, j]).any() for j in partners)                     and others[:, r].any():
                M[ci, r] = False
    cols = [c for c in M if c.any()]
    # letters ordered by their highest-mean member -> 'a' on top
    cols.sort(key=lambda c: np.argmax(c))
    letters = _letter_seq(len(cols))
    out = ["".join(letters[ci] for ci, c in enumerate(cols) if c[r])
           for r in range(k)]
    return pd.Series(out, index=order, name="letters")


def _letter_seq(n):
    base = "abcdefghijklmnopqrstuvwxyz"
    seq = list(base) + list(base.upper())
    while len(seq) < n:                             # aa, ab, ... for > 52
        seq += [x + y for x in base for y in base]
    return seq[:n]


def cld_check(pmat, letters, alpha=0.05):
    """True iff for every pair: shares a letter <=> not significant (<=52 letters)."""
    tok = {g: set(s) for g, s in letters.items()}
    for a, b in itertools.combinations(letters.index, 2):
        if bool(tok[a] & tok[b]) == (pmat.loc[a, b] < alpha):
            return False, (a, b)
    return True, None
```

Usage (Mode A, one variable):
```python
F, d1, d2, p = welch_anova([g.y.values for _, g in samp.groupby("plot")])
H, p_kw = stats.kruskal(*[g.y.values for _, g in samp.groupby("plot")])
eps2 = H / (len(samp) - 1)
pairs = games_howell(samp, "y", "plot")            # or dunn_test(samp, "y", "plot", "holm")
names = samp.groupby("plot").y.mean().sort_values(ascending=False).index
P = pairs_to_matrix(pairs, list(names))
letters = cld(P, samp.groupby("plot").y.mean())     # k <= 60
assert cld_check(P, letters)[0]
```

Spatial thinning core (§1.3 A1):
```python
def thin_pixels(mask, s, n_max, rng):
    oy, ox = rng.integers(0, s, 2)
    r, c = np.nonzero(mask)
    keep = ((r - oy) % s == 0) & ((c - ox) % s == 0)
    r, c = r[keep], c[keep]
    if len(r) > n_max:
        idx = rng.choice(len(r), n_max, replace=False)
        r, c = r[idx], c[idx]
    return r, c          # reuse for every variable of the plot (paired samples)
```

### References
Games & Howell (1976) *J. Educ. Stat.* 1:113 · Welch (1951) *Biometrika* 38:330 · Dunn (1964)
*Technometrics* 6:241 · Piepho (2004) *J. Comput. Graph. Stat.* 13:456 · Hurlbert (1984) *Ecol. Monogr.*
54:187 · Tomczak & Tomczak (2014) *Trends Sport Sci.* 21:19 · Okabe & Ito (2008) Color Universal Design.
