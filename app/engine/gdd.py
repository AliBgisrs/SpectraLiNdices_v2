"""Growing degree days from free, key-less weather services.

Primary : Open-Meteo Historical Weather API (ERA5 / ERA5-Land reanalysis)
          https://archive-api.open-meteo.com/v1/archive
Recent  : Open-Meteo Forecast API with past_days (covers the archive's ~5-day lag)
Fallback: NASA POWER daily point API (T2M_MAX / T2M_MIN), https://power.larc.nasa.gov
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.parse
import urllib.request

import numpy as np

# Base / upper temperature thresholds (deg C) by crop (docs/thermal_gdd_spec.md, NDAWN / Montana State
# conventions; pulses use base 0 C as in Montana State stage tables, cotton = DD60 without cap).
# NDAWN additionally caps Tmax at 21 C for spring wheat/barley before Haun 2.0 - set Tupper to 21 to mimic.
# method: "avg_cap" = McMaster & Wilhelm (1997) Method 2: clamp Tmax and Tmin to [Tbase, Tupper]
#         before averaging; "avg" = Method 1: ((Tmax+Tmin)/2 - Tbase), floored at 0.
CROPS = {
    "Corn (maize)":        {"tbase": 10.0, "tupper": 30.0, "method": "avg_cap"},
    "Soybean":             {"tbase": 10.0, "tupper": 30.0, "method": "avg_cap"},
    "Spring wheat":        {"tbase": 0.0,  "tupper": 35.0, "method": "avg_cap"},
    "Winter wheat":        {"tbase": 0.0,  "tupper": 35.0, "method": "avg_cap"},
    "Durum wheat":         {"tbase": 0.0,  "tupper": 35.0, "method": "avg_cap"},
    "Barley":              {"tbase": 0.0,  "tupper": 35.0, "method": "avg_cap"},
    "Oat":                 {"tbase": 0.0,  "tupper": 35.0, "method": "avg_cap"},
    "Canola":              {"tbase": 5.0,  "tupper": 30.0, "method": "avg_cap"},
    "Pea (field pea)":     {"tbase": 0.0,  "tupper": 30.0, "method": "avg_cap"},
    "Lentil":              {"tbase": 0.0,  "tupper": 30.0, "method": "avg_cap"},
    "Chickpea":            {"tbase": 0.0,  "tupper": 32.0, "method": "avg_cap"},
    "Dry bean":            {"tbase": 10.0, "tupper": 30.0, "method": "avg_cap"},
    "Sunflower":           {"tbase": 6.7,  "tupper": 34.0, "method": "avg_cap"},
    "Sorghum":             {"tbase": 10.0, "tupper": 38.0, "method": "avg_cap"},
    "Cotton":              {"tbase": 15.6, "tupper": 50.0, "method": "avg_cap"},
    "Potato":              {"tbase": 7.0,  "tupper": 30.0, "method": "avg_cap"},
    "Sugar beet":          {"tbase": 1.1,  "tupper": 30.0, "method": "avg_cap"},
    "Alfalfa":             {"tbase": 5.0,  "tupper": 30.0, "method": "avg_cap"},
    "Rice":                {"tbase": 10.0, "tupper": 34.4, "method": "avg_cap"},
    "Flax":                {"tbase": 5.0,  "tupper": 30.0, "method": "avg_cap"},
    "Hemp":                {"tbase": 1.0,  "tupper": 35.0, "method": "avg_cap"},
    "Custom":              {"tbase": 10.0, "tupper": 30.0, "method": "avg_cap"},
}


def _load_literature_tbase():
    """Override / extend CROPS with the panel's literature review (docs/gdd_tbase.json) when present."""
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "docs", "gdd_tbase.json")
    if not os.path.exists(path):
        return
    try:
        lit = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        print("Warning: could not read gdd_tbase.json:", e)
        return
    custom = CROPS.pop("Custom")
    for crop, v in lit.items():
        if v.get("tbase") is None:
            continue
        CROPS[crop] = {"tbase": float(v["tbase"]), "tupper": None if v.get("tupper") is None else float(v["tupper"]),
                       "method": v.get("method") or "avg_cap", "source": v.get("source", ""),
                       "doi_or_url": v.get("doi_or_url", ""), "confidence": v.get("confidence", ""),
                       "notes": v.get("notes", "")}
    CROPS["Custom"] = custom


_load_literature_tbase()


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "SpectralInfo-PlotAnalytics/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_daily_openmeteo(lat, lon, start: dt.date, end: dt.date):
    params = {"latitude": f"{lat:.5f}", "longitude": f"{lon:.5f}", "start_date": start.isoformat(),
              "end_date": end.isoformat(), "daily": "temperature_2m_max,temperature_2m_min",
              "hourly": "temperature_2m,relative_humidity_2m", "timezone": "auto"}
    url = "https://archive-api.open-meteo.com/v1/archive?" + urllib.parse.urlencode(params)
    d = _get(url)
    days = d["daily"]["time"]
    tmax = np.array([np.nan if v is None else v for v in d["daily"]["temperature_2m_max"]], float)
    tmin = np.array([np.nan if v is None else v for v in d["daily"]["temperature_2m_min"]], float)
    hourly = d.get("hourly", {})
    src = "Open-Meteo Historical Weather API (ERA5/ERA5-Land)"
    # the archive lags ~5 days - top up the tail from the forecast API (past_days <= 92)
    if np.isnan(tmax).any() and (dt.date.today() - end).days < 92:
        past = min(92, (dt.date.today() - start).days + 1)
        p2 = {"latitude": params["latitude"], "longitude": params["longitude"], "past_days": past,
              "forecast_days": 1, "daily": params["daily"], "hourly": params["hourly"], "timezone": "auto"}
        f = _get("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(p2))
        fmap = dict(zip(f["daily"]["time"], zip(f["daily"]["temperature_2m_max"], f["daily"]["temperature_2m_min"])))
        for i, day in enumerate(days):
            if np.isnan(tmax[i]) and day in fmap and fmap[day][0] is not None:
                tmax[i], tmin[i] = fmap[day]
        hmap = dict(zip(f["hourly"]["time"], zip(f["hourly"]["temperature_2m"], f["hourly"]["relative_humidity_2m"])))
        ht = hourly.get("time", [])
        for i, t in enumerate(ht):
            if hourly["temperature_2m"][i] is None and t in hmap:
                hourly["temperature_2m"][i], hourly["relative_humidity_2m"][i] = hmap[t]
        src += " + Open-Meteo Forecast API (recent days)"
    return {"source": src, "url": url, "dates": days, "tmax": tmax, "tmin": tmin, "hourly": hourly,
            "elevation": d.get("elevation"), "timezone": d.get("timezone")}


def fetch_daily_nasapower(lat, lon, start: dt.date, end: dt.date):
    params = {"parameters": "T2M_MAX,T2M_MIN", "community": "AG", "longitude": f"{lon:.4f}",
              "latitude": f"{lat:.4f}", "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"),
              "format": "JSON"}
    url = "https://power.larc.nasa.gov/api/temporal/daily/point?" + urllib.parse.urlencode(params)
    d = _get(url, timeout=90)
    p = d["properties"]["parameter"]
    keys = sorted(p["T2M_MAX"].keys())
    fill = d.get("header", {}).get("fill_value", -999.0)
    tmax = np.array([p["T2M_MAX"][k] for k in keys], float)
    tmin = np.array([p["T2M_MIN"][k] for k in keys], float)
    tmax[tmax == fill], tmin[tmin == fill] = np.nan, np.nan
    days = [f"{k[:4]}-{k[4:6]}-{k[6:]}" for k in keys]
    return {"source": "NASA POWER daily (MERRA-2)", "url": url, "dates": days, "tmax": tmax,
            "tmin": tmin, "hourly": {}, "elevation": None, "timezone": None}


def fetch_weather(lat, lon, start, end, log=print):
    try:
        w = fetch_daily_openmeteo(lat, lon, start, end)
        if np.isfinite(w["tmax"]).sum() >= 0.9 * len(w["tmax"]):
            return w
        log("Open-Meteo returned gaps; trying NASA POWER")
    except Exception as e:
        log(f"Open-Meteo failed ({e}); trying NASA POWER")
    return fetch_daily_nasapower(lat, lon, start, end)


def daily_gdd(tmax, tmin, tbase, tupper, method="avg_cap"):
    tmax = np.asarray(tmax, float)
    tmin = np.asarray(tmin, float)
    if method == "avg_cap":
        tx = np.clip(tmax, tbase, tupper)
        tn = np.clip(tmin, tbase, tupper)
        g = (tx + tn) / 2 - tbase
    else:
        g = (tmax + tmin) / 2 - tbase
    return np.clip(g, 0, None)


def gdd_between(weather, sowing: dt.date, image: dt.date, crop, tbase=None, tupper=None):
    c = dict(CROPS.get(crop, CROPS["Custom"]))
    if tbase is not None:
        c["tbase"] = float(tbase)
    if tupper is not None:
        c["tupper"] = float(tupper)
    dates = [dt.date.fromisoformat(d) for d in weather["dates"]]
    sel = np.array([(sowing <= d <= image) for d in dates])
    tu = c["tupper"] if c.get("tupper") is not None else 99.0  # no cap
    g = daily_gdd(weather["tmax"], weather["tmin"], c["tbase"], tu, c["method"])
    gs = g[sel]
    missing = int(np.isnan(gs).sum())
    return {"GDD_cum_C": float(np.nansum(gs)), "Days_after_sowing": (image - sowing).days,
            "Tbase_C": c["tbase"], "Tupper_C": c.get("tupper"), "Method": c["method"],
            "Tbase_source": c.get("source", "") + (f" ({c['doi_or_url']})" if c.get("doi_or_url") else ""),
            "Tbase_confidence": c.get("confidence", ""),
            "Missing_days": missing}, g


def air_at_time(weather, when: dt.datetime):
    """Hourly air temperature / RH closest to the image time (local time zone)."""
    h = weather.get("hourly") or {}
    if not h.get("time"):
        return None, None
    times = [dt.datetime.fromisoformat(t) for t in h["time"]]
    i = int(np.argmin([abs((t - when).total_seconds()) for t in times]))
    ta, rh = h["temperature_2m"][i], h["relative_humidity_2m"][i]
    return (None if ta is None else float(ta)), (None if rh is None else float(rh))
