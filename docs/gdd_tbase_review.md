# GDD base / upper temperatures by crop — literature review

Prepared 2026-10-21 for SpectralInfo thermal/GDD module. Companion machine-readable file: `gdd_tbase.json`.

## 1. Method

**Equation.** Daily GDD = ((Tmax + Tmin)/2) − Tbase, floored at zero, following McMaster & Wilhelm (1997), *Agric. For. Meteorol.* 87:291–300. They distinguish two interpretations of the same equation:

- **Method 1** — compute the daily mean first; if the mean < Tbase, set the mean = Tbase (equivalently floor the day's GDD at 0). No upper cap.
- **Method 2** — before averaging, reset any Tmax or Tmin that is below Tbase to Tbase, and (where used) reset any Tmax above Tupper to Tupper. This is the "corn method" used operationally by NDAWN, Oklahoma Mesonet ("cutoff method"), KSU Mesonet, HPRCC/AWDN and most US extension calculators. McMaster & Wilhelm report that Method 1 vs Method 2 differ by up to 83 % for wheat (Tbase 0 °C) and more for corn (Tbase 10 °C), so the method must be stated with the Tbase.

**Recommendation for the module.** Use Method 2 for every crop. In the JSON, `method = "avg_cap"` means Method 2 with Tmax capped at `tupper`; `method = "avg"` means Method 2 with the Tbase floor only (no upper cap). When `tupper` is null the floor-only variant is intended.

**Sources actually opened (tool-verified).** NDAWN help pages (corn, wheat, barley, canola, sunflower, soybean, sugarbeet); Miller, Lanier & Brandt 2001 MSU MontGuide MT200103AG (PDF, Table 1); UNL Digital Commons record of McMaster & Wilhelm 1997 (abstract) and the doi.org resolver for 10.1016/S0168-1923(97)00027-0 (resolves to Elsevier PII S0168192397000270); Oklahoma Mesonet degree-day calculator text (A. Sutherland, 2012); KSU MF3740 *Understanding Growing Degree Days* (Table 1, citing Luo 2011); Univ. Arkansas MP297 Grain Sorghum Handbook ch. 1; Univ. Arkansas FSA-2124 DD50 rice program; OSU PSS-3002 cotton heat units; Penn State *Understanding Growing Degree Days*; UF/IFAS AE428 *Degree-Days: Growing, Heating, and Cooling*; Manitoba Agriculture *Heat Units for Potato Production*; Allen, Vigil & Jabro 2014 *Agron. J.* (camelina, full PDF); Lisson, Mendham & Carberry 2000 *Aust. J. Exp. Agric.* (hemp, abstract); Mohammed Yusoff et al. 2013 *Crop Pasture Sci.* (faba bean, oats, abstract); Torabi et al. 2015 *Int. J. Plant Prod.* (safflower emergence); Safahani et al. 2017 *Legume Res.* (lentil emergence); Mirsky et al. 2009 *Agron. J.* (cereal rye); Rothamsted repository record for Milford et al. 1985 (sugar beet, citation only); Canola Council of Canada "GDDs to date" (base 0 practice). Items marked "search-found" below were surfaced by WebSearch with quoted text but the full page was not opened (paywall/403); they are used only to describe the range, never as the sole basis for a recommendation.

**Confidence rubric.** High = operational network value (NDAWN/Mesonet/extension) plus peer-reviewed agreement; Medium = one authoritative source or a modest disagreement (≤ 5 °C spread) resolved by a clear operational convention; Low = only stage-specific (germination/emergence) estimates, or sources disagree widely with no dominant operational convention.

## 2. Summary table

| Crop | Tbase °C (rec.) | Tupper °C (rec.) | Range found in literature | Method notes | Source(s) with DOI/URL | Confidence |
|---|---|---|---|---|---|---|
| Corn (maize) | 10 | 30 | Tbase 10; Tupper 30 (NDAWN, Mesonet, KSU, PSU) | Method 2; cap Tmax 30, floor Tmin/Tmax 10 | McMaster & Wilhelm 1997 doi:10.1016/S0168-1923(97)00027-0; NDAWN https://ndawn.ndsu.nodak.edu/help-corn-growing-degree-days.html | High |
| Soybean | 10 | 30 | Tbase 7–14 in models; Tupper 30 (NDAWN) or 35 (OK Mesonet) | Method 2; NDAWN reuses corn thresholds | NDAWN https://ndawn.ndsu.nodak.edu/help-soybean-growing-degree-days.html; OK Mesonet calculator PDF | High |
| Spring wheat | 0 | none | Tbase 0 (NDAWN, MSU, McMaster); some cultivar studies 3–8 (search-found) | Method 2 floor 0. NDAWN caps Tmax at 21 °C before Haun 2.0 and 35 °C after; omitted here as stage-dependent | NDAWN https://ndawn.ndsu.nodak.edu/help-wheat-growing-degree-days.html (Bauer et al. 1984); MSU MT200103AG | High |
| Winter wheat | 0 | none | Tbase 0; Tupper 30 (OK Mesonet) or 26 (KSU MF3740) | As spring wheat; caps rarely bind in winter-wheat season | McMaster & Wilhelm 1997; OK Mesonet; KSU MF3740 | High |
| Durum wheat | 0 | none | No durum-specific value found; treated as wheat | As wheat (NDAWN model derived from hard red spring wheat) | NDAWN wheat page; MSU MT200103AG (hard red) | Medium |
| Barley | 0 | none | Tbase 0 (NDAWN, MSU); KSU lists 0/30 | Method 2 floor 0; NDAWN stage caps 21/35 °C as for wheat | NDAWN https://ndawn.ndsu.nodak.edu/help-barley-growing-degree-days.html; MSU MT200103AG | High |
| Oat | 0 | none | 0 (MSU); 1.6 emergence / 3.0 leaf appearance (Yusoff 2013); 5 (KSU) | Method 2 floor 0 | MSU MT200103AG; Mohammed Yusoff et al. 2013 doi:10.1071/CP12305 | Medium |
| Canola | 5 | none | 0 (MSU, Canola Council), 2.2 (KSU), 5 (NDAWN, Canadian "Base 5"); emergence 0.4–1.2 (Vigil 1997, search-found) | Method 2 floor 5; no upper cap identified | NDAWN https://ndawn.ndsu.nodak.edu/help-canola-growing-degree-days.html; MSU MT200103AG | Medium |
| Pea (field pea) | 0 | none | 0 (MSU); 5 in processing-pea heat-unit work (search-found) | Method 2 floor 0 | MSU MT200103AG https://landresources.montana.edu/soilfertility/documents/PDF/pub/GDDPlantStagesMT200103AG.pdf | Medium |
| Lentil | 0 | none | 0 (MSU; Australian phenology data); 4.5 for emergence (Safahani 2017) | Method 2 floor 0 | MSU MT200103AG; Safahani et al. 2017 doi:10.18805/lr.v0i0.7301 | Medium |
| Chickpea | 0 | none | 0 (MSU desi); 1 (NZ kabuli, search-found); 4–6 emergence/germination | Method 2 floor 0 | MSU MT200103AG | Medium |
| Dry bean | 10 | 30 | 10/30 (HPRCC-AWDN, search-found); 10–15 Brazilian studies | Method 2; corn thresholds | HPRCC https://hprcc.unl.edu/archive/data/AWDN/crop_drybean.txt (search-found); PSU/NDAWN warm-season convention | Medium |
| Sunflower | 6.7 | none | 0 (MSU), 6.7 (NDAWN, Manitoba), 7.8 (KSU); 3.9–9.7 by genotype (Maldaner 2018, search-found) | Method 2 floor 6.7; NDAWN: no upper threshold identified | NDAWN https://ndawn.ndsu.nodak.edu/help-sunflower-growing-degree-days.html | High |
| Sorghum | 10 | 37.8 | Tbase 7.8–12.8; Tupper 35–37.8 | Method 2; cap Tmax 100 °F, floor 50 °F | Univ. Arkansas MP297 ch.1 https://www.uaex.uada.edu/publications/PDF/MP297/1_growth_development.pdf; KSU MF3740 | High |
| Cotton | 15.6 | 35 | Tbase 15.6 universal (DD60); Tupper none (OSU), 33.9 (UGA), 35 (KSU, TAMU), 37.8 (OK Mesonet) | Method 2; cap Tmax 95 °F | OSU PSS-3002 https://pods.okstate.edu/fact-sheets/PSS-3002.pdf; KSU MF3740 | High (Tbase) / Medium (cap) |
| Potato | 7 | 30 | 7 (Sands et al. 1979 via FSHS; Manitoba); upper 30 (Manitoba), 36 (HPRCC) | Method 2; P-days are a separate nonlinear index | Manitoba Agric. https://www.gov.mb.ca/agriculture/weather/heat-units-for-potato-production-in-mb.html; Sands, Hackett & Nix 1979 Field Crops Res. 2:309–331 | Medium |
| Sugar beet | 1.1 | 30 | 1.1/30 (NDAWN); 3 for leaf expansion (Milford et al. 1985) | Method 2; cap Tmax 30 | NDAWN https://ndawn.ndsu.nodak.edu/help-sugarbeet-growing-degree-days.html; Milford et al. 1985 doi:10.1111/j.1744-7348.1985.tb03106.x | Medium |
| Alfalfa | 5 | 30 | 5 (PSU, UMN, MSU-Ext, OK Mesonet); 0 (KSU); Tupper 30 (Mesonet, KSU) | Method 2; cap Tmax 30 | Penn State https://extension.psu.edu/understanding-growing-degree-days; OK Mesonet calculator | High |
| Rice | 10 | 34.4 | Tbase 10 (DD50 = 50 °F); cap 94 °F; DD50 also caps Tmin at 70 °F | Method 2; cap Tmax 94 °F (DD50 additionally sets Tmin = 70 °F if > 70 °F) | Univ. Arkansas FSA-2124 https://www.uaex.uada.edu/publications/PDF/FSA-2124.pdf | High |
| Flax | 0 | none | 0 (MSU; Vermont trials base 32 °F); 4.4–5 in other extension tables | Method 2 floor 0 | MSU MT200103AG | Medium |
| Hemp | 1 | none | 0–2.5 (prior work), 1.0 adopted (Lisson 2000); 3.4 germination (Geneve, search-found); US trials use 10 | Method 2 floor 1 | Lisson, Mendham & Carberry 2000 doi:10.1071/EA99058 | Low |
| Camelina | 0 | none | −0.7 (Allen 2014 emergence); 0.45–0.51 (Sintim 2015 abstract, search-found) | Method 2 floor 0 | Allen, Vigil & Jabro 2014 doi:10.2134/agronj13.0469 | Low |
| Safflower | 3.4 | none | 3.4 emergence (Torabi 2015); 5 common in models | Method 2 floor 3.4 | Torabi, Adibniya & Rahimi 2015 doi:10.22069/ijpp.2015.2223 | Low |
| Millet | 10 | none | 10 (pearl millet planting studies; UNL proso trials base 50 °F); germination 8–13.5 | Method 2 floor 10 | UNL CropWatch proso trial reports (base 50 °F, search-found); Mohamed, Clarke & Ong 1988 (search-found) | Low |
| Peanut | 13.3 | 35 | Tbase 12.8 (OK Mesonet), 13.3 (UF/IFAS, UGA/NCSU DD56); Tupper 30 (AgroClimate default), 35 (Mesonet, NCSU) | Method 2; cap Tmax 95 °F | UF/IFAS AE428 https://ask.ifas.ufl.edu/publication/AE428; OK Mesonet calculator | Medium |
| Faba bean | 1.2 | none | 1.2 emergence / 2.4 leaf appearance (Yusoff 2013); 2.1–4.5 by subperiod (Confalone, search-found); 0 by analogy to other cool-season pulses | Method 2 floor 1.2 | Mohammed Yusoff et al. 2013 doi:10.1071/CP12305 | Low |
| Mustard | 0 | none | 0 (MSU, B. juncea and S. alba); 5 if treated like canola | Method 2 floor 0 | MSU MT200103AG | Medium |
| Triticale | 0 | none | 0 (Naylor & Su 1998, leaf appearance); 8–10 reported for Brazilian cultivars (search-found) | Method 2 floor 0 | Naylor & Su 1998 J. Agric. Sci. (Cambridge record, search-found); cereal convention (NDAWN wheat/barley) | Medium |
| Rye | 4.4 | none | 4.4 (Mirsky 2009 rye phenology model; Wisconsin tables 40 °F); 0 (small-grain networks); −4.5 (Arkansas chamber study, search-found) | Method 2 floor 4.4; use 0 if you need one base for all small grains | Mirsky et al. 2009 doi:10.2134/agronj2009.0130 | Low |

## 3. Decisions where sources disagree

- **Wheat/barley upper cap.** NDAWN (Bauer et al.) caps Tmax at 21 °C until Haun 2.0 (~395 GDD) and 35 °C thereafter; MSU and McMaster & Wilhelm use no cap. A stage-dependent cap cannot be expressed as one `tupper`, so it is set to null and documented here; if a single cap is wanted, 35 °C (NDAWN post-Haun-2) is the defensible choice and rarely binds in the northern Plains.
- **Canola 0 vs 5 °C.** MSU Table 1 and Canola Council tables use 0 °C; NDAWN and Canadian "Base 5" mapping use 5 °C; emergence studies give ~0.4–1.2 °C. 5 °C is recommended because it is what NDAWN's staging model was fitted with and is the prevailing Prairie operational base; use 0 °C only when reproducing MSU/Canola Council stage tables.
- **Sunflower 0 vs 6.7 °C.** MSU used 0 °C for all Montana crops; NDAWN and Manitoba use 6.7 °C (44 °F) with no cap, and genotype-specific estimates bracket 6.7. 6.7 °C recommended.
- **Sorghum Tbase.** 10 °C (Arkansas, KSU Mesonet) vs 7.8 °C (KSU MF3740/Luo 2011) vs 12.8 °C (OK Mesonet). 10/37.8 is the most widely used pairing and matches the published Arkansas GDU stage tables.
- **Cotton cap.** Base 60 °F is universal; the cap ranges 93–100 °F or none. 95 °F (35 °C) is used by KSU and Texas A&M and is a compromise; stage totals in the OSU/Mid-South tables were generated without a cap, so expect small differences.
- **Sugar beet 1.1 vs 3 °C.** NDAWN's operational model uses 1.1 °C with a 30 °C cap; the European physiological literature (Milford et al. 1985) uses 3 °C for leaf expansion. 1.1/30 recommended for a North American network context; swap to 3 °C with no cap if coupling to European beet models.
- **Rye.** Choosing 4.4 °C follows the only rye-specific peer-reviewed phenology model found (Mirsky et al. 2009). If the module needs one base for all small grains, 0 °C is consistent with wheat/barley/triticale and with NDAWN practice.
- **Faba bean, safflower, camelina, hemp, millet.** Only emergence/germination-based bases or single studies were found; recommendations use the measured value from the best peer-reviewed source and are flagged Low. Whole-season bases may differ.
- **Legumes (pea, lentil, chickpea).** Emergence-stage bases are 4–6 °C, but the operational stage tables (MSU) are built on 0 °C and whole-season phenology work uses 0–1 °C. 0 °C recommended for stage prediction.

## 4. Gaps and caveats

- Peer-reviewed DOIs could not be retrieved (paywall/403) for Bauer et al. 1984 (NDAWN wheat), Sands et al. 1979 (potato), Covell et al. 1986 (legume germination), Soltani et al. 2006 (chickpea), Ong 1983/84 (pearl millet), Naylor & Su 1998 (triticale); they are cited bibliographically via the opened secondary sources, without DOIs.
- The McMaster & Wilhelm DOI was verified only by the doi.org redirect to Elsevier PII S0168192397000270; the abstract was read on UNL Digital Commons.
- No NDAWN page for dry bean or potato was found (404); dry bean thresholds come from the Nebraska AWDN file.
- All values are air-temperature based; emergence models often use soil temperature, which will shift the apparent base.
