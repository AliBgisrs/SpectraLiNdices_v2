# Thermal analysis, GDD and weather-data specification

Scope: the Python back end of the UAV plot-analysis app. Input is a Pix4Dfields 2.13 multispectral orthomosaic with bands Green, Red, Red edge, NIR, Thermal IR and Alpha (float32, nodata = -10000).
Reference scene: 2026-06-23, about 17:00 MDT, UTC 12N, near 48.5 N / 110.8 W (north-central Montana). Probable sensor: Sentera 6X Thermal (FLIR Radiometric Boson 320).

Every DOI listed in the References section was checked against the Crossref API on 2026-10-20. Every API URL in Section D was called on 2026-10-20 and returned the response shown. Values that could not be confirmed against a primary source are marked **[unverified]**.

---

## A. Decoding the thermal band

### A.1 What Pix4Dfields writes

The Pix4Dfields help page "Processing thermal images" lists the thermal unit per camera:

| Camera | Unit in the Pix4Dfields output |
|---|---|
| MicaSense Altum / Altum-PT | centikelvin (cK) |
| Sentera 6X (Thermal) | centikelvin (cK) |

The page says the unit "is defined by the original images", so Pix4Dfields keeps whatever unit the camera writes. Conversion given on the page:

```
T_C = lwir / 100 - 273.15
```

Check against the reference scene:

| Statistic | Raw value | °C |
|---|---|---|
| median | 28866 | **15.51** |
| P1 | 27450 | 1.35 |
| P99 | 29750 | 24.35 |
| outlier | 3756 | -235.6, not physical, so masked |

Sensor limits for the Sentera 6X Thermal, from the vendor: Boson 320 × 256 pixels, 7.5–13.5 µm, NETD < 50 mK, **absolute accuracy ±5 °C**, thermal GSD about 17.5 cm at 60 m AGL. Pix4Dfields resamples the thermal band onto the finer multispectral grid, so one thermal pixel covers about 3 × 3 to 6 × 6 output pixels. Mixed canopy/soil pixels are common as a result. See A.4.

### A.2 Unit auto-detection

1. Build the valid mask: `valid = (band != -10000) & isfinite(band) & (alpha > 0)`.
2. Compute `m = median(band[valid])`. The median is robust to the 3756-type outliers.
3. Read the unit off the first range below that contains `m`. Each range is the physical window -60 to +80 °C written in that unit. The ranges do not overlap.

| Unit | Range test on the median `m` | Conversion to °C | Typical source |
|---|---|---|---|
| °C | -60 ≤ m ≤ 80 | `v` | DJI, ThermoMap, Agisoft exports |
| K | 213 ≤ m ≤ 353 | `v - 273.15` | some reflectance tools |
| dK (0.1 K) | 2131 ≤ m ≤ 3531 | `v/10 - 273.15` | FLIR TLinear low-resolution mode |
| **cK (0.01 K)** | 21315 ≤ m ≤ 35315 | `v/100 - 273.15` | **Sentera 6X, MicaSense Altum in Pix4D** |
| mK | 213150 ≤ m ≤ 353150 | `v/1000 - 273.15` | rare |
| none of the above | – | none | uncalibrated DN: return relative metrics only and flag `thermal_unit="unknown"` |

- If a user setting (`thermal_unit`) or the GeoTIFF band description names a unit, that wins over detection.
- °F cannot be told apart from °C by value range alone. Support it only as an explicit override.
- Save `thermal_unit_detected` and `thermal_unit_confidence` in the output. Confidence is "high" when ≥ 98 % of valid pixels decode inside -40 to +80 °C.

### A.3 Outlier handling, in order

1. **Physical window.** After conversion, set pixels outside **-40 to +80 °C** to NaN. This removes the 3756 value. Make the window configurable.
2. **Robust z-filter.** Compute `MAD = median(|T - median(T)|)` over the field. Set to NaN any pixel with `|T - median| > 6 × 1.4826 × MAD`. 6σ keeps genuinely hot soil and cool wet spots.
3. **Edge and seam erosion.** Erode the Alpha mask by `ceil(2 × thermal_GSD / output_GSD)` pixels, about 2 thermal pixels. Orthomosaic edges and vignetted image borders are cold-biased.
4. **Plot statistics.** Use trimmed statistics: mean of P2–P98, the median, and P5/P95. Report `n_valid` and `pct_valid` for each plot, and drop plots with `pct_valid < 50 %` (configurable).
5. **QC flags for the scene.**
   - `thermal_cold_tail`: set when field P1 < (T_air − 15 °C). In the reference scene P1 is 1.35 °C against T_air of about 18.7 °C, so the flag fires. Likely causes are edge pixels, sky reflection off water or metal, or vignetting.
   - `thermal_offset_suspect`: set when the median canopy temperature lies more than 3 °C below the Idso lower baseline (B.4.4).

### A.4 Thermal–multispectral co-registration

- Thermal pixels are coarser, so a vegetation mask taken from the multispectral bands at full resolution includes mixed pixels at canopy edges.
- Before applying the vegetation mask to the thermal band, either:
  - (a) erode the NDVI mask by 1 thermal-pixel width, or
  - (b) block-average NDVI to the native thermal GSD and apply the threshold at that resolution.

  Use (b) when `thermal_GSD/output_GSD ≥ 3`. Leinonen & Jones (2004) showed that combining visible and thermal imagery for canopy/soil separation is essential and that edge pixels bias Tc.

---

## B. Thermal analysis per plot

### B.1 Inputs

| Input | Source | Required |
|---|---|---|
| T (°C) per pixel | thermal band, decoded as in A | yes |
| NDVI = (NIR − Red)/(NIR + Red) | orthomosaic | yes |
| plot polygons | user | yes |
| T_air (°C), RH (%) at image time | Open-Meteo hourly, interpolated to the image minute (D), or user | optional; enables CTD, Idso CWSI and the T_air+5 dry reference |
| crop type | user | optional; picks the baseline and critical temperature |
| well-watered reference plot IDs | user | optional; enables DANS |

### B.2 Canopy and soil separation

| Class | Default rule | Notes |
|---|---|---|
| canopy | NDVI ≥ **0.60** | Lower to 0.50 when field-median NDVI < 0.55 (early season). Configurable. |
| soil | NDVI ≤ **0.20** | Bare dry soil usually has NDVI 0.10–0.20 for these bands. |
| mixed | 0.20 < NDVI < 0.60 | Excluded from component temperatures. Used only in TVDI and plot-mean T_s. |
| shadow (optional) | NIR reflectance < 0.05 and NDVI < 0.6 | Exclude. At 17:00 MDT on 23 June at 48.5 N / 110.8 W, local solar time is about 15:37 and sun elevation about 41°, so shadows are noticeable. |

Optional refinement: run a 2-component Gaussian mixture on canopy-masked T and drop the warm tail. This follows the Canny/histogram cleaning in Bian et al. (2019), which made the canopy temperature histogram unimodal.

Vegetation fraction: `f_veg = n_canopy / n_valid` for each plot.

### B.3 Per-plot temperature metrics, no weather needed

| Metric | Formula | Meaning / source |
|---|---|---|
| Tc, canopy temperature | trimmed mean (P2–P98) of T[canopy]; also report the median | primary stress signal |
| T_soil | trimmed mean of T[soil] | background; dominates the plot mean at low cover |
| T_plot | mean of all valid pixels | mixed signal |
| ΔT_cs | T_soil − Tc | canopy–soil contrast. Large is healthy and transpiring; small means closed stomata or sparse cover. |
| CTSD | standard deviation of T[canopy] | heterogeneity (Zhang et al., 2019) |
| CTCV | CTSD / Tc(K) × 100, using Kelvin so the denominator stays positive | heterogeneity (Zhang et al., 2019) |
| IQR_T, P90−P10 | percentile spreads of T[canopy] | robust heterogeneity |
| hot-spot fraction | share of canopy pixels with T > field canopy P90 | patchy stress or disease |
| NRCT | (Tc − T_min,field)/(T_max,field − T_min,field), with T_min,field = P1 and T_max,field = P99 of all canopy pixels in the field | 0 is coolest, 1 is warmest. Correlated with RWC (R² 0.84) and grain yield (R² 0.81) in wheat (Elsayed et al., 2015, 2017). |
| CWSI_img, Ig, I3 | B.4.1, using the image-only fallback for T_dry | relative stress |
| DANS | Tc − Tc_NS, where Tc_NS = mean Tc of user reference plots, or field canopy P5 if none are given | degrees above non-stressed (DeJonge et al., 2015). DANS < 1 °C counts as non-stressed. |
| DACT | max(0, Tc − T_crit) | degrees above critical temperature (DeJonge et al., 2015). T_crit: corn 28 °C, cotton 28 °C, soybean 27 °C (Bockhold et al., 2011, citing Wanjura & Upchurch and Evett). |
| TVDI | B.5 | NDVI–Ts triangle (Sandholt et al., 2002) |

### B.4 Crop Water Stress Index (CWSI)

#### B.4.1 Image-based (statistical) CWSI

The index (Jones, 1999; Maes & Steppe, 2012):

```
CWSI = (Tc − T_wet) / (T_dry − T_wet)
```

Defaults from Bian et al. (2019), the "statistical CWSI_s":

- `T_wet` = **mean of the coolest 5 %** of canopy pixels over the **whole field**, not per plot. Per-plot references would normalise each plot against itself.
- `T_dry` = **T_air + 5 °C** when T_air is known. Maes & Steppe (2012) note that ΔT_dry "is usually set arbitrarily at 5 °C", and DeJonge et al. (2015) used a 5 °C upper limit for maize.
- Image-only fallback: `T_dry` = mean of the warmest 5 % of canopy pixels in the field (≈ P95–P100).
- Second fallback: `T_dry` = median T_soil, but only when f_veg is low. Soil temperature is above the true non-transpiring leaf limit, so this fallback understates CWSI.
- Guards:
  - If `T_dry − T_wet < 2 °C`, return NaN with flag `cwsi_low_contrast`. Contrast is too small after late-day cooling or under cloud.
  - Report the raw value, and a value clipped to [0, 1] for maps.

In Bian et al. (2019), the histogram-based simplified CWSI correlated with stomatal conductance (R² = 0.66) and with 0–45 cm soil water (r = 0.81).

Indices from the same references (Jones, 1999; Jones et al., 2002; Jones et al., 2009):

```
Ig = (T_dry − Tc) / (Tc − T_wet)     # proportional to stomatal conductance g_s
I3 = (Tc − T_wet) / (T_dry − Tc)     # proportional to stomatal resistance (1/g_s)
```

If the denominator is within 0.1 °C of zero, return NaN. Report the median of the per-pixel Ig rather than Ig of the mean, because Ig is non-linear.

#### B.4.2 Empirical CWSI with the Idso baseline (needs T_air and RH)

```
es(T) = 0.6108 · exp(17.27·T / (T + 237.3))        # kPa, FAO-56 Tetens
ea    = es(T_air) · RH/100
VPD   = es(T_air) − ea                              # kPa
dT    = Tc − T_air
dT_LL = a + b·VPD                                   # non-water-stressed baseline (Idso et al., 1981; Idso, 1982)
dT_UL = a + b·VPG,  VPG = es(T_air) − es(T_air + a) # non-transpiring limit (Idso, 1982); b < 0 and VPG < 0, so dT_UL > a
       or dT_UL = 5 °C (fixed, DeJonge et al., 2015) # default; more robust
CWSI_idso = (dT − dT_LL) / (dT_UL − dT_LL)
```

Theoretical CWSI (Jackson et al., 1981) needs net radiation, aerodynamic resistance and wind. Leave it out, since it cannot be computed from the orthomosaic plus T/RH.

**Applicability rules.** Baselines were fitted under clear skies around solar noon (Idso, 1982). Flag `idso_out_of_window` when the image is outside 11:00–15:00 local solar time, or when cloud cover is above 30 %. Open-Meteo supplies `cloud_cover` and `shortwave_radiation` hourly.

#### B.4.3 Default non-water-stressed baselines

Form: `Tc − Ta = a + b·VPD`, with VPD in kPa.

| Crop | a (°C) | b (°C kPa⁻¹) | Upper limit | Status / source |
|---|---|---|---|---|
| Corn/maize, default | **2.34** | **−1.79** | 5 °C | DeJonge et al. (2015), northern Colorado (semi-arid, high plains, the closest analogue to central Montana). The authors say it is very close to the Idso (1981) and Taghvaeian et al. (2012) baselines. |
| Corn/maize, Idso | 3.11 | −1.97 | VPG | Idso (1982), quoted in secondary literature; table not seen directly |
| Wheat (pre-heading) | 3.38 | −3.25 | VPG | Idso (1982), quoted in secondary literature |
| Cotton | 1.49 | −2.09 | VPG | Idso (1982), quoted in secondary literature |
| Alfalfa | 0.51 | −1.92 | VPG | Idso (1982). Intercept "≈ 0.5 °C" confirmed in secondary literature; slope **[unverified]** |
| Soybean | 1.44 | −1.34 | VPG | widely reproduced Idso (1982) value **[unverified]** |
| Barley | use wheat | use wheat | VPG | no verified Idso value; C3 small-grain surrogate **[unverified]** |
| Spring canola, potato, sugar beet, dry bean, sunflower, pea, lentil | none | none | – | No reliable transferable baseline found. Use B.4.1 only. |

Notes:
- Baselines depend on crop, growth stage and climate (DeJonge et al., 2015). The slope sets how sensitive CWSI is to errors in T_air and Tc. With a slope of −3, a 1 °C T_air error changes CWSI by 2–8 %; with −0.5 it changes it by 28–83 % (Irrigation Science 2022, DOI 10.1007/s00271-022-00819-6).
- With a ±5 °C sensor (Boson) and reanalysis T_air, label CWSI_idso **indicative** and make CWSI_img the primary output.
- Store baselines in a config file such as `crop_params.yaml` so users can enter local fits.

#### B.4.4 Worked check on the reference scene

Open-Meteo archive at 2026-06-23 17:00 MDT: T_air = 18.7 °C, RH = 44 %, giving es = 2.157 kPa and **VPD = 1.21 kPa**.

- Corn baseline: dT_LL = 2.34 − 1.79 × 1.21 = **+0.17 °C**, so Tc_LL ≈ 18.9 °C.
- Observed field median: 15.5 °C, i.e. dT ≈ −3.2 °C and CTD ≈ +3.2 °C. That gives CWSI_idso ≈ (−3.2 − 0.17)/(5 − 0.17) ≈ **−0.70**. A negative value is not physical.
- Likely causes, more than one may apply:
  - Boson absolute bias, specified as ±5 °C.
  - Late-afternoon acquisition outside the baseline window. Solar time was about 15:37, so `idso_out_of_window` fires.
  - Reanalysis T_air bias. NASA POWER gives 21.2 °C for the same instant (16:00 LST), against 18.7 °C from Open-Meteo.
- The app should therefore raise `thermal_offset_suspect` and fall back to CWSI_img, NRCT and DANS, which do not depend on absolute calibration.

Optional absolute correction: let the user enter one or two ground reference temperatures, such as a water surface or a reference panel measured with a handheld IRT. Fit `T_corr = g·T + o`. Without references, apply no offset.

### B.5 TVDI (Sandholt et al., 2002), computed over the whole field

1. Use all valid pixels with 0.1 ≤ NDVI ≤ 0.95 and NDVI bins of 0.01.
2. For each bin with n ≥ 50: `Ts_max(bin)` = P99 of T and `Ts_min(bin)` = P1 of T.
3. Dry edge: fit `Ts_max = a1 + b1·NDVI` by OLS on the bins to the right of the Ts_max peak, where b1 should be < 0.
4. Wet edge: `Ts_min` = mean of the bin P1 values, or a linear fit.
5. `TVDI = (T − Ts_min) / (a1 + b1·NDVI − Ts_min)`, clipped to [0, 1]. Aggregate to plot mean and median.
6. If b1 ≥ 0 or the dry-edge R² < 0.5, return NaN and flag `tvdi_no_dry_edge`. This happens when the field has too little range in vegetation cover.

WDI (Moran et al., 1994) is the trapezoid extension of TVDI. It needs net radiation and wind, so it is out of scope.

### B.6 Output schema, one record per plot

```
plot_id, n_valid, pct_valid, f_veg,
Tc_mean, Tc_median, Tc_p05, Tc_p95, CTSD, CTCV, IQR_T, hot_fraction,
T_soil_mean, T_plot_mean, dT_canopy_soil,
CTD (= T_air − Tc), DANS, DACT,
CWSI_img, Ig_median, I3_median, NRCT, TVDI_mean,
CWSI_idso, VPD_kPa, T_air_C, RH_pct, weather_source,
thermal_unit_detected, qc_flags[]
```

Scene-level output: T_wet, T_dry and the method used for each, dry-edge coefficients, and the baseline (a, b, crop).

### B.7 Interpretation guide for the UI

| Indicator | Low / good | Moderate | High / stressed |
|---|---|---|---|
| CWSI (img or Idso) | < 0.3 | 0.3–0.5 | > 0.5. Irrigation trigger is often 0.3–0.4 in maize and cotton. |
| CTD (T_air − Tc) | > +2 °C (transpiring) | 0 to +2 °C | < 0 (canopy warmer than air) |
| DANS | < 1 °C | 1–3 °C | > 3 °C |
| NRCT | < 0.3 | 0.3–0.6 | > 0.6. Relative within the field only. |
| CTSD within a plot | < 0.5 °C | 0.5–1.5 °C | > 1.5 °C (patchy) |

---

## C. Growing degree days (GDD)

### C.1 Formulas

Daily T_max and T_min are in °C. T_b is the base temperature and T_u the upper cutoff.

**Method A**, average then floor (McMaster & Wilhelm, 1997, interpretation 1):
```
GDD_day = max(0, (min(Tmax,Tu) + min(Tmin,Tu))/2 − Tb)
```

**Method B**, clamp each extreme, then average. This is the NDAWN/NDSU "86/50" style and the app default:
```
Tx = min(max(Tmax, Tb), Tu)      # Tu = +inf when the crop has no upper cutoff
Tn = min(max(Tmin, Tb), Tu)
GDD_day = (Tx + Tn)/2 − Tb
```

For corn this is exactly the 86/50 °F = 30/10 °C method. Method A underestimates GDD on days when T_min < T_b < T_max (McMaster & Wilhelm, 1997). Use Method B everywhere unless the crop row in C.2 says otherwise.

Further definitions:
- Accumulation: `GDD_cum = Σ GDD_day` from the biofix date (planting, emergence or green-up) through the image date, inclusive.
- Units: `GDD_°F = 1.8 × GDD_°C`. Expose both.
- Optional hourly method using Open-Meteo hourly temperature: `GDD_day = Σ_h max(0, min(T_h, Tu) − Tb) / 24`.

**Rice, Arkansas DD50:**
```
Tmax' = min(Tmax, 94 °F)
Tmin' = min(Tmin, 70 °F)
DD50  = max(0, (Tmax' + Tmin')/2 − 50)      # ≤ 32 °F-units per day
```
In °C the caps are 34.4 and 21.1, with base 10.

**Potato, P-days (Sands et al., 1979).** Cardinal temperatures: T_min = 7, T_opt = 21, T_max = 30 °C.
```
P(T) = 0                          if T < 7 or T ≥ 30
     = 10·[1 − (T−21)²/(21−7)²]   if 7 ≤ T < 21
     = 10·[1 − (T−21)²/(30−21)²]  if 21 ≤ T < 30
P-day = (1/24)·[5·P(Tn) + 8·P((2Tn+Tx)/3) + 8·P((Tn+2Tx)/3) + 3·P(Tx)]
```

**Spring wheat and barley, NDAWN (Bauer et al., 1984).** Base 0 °C. T_max is capped at 21 °C (70 °F) until Haun stage 2.0, then at 35 °C (95 °F). Haun 2.0 falls at 395 °F-GDD for wheat (≈ 219 °C-GDD) and 384 °F-GDD for barley (≈ 213 °C-GDD), counted from planting. Max and min below 0 °C are set to 0.

### C.2 Crop parameter table

| Crop | T_b °C (°F) | T_u °C (°F) | Method | Source / status |
|---|---|---|---|---|
| Corn | 10 (50) | 30 (86), applied to both T_max and T_min | B | NDAWN corn page; MSU Extension |
| Soybean | 10 (50) | 30 (86) | B | NDSU (same base as corn); upper-cap convention |
| Spring wheat | 0 (32) | T_max cap 21 → 35 (70 → 95) at Haun 2.0 | B + stage cap | NDAWN wheat (Bauer et al., 1984) |
| Spring wheat, simple | 0 (32) | none | B | Montana State Univ. MT200103AG (Miller, Lanier & Brandt, 2001) |
| Winter wheat | 0 (32) | none (model upper threshold 130 °F is irrelevant) | B; biofix = spring green-up, or Jan 1 if unknown | MSU MT200103AG |
| Barley | 0 (32) | T_max cap 21 → 35 at Haun 2.0 | B + stage cap | NDAWN barley |
| Oat, flax, canary seed | 0 (32) | none | B | MSU MT200103AG |
| Canola | 5 (41) | none | B | NDAWN canola. The MSU stage table uses base 0 °C; match the base to whichever stage table you use. |
| Cotton | 15.6 (60) | none (DD60). Optional 30 (86) "modified". Arizona model: 12.8/30 °C (55/86 °F), single sine. | A/B | Oklahoma State & Univ. of Arkansas Extension |
| Potato | 7 (P-days, 7/21/30 °C) | 30 | P-days | Sands et al. (1979). Simple alternative: T_b 4.4 or 7.2 °C (40/45 °F), Purdue MRCC. |
| Sugar beet | 1.1 (34) | 30 (86) on T_max | B | NDAWN sugarbeet (Holen & Dexter, 1997) |
| Dry bean | 10 (50) | 30 (86) | B | common convention **[unverified]** |
| Sunflower | 6.7 (44) | none | B | NDAWN sunflower. The MSU stage table uses base 0 °C. |
| Sorghum | 10 (50) | 30 (86) | B | base from Purdue MRCC; upper cap **[unverified]** |
| Field pea | 0 (32) | none | B | MSU MT200103AG. Processing pea (Quebec) uses 5 °C; AMS glossary gives 40 °F for canning pea. |
| Lentil, chickpea | 0 (32) | none | B | MSU MT200103AG |
| Alfalfa | 5 (41) | none | B | common convention **[unverified primary]** |
| Rice | 10 (50) | T_max cap 34.4 (94), T_min cap 21.1 (70) | DD50 | Univ. of Arkansas DD50 program |

GDD (°C-days, base 0 °C) to complete maturity, from MSU MT200103AG (Saskatchewan data that MSU uses for Montana):
- barley 1269–1522
- HRS wheat 1538–1665
- pea 1527–1686
- lentil 1740–1876
- sunflower 1780–1972
- canola (B. napus): stage 8.1 at 1326–1445; swathing at 1432–1557

Anthesis: barley 738–936, wheat 807–901. These can drive an optional "expected growth stage" label.

### C.3 Example for the reference site (verified data)

Open-Meteo archive, 2026-05-01 to 2026-06-23 (54 days, no nulls), Method B:

| Crop | T_b / T_u (°C) | GDD (°C-days) |
|---|---|---|
| Corn | 10/30 | 282.6 |
| Wheat | 0 | 737.3 |
| Canola | 5 | 483.9 |
| Sunflower | 6.7 | 406.8 |
| Sugar beet | 1.1/30 | 678.0 |

Inputs to the app:
- `planting_date`: required for GDD. Default to the first of the planting month and flag `gdd_biofix_default`.
- `crop`: required.

---

## D. Weather data (no API key)

### D.1 Open-Meteo Historical (Archive) API, primary source

```
GET https://archive-api.open-meteo.com/v1/archive
    ?latitude=48.5&longitude=-110.8
    &start_date=2026-05-01&end_date=2026-06-23
    &daily=temperature_2m_max,temperature_2m_min
    &hourly=temperature_2m,relative_humidity_2m
    &timezone=auto
```

Optional extras:
- `&temperature_unit=fahrenheit`
- hourly variables `,cloud_cover,shortwave_radiation,wind_speed_10m`, used for the Idso validity flags
- `&models=era5_land` (or `era5`, `best_match`), for climate consistency

Verified on 2026-10-20 (HTTP 200). Response, abridged:

```json
{
  "latitude": 48.541298, "longitude": -110.75757,
  "generationtime_ms": 0.26, "utc_offset_seconds": -21600,
  "timezone": "America/Denver", "timezone_abbreviation": "GMT-6",
  "elevation": 1021.0,
  "hourly_units": {"time": "iso8601", "temperature_2m": "°C", "relative_humidity_2m": "%"},
  "hourly": {
    "time": ["2026-05-01T00:00", "...", "2026-06-23T16:00", "2026-06-23T17:00", "2026-06-23T18:00", "..."],
    "temperature_2m":       [..., 18.3, 18.7, 19.1, ...],
    "relative_humidity_2m": [..., 45,   44,   41,   ...]
  },
  "daily_units": {"time": "iso8601", "temperature_2m_max": "°C", "temperature_2m_min": "°C"},
  "daily": {
    "time": ["2026-05-01", "2026-05-02", "2026-05-03", "...", "2026-06-21", "2026-06-22", "2026-06-23"],
    "temperature_2m_max": [22.3, 18.3, 22.6, ..., 20.2, 17.9, 19.1],
    "temperature_2m_min": [2.8,  7.8,  6.3,  ..., 10.5,  9.3,  6.8]
  }
}
```

Facts from the docs:
- `start_date` and `end_date` are required (ISO yyyy-mm-dd).
- `timezone=auto` resolves the zone from the coordinates. Daily aggregates are then computed on local days. The offset is fixed at request time, so DST changes inside the range are ignored.
- Data delay: ERA5 and ERA5-Land are "daily with 5 days delay". The default best match combines IFS HRES, ERA5 and ERA5-Land, and IFS updates "every 6 hours with no delay". The most recent ~5 days can still be missing or provisional.
- Grid: IFS 9 km, ERA5-Land ~11 km, ERA5 ~25 km. The returned cell centre (48.54, −110.76) and elevation (1021 m) differ from the requested point. Store both.
- An API key is "only required to commercial use". The free tier is for non-commercial use. A limit of about 10 000 calls/day and CC-BY 4.0 attribution are widely documented but **[unverified on the page fetched]**. Show "Weather data by Open-Meteo.com" in the UI.

Parsing rules:
- Convert `hourly.time` to local datetimes. Values may be `null`; treat as NaN.
- Interpolate T and RH linearly to the image timestamp, e.g. 17:00 → index of "2026-06-23T17:00".
- Read the image time from EXIF/XMP, convert it to UTC, then to the `utc_offset_seconds` of the response.

### D.2 Open-Meteo Forecast API, for recent dates inside the archive lag

```
GET https://api.open-meteo.com/v1/forecast
    ?latitude=48.5&longitude=-110.8
    &daily=temperature_2m_max,temperature_2m_min
    &hourly=temperature_2m,relative_humidity_2m
    &timezone=auto&past_days=92&forecast_days=1
```

- `past_days` accepts 0–92. Verified on 2026-10-20: it returned 93 daily rows, with the **oldest rows `null`**. Only roughly the last 2–3 months are populated.
- `start_date=2026-06-20&end_date=2026-06-23` on this endpoint returned **HTTP 400** on 2026-10-20, because the dates lie outside its window. Use it only for dates within the last ~90 days.
- The same JSON structure as D.1.

Selection logic:
```
if end_date <= today - 6 days:  use archive
else: archive for [start, today-6] + forecast(past_days=min(92, (today-start).days)) for the remainder;
      fill archive nulls from forecast; record weather_source per day
```

### D.3 NASA POWER, fallback (no key; MERRA-2 0.5° × 0.625°)

Daily:
```
GET https://power.larc.nasa.gov/api/temporal/daily/point
    ?parameters=T2M_MAX,T2M_MIN,T2M,RH2M
    &community=AG&longitude=-110.8&latitude=48.5
    &start=20260501&end=20260623&format=JSON
```

Hourly:
```
GET https://power.larc.nasa.gov/api/temporal/hourly/point
    ?parameters=T2M,RH2M&community=AG
    &longitude=-110.8&latitude=48.5
    &start=20260623&end=20260623&format=JSON&time-standard=LST
```

`time-standard` takes LST or UTC. **LST in POWER is local standard time, without DST: MST = UTC−7.** The image time 17:00 MDT therefore corresponds to POWER key `2026062316`.

Verified response, daily (abridged; `messages: []`, header `api` v2.10.0, sources MERRA2/POWER):

```json
{
  "type": "Feature",
  "geometry": {"type": "Point", "coordinates": [-110.8, 48.5, 930.6]},
  "properties": {"parameter": {
      "T2M_MAX": {"20260501": 24.67, "20260502": 15.87, "...": 0, "20260623": 22.89},
      "T2M_MIN": {"20260501": 5.08,  "20260502": 8.35,  "...": 0, "20260623": 7.44},
      "T2M":     {"20260501": 15.3,  "...": 0, "20260623": 15.93},
      "RH2M":    {"20260501": 41.52, "...": 0, "20260623": 59.86}}},
  "header": {"title": "NASA/POWER Source Native Resolution Daily Data",
             "fill_value": -999.0, "start": "20260501", "end": "20260623", "time_standard": "LST"},
  "messages": [],
  "parameters": {"T2M_MAX": {"units": "C"}, "T2M_MIN": {"units": "C"}, "T2M": {"units": "C"}, "RH2M": {"units": "%"}},
  "times": {"data": 0.0, "process": 0.0}
}
```

Hourly response: `properties.parameter.T2M["2026062316"] = 22.11`, `"2026062317" = 21.19`, `"2026062318" = 19.96`. RH2M is 34.96, 37.68 and 41.92 % for the same hours.

Parsing rules:
- Keys are `YYYYMMDD` (daily) or `YYYYMMDDHH` (hourly).
- Replace `-999` with NaN.
- Daily values are aggregated on LST days.

**Cross-source discrepancy at the reference time** (17:00 MDT, 2026-06-23):

| Source | T_air | RH |
|---|---|---|
| Open-Meteo | 18.7 °C | 44 % |
| NASA POWER (16:00 LST) | 21.19 °C | 37.7 % |

Daily T_max also differs: 19.1 vs 22.9 °C. Grid cells are coarse and elevation differs (1021 m vs 931 m model terrain). Always store `weather_source` and show the values to the user, with an override field. Consider an elevation lapse-rate adjustment of −6.5 °C km⁻¹ from the model cell elevation to the DEM elevation of the field; this is optional.

### D.4 Minimal Python client (requests only)

```python
import requests, datetime as dt
OM_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
OM_FORECAST = "https://api.open-meteo.com/v1/forecast"
POWER_DAILY = "https://power.larc.nasa.gov/api/temporal/daily/point"

def om_archive(lat, lon, start, end):
    p = dict(latitude=lat, longitude=lon, start_date=start, end_date=end,
             daily="temperature_2m_max,temperature_2m_min",
             hourly="temperature_2m,relative_humidity_2m", timezone="auto")
    r = requests.get(OM_ARCHIVE, params=p, timeout=30); r.raise_for_status(); return r.json()

def power_daily(lat, lon, start, end):
    p = dict(parameters="T2M_MAX,T2M_MIN,T2M,RH2M", community="AG",
             latitude=lat, longitude=lon, start=start.replace("-", ""),
             end=end.replace("-", ""), format="JSON")
    r = requests.get(POWER_DAILY, params=p, timeout=60); r.raise_for_status()
    par = r.json()["properties"]["parameter"]
    clean = lambda v: None if v == -999 else v
    return {k: {d: clean(v) for d, v in s.items()} for k, s in par.items()}

def gdd_method_b(tmax, tmin, tb, tu=float("inf")):
    tx = min(max(tmax, tb), tu); tn = min(max(tmin, tb), tu)
    return (tx + tn) / 2.0 - tb
```

Use a 24 h on-disk cache keyed on (lat rounded to 0.01, lon, start, end, source), plus 3 retries with backoff.

---

## References (DOIs checked against Crossref, 2026-10-20)

Thermal / CWSI (peer-reviewed, mostly Q1):
1. Idso, S.B., Jackson, R.D., Pinter, P.J., Reginato, R.J., Hatfield, J.L. (1981). Normalizing the stress-degree-day parameter for environmental variability. *Agricultural Meteorology* 24, 45–55. https://doi.org/10.1016/0002-1571(81)90032-7
2. Jackson, R.D., Idso, S.B., Reginato, R.J., Pinter, P.J. (1981). Canopy temperature as a crop water stress indicator. *Water Resources Research* 17, 1133–1138. https://doi.org/10.1029/WR017i004p01133
3. Idso, S.B. (1982). Non-water-stressed baselines: a key to measuring and interpreting plant water stress. *Agricultural Meteorology* 27, 59–70. https://doi.org/10.1016/0002-1571(82)90020-6
4. Jones, H.G. (1999). Use of infrared thermometry for estimation of stomatal conductance as a possible aid to irrigation scheduling. *Agricultural and Forest Meteorology* 95, 139–149. https://doi.org/10.1016/S0168-1923(99)00030-1
5. Jones, H.G., Stoll, M., Santos, T., de Sousa, C., Chaves, M.M., Grant, O.M. (2002). Use of infrared thermography for monitoring stomatal closure in the field: application to grapevine. *Journal of Experimental Botany* 53, 2249–2260. https://doi.org/10.1093/jxb/erf083
6. Leinonen, I., Jones, H.G. (2004). Combining thermal and visible imagery for estimating canopy temperature and identifying plant stress. *Journal of Experimental Botany* 55, 1423–1431. https://doi.org/10.1093/jxb/erh146
7. Cohen, Y., Alchanatis, V., et al. (2005). Estimation of leaf water potential by thermal imagery and spatial analysis. *Journal of Experimental Botany* 56, 1843–1852. https://doi.org/10.1093/jxb/eri174
8. Jones, H.G., Serraj, R., Loveys, B.R., Xiong, L., et al. (2009). Thermal infrared imaging of crop canopies for the remote diagnosis and quantification of plant responses to water stress in the field. *Functional Plant Biology* 36, 978–989. https://doi.org/10.1071/FP09123
9. Meron, M., et al. (2010). Crop water stress mapping for site-specific irrigation by thermal imagery and artificial reference surfaces. *Precision Agriculture* 11, 148–162. https://doi.org/10.1007/s11119-009-9153-x
10. Maes, W.H., Steppe, K. (2012). Estimating evapotranspiration and drought stress with ground-based thermal remote sensing in agriculture: a review. *Journal of Experimental Botany* 63, 4671–4712. https://doi.org/10.1093/jxb/ers165
11. Taghvaeian, S., Chávez, J.L., Hansen, N.C. (2012). Infrared thermometry to estimate crop water stress index and water use of irrigated maize in northeastern Colorado. *Remote Sensing* 4, 3619–3637. https://doi.org/10.3390/rs4113619
12. Gonzalez-Dugo, V., Zarco-Tejada, P., et al. (2013). Using high resolution UAV thermal imagery to assess the variability in the water status of five fruit tree species within a commercial orchard. *Precision Agriculture* 14, 660–678. https://doi.org/10.1007/s11119-013-9322-9
13. DeJonge, K.C., Taghvaeian, S., Trout, T.J., Comas, L.H. (2015). Comparison of canopy temperature-based water stress indices for maize. *Agricultural Water Management* 156, 51–62. https://doi.org/10.1016/j.agwat.2015.03.023
14. Elsayed, S., Rischbeck, P., Schmidhalter, U. (2015). Comparing the performance of active and passive reflectance sensors to assess the normalized relative canopy temperature and grain yield of drought-stressed barley cultivars. *Field Crops Research* 177. https://doi.org/10.1016/j.fcr.2015.03.010
15. Elsayed, S., Elhoweity, M., Ibrahim, H.H., Dewir, Y.H., Migdadi, H.M., Schmidhalter, U. (2017). Thermal imaging and passive reflectance sensing to estimate the water status and grain yield of wheat under different irrigation regimes. *Agricultural Water Management* 189, 98–110. https://doi.org/10.1016/j.agwat.2017.05.001
16. Ihuoma, S.O., Madramootoo, C.A. (2017). Recent advances in crop water stress detection. *Computers and Electronics in Agriculture* 141, 267–275. https://doi.org/10.1016/j.compag.2017.07.026
17. Bian, J., Zhang, Z., Chen, J., et al. (2019). Simplified evaluation of cotton water stress using high resolution unmanned aerial vehicle thermal imagery. *Remote Sensing* 11(3), 267. https://doi.org/10.3390/rs11030267. Source of T_wet = mean of the coolest 5 % of canopy pixels and T_dry = T_air + 5 °C.
18. Zhang, L., Niu, Y., Zhang, H., Han, W., Li, G., Tang, J., Peng, X. (2019). Maize canopy temperature extracted from UAV thermal and RGB imagery and its application in water stress monitoring. *Frontiers in Plant Science* 10, 1270. https://doi.org/10.3389/fpls.2019.01270. Source of CTSD/CTCV and the 28 °C DANS reference.
19. Gerhards, M., Schlerf, M., Mallick, K., Udelhoven, T. (2019). Challenges and future perspectives of multi-/hyperspectral thermal infrared remote sensing for crop water-stress detection: a review. *Remote Sensing* 11, 1240. https://doi.org/10.3390/rs11101240
20. Pineda, M., Barón, M., Pérez-Bueno, M.L. (2021). Thermal imaging for plant stress detection and phenotyping. *Remote Sensing* 13(1), 68. https://doi.org/10.3390/rs13010068
21. (2022). Assessing the impact of measurement errors in the calculation of CWSI for characterizing the water status of several crop species. *Irrigation Science*. https://doi.org/10.1007/s00271-022-00819-6
22. Sandholt, I., Rasmussen, K., Andersen, J. (2002). A simple interpretation of the surface temperature/vegetation index space for assessment of surface moisture status. *Remote Sensing of Environment* 79, 213–224. https://doi.org/10.1016/S0034-4257(01)00274-7
23. Moran, M.S., Clarke, T.R., Inoue, Y., Vidal, A. (1994). Estimating crop water deficit using the relation between surface-air temperature and spectral vegetation index. *Remote Sensing of Environment* 49, 246–263. https://doi.org/10.1016/0034-4257(94)90020-5
24. Bockhold, D.L., Thompson, A.L., Sudduth, K.A., Henggeler, J.C. (2011). Irrigation scheduling based on crop canopy temperature for humid environments. *Transactions of the ASABE* 54(6), 2021–2028. Source of T_crit for corn, cotton and soybean.

GDD:
25. McMaster, G.S., Wilhelm, W.W. (1997). Growing degree-days: one equation, two interpretations. *Agricultural and Forest Meteorology* 87, 291–300. https://doi.org/10.1016/S0168-1923(97)00027-0
26. Sands, P.J., Hackett, C., Nix, H.A. (1979). A model of the development and bulking of potatoes (*Solanum tuberosum* L.). *Field Crops Research* 2, 309–331. https://doi.org/10.1016/0378-4290(79)90033-9
27. NDAWN/NDSU crop GDD help pages: corn, wheat, barley, canola, sunflower, sugarbeet. https://ndawn.ndsu.nodak.edu/help-corn-growing-degree-days.html (replace `corn` with `wheat`, `barley`, `canola`, `sunflower` or `sugarbeet`).
28. Miller, P., Lanier, W., Brandt, S. (2001). Using Growing Degree Days to Predict Plant Stages. Montana State University Extension MT200103AG. https://landresources.montana.edu/soilfertility/documents/PDF/pub/GDDPlantStagesMT200103AG.pdf
29. University of Arkansas DD50 Rice Program, About page. https://dd50.uada.edu/About

Sensor / software / data:
30. Pix4D Support, "Processing thermal images – PIX4Dfields". https://support.pix4d.com/hc/en-us/articles/374474578998809
31. Sentera 6X Thermal product and spec pages: FLIR Radiometric Boson 320, ±5 °C, NETD < 50 mK.
32. Open-Meteo Historical Weather API docs: https://open-meteo.com/en/docs/historical-weather-api. Forecast API docs: https://open-meteo.com/en/docs
33. NASA POWER API, Daily and Hourly point endpoints: https://power.larc.nasa.gov/
