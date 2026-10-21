"""Advanced thermal analysis (canopy temperature based water-stress indicators)."""
from __future__ import annotations

import numpy as np

# Non-water-stressed baselines (Tc - Ta) = a + b * VPD [kPa].
# Corn default from DeJonge et al. (2015, doi:10.1016/j.agwat.2015.03.023); others Idso (1982) via
# secondary sources (see docs/thermal_gdd_spec.md). Barley uses the wheat baseline as a surrogate.
IDSO_BASELINES = {
    "Corn":    (2.34, -1.79),
    "Wheat":   (3.38, -3.25),
    "Barley":  (3.38, -3.25),
    "Cotton":  (1.49, -2.09),
    "Alfalfa": (0.51, -1.92),
    "Soybean": (1.44, -1.34),
}
TCRIT = {"Corn": 28.0, "Cotton": 28.0, "Soybean": 27.0}  # DACT critical canopy temperature (DeJonge 2015)

VALID_C = (-30.0, 80.0)


def es_kpa(t_c):
    """Saturation vapour pressure (Tetens / FAO-56)."""
    return 0.6108 * np.exp(17.27 * t_c / (t_c + 237.3))


def field_references(tc_canopy_sample, ta=None, twet=None, tdry=None, tail_pct=5.0, dry_offset=5.0):
    """Field-wide wet/dry references for image-based CWSI (Bian et al. 2019):
    Twet = mean of the coolest 5 % canopy pixels of the whole field; Tdry = Tair + 5 C
    (or mean of the warmest 5 % when Tair is unknown)."""
    v = tc_canopy_sample[np.isfinite(tc_canopy_sample)]
    if v.size == 0:
        return None
    lo, hi = np.percentile(v, [tail_pct, 100 - tail_pct])
    tw = float(twet) if twet not in (None, "") else float(v[v <= lo].mean())
    if tdry not in (None, ""):
        td, how = float(tdry), "user"
    elif ta is not None:
        td, how = float(ta) + dry_offset, f"Tair + {dry_offset:g} C"
    else:
        td, how = float(v[v >= hi].mean()), f"mean of warmest {tail_pct:g} % canopy"
    return {"Twet_C": tw, "Tdry_C": td, "Tdry_method": how, "Tc_field_min": float(v.min()),
            "Tc_field_max": float(v.max()), "Tc_field_p1": float(np.percentile(v, 1)),
            "Tc_field_p99": float(np.percentile(v, 99)), "Tc_field_p90": float(np.percentile(v, 90))}


def plot_thermal(tc_pix, ts_pix, refs, ta=None, rh=None, crop=None):
    """Per-plot thermal indicators from canopy (tc_pix) and soil (ts_pix) temperatures (deg C)."""
    out = {}
    tc = tc_pix[np.isfinite(tc_pix)]
    ts = ts_pix[np.isfinite(ts_pix)] if ts_pix is not None else np.array([])
    if tc.size == 0:
        return out
    tcm = float(np.mean(tc))
    out["Tcanopy_mean_C"] = tcm
    out["Tcanopy_p5_C"], out["Tcanopy_p95_C"] = (float(x) for x in np.percentile(tc, [5, 95]))
    out["Tcanopy_IQR_C"] = float(np.subtract(*np.percentile(tc, [75, 25])))
    out["Tcanopy_CV_pct"] = float(100 * np.std(tc, ddof=1) / abs(tcm)) if tc.size > 1 and tcm else np.nan
    if ts.size:
        out["Tsoil_mean_C"] = float(np.mean(ts))
        out["Tsoil_minus_Tcanopy_C"] = float(np.mean(ts) - tcm)
    if refs:
        tw, td = refs["Twet_C"], refs["Tdry_C"]
        if td - tw >= 2.0:
            cw = (tc - tw) / (td - tw)
            out["CWSI_empirical_mean"] = float(np.mean(np.clip(cw, 0, 1)))
            out["CWSI_empirical_median"] = float(np.median(np.clip(cw, 0, 1)))
            with np.errstate(divide="ignore", invalid="ignore"):
                ig = (td - tc) / (tc - tw)
                i3 = (tc - tw) / (td - tc)
            ig = ig[np.isfinite(ig) & (tc > tw + 0.05)]
            i3 = i3[np.isfinite(i3) & (tc < td - 0.05)]
            out["Ig_median"] = float(np.median(ig)) if ig.size else np.nan
            out["I3_median"] = float(np.median(i3)) if i3.size else np.nan
        span = refs["Tc_field_p99"] - refs["Tc_field_p1"]
        if span > 0:
            out["NRCT_mean"] = float(np.mean(np.clip((tc - refs["Tc_field_p1"]) / span, 0, 1)))
        out["Frac_canopy_above_field_p90"] = float(np.mean(tc > refs["Tc_field_p90"]))
    ck = _crop_key(crop)
    if ck in TCRIT:
        out["DACT_C"] = float(max(0.0, tcm - TCRIT[ck]))
    if ta is not None:
        out["Tair_C"] = float(ta)
        out["CTD_C"] = float(ta - tcm)
        if rh is not None:
            vpd = float(es_kpa(ta) * (1 - rh / 100.0))
            out["VPD_kPa"] = vpd
            base = IDSO_BASELINES.get(ck)
            if base:
                a, b = base
                ll = a + b * vpd                                   # lower limit (well-watered)
                vpg = float(es_kpa(ta) - es_kpa(ta + a))           # vapour-pressure gradient
                ul = a + b * vpg                                   # upper limit (non-transpiring)
                if ul - ll > 0.1:
                    raw = (tcm - ta - ll) / (ul - ll)
                    out["CWSI_Idso_raw"] = float(raw)
                    out["CWSI_Idso"] = float(np.clip(raw, 0, 1))
                    out["Idso_LL_C"], out["Idso_UL_C"] = ll, ul
    return out


def _crop_key(crop):
    if not crop:
        return None
    c = crop.lower()
    for k in IDSO_BASELINES:
        if k.lower().split()[0] in c:
            return k
    if "maize" in c:
        return "Corn"
    return None
