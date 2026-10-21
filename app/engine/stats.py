"""Between-plot significance testing (see docs/viz_stats_spec.md, section 1).

Mode A - plot vs plot (only a PLOTID column). There is one polygon per plot, so within-plot
         sub-samples (0.5 m sub-plot cell means, or spatially thinned pixels) act as replicates.
         This is pseudo-replication: results are DESCRIPTIVE ("plots differ in their within-plot
         distributions"), not treatment inference. Welch ANOVA and Kruskal-Wallis are both run;
         the primary path is chosen from residual skew/kurtosis. Post-hoc depends on plot count:
           k <= 60      Games-Howell (Welch path) or Dunn (KW path) + compact letters
           60 < k <=150 Dunn only + number of plots each plot differs from
           k > 150      each plot vs the rest of the field (Mann-Whitney U, BH)
Mode B - treatment groups (optional group column, optional block column): plot means are the
         replicates. CRD one-way or RCBD additive ANOVA, assumption checks, then Tukey HSD /
         Games-Howell / Dunn by decision table, compact letters.
Compact letters: Piepho (2004) insert-absorb-sweep; 'a' marks the highest mean.
Reference functions validated against pingouin / scikit-posthocs / scipy (spec section 3).
"""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
from scipy import stats

DISCLAIMER = ("Plot-vs-plot tests use within-plot sub-samples as replicates (pseudo-replication). "
              "They show whether plots differ in their within-plot distributions and are descriptive; "
              "treatment inference requires replicated plots (supply a treatment column).")

# smallest meaningful difference per variable (spec A3); others: 2 % of the field median
GH_MAX_K = 20  # Games-Howell up to this many plots; Dunn (rank-based, fast) above

DELTA = {"NDVI": 0.02, "GNDVI": 0.02, "OSAVI": 0.02, "SAVI": 0.02, "MSAVI2": 0.02, "EVI2": 0.02, "NDRE": 0.015,
         "Thermal_C": 0.5, "Tcanopy_C": 0.5, "Tsoil_C": 0.5, "PlantHeight_m": 0.05, "CWSI_empirical_mean": 0.05}


# ----------------------------------------------------------------------------- reference functions
def p_adjust(p, method="holm"):
    p = np.asarray(p, float)
    m = len(p)
    if m == 0:
        return p
    o = np.argsort(p)
    if method == "holm":
        adj = np.maximum.accumulate((m - np.arange(m)) * p[o])
    elif method in ("bh", "fdr_bh"):
        adj = np.minimum.accumulate((m / np.arange(m, 0, -1)) * p[o][::-1])[::-1]
    elif method == "bonferroni":
        adj = p[o] * m
    else:
        adj = p[o]
    out = np.empty(m)
    out[o] = np.minimum(adj, 1)
    return out


def welch_anova(groups):
    n = np.array([len(g) for g in groups], float)
    m = np.array([np.mean(g) for g in groups])
    v = np.array([np.var(g, ddof=1) for g in groups])
    v = np.where(v <= 0, 1e-12, v)
    k = len(groups)
    w = n / v
    sw = w.sum()
    mw = (w * m).sum() / sw
    a = (w * (m - mw) ** 2).sum() / (k - 1)
    tmp = (((1 - w / sw) ** 2) / (n - 1)).sum()
    b = 1 + 2 * (k - 2) / (k ** 2 - 1) * tmp
    F = a / b
    df1, df2 = k - 1, (k ** 2 - 1) / (3 * tmp)
    return float(F), float(df1), float(df2), float(stats.f.sf(F, df1, df2))


def games_howell(groups, names):
    n = np.array([len(g) for g in groups], float)
    m = np.array([np.mean(g) for g in groups])
    v = np.array([np.var(g, ddof=1) for g in groups])
    k = len(groups)
    i, j = np.triu_indices(k, 1)
    v = np.maximum(v, 1e-12 * np.maximum(1.0, np.abs(m)) ** 2)  # zero-variance floor (as in welch_anova)
    vi, vj = v[i] / n[i], v[j] / n[j]
    se = np.sqrt(vi + vj)
    diff = m[i] - m[j]
    dfw = (vi + vj) ** 2 / (vi ** 2 / (n[i] - 1) + vj ** 2 / (n[j] - 1))
    q = np.abs(diff) / se * np.sqrt(2)
    p = np.where(q > 40, 0.0, 1.0)  # avoid integrating hopeless tails
    mid = q <= 40
    if mid.any():
        p[mid] = stats.studentized_range.sf(q[mid], k, dfw[mid])
    return pd.DataFrame({"A": np.array(names)[i], "B": np.array(names)[j], "diff": diff, "stat": q, "df": dfw,
                         "p_adj": np.clip(p, 0, 1)})


def dunn_test(groups, names, method="holm"):
    x = np.concatenate(groups)
    r = stats.rankdata(x)
    N = len(x)
    idx = np.cumsum([0] + [len(g) for g in groups])
    rbar = np.array([r[idx[a]:idx[a + 1]].mean() for a in range(len(groups))])
    n = np.diff(idx).astype(float)
    _, t = np.unique(x, return_counts=True)
    tie = (t ** 3 - t).sum() / (12 * (N - 1))
    i, j = np.triu_indices(len(names), 1)
    se = np.sqrt((N * (N + 1) / 12 - tie) * (1 / n[i] + 1 / n[j]))
    z = (rbar[i] - rbar[j]) / se
    p = 2 * stats.norm.sf(np.abs(z))
    med = np.array([np.median(g) for g in groups])
    return pd.DataFrame({"A": np.array(names)[i], "B": np.array(names)[j], "diff": med[i] - med[j], "stat": z,
                         "df": np.nan, "p_raw": p, "p_adj": p_adjust(p, method)})


def tukey_from_mse(means, ns, mse, df_resid, names):
    means, ns = np.asarray(means, float), np.asarray(ns, float)
    k = len(means)
    i, j = np.triu_indices(k, 1)
    se = np.sqrt(mse / 2 * (1 / ns[i] + 1 / ns[j]))
    q = np.abs(means[i] - means[j]) / se
    p = stats.studentized_range.sf(q, k, df_resid)
    return pd.DataFrame({"A": np.array(names)[i], "B": np.array(names)[j], "diff": means[i] - means[j],
                         "stat": q, "df": df_resid, "p_adj": np.clip(p, 0, 1)})


def pairs_to_matrix(pairs, names, pcol="p_adj"):
    P = pd.DataFrame(1.0, index=names, columns=names)
    for a, b, p in pairs[["A", "B", pcol]].itertuples(index=False):
        P.loc[a, b] = P.loc[b, a] = p
    return P


def cld(pmat, means, alpha=0.05):
    """Compact letter display (Piepho 2004 insert-absorb-sweep). Returns Series (desc. means)."""
    order = means.sort_values(ascending=False).index.tolist()
    P = pmat.loc[order, order].to_numpy()
    k = len(order)
    cols = [np.ones(k, bool)]
    for a, b in zip(*np.where(np.triu(P < alpha, 1))):
        new = []
        for c in cols:
            if c[a] and c[b]:
                c1, c2 = c.copy(), c.copy()
                c1[a] = False
                c2[b] = False
                new += [c1, c2]
            else:
                new.append(c)
        new = [c for c in new if c.any()]
        keep = []
        for idx, c in enumerate(new):
            redundant = any((idx != jdx) and np.all(c <= d) and (not np.array_equal(c, d) or jdx < idx)
                            for jdx, d in enumerate(new))
            if not redundant:
                keep.append(c)
        cols = keep
    M = np.array(cols)
    for ci in range(len(M)):
        for r in np.flatnonzero(M[ci]):
            partners = np.flatnonzero(M[ci])
            partners = partners[partners != r]
            others = np.delete(M, ci, axis=0)
            if others[:, r].any() and all((others[:, r] & others[:, j]).any() for j in partners):
                M[ci, r] = False
    cols = [c for c in M if c.any()]
    cols.sort(key=lambda c: np.argmax(c))
    letters = _letter_seq(len(cols))
    out = ["".join(letters[ci] for ci, c in enumerate(cols) if c[r]) for r in range(k)]
    return pd.Series(out, index=order, name="letters")


def _letter_seq(n):
    base = "abcdefghijklmnopqrstuvwxyz"
    seq = list(base) + list(base.upper())
    while len(seq) < n:
        seq += [x + y for x in base for y in base]
    return seq[:n]


def cld_check(pmat, letters, alpha=0.05):
    tok = {g: set(s) for g, s in letters.items()}
    for a, b in itertools.combinations(letters.index, 2):
        if bool(tok[a] & tok[b]) == (pmat.loc[a, b] < alpha):
            return False, (a, b)
    return True, None


def _effect_label(v, cuts):
    if v is None or not np.isfinite(v):
        return ""
    return "large" if v >= cuts[2] else "medium" if v >= cuts[1] else "small" if v >= cuts[0] else "negligible"


def _delta(var, field_median):
    if var in DELTA:
        return DELTA[var]
    return abs(0.02 * field_median) if np.isfinite(field_median) else 0.0


def _summary(names, groups):
    rows = []
    for nm, g in zip(names, groups):
        n = len(g)
        sd = float(np.std(g, ddof=1)) if n > 1 else np.nan
        se = sd / np.sqrt(n) if n > 1 else np.nan
        tq = stats.t.ppf(0.975, n - 1) if n > 1 else np.nan
        q1, med, q3 = np.percentile(g, [25, 50, 75])
        rows.append({"Unit": nm, "n_used": n, "mean": float(np.mean(g)), "SD": sd, "SE": se,
                     "CI95_low": float(np.mean(g) - tq * se) if n > 1 else np.nan,
                     "CI95_high": float(np.mean(g) + tq * se) if n > 1 else np.nan,
                     "median": float(med), "Q1": float(q1), "Q3": float(q3), "min": float(np.min(g)),
                     "max": float(np.max(g)), "CV_pct": 100 * sd / abs(np.mean(g)) if np.mean(g) else np.nan})
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- Mode A
def test_plots(samples_by_plot: dict, var="", alpha=0.05, p_method="auto"):
    """samples_by_plot: {plot_id: 1-D array of replicate values} for one variable."""
    names = [p for p, s in samples_by_plot.items() if s is not None and np.isfinite(np.asarray(s, float)).sum() >= 3]
    groups = [np.asarray(samples_by_plot[p], float) for p in names]
    groups = [g[np.isfinite(g)] for g in groups]
    k = len(names)
    res = {"Variable": var, "Mode": "A: plot vs plot (descriptive)", "k": k}
    if k < 2:
        res["flags"] = "fewer than 2 plots with data"
        return res, None
    allv = np.concatenate(groups)
    N = allv.size
    res["N"] = int(N)
    resid = np.concatenate([g - g.mean() for g in groups])
    skew = float(np.median([abs(stats.skew(g)) for g in groups if len(g) > 3]))
    kurt = float(np.median([stats.kurtosis(g) for g in groups if len(g) > 3]))
    res["median_abs_skew"], res["median_excess_kurtosis"] = skew, kurt
    F, d1, d2, pw = welch_anova(groups)
    omega = max(0.0, d1 * (F - 1) / (d1 * (F - 1) + N))
    grand = allv.mean()
    ssb = sum(len(g) * (g.mean() - grand) ** 2 for g in groups)
    sst = ((allv - grand) ** 2).sum()
    H, pk = stats.kruskal(*groups)
    eps2 = float(H / (N - 1))
    res.update(welch_F=F, welch_df1=d1, welch_df2=d2, welch_p=pw, omega_squared=omega,
               eta_squared=float(ssb / sst) if sst > 0 else np.nan, kruskal_H=float(H), kruskal_p=float(pk),
               epsilon_squared=eps2)
    rs = resid if resid.size <= 5000 else np.random.default_rng(0).choice(resid, 5000, replace=False)
    res["shapiro_resid_p"] = float(stats.shapiro(rs).pvalue) if rs.size >= 3 else np.nan
    try:
        res["levene_p"] = float(stats.levene(*groups, center="median").pvalue)
    except Exception:
        res["levene_p"] = np.nan
    welch_path = skew <= 1 and kurt <= 2
    res["primary_test"] = "Welch ANOVA" if welch_path else "Kruskal-Wallis"
    res["p_primary"] = pw if welch_path else float(pk)
    res["effect_size"] = omega if welch_path else eps2
    res["effect_label"] = _effect_label(res["effect_size"], (0.01, 0.06, 0.14) if welch_path else (0.01, 0.08, 0.26))
    res["significant"] = bool(res["p_primary"] < alpha)
    meth = p_method if p_method != "auto" else ("holm" if k <= 20 else "bh")
    summ = _summary(names, groups)
    delta = _delta(var, float(np.median(allv)))
    res["delta_practical"] = delta
    out = {"summary": summ, "pairs": None, "P": None, "letters": {}, "posthoc": None, "names": names}
    gh = welch_path and k <= GH_MAX_K  # exact studentized-range p costs ~20 ms per pair
    if k <= 60:
        pairs = games_howell(groups, names) if gh else dunn_test(groups, names, meth)
        out["posthoc"] = "Games-Howell" if gh else f"Dunn ({meth})"
    elif k <= 150:
        pairs = dunn_test(groups, names, meth)
        out["posthoc"] = f"Dunn ({meth})"
    else:
        pairs = None
        out["posthoc"] = "one-vs-rest Mann-Whitney U (BH)"
        flags, praw = [], []
        for i, g in enumerate(groups):
            rest = np.concatenate([groups[j] for j in range(k) if j != i])
            praw.append(stats.mannwhitneyu(g, rest).pvalue)
        padj = p_adjust(np.array(praw), "bh")
        fm = np.median(allv)
        for i, g in enumerate(groups):
            flags.append("typical of field" if padj[i] >= alpha else ("above field" if np.median(g) > fm else "below field"))
        summ["p_vs_field_BH"], summ["field_flag"] = padj, flags
    if pairs is not None:
        pairs["sig_stat"] = pairs["p_adj"] < alpha
        pairs["sig_practical"] = pairs["sig_stat"] & (pairs["diff"].abs() >= delta)
        res["n_pairs"] = int(len(pairs))
        res["n_sig_pairs"] = int(pairs["sig_stat"].sum())
        res["n_sig_practical_pairs"] = int(pairs["sig_practical"].sum())
        cnt = pd.concat([pairs.loc[pairs.sig_stat, "A"], pairs.loc[pairs.sig_stat, "B"]]).value_counts()
        summ["n_sig_partners"] = summ["Unit"].map(cnt).fillna(0).astype(int)
        out["pairs"] = pairs
        if k <= 60:
            P = pairs_to_matrix(pairs, names)
            loc = pd.Series([g.mean() if gh else np.median(g) for g in groups], index=names)
            lt = cld(P, loc, alpha)
            out["P"], out["letters"] = P, lt.to_dict()
            summ["letters"] = summ["Unit"].map(out["letters"])
    return res, out


# ----------------------------------------------------------------------------- Mode B
def _ols_sse(y, X):
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    r = y - X @ beta
    return float(r @ r), r, np.linalg.matrix_rank(X)


def _dummies(s):
    lv = sorted(s.unique(), key=lambda x: (len(str(x)), str(x)))
    return np.column_stack([(s == l).to_numpy(float) for l in lv[1:]]) if len(lv) > 1 else np.zeros((len(s), 0))


def test_groups(values: pd.Series, groups: pd.Series, blocks: pd.Series | None = None, var="", alpha=0.05):
    """Plot values (mean per plot) as replicates of treatment groups; optional RCBD block."""
    df = pd.DataFrame({"y": values.astype(float), "g": groups.astype(str)})
    if blocks is not None:
        df["b"] = blocks.astype(str).values
    df = df.dropna()
    df = df[~df["g"].isin(["", "nan", "None", "<NA>"])]
    cnt = df["g"].value_counts()
    df = df[df["g"].isin(cnt[cnt >= 2].index)]
    names = sorted(df["g"].unique(), key=lambda x: (len(x), x))
    res = {"Variable": var, "Mode": "B: treatment groups (plot means)", "k": len(names), "N": int(len(df))}
    if len(names) < 2:
        res["flags"] = "need >= 2 groups with >= 2 plots"
        return res, None
    groups_l = [df.loc[df.g == g, "y"].to_numpy() for g in names]
    y = df["y"].to_numpy()
    one = np.ones((len(df), 1))
    Xg = _dummies(df["g"])
    use_block = blocks is not None and df["b"].nunique() > 1
    if use_block:
        Xb = _dummies(df["b"])
        sse_full, resid, rank = _ols_sse(y, np.hstack([one, Xg, Xb]))
        sse_red, _, rank_red = _ols_sse(y, np.hstack([one, Xb]))
        df_g, df_e = rank - rank_red, len(df) - rank
        if df_g <= 0 or df_e <= 0:
            res["flags"] = "design not estimable: blocks confounded with treatments or no residual df"
            return res, None
        ss_g = sse_red - sse_full
        F = (ss_g / df_g) / (sse_full / df_e)
        p = float(stats.f.sf(F, df_g, df_e))
        res["model"] = "RCBD additive: y ~ group + block (type II SS)"
    else:
        sse_full, resid, rank = _ols_sse(y, np.hstack([one, Xg]))
        df_g, df_e = len(names) - 1, len(df) - rank
        if df_e <= 0:
            res["flags"] = "no residual degrees of freedom (one plot per group)"
            return res, None
        ss_g = float(((y - y.mean()) ** 2).sum() - sse_full)
        F = (ss_g / df_g) / (sse_full / df_e) if df_e > 0 else np.nan
        p = float(stats.f.sf(F, df_g, df_e)) if df_e > 0 else np.nan
        res["model"] = "CRD one-way: y ~ group"
    sst = float(((y - y.mean()) ** 2).sum())
    mse = sse_full / df_e if df_e > 0 else np.nan
    res.update(anova_F=float(F), anova_df1=df_g, anova_df2=df_e, anova_p=p,
               eta_squared=ss_g / sst if sst else np.nan,
               partial_eta_squared=ss_g / (ss_g + sse_full) if (ss_g + sse_full) else np.nan,
               omega_squared=max(0.0, (ss_g - df_g * mse) / (sst + mse)) if sst else np.nan)
    res["shapiro_resid_p"] = float(stats.shapiro(resid).pvalue) if 3 <= resid.size <= 5000 else np.nan
    res["levene_p"] = float(stats.levene(*groups_l, center="median").pvalue)
    H, pk = stats.kruskal(*groups_l)
    res["kruskal_H"], res["kruskal_p"] = float(H), float(pk)
    skew = abs(float(stats.skew(resid))) if resid.size > 3 else 0
    if res["shapiro_resid_p"] < 0.05 and skew > 1:
        primary, posthoc = ("Friedman" if use_block else "Kruskal-Wallis"), "Dunn (holm)"
        if use_block:
            try:
                piv = df.pivot_table(index="b", columns="g", values="y", aggfunc="mean").dropna()
                pk = float(stats.friedmanchisquare(*[piv[c] for c in piv.columns]).pvalue)
            except Exception:
                pass
        p_primary = pk
    elif res["levene_p"] < 0.05 and not use_block:
        primary, posthoc = "Welch ANOVA", "Games-Howell"
        _, _, _, p_primary = welch_anova(groups_l)
    else:
        primary, posthoc = ("RCBD ANOVA" if use_block else "ANOVA"), "Tukey HSD"
        p_primary = p
    res.update(primary_test=primary, p_primary=float(p_primary), significant=bool(p_primary < alpha),
               effect_size=res["omega_squared"], effect_label=_effect_label(res["omega_squared"], (0.01, 0.06, 0.14)))
    means = pd.Series([g.mean() for g in groups_l], index=names)
    summ = _summary(names, groups_l)
    if res["significant"]:
        if posthoc == "Tukey HSD":
            pairs = tukey_from_mse(means.values, [len(g) for g in groups_l], mse, df_e, names)
        elif posthoc == "Games-Howell":
            pairs = games_howell(groups_l, names)
        else:
            pairs = dunn_test(groups_l, names, "holm")
        pairs["sig_stat"] = pairs["p_adj"] < alpha
        pairs["sig_practical"] = pairs["sig_stat"] & (pairs["diff"].abs() >= _delta(var, float(np.median(y))))
        P = pairs_to_matrix(pairs, names)
        letters = cld(P, means, alpha).to_dict()
    else:
        pairs, P = None, None
        letters = {g: "a" for g in names}
        res["flags"] = "omnibus n.s. - post-hoc not run"
    summ["SE_model"] = [np.sqrt(mse / len(g)) for g in groups_l]
    summ["letters"] = summ["Unit"].map(letters)
    return res, {"summary": summ, "pairs": pairs, "P": P, "letters": letters, "posthoc": posthoc, "names": names}


def adjust_across_variables(results: list[dict], alpha=0.05):
    """Add p_omnibus_BH (BH across all variables of the run) to each omnibus record."""
    ps = np.array([r.get("p_primary", np.nan) for r in results], float)
    ok = np.isfinite(ps)
    adj = np.full(ps.shape, np.nan)
    adj[ok] = p_adjust(ps[ok], "bh")
    for r, a in zip(results, adj):
        r["p_omnibus_BH"] = a
        if np.isfinite(a) and r.get("significant") and a >= alpha:
            r["flags"] = (r.get("flags", "") + "; significant only per-variable").strip("; ")
    return results
