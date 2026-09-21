# -*- coding: utf-8 -*-
"""Real (trained) ML for Project John — two models:
  #2 Prevention  — predict which claims will PEND (and the drivers / straight-through candidates)
  #1 Confidence  — a CALIBRATED probability that the agent's decision is correct (upheld)

Base data is synthetic (no PHI), but the models are genuinely trained + calibrated with
scikit-learn — not scripted. In production these train on the payer's own history.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.model_selection import train_test_split
from sklearn.metrics import roc_auc_score, brier_score_loss

_RNG = np.random.default_rng(20260920)

# ── Feature vocabulary (matches the claim model) ─────────────────────────────────
CATEGORIES = ["Authorization", "Pricing", "Coding", "Provider", "COB", "Medical Necessity",
              "Duplicate", "Timely Filing", "Manual Pricing", "PCP", "OON", "Workers Comp"]
POS = ["11", "22", "21", "02", "23", "19"]           # office, outpatient, inpatient, telehealth, ER, off-campus
SPECIALTIES = ["Radiology", "Orthopedic Surgery", "Cardiology", "Internal Medicine",
               "Family Medicine", "Psychiatry", "Emergency Medicine", "Physical Therapy", "Oncology"]

_FEATURES = ["edit_category", "pos", "specialty", "claim_type", "par", "in_network",
             "has_auth", "other_insurance", "billed", "allowed", "billed_allowed_ratio",
             "units", "max_units", "days_to_receipt"]

def _gen_population(n=9000):
    """Generate a synthetic population of claims with a latent, learnable pend-risk + noise,
    then (for those that pend) a latent decision-correctness. Returns a DataFrame."""
    cat = _RNG.choice(CATEGORIES, n, p=_norm([0.16,0.16,0.10,0.12,0.14,0.05,0.05,0.05,0.06,0.04,0.04,0.03]))
    pos = _RNG.choice(POS, n, p=_norm([0.45,0.22,0.08,0.08,0.10,0.07]))
    spec = _RNG.choice(SPECIALTIES, n)
    ctype = _RNG.choice(["Professional", "Institutional"], n, p=[0.82, 0.18])
    par = _RNG.integers(0, 2, n)                       # 1 = participating
    in_net = np.where(par == 1, _RNG.random(n) > 0.05, _RNG.random(n) > 0.6).astype(int)
    has_auth = (_RNG.random(n) > 0.35).astype(int)
    other_ins = (_RNG.random(n) > 0.82).astype(int)
    allowed = _RNG.gamma(3.0, 180, n).clip(30, 6000).round(2)
    ratio = _RNG.normal(1.9, 0.7, n).clip(1.0, 5.0)
    billed = (allowed * ratio).round(2)
    max_units = _RNG.integers(1, 9, n)
    units = np.maximum(1, (max_units * _RNG.normal(0.9, 0.5, n)).round()).astype(int).clip(1, 20)
    days = _RNG.normal(60, 90, n).clip(2, 800).round().astype(int)

    df = pd.DataFrame({"edit_category": cat, "pos": pos, "specialty": spec, "claim_type": ctype,
                       "par": par, "in_network": in_net, "has_auth": has_auth,
                       "other_insurance": other_ins, "billed": billed, "allowed": allowed,
                       "billed_allowed_ratio": ratio.round(3), "units": units,
                       "max_units": max_units, "days_to_receipt": days})

    # ── latent pend-risk (the signal the prevention model learns) ──
    z = -2.6
    z = z + 1.9 * ((df.edit_category == "Authorization") & (df.has_auth == 0))
    z = z + 1.4 * (df.in_network == 0)
    z = z + 1.2 * (df.billed_allowed_ratio > 2.6)
    z = z + 1.3 * (df.units > df.max_units)
    z = z + 2.2 * (df.days_to_receipt > 365)
    z = z + 0.9 * ((df.pos == "02") | (df.pos == "21"))          # site-of-service sensitivity
    z = z + 0.7 * (df.other_insurance == 1)
    z = z + 0.5 * (df.edit_category.isin(["Coding", "Medical Necessity", "Pricing"]))
    z = z + _RNG.normal(0, 0.4, n)                                # irreducible noise
    p_pend = 1 / (1 + np.exp(-z))
    df["pended"] = (_RNG.random(n) < p_pend).astype(int)

    # ── latent decision-correctness for pended claims (the confidence label) ──
    # deterministic edits are almost always right; judgment edits carry more error.
    base = np.select(
        [df.edit_category.isin(["Duplicate", "Timely Filing"]),
         df.edit_category.isin(["Authorization", "Provider", "COB"]),
         df.edit_category.isin(["Pricing", "Manual Pricing", "OON"]),
         df.edit_category.isin(["Coding", "Medical Necessity"])],
        [0.985, 0.955, 0.92, 0.80], default=0.90)
    base = base - 0.05 * (df.has_auth == 0) * (df.edit_category != "Authorization")
    base = base - 0.06 * (df.other_insurance == 1)
    base = np.clip(base + _RNG.normal(0, 0.03, n), 0.5, 0.999)
    df["upheld"] = (_RNG.random(n) < base).astype(int)
    return df

def _norm(w):
    w = np.array(w, float); return w / w.sum()

def _X(df):
    """One-hot encode to a stable design matrix."""
    return pd.get_dummies(df[_FEATURES], columns=["edit_category", "pos", "specialty", "claim_type"])

# ── Model training (lazy, cached) ────────────────────────────────────────────────
_STATE = {}

def _train():
    if _STATE:
        return _STATE
    df = _gen_population()
    Xall = _X(df)
    cols = Xall.columns
    # ---- #2 Prevention: P(pend) ----
    Xtr, Xte, ytr, yte = train_test_split(Xall, df.pended, test_size=0.25, random_state=7, stratify=df.pended)
    prev = HistGradientBoostingClassifier(max_depth=4, learning_rate=0.12, max_iter=250, random_state=7)
    prev.fit(Xtr, ytr)
    prev_auc = roc_auc_score(yte, prev.predict_proba(Xte)[:, 1])
    # permutation-free importance proxy: correlation of each raw driver with pend
    drivers = _driver_table(df)
    # ---- #1 Confidence: calibrated P(correct) on the pended subset ----
    d2 = df[df.pended == 1].copy()
    X2 = _X(d2).reindex(columns=cols, fill_value=0)
    Xtr2, Xte2, ytr2, yte2 = train_test_split(X2, d2.upheld, test_size=0.25, random_state=11, stratify=d2.upheld)
    base = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.1, max_iter=200, random_state=11)
    conf = CalibratedClassifierCV(base, method="isotonic", cv=3)
    conf.fit(Xtr2, ytr2)
    proba = conf.predict_proba(Xte2)[:, 1]
    brier = brier_score_loss(yte2, proba)
    conf_auc = roc_auc_score(yte2, proba)
    frac_pos, mean_pred = calibration_curve(yte2, proba, n_bins=8, strategy="quantile")
    # honest "preventable / returnable-to-STP" share: fraction of PENDED claims attributable
    # to deterministic drivers that can be fixed at the source or auto-adjudicated.
    dp = df[df.pended == 1]
    det = ((dp.edit_category == "Authorization") & (dp.has_auth == 0)) | \
          (dp.days_to_receipt > 365) | (dp.units > dp.max_units)
    recoverable_share = float(det.mean())
    _STATE.update(dict(cols=cols, prev=prev, prev_auc=float(prev_auc), drivers=drivers,
                       conf=conf, brier=float(brier), conf_auc=float(conf_auc),
                       calib=[{"predicted": round(float(m), 3), "actual": round(float(a), 3)}
                              for m, a in zip(mean_pred, frac_pos)],
                       n_train=len(df), n_pended=int(df.pended.sum()),
                       recoverable_share=recoverable_share, df=df,
                       prev_test_n=int(len(yte)), conf_test_n=int(len(yte2))))
    return _STATE

def training_meta():
    s = _train()
    df = s["df"]
    return {
        "total": int(len(df)),
        "prevention_train": int(len(df) - s["prev_test_n"]), "prevention_test": s["prev_test_n"],
        "confidence_train": int(s["n_pended"] - s["conf_test_n"]), "confidence_test": s["conf_test_n"],
        "split": "75 / 25 stratified train/test",
        "pend_rate": round(float(df.pended.mean()) * 100, 1),
        "upheld_rate": round(float(df[df.pended == 1].upheld.mean()) * 100, 1),
        "features": [f for f in _FEATURES],
    }

def training_sample(limit=200, offset=0, pended_only=False):
    """Return actual rows of the training dataset so an audience can inspect it."""
    s = _train()
    df = s["df"]
    if pended_only:
        df = df[df.pended == 1]
    view = df.iloc[offset:offset + limit].copy()
    view["billed"] = view["billed"].round(2); view["allowed"] = view["allowed"].round(2)
    view["billed_allowed_ratio"] = view["billed_allowed_ratio"].round(2)
    return {"rows": view.to_dict(orient="records"), "total": int(len(df)),
            "columns": list(view.columns)}

def _driver_table(df):
    """Top pend drivers: pend-rate and volume for interpretable feature conditions."""
    conds = [
        ("Authorization missing", (df.edit_category == "Authorization") & (df.has_auth == 0)),
        ("Out-of-network / non-PAR provider", df.in_network == 0),
        ("Billed ≥ 2.6× allowed (pricing)", df.billed_allowed_ratio > 2.6),
        ("Units exceed authorized max", df.units > df.max_units),
        ("Received > 365 days (timely filing)", df.days_to_receipt > 365),
        ("Facility / telehealth site of service", df.pos.isin(["02", "21"])),
        ("Other insurance present (COB)", df.other_insurance == 1),
        ("Coding / medical-necessity edits", df.edit_category.isin(["Coding", "Medical Necessity"])),
    ]
    base_rate = df.pended.mean()
    out = []
    for label, m in conds:
        sub = df[m]
        if len(sub) < 20:
            continue
        rate = sub.pended.mean()
        out.append({"driver": label, "volume": int(len(sub)),
                    "pend_rate": round(float(rate) * 100, 1),
                    "lift": round(float(rate / base_rate), 2)})
    out.sort(key=lambda x: -x["pend_rate"])
    return out

# ── Public API ───────────────────────────────────────────────────────────────────
def prevention_summary(total_pended):
    s = _train()
    # straight-through candidates: high-pend-rate drivers that resolve deterministically
    stp = [d for d in s["drivers"] if d["driver"] in
           ("Authorization missing", "Received > 365 days (timely filing)", "Units exceed authorized max")]
    driver_share_pct = round(s["recoverable_share"] * 100)          # pends from deterministic drivers
    stp_lift_pct = round(s["recoverable_share"] * 0.6 * 100)        # conservative automation yield
    return {
        "model": "HistGradientBoosting (pend-risk classifier)",
        "auc": round(s["prev_auc"], 3),
        "trained_on": s["n_train"],
        "drivers": s["drivers"],
        "stp_candidates": stp,
        "driver_share_pct": driver_share_pct,
        "stp_lift_pct": stp_lift_pct,
        "stp_lift_claims": int(total_pended * stp_lift_pct / 100),
    }

def confidence_summary():
    s = _train()
    return {
        "model": "HistGradientBoosting + isotonic calibration",
        "auc": round(s["conf_auc"], 3),
        "brier": round(s["brier"], 4),
        "trained_on": s["n_pended"],
        "calibration": s["calib"],
    }

def _row_from_claim(claim):
    return {
        "edit_category": claim.get("edit_category", "Pricing"),
        "pos": str(claim.get("place_of_service", "11")),
        "specialty": claim.get("provider_specialty", "Internal Medicine"),
        "claim_type": claim.get("claim_type", "Professional"),
        "par": 1, "in_network": 1,
        "has_auth": 1 if claim.get("auth_number") else 0,
        "other_insurance": 0,
        "billed": claim.get("billed_amount") or 0.0,
        "allowed": claim.get("allowed_amount") or 0.0,
        "billed_allowed_ratio": round((claim.get("billed_amount") or 1) / max(claim.get("allowed_amount") or 1, 1), 3),
        "units": claim.get("units_billed", 1), "max_units": 8,
        "days_to_receipt": claim.get("days_in_queue", 30) or 30,
    }

def score_confidence(claim):
    """Calibrated P(decision correct) for one real claim dict."""
    s = _train()
    X = _X(pd.DataFrame([_row_from_claim(claim)])).reindex(columns=s["cols"], fill_value=0)
    return float(s["conf"].predict_proba(X)[0, 1])

def score_confidence_batch(claims):
    """Vectorized calibrated confidence for many claims."""
    s = _train()
    X = _X(pd.DataFrame([_row_from_claim(c) for c in claims])).reindex(columns=s["cols"], fill_value=0)
    return [float(p) for p in s["conf"].predict_proba(X)[:, 1]]

def warmup():
    _train()
