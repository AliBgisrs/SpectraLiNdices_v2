"""Input discovery and reading: Pix4D outputs, plot boundaries, field data."""
from __future__ import annotations

import datetime as dt
import glob
import os
import re

import numpy as np
import rasterio

# Canonical band keys and the Pix4D band-description aliases that map to them.
BAND_ALIASES = {
    "B": ["blue", "b"],
    "G": ["green", "g"],
    "R": ["red", "r"],
    "RE": ["red edge", "rededge", "red_edge", "re", "red-edge"],
    "NIR": ["nir", "near infrared", "near-infrared", "nir1"],
    "T": ["thermal ir", "thermal", "lwir", "tir", "thermal_ir", "temperature"],
    "A": ["alpha", "mask"],
}
BAND_LABEL = {"B": "Blue", "G": "Green", "R": "Red", "RE": "RedEdge", "NIR": "NIR", "T": "Thermal"}


def _match_band(desc: str | None):
    if not desc:
        return None
    d = desc.strip().lower()
    for key, names in BAND_ALIASES.items():
        if d in names:
            return key
    return None


def raster_info(path):
    with rasterio.open(path) as s:
        descs = list(s.descriptions)
        return {
            "path": path, "name": os.path.basename(path), "count": s.count,
            "width": s.width, "height": s.height, "dtype": s.dtypes[0],
            "crs": s.crs.to_string() if s.crs else None, "res": list(s.res),
            "nodata": s.nodata, "bounds": list(s.bounds), "descriptions": descs,
            "datetime": s.tags().get("TIFFTAG_DATETIME"),
            "software": s.tags().get("TIFFTAG_SOFTWARE"),
            "size_mb": round(os.path.getsize(path) / 2**20, 1),
        }


def scan_input_folder(folder):
    """Find Pix4D Fields / Mapper outputs and classify them."""
    if not folder or not os.path.isdir(folder):
        raise FileNotFoundError(f"Input folder not found: {folder}")
    tifs = sorted(glob.glob(os.path.join(folder, "**", "*.tif"), recursive=True) +
                  glob.glob(os.path.join(folder, "**", "*.tiff"), recursive=True))
    found = {"multispectral": None, "dsm": None, "rgb": None, "index_maps": [], "other": [],
             "band_map": {}, "warnings": []}
    for p in tifs:
        try:
            info = raster_info(p)
        except Exception as e:
            found["warnings"].append(f"Cannot read {p}: {e}")
            continue
        n = info["name"].lower()
        bands = {i + 1: _match_band(d) for i, d in enumerate(info["descriptions"])}
        spectral = [k for k in bands.values() if k in ("G", "R", "RE", "NIR", "B")]
        if "dsm" in n or "surface model" in n or (info["count"] == 1 and (info["descriptions"][0] or "").upper() == "DSM"):
            if n.endswith(".rgb.tif") or info["dtype"] == "uint8":
                continue  # colourised preview of DSM
            found["dsm"] = info
        elif len(spectral) >= 2 and info["dtype"].startswith("float"):
            if found["multispectral"] is None or info["count"] > found["multispectral"]["count"]:
                found["multispectral"] = info
                found["band_map"] = {k: i for i, k in bands.items() if k}
        elif info["dtype"] == "uint8" and info["count"] in (3, 4) and ("rgb" in n or "ortho" in n):
            if found["rgb"] is None or "orthomosaic" in n:
                found["rgb"] = info
        elif info["count"] == 1 and info["dtype"].startswith("float"):
            found["index_maps"].append(info)
        else:
            found["other"].append(info)
    if found["multispectral"] is None:
        found["warnings"].append("No multispectral float orthomosaic found (expected *.data.tif with band names).")
    if found["dsm"] is None:
        found["warnings"].append("No DSM found - plant height will be unavailable.")
    if "T" not in found["band_map"]:
        found["warnings"].append("No thermal band detected - thermal analysis disabled.")
    return found


def image_datetime(info):
    s = (info or {}).get("datetime")
    if not s:
        return None
    try:
        return dt.datetime.strptime(s.strip(), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def raster_center_lonlat(info):
    from pyproj import Transformer
    l, b, r, t = info["bounds"]
    tr = Transformer.from_crs(info["crs"], "EPSG:4326", always_xy=True)
    lon, lat = tr.transform((l + r) / 2, (b + t) / 2)
    return float(lat), float(lon)


# ---------------------------------------------------------------------------- boundaries
def boundary_layers(path):
    import pyogrio
    try:
        layers = pyogrio.list_layers(path)
        return [str(l[0]) for l in layers if (l[1] or "").lower() not in ("", "none")] or [str(l[0]) for l in layers]
    except Exception:
        return []


def inspect_boundary(path, layer=None):
    import geopandas as gpd
    layers = boundary_layers(path)
    if layer is None and layers:
        layer = layers[0]
    gdf = gpd.read_file(path, layer=layer) if layer else gpd.read_file(path)
    cols = [c for c in gdf.columns if c != "geometry"]
    guess = None
    for c in cols:
        if re.fullmatch(r"(plot_?id|plotid|plot|id|fid|plot_no|plotno|plot_num)", c, re.I):
            guess = c
            if "plot" in c.lower():
                break
    sample = gdf[cols].head(5).astype(str).to_dict("records")
    uniq = {c: int(gdf[c].nunique()) for c in cols}
    return {"layers": layers, "layer": layer, "columns": cols, "n_features": int(len(gdf)),
            "crs": gdf.crs.to_string() if gdf.crs else None, "id_guess": guess,
            "sample": sample, "unique_counts": uniq,
            "geom_types": sorted(set(gdf.geom_type.astype(str)))}


def norm_id(series):
    """Plot IDs as clean strings; integer-valued floats (common in GDB/Excel) lose the '.0'."""
    import pandas as pd
    s = series
    if pd.api.types.is_float_dtype(s):
        v = s.dropna()
        if len(v) and (v == v.round()).all():
            s = s.astype("Int64")
    return pd.Series([("" if pd.isna(x) else str(x).strip()) for x in s.astype(object)], index=series.index)


def read_boundaries(path, id_col, raster_crs, layer=None, buffer_m=0.0, extra_cols=None):
    import geopandas as gpd
    gdf = gpd.read_file(path, layer=layer) if layer else gpd.read_file(path)
    if id_col not in gdf.columns:
        raise KeyError(f"ID column '{id_col}' not in boundary file")
    if gdf.crs is None:
        gdf = gdf.set_crs(raster_crs)
    gdf = gdf.to_crs(raster_crs)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    gdf["geometry"] = gdf.geometry.make_valid() if hasattr(gdf.geometry, "make_valid") else gdf.geometry.buffer(0)
    gdf = gdf[gdf.geom_type.isin(["Polygon", "MultiPolygon", "GeometryCollection"])]
    if buffer_m:
        gdf["geometry"] = gdf.geometry.buffer(-abs(buffer_m) if buffer_m < 0 else buffer_m)
        gdf = gdf[~gdf.geometry.is_empty]
    gdf["PLOTID"] = norm_id(gdf[id_col])
    gdf = gdf[gdf["PLOTID"] != ""]
    dup = gdf["PLOTID"][gdf["PLOTID"].duplicated()].unique().tolist()
    if dup:  # dissolve duplicate ids into one multipolygon
        keep = [c for c in (extra_cols or []) if c in gdf.columns]
        gdf = gdf.dissolve(by="PLOTID", aggfunc="first", as_index=False)[["PLOTID", "geometry"] + keep]
    keep = ["PLOTID", "geometry"] + [c for c in (extra_cols or []) if c in gdf.columns and c != "PLOTID"]
    return gdf[keep].reset_index(drop=True), dup


# ---------------------------------------------------------------------------- field data
def inspect_table(path, sheet=None):
    import pandas as pd
    ext = os.path.splitext(path)[1].lower()
    sheets = []
    if ext in (".xlsx", ".xlsm", ".xls"):
        sheets = pd.ExcelFile(path).sheet_names
        df = pd.read_excel(path, sheet_name=sheet or sheets[0])
    else:
        df = pd.read_csv(path, sep=None, engine="python")
    return {"sheets": sheets, "sheet": sheet or (sheets[0] if sheets else None),
            "columns": [str(c) for c in df.columns], "n_rows": int(len(df)),
            "numeric": [str(c) for c in df.select_dtypes("number").columns],
            "sample": df.head(5).astype(str).to_dict("records")}


def read_table(path, sheet=None):
    import pandas as pd
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(path, sheet_name=sheet or 0)
    return pd.read_csv(path, sep=None, engine="python")


def thermal_to_celsius(arr, mode="auto", scale=None, offset=None):
    """Convert Pix4D thermal band to deg C. Returns (array, mode_used)."""
    a = np.asarray(arr, dtype=np.float32)
    if mode == "custom":
        return a * float(scale or 1) + float(offset or 0), "custom"
    if mode == "auto":
        v = a[np.isfinite(a)]
        med = float(np.median(v)) if v.size else 0.0
        if 15000 < med < 40000:
            mode = "centikelvin"
        elif 1500 < med < 4000:
            mode = "decikelvin"
        elif 150 < med < 400:
            mode = "kelvin"
        else:
            mode = "celsius"
    conv = {"centikelvin": (0.01, -273.15), "decikelvin": (0.1, -273.15),
            "kelvin": (1.0, -273.15), "celsius": (1.0, 0.0)}[mode]
    return a * conv[0] + conv[1], mode
