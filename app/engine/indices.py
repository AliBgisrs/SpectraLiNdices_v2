"""Vegetation-index catalogue.

`DEFAULT_15` is the panel's literature-ranked list (see docs/vegetation_indices_review.md)
for a Green/Red/RedEdge/NIR sensor. Extra entries are offered in the UI as optional, and
indices needing a band the sensor lacks (e.g. Blue) are skipped automatically.
Formulas are numpy expressions over band variables B, G, R, RE, NIR (reflectance 0-1).
"""
from __future__ import annotations

import ast
import json
import os

CATALOG = [
    # abbr, name, formula, bands, sensitivity
    ("NDVI", "Normalized Difference Vegetation Index", "(NIR-R)/(NIR+R)", "R NIR", "biomass, LAI, vigour"),
    ("NDRE", "Normalized Difference Red Edge", "(NIR-RE)/(NIR+RE)", "RE NIR", "chlorophyll, N status (dense canopy)"),
    ("GNDVI", "Green NDVI", "(NIR-G)/(NIR+G)", "G NIR", "chlorophyll, N"),
    ("SAVI", "Soil-Adjusted Vegetation Index (L=0.5)", "1.5*(NIR-R)/(NIR+R+0.5)", "R NIR", "biomass, sparse canopy"),
    ("OSAVI", "Optimized SAVI", "1.16*(NIR-R)/(NIR+R+0.16)", "R NIR", "biomass, LAI, low cover"),
    ("MSAVI2", "Modified SAVI (self-adjusting)", "(2*NIR+1-np.sqrt(np.abs((2*NIR+1)**2-8*(NIR-R))))/2", "R NIR", "biomass, sparse canopy"),
    ("EVI2", "Two-band Enhanced Vegetation Index", "2.5*(NIR-R)/(NIR+2.4*R+1)", "R NIR", "biomass, LAI (no saturation)"),
    ("CIre", "Chlorophyll Index - Red Edge", "NIR/RE-1", "RE NIR", "canopy chlorophyll, N"),
    ("CIg", "Chlorophyll Index - Green", "NIR/G-1", "G NIR", "canopy chlorophyll, N"),
    ("MTCI", "MERIS Terrestrial Chlorophyll Index", "(NIR-RE)/(RE-R)", "R RE NIR", "chlorophyll"),
    ("RVI", "Ratio Vegetation Index (Simple Ratio)", "NIR/R", "R NIR", "biomass, LAI"),
    ("RDVI", "Renormalized Difference Vegetation Index", "(NIR-R)/np.sqrt(np.abs(NIR+R))", "R NIR", "biomass, LAI"),
    ("MCARI", "Modified Chlorophyll Absorption Ratio Index", "((RE-R)-0.2*(RE-G))*(RE/R)", "G R RE", "chlorophyll"),
    ("TCARI_OSAVI", "TCARI / OSAVI", "(3*((RE-R)-0.2*(RE-G)*(RE/R)))/(1.16*(NIR-R)/(NIR+R+0.16))", "G R RE NIR", "chlorophyll (soil-insensitive)"),
    ("MTVI2", "Modified Triangular Vegetation Index 2", "1.5*(1.2*(NIR-G)-2.5*(R-G))/np.sqrt(np.abs((2*NIR+1)**2-(6*NIR-5*np.sqrt(np.abs(R)))-0.5))", "G R NIR", "LAI"),
    ("WDRVI", "Wide Dynamic Range VI (a=0.1)", "(0.1*NIR-R)/(0.1*NIR+R)", "R NIR", "biomass at high LAI"),
    ("DVI", "Difference Vegetation Index", "NIR-R", "R NIR", "biomass"),
    ("NGRDI", "Normalized Green-Red Difference Index", "(G-R)/(G+R)", "G R", "phenology, canopy cover"),
    ("NDREI_RE_R", "Red-edge/Red normalized difference", "(RE-R)/(RE+R)", "R RE", "chlorophyll"),
    ("MCARI2", "Modified Chlorophyll Absorption Ratio Index 2", "1.5*(2.5*(NIR-R)-1.3*(NIR-G))/np.sqrt(np.abs((2*NIR+1)**2-(6*NIR-5*np.sqrt(np.abs(R)))-0.5))", "G R NIR", "LAI"),
    ("NLI", "Non-Linear Index", "(NIR**2-R)/(NIR**2+R)", "R NIR", "biomass"),
    ("GCVI", "Green Chlorophyll Vegetation Index", "NIR/G-1", "G NIR", "chlorophyll"),
    ("EVI", "Enhanced Vegetation Index (needs Blue)", "2.5*(NIR-R)/(NIR+6*R-7.5*B+1)", "B R NIR", "biomass, LAI"),
    ("NDWI_G", "Green-NIR Water Index (McFeeters)", "(G-NIR)/(G+NIR)", "G NIR", "open water / canopy water"),
]

# Fallback default (overwritten by docs/vegetation_indices.json from the literature panel if present).
DEFAULT_15 = ["NDVI", "NDRE", "GNDVI", "SAVI", "RVI", "OSAVI", "NGRDI", "CIre", "CIg", "MSAVI2",
              "DVI", "MCARI", "EVI2", "TCARI_OSAVI", "MTCI"]

ALLOWED_NAMES = {"B", "G", "R", "RE", "NIR", "np"}
ALLOWED_NP = {"sqrt", "abs", "log", "exp", "arctan", "where", "maximum", "minimum", "power", "pi"}


def validate_formula(expr: str):
    """Allow only arithmetic on band names and a whitelist of np functions."""
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in ALLOWED_NAMES:
            raise ValueError(f"Unknown name '{node.id}' (use B, G, R, RE, NIR, np.*)")
        if isinstance(node, ast.Attribute):
            if not (isinstance(node.value, ast.Name) and node.value.id == "np" and node.attr in ALLOWED_NP):
                raise ValueError(f"Function np.{node.attr} not allowed")
        if isinstance(node, (ast.Call,)) and not isinstance(node.func, ast.Attribute):
            raise ValueError("Only np.* functions may be called")
        if isinstance(node, (ast.Lambda, ast.Subscript, ast.comprehension, ast.Starred)):
            raise ValueError("Unsupported syntax in formula")
    bands = sorted({n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id != "np"})
    return bands


def load_catalog(docs_dir=None):
    cat = []
    for abbr, name, f, bands, sens in CATALOG:
        cat.append({"abbr": abbr, "name": name, "formula": f, "bands": bands.split(),
                    "sensitivity": sens, "reference": "", "doi": "", "rank": None})
    default = list(DEFAULT_15)
    path = os.path.join(docs_dir or "", "vegetation_indices.json")
    if docs_dir and os.path.exists(path):
        try:
            lit = json.load(open(path, encoding="utf-8"))
            default = []
            by = {c["abbr"].upper(): c for c in cat}
            for rank, it in enumerate(lit, 1):
                key = it["abbr"].replace("/", "_").replace(" ", "").upper()
                f = it.get("formula_python") or it.get("formula")
                bands = validate_formula(f)
                entry = by.get(key)
                if entry is None:
                    entry = {"abbr": it["abbr"].replace("/", "_").replace(" ", ""), "name": it.get("name", it["abbr"]),
                             "formula": f, "bands": bands, "sensitivity": it.get("sensitivity", "")}
                    cat.append(entry)
                    by[key] = entry
                entry.update(formula=f, bands=bands, name=it.get("name", entry["name"]),
                             sensitivity=it.get("sensitivity", entry["sensitivity"]),
                             reference=it.get("reference", ""), doi=it.get("doi", ""), rank=rank)
                default.append(entry["abbr"])
        except Exception as e:  # keep fallback if the file is malformed
            print("Warning: could not load literature index list:", e)
    for c in cat:
        c["default"] = c["abbr"] in default
    cat.sort(key=lambda c: (c["rank"] is None, c["rank"] or 0, c["abbr"]))
    return cat


def compute_indices(bk, bands: dict, selected: list[dict]):
    """bands: {'G': dev_array, ...} on the backend; returns {abbr: dev_array} (non-finite -> NaN)."""
    ns = {k: v for k, v in bands.items()}
    ns["np"] = bk.math_ns()
    out = {}
    for ix in selected:
        if any(b not in bands for b in ix["bands"]):
            continue
        val = eval(compile(ix["formula"], ix["abbr"], "eval"), {"__builtins__": {}}, ns)
        out[ix["abbr"]] = val
    return out
