"""Create synthetic plot boundaries + field data aligned with the strips in inputFolder.

Strips are found from the NDVI column profile; each strip is cut into plots along its
length. Writes GeoJSON, Shapefile and GeoPackage versions plus a field-data CSV with a
synthetic yield that is correlated with NDVI (for testing the integration step only).
"""
import os
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from shapely.geometry import box

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ortho = os.path.join(ROOT, "inputFolder", "Orthomosaic.data.tif")
out = os.path.join(ROOT, "tests", "test_plots")
os.makedirs(out, exist_ok=True)

with rasterio.open(ortho) as s:
    f = 8
    r = s.read(2, out_shape=(s.height // f, s.width // f)).astype(float)
    n = s.read(4, out_shape=(s.height // f, s.width // f)).astype(float)
    T = s.transform * s.transform.scale(f, f)
    crs = s.crs
nd = (n - r) / (n + r)
nd[(r == -10000) | (n == -10000)] = np.nan

blocks = [(265, 400, 2), (405, 495, 1), (510, 1055, 8), (1240, 1390, 2)]  # thumb rows: (y0, y1, plots per strip)
prof = np.nanmean(nd[510:1055], axis=0)
veg = prof > 0.55
runs, start = [], None
for x, v in enumerate(veg):
    if v and start is None:
        start = x
    if (not v or x == len(veg) - 1) and start is not None:
        if x - start > 15:
            runs.append((start + 2, x - 2))
        start = None
print("strips (thumb px):", runs)
recs = []
pid = 100
treat = ["T1", "T2", "T3", "T4"]
for bi, (y0, y1, k) in enumerate(blocks, 1):
    edges = np.linspace(y0, y1, k + 1)
    for si, (x0, x1) in enumerate(runs, 1):
        for j in range(k):
            ya, yb = edges[j] + 3, edges[j + 1] - 3
            (xa, ya_m) = T * (x0, ya)
            (xb, yb_m) = T * (x1, yb)
            pid += 1
            recs.append({"Plot_ID": pid, "Block": bi, "Strip": si, "Treatment": treat[(si + j) % 4],
                         "SowDate": "2026-05-05", "geometry": box(min(xa, xb), min(ya_m, yb_m), max(xa, xb), max(ya_m, yb_m))})
g = gpd.GeoDataFrame(recs, crs=crs)
g.to_file(os.path.join(out, "plots.geojson"), driver="GeoJSON")
g.to_file(os.path.join(out, "plots.shp"))
g.to_file(os.path.join(out, "plots.gpkg"), layer="plots", driver="GPKG")
rng = np.random.default_rng(1)
fd = pd.DataFrame({"PlotNumber": g["Plot_ID"], "Yield_kg_ha": np.nan, "Biomass_g_m2": np.nan,
                   "Maturity_days": rng.integers(85, 100, len(g))})
# synthetic yield ~ NDVI of the plot
with rasterio.open(ortho) as s:
    from rasterio.mask import mask
    for i, geom in enumerate(g.geometry):
        a, _ = mask(s, [geom], crop=True, indexes=[2, 4], nodata=-10000, filled=False)
        v = ((a[1] - a[0]) / (a[1] + a[0])).compressed().mean()
        fd.loc[i, "Yield_kg_ha"] = 2000 + 5000 * v + rng.normal(0, 150)
        fd.loc[i, "Biomass_g_m2"] = 300 + 900 * v + rng.normal(0, 40)
fd.to_csv(os.path.join(out, "field_data.csv"), index=False)
print(len(g), "plots written to", out)
