# Vegetation indices for a G / R / RE / NIR UAV orthomosaic: literature review and ranking

**Scope:** The 15 vegetation indices (VIs) most widely used in UAV-based precision agriculture and plant phenotyping that can be computed from the **Green, Red, Red-edge and NIR** reflectance bands of the Pix4Dfields orthomosaic (float, 0-1). There is no blue band. Thermal indices are covered separately and left out here.
**Date of search:** 20 October 2026
**Machine-readable companion:** `docs/vegetation_indices.json`

---

## 1. Search method

### 1.1 Databases and tools

| Source | Purpose | Queries | Records returned / screened |
|---|---|---|---|
| Consensus (Semantic Scholar/OpenAlex/PubMed corpus) | Find reviews of UAV VIs | 1 (monthly quota then ran out) | 10 / 10 |
| PubMed (NCBI) | Q1 plant-science journals: Plant Methods, Front. Plant Sci., Plant Phenomics, BMC Plant Biol., Plant Science | 4 Boolean queries (UAV x index family x trait) | 174 hits, 117 retrieved; ~75 abstracts screened |
| Europe PMC (includes AGRICOLA records for Elsevier and Springer agriculture journals) | Abstract-level screening restricted to Q1 journals (RSE, ISPRS JPRS, CEA, Precision Agriculture, FCR, EJA, AFM, Biosystems Eng., JAG, Plant Methods, Front. Plant Sci., Smart Agric. Technol., Plant Phenomics, Remote Sensing) | 14 queries (one per index, `ABSTRACT:"<index>" AND UAV AND <Q1 journal list>`, sorted by citations) | 254 hits; ~180 abstracts screened |
| Crossref REST API | Find papers by title in Q1 ISSNs; check **every DOI cited here** | ~27 search queries + 70 DOI look-ups | ~190 records screened; 70 DOIs checked |
| OpenAlex REST API | Bibliometric **usage count** for each candidate index (title + abstract search) | 36 count queries | full corpus counts (see 1.2) |
| Web search | Find and read the bibliometric reviews (Radočaj et al. 2023; Peticilă et al. 2025; Yan et al. 2025; Mukiibi et al. 2024) | 6 | 6 result pages |

In total about **450 bibliographic records were screened** at title or abstract level. About 60 were kept as relevant Q1 UAV/proximal applications, and 41 are cited below. Every DOI in this document was retrieved through Crossref, PubMed or Europe PMC. None was typed from memory. NDVI is the one exception: its original source (Rouse et al. 1974, NASA SP-351 conference proceedings) has no DOI.

### 1.2 How the ranking was built

1. **Review evidence (qualitative ranking).** These reviews were used:
   - Radočaj et al. (2023), *Agriculture* 13:707, doi:10.3390/agriculture13030707. A WoS-CC bibliometric review of "precision agriculture" x "vegetation index" since 2000. It finds that NDVI dominates with about 2,200 studies, that multispectral VIs are used far more often than RGB VIs, and it names EVI and SAVI as the main NDVI improvements.
   - Peticilă et al. (2025), *AgriEngineering* 7:431, doi:10.3390/agriengineering7120431. 472 UAV-VI papers from 2015-2024. NDVI, NDRE and GNDVI are the most widely applied.
   - Zhang et al. (2025), *Smart Agricultural Technology*, doi:10.1016/j.atech.2025.101406. A scoping review of UAV multispectral precision agriculture. NDVI, GNDVI and SAVI are mature, and TCARI, RDVI and OSAVI are emerging.
   - Mukiibi et al. (2024), *Potato Research*, doi:10.1007/s11540-024-09748-7. A systematic review of potato studies. NDVI, CIred-edge, CIgreen and OSAVI are used most often.
   - Giovos et al. (2021), *Agriculture* 11:457, doi:10.3390/agriculture11050457. 113 viticulture papers. NDVI is the most used.
   - Vera-Esmeraldas et al. (2025), *Agronomy*, doi:10.3390/agronomy15112569. NDVI, NDRE, SAVI and GNDVI in UAV viticulture.
   - Xue & Su (2017), *J. Sensors*, doi:10.1155/2017/1353691 (more than 100 VIs), and Yan et al. (2025), *Int. J. Appl. Earth Obs. Geoinf.*, doi:10.1016/j.jag.2025.104560 (global systematic review of VIs), as general references.

   The full texts of Radočaj et al. and Peticilă et al. could not be downloaded (HTTP 403), so their per-index count tables were not available. Only their abstract-level findings are used.

2. **Our own usage count (quantitative ranking).** Because the full review tables were not available, every candidate index was counted reproducibly in OpenAlex (about 250 M works). The query was:
   `title_and_abstract.search: (UAV OR UAS OR drone OR drones OR "unmanned aerial") AND (crop OR crops OR agriculture OR agricultural OR phenotyping OR yield OR wheat OR maize OR rice OR soybean OR potato OR cotton OR vineyard) AND (<abbreviation> OR "<full name>")`
   The reference set (same UAV and agriculture filter AND "vegetation index/indices") contains **3,942 papers**. Counts only cover titles and abstracts. Indices that appear only inside machine-learning feature tables are therefore under-counted, which is why the review evidence in step 1 was used as a tie-breaker.

| Index | UAV+ag papers (OpenAlex, title/abstract) | Computable without blue/SWIR? |
|---|---|---|
| NDVI | 2,241 | yes |
| NDRE | 545 | yes |
| GNDVI | 389 | yes |
| SAVI* | 298 | yes |
| ExG | 205 | **no (blue)** |
| RVI / SR | 184 | yes |
| EVI | 178 | **no (blue)** |
| VARI | 161 | **no (blue)** |
| OSAVI | 141 | yes |
| NGRDI / GRVI (G-R form)** | 92-182 | yes |
| CIre | 86 | yes |
| NDWI*** | 81 | G-NIR form only; see section 3.2 |
| CIg | 71 | yes |
| MSAVI / MSAVI2 | 65 | yes |
| DVI | 59 | yes |
| MCARI | 50 | yes |
| EVI2 | 49 | yes |
| TCARI (incl. TCARI/OSAVI) | 46 | yes |
| MTCI | 43 | yes |
| LCI | 38 | yes |
| RDVI | 35 | yes |
| CCCI | 32 | yes |
| WDRVI | 25 | yes |
| TVI | 22 | yes |
| MTVI2 | 19 | yes |
| PSRI / SIPI | 16 / 16 | **no (blue)** |
| MSR | 12 | yes |
| GSAVI / GOSAVI | 9 | yes |
| NLI / MNLI | 1 | yes |

\* The SAVI phrase also matches papers that write out "optimized/modified soil adjusted vegetation index", so the count is slightly inflated.
\** "NGRDI" alone gives 92. Adding "GRVI" gives 182, but GRVI is sometimes used for the NIR/G "green ratio VI".
\*** Most NDWI hits use the SWIR (Gao) form or are water-body studies.

---

## 2. Ranked table of the 15 indices

Band symbols: **G** = green, **R** = red, **RE** = red-edge, **NIR** = near-infrared (surface reflectance, 0-1).

| Rank | Abbr. | Full name | Formula (exact) | Value range | Sensitive to | Original reference |
|---|---|---|---|---|---|---|
| 1 | **NDVI** | Normalized Difference Vegetation Index | (NIR − R) / (NIR + R) | [−1, 1]; crops 0.2-0.95 | green biomass, cover/vigour, LAI (saturates at LAI > ~3), yield proxy | Rouse et al. 1974, NASA SP-351 (no DOI) |
| 2 | **NDRE** | Normalized Difference Red-Edge | (NIR − RE) / (NIR + RE) | [−1, 1]; crops 0.1-0.6 | canopy chlorophyll, N status; less saturation | Gitelson & Merzlyak 1994, J. Plant Physiol., doi:10.1016/S0176-1617(11)81633-0 |
| 3 | **GNDVI** | Green NDVI | (NIR − G) / (NIR + G) | [−1, 1]; crops 0.3-0.9 | chlorophyll, N, biomass | Gitelson et al. 1996, RSE, doi:10.1016/S0034-4257(96)00072-7 |
| 4 | **SAVI** | Soil-Adjusted VI (L = 0.5) | 1.5 · (NIR − R) / (NIR + R + 0.5) | [−1, 1]; crops 0.1-0.8 | cover, biomass, LAI in sparse canopies | Huete 1988, RSE, doi:10.1016/0034-4257(88)90106-X |
| 5 | **RVI (SR)** | Ratio VI / Simple Ratio | NIR / R | [0, ∞); dense canopy 8-30 | LAI, biomass, canopy chlorophyll (no saturation) | Jordan 1969, Ecology, doi:10.2307/1936256 |
| 6 | **OSAVI** | Optimized SAVI | (NIR − R) / (NIR + R + 0.16) | [−0.86, 0.86] (×1.16 variant → [−1, 1]) | LAI, cover, biomass with soil correction | Rondeaux et al. 1996, RSE, doi:10.1016/0034-4257(95)00186-7 |
| 7 | **NGRDI** | Normalized Green-Red Difference Index (GRVI) | (G − R) / (G + R) | [−1, 1]; vegetation 0-0.3 | green fraction, early biomass, phenology/senescence | Tucker 1979, RSE, doi:10.1016/0034-4257(79)90013-0; named by Hunt et al. 2005 |
| 8 | **CIre** | Red-edge Chlorophyll Index | NIR / RE − 1 | [−1, ∞); crops 0.5-10 | canopy chlorophyll, N uptake, GPP (linear) | Gitelson et al. 2003, J. Plant Physiol., doi:10.1078/0176-1617-00887 (canopy: GRL 2005, doi:10.1029/2005GL022688) |
| 9 | **CIg** | Green Chlorophyll Index | NIR / G − 1 | [−1, ∞); crops 1-15 | canopy chlorophyll, N, productivity | Gitelson et al. 2003 (as above) |
| 10 | **MSAVI2** | Modified SAVI (self-adjusting L) | [2·NIR + 1 − √((2·NIR + 1)² − 8·(NIR − R))] / 2 | [−1, 1]; crops 0.1-0.8 | cover, LAI, biomass on bright soils | Qi et al. 1994, RSE, doi:10.1016/0034-4257(94)90134-1 |
| 11 | **DVI** | Difference VI | NIR − R | [−1, 1]; crops 0.1-0.6 | biomass, LAI, cover | Tucker 1979, RSE, doi:10.1016/0034-4257(79)90013-0 |
| 12 | **MCARI** | Modified Chlorophyll Absorption in Reflectance Index | [(RE − R) − 0.2·(RE − G)] · (RE / R) | unbounded; typically 0-1.5 | leaf chlorophyll (confounded by LAI) | Daughtry et al. 2000, RSE, doi:10.1016/S0034-4257(00)00113-9 |
| 13 | **EVI2** | Two-band Enhanced VI | 2.5 · (NIR − R) / (NIR + 2.4·R + 1) | [−0.74, 1.25]; crops 0.1-0.9 | biomass, LAI; less saturation; blue-free EVI | Jiang et al. 2008, RSE, doi:10.1016/j.rse.2008.06.006 |
| 14 | **TCARI/OSAVI** | Transformed CARI normalised by OSAVI | 3·[(RE − R) − 0.2·(RE − G)·(RE / R)] / [1.16·(NIR − R)/(NIR + R + 0.16)] | unbounded; crops 0-1 (**lower = more chlorophyll**) | leaf chlorophyll / N, with LAI and soil effects reduced; stress | Haboudane et al. 2002, RSE, doi:10.1016/S0034-4257(02)00018-4 |
| 15 | **MTCI** | MERIS Terrestrial Chlorophyll Index | (NIR − RE) / (RE − R) | unbounded; vegetation 0.5-6 | canopy chlorophyll, N; red-edge position proxy | Dash & Curran 2004, IJRS, doi:10.1080/0143116042000274015 |

### 2.1 Notes on the formulas

- **OSAVI:** This is the original Rondeaux et al. (1996) form. Many UAV papers multiply it by (1 + 0.16), which rescales it to [−1, 1] without changing correlations. The scaled form is used *inside* TCARI/OSAVI because that is how Haboudane et al. (2002) defined it.
- **MCARI and TCARI** were defined at R700/R670/R550 nm. UAV red-edge bands are centred near 715-735 nm (e.g., MicaSense RedEdge ~717 nm, Parrot Sequoia ~735 nm, DJI P4M/Mavic 3M ~730 nm), so the RE-based versions are broadband approximations. Absolute values are not comparable across cameras.
- **MTCI** was defined at 753.75 / 708.75 / 681.25 nm. With a single broad RE band, the denominator (RE − R) can approach 0 over soil or shadow. Mask those pixels, for example to NDVI > 0.3.
- **Chlorophyll indices (CIre, CIg)** are written in the standard Gitelson form, (NIR/band) − 1.
- **Division by zero:** RVI, CIre, CIg, MCARI, TCARI/OSAVI and MTCI are ratios. In the numpy code, wrap them in `np.errstate(divide='ignore', invalid='ignore')` and set non-finite values to NaN.

---

## 3. Supporting Q1 application papers (all found via the tools; DOIs checked with Crossref)

Quartile notes: Remote Sensing (MDPI) is **Q1 in SJR** (Earth & Planetary Sciences), and its JCR quartile varies between Q1 and Q2 by year and category. Frontiers in Plant Science, Plant Methods, Plant Phenomics, BMC Plant Biology and Plant Science are Q1 (SJR, Plant Science). Smart Agricultural Technology is Q1 (SJR, Agronomy/Engineering). IEEE TGRS is Q1. Potato Research, *Agriculture* (MDPI), *Agronomy* (MDPI), *AgriEngineering* and *J. Sensors* are reviews used only for the ranking evidence. Their quartiles are not claimed here: AgriEngineering is Q2 in most rankings, and Agriculture and Agronomy are Q1 or Q2 depending on category and year.

**1. NDVI**
- Hassan et al. 2019, *Plant Science*. UAV-NDVI across the wheat cycle predicts grain yield. doi:10.1016/j.plantsci.2018.10.022
- Zaman-Allah et al. 2015, *Plant Methods*. UAV multispectral phenotyping of maize. doi:10.1186/s13007-015-0078-2
- Deng et al. 2018, *ISPRS J. Photogramm. Remote Sens.*. NDVI vs red-edge NDVI from two UAV cameras. doi:10.1016/j.isprsjprs.2018.09.008
- Maresma et al. 2020, *Comput. Electron. Agric.*. Accuracy of NDVI-based corn yield prediction depends on time of sensing. doi:10.1016/j.compag.2020.105236

**2. NDRE**
- Deng et al. 2018, *ISPRS JPRS*. reNDVI (= NDRE) beat NDVI for maize SPAD. doi:10.1016/j.isprsjprs.2018.09.008
- Yang et al. 2020, *Front. Plant Sci.*. UAV multispectral water- and N-use efficiency in wheat (NDRE, GNDVI, CIre, NGRDI). doi:10.3389/fpls.2020.00927
- Potgieter et al. 2017, *Front. Plant Sci.*. Sorghum leaf-area dynamics from UAV NDVI/NDRE. doi:10.3389/fpls.2017.01532
- Liang et al. 2021, *Front. Plant Sci.*. Rice NUE phenotyping with UAV NDRE. doi:10.3389/fpls.2021.740414

**3. GNDVI**
- Candiago et al. 2015, *Remote Sensing*. NDVI, GNDVI and SAVI from UAV for vineyards and tomato. doi:10.3390/rs70404026
- Kyratzis et al. 2017, *Front. Plant Sci.*. Durum wheat phenotyping with UAV NDVI, GNDVI and SR. doi:10.3389/fpls.2017.01114
- Hunt et al. 2017, *Precision Agriculture*. Potato N status from small UAVs (NDVI, GNDVI). doi:10.1007/s11119-017-9518-5
- Selvaraj et al. 2020, *Plant Methods*. Cassava high-throughput phenotyping (NDRE, GNDVI). doi:10.1186/s13007-020-00625-1

**4. SAVI**
- Candiago et al. 2015, *Remote Sensing*. doi:10.3390/rs70404026
- Han et al. 2021, *Front. Plant Sci.*. Wheat growth parameters and yield under water treatments (NDVI, SAVI, DVI). doi:10.3389/fpls.2021.609876
- Modica et al. 2020, *Comput. Electron. Agric.*. Citrus and olive vigour from UAV (NDVI, NDRE, SAVI). doi:10.1016/j.compag.2020.105500
- Teodoro et al. 2024, *Plant Methods*. Maize and soybean phenotyping (NDVI, NDRE, GNDVI, SAVI). doi:10.1186/s13007-024-01294-0

**5. RVI / SR**
- Peng & Gitelson 2011, *Agric. For. Meteorol.*. SR, CIg, CIre and MTCI were the best of 15 VIs for maize GPP. doi:10.1016/j.agrformet.2011.05.005
- Su et al. 2018, *Comput. Electron. Agric.*. Wheat yellow rust from UAV multispectral (NDVI, OSAVI, RVI). doi:10.1016/j.compag.2018.10.017
- Kyratzis et al. 2017, *Front. Plant Sci.* (SR). doi:10.3389/fpls.2017.01114
- Kaushal et al. 2024, *Front. Plant Sci.*. Wheat phenomic prediction (RVI, CIg). doi:10.3389/fpls.2024.1410249

**6. OSAVI**
- Zheng et al. 2018, *Precision Agriculture*. Rice above-ground biomass from UAV texture and OSAVI. doi:10.1007/s11119-018-9600-7
- Su et al. 2018, *Comput. Electron. Agric.* doi:10.1016/j.compag.2018.10.017
- Pokhrel et al. 2023, *Front. Plant Sci.*. Cotton physiological parameters (OSAVI, MSAVI, RVI). doi:10.3389/fpls.2023.1248152
- Li et al. 2024, *Front. Plant Sci.*. Cotton emergence (NDVI, SAVI, OSAVI, EVI2, RVI, MCARI). doi:10.3389/fpls.2024.1333089

**7. NGRDI (GRVI)**
- Hunt et al. 2005, *Precision Agriculture*. Introduced the NGRDI name for model-aircraft crop biomass and N. doi:10.1007/s11119-005-2324-5
- Torres-Sánchez et al. 2014, *Comput. Electron. Agric.*. Multi-temporal vegetation-fraction mapping in wheat from UAV. doi:10.1016/j.compag.2014.02.009
- Roth et al. 2017, *Precision Agriculture*. Cover-crop biomass from UAS RGB and NIR (NDVI, GRVI). doi:10.1007/s11119-017-9501-1
- Yang et al. 2020, *Front. Plant Sci.* doi:10.3389/fpls.2020.00927

**8. CIre**
- Peng & Gitelson 2011, *Agric. For. Meteorol.* doi:10.1016/j.agrformet.2011.05.005
- Li et al. 2014, *Field Crops Res.*. Red-edge indices (CCCI, MTCI, NDRE, CIre) for maize N status. doi:10.1016/j.fcr.2013.12.018
- Wang et al. 2021, *Precision Agriculture*. Paddy-rice N status from UAV multispectral (CIre). doi:10.1007/s11119-021-09823-w
- Liu et al. 2018, *Comput. Electron. Agric.*. Oilseed rape NNI from UAV (NDVI, MSAVI2, CIre). doi:10.1016/j.compag.2018.05.026

**9. CIg**
- Peng & Gitelson 2011, *Agric. For. Meteorol.* doi:10.1016/j.agrformet.2011.05.005
- Burns et al. 2022, *Precision Agriculture*. Maize N deficiency (NDVI, GNDVI, CIg). doi:10.1007/s11119-021-09861-4
- Ranđelović et al. 2023, *Plant Methods*. Soybean fresh biomass from temporal UAV data (CIg). doi:10.1186/s13007-023-01054-6
- Kaushal et al. 2024, *Front. Plant Sci.* doi:10.3389/fpls.2024.1410249

**10. MSAVI2**
- Rasmussen et al. 2020, *Precision Agriculture*. UAV vs Sentinel-2 management zones (NDVI, MSAVI2). doi:10.1007/s11119-020-09759-7
- Liu et al. 2018, *Comput. Electron. Agric.* doi:10.1016/j.compag.2018.05.026
- Pokhrel et al. 2023, *Front. Plant Sci.* doi:10.3389/fpls.2023.1248152
- Carella et al. 2024, *Front. Plant Sci.*. Olive water status (NDVI, NDRE, GNDVI, MSAVI). doi:10.3389/fpls.2024.1448656

**11. DVI**
- Han et al. 2021, *Front. Plant Sci.* doi:10.3389/fpls.2021.609876
- Deng et al. 2025, *Front. Plant Sci.*. Sorghum yield from UAV multispectral data with stacking ensemble learning (DVI). doi:10.3389/fpls.2025.1636015
- Narmilan et al. 2022, *Remote Sensing*. Sugarcane canopy chlorophyll from UAV VIs (RVI, DVI). doi:10.3390/rs14051140

**12. MCARI**
- Zhang et al. 2023, *Precision Agriculture*. Wheat above-ground biomass from UAV multimodal fusion (NDVI, NDRE, GNDVI, SAVI, OSAVI, MCARI). doi:10.1007/s11119-023-10062-4
- Li et al. 2024, *Front. Plant Sci.* doi:10.3389/fpls.2024.1333089
- Lu et al. 2022, *Front. Plant Sci.*. Jujube chlorophyll under mite stress (NDVI, RVI, MCARI, TCARI). doi:10.3389/fpls.2022.1009630
- Li et al. 2019, *Comput. Electron. Agric.*. Canopy N density in wheat from UAV hyperspectral data (MCARI). doi:10.1016/j.compag.2019.04.005

**13. EVI2**
- Li et al. 2022, *Remote Sens. Environ.*. EVI2 was the best VI for wheat above-ground biomass (UAV and hand-held data). doi:10.1016/j.rse.2022.112967
- Li et al. 2024, *Front. Plant Sci.* doi:10.3389/fpls.2024.1333089
- Vasconcelos et al. 2025, *Front. Plant Sci.*. Soybean drought resilience assessed with UAV EVI2. doi:10.3389/fpls.2025.1630127

**14. TCARI/OSAVI**
- Berni et al. 2009, *IEEE Trans. Geosci. Remote Sens.*. Early UAV narrowband study (NDVI, TCARI/OSAVI, PRI). doi:10.1109/TGRS.2008.2010457
- Ballester et al. 2017, *Precision Agriculture*. UAS water stress in five fruit-tree species (NDVI, TCARI/OSAVI, PRI). doi:10.1007/s11119-017-9512-y
- Bagheri 2020, *Comput. Electron. Agric.*. Fire-blight detection in pear (TCARI, RDVI). doi:10.1016/j.compag.2019.105147
- Lu et al. 2022, *Front. Plant Sci.* doi:10.3389/fpls.2022.1009630

**15. MTCI**
- Liu et al. 2022, *Front. Plant Sci.*. DJI P4M: MTCI was best for wheat N partial factor productivity at jointing. doi:10.3389/fpls.2022.1032170
- Li et al. 2014, *Field Crops Res.* (MTCI was second best for maize N). doi:10.1016/j.fcr.2013.12.018
- Peng & Gitelson 2011, *Agric. For. Meteorol.* doi:10.1016/j.agrformet.2011.05.005
- Parida et al. 2026, *BMC Plant Biology*. Maize chlorophyll and fluorescence from UAV multispectral data (MTCI, NDRE). doi:10.1186/s12870-026-09661-w

Years follow the Crossref 'issued' date, which can be the online-first year and so may differ by one from the print volume year. Volume and page numbers are left out on purpose: take them from the DOI landing page when citing formally. Every DOI listed was resolved through Crossref.

---

## 4. Justification of the ranking and excluded candidates

### 4.1 Justification

- **Ranks 1-3 (NDVI, NDRE, GNDVI)** agree across every source. They are the three most-used UAV indices in Peticilă et al. (2025). NDVI is the dominant index in Radočaj et al. (2023), with about 2,200 WoS papers. In our OpenAlex count they lead by a wide margin (2,241 / 545 / 389 UAV-agriculture papers).
- **Ranks 4-6 (SAVI, RVI, OSAVI)** form the soil-adjusted and ratio family. They are the next most-counted computable indices, and the reviews name them repeatedly (Radočaj et al.: SAVI as the main NDVI improvement; Zhang et al. 2025: SAVI and OSAVI; Mukiibi et al. 2024: OSAVI). RVI is ranked above OSAVI on count, and because it stays linear at high LAI where NDVI saturates.
- **Rank 7 (NGRDI)** is a visible-band index. It is kept because it is heavily used in UAV phenotyping and canopy-fraction work, it can be computed from G and R alone, and its count (92-182) exceeds that of the chlorophyll indices. It also lets RGB-only flights be cross-checked.
- **Ranks 8-9 (CIre, CIg)** are Gitelson chlorophyll indices. They are near-linear with canopy chlorophyll and N, and they are the most-used VIs in the potato systematic review alongside NDVI. In Q1 maize and wheat N studies they repeatedly rank among the best indices (Peng & Gitelson 2011; Li et al. 2014).
- **Ranks 10-11 (MSAVI2, DVI)** have similar counts (65 and 59). They are widely used for sparse canopies, management zones and biomass or yield models.
- **Ranks 12-15 (MCARI, EVI2, TCARI/OSAVI, MTCI)** are the chlorophyll-absorption indices and EVI2. Their counts lie between 43 and 50, so their order is close to a tie, and they are kept ahead of the runners-up (LCI 38, RDVI 35, CCCI 32) for these reasons:
  - **EVI2** is the only blue-free replacement for EVI, which is the 7th most-used index overall (178) but cannot be computed here.
  - **TCARI/OSAVI and MCARI** are the standard Haboudane/Daughtry chlorophyll indices, named as emerging UAV indices by Zhang et al. (2025).
  - **MTCI** is consistently one of the two best red-edge N indices in Q1 field studies.
- **Caveat:** abstract-level counts under-represent indices used only inside machine-learning feature sets. Ranks 10-15 are separated by small margins (43-65 papers each) and should be read as a group of roughly equal importance, not a strict order.

### 4.2 Excluded candidates

| Candidate | Reason for exclusion |
|---|---|
| EVI (178), ARVI, SIPI (16), PSRI (16), NPCI, mNDblue | Require a **blue** band (~450-500 nm). EVI2 is included as the blue-free EVI. |
| ExG (205), VARI (161), TGI, GLI, CIVE, ExGR, RGBVI | RGB indices that require **blue**. |
| NDWI-Gao, NDMI, NMDI, MSI, NBR | Require **SWIR** (1240/1640/2130 nm). |
| NDWI-McFeeters (G − NIR)/(G + NIR) | An open-water index. It is exactly −GNDVI, so it adds no information. |
| PRI, PRI(570-515), CRI, ARI-narrow | Require narrow bands (531/570/515 nm) that a 4-band broadband camera does not provide. |
| REIP (linear four-point), Datt indices, red-edge ratios at 705/750 | Need several narrow red-edge bands. Only one RE band is available. |
| CWSI, canopy-air temperature indices | Thermal. Handled separately. |
| LCI (38), RDVI (35), CCCI (32), WDRVI (25), TVI (22), MTVI2 (19), MSR (12), GOSAVI/GSAVI (9), MNLI (1), MGRVI, MCARI2 | **Computable from G/R/RE/NIR** but ranked below the top 15 on usage. Good candidates for an "extended" set. For reference: RDVI = (NIR − R)/√(NIR + R); WDRVI = (0.1·NIR − R)/(0.1·NIR + R); CCCI = NDRE/NDVI (simplified form); LCI = (NIR − RE)/(NIR + R); MTVI2 = 1.5·[1.2·(NIR − G) − 2.5·(R − G)] / √[(2·NIR + 1)² − (6·NIR − 5·√R) − 0.5]. |

### 4.3 Practical recommendations for the Pix4Dfields product

1. Compute all 15 indices on the float reflectance orthomosaic. Mask pixels with NIR + R = 0 or a non-finite result, and mask soil and shadow (e.g., NDVI < 0.3) before reporting the chlorophyll indices (MCARI, TCARI/OSAVI, MTCI, CIre).
2. For dense, high-LAI crops after canopy closure, prefer the red-edge and chlorophyll family (NDRE, CIre, MTCI, TCARI/OSAVI) or RVI/EVI2 over NDVI, which saturates.
3. For early-season or sparse canopies, prefer SAVI, OSAVI, MSAVI2 or NGRDI.
4. Record the camera's red-edge centre wavelength in the metadata. RE-based index values are not comparable across sensors.
