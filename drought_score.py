"""
Drought-Based Smart Subsidy System - scoring engine

Run with sample data:     python drought_score.py
Run with your own data:   python drought_score.py --input real_plots.csv

Required CSV columns:
  plot_id, farmer, village, lat, lon,
  ndvi_current, ndvi_normal,          # plot-level vegetation (e.g. Sentinel-2)
  rain_actual_mm, rain_normal_mm,     # area-level rainfall (e.g. CHIRPS/IMD)
  cloud_cover_pct                     # how cloudy the satellite pass was
Optional columns (used when present):
  ts_cur_1..ts_cur_6, ts_norm_1..ts_norm_6   # NDVI trend this season vs normal -> early warning
  soil_moisture_anomaly_pct, temp_max_anomaly_c  # context indicators

How it works (rule-based and explainable, not a machine-learning model)
  1. Trigger-1 gate: plots whose area rainfall deficit is NOT above the Trigger-1 level
     (Maharashtra Drought Manual: more than 25% deficit) are not scored.
  2. Drought stress score (0-100) = weighted NDVI drop + rainfall deficit.
  3. Priority for field inspection: Low / Medium / High.
  4. Early warning: is vegetation falling further below normal over recent weeks?
"""
import argparse

import numpy as np
import pandas as pd

LOW, MEDIUM, HIGH = "Low priority", "Medium priority", "High priority"

# ---- Tunable settings (change these while you calibrate on real data) ----
CONFIG = {
    "weights": {"ndvi": 0.7, "rain": 0.3},  # NDVI is plot-level evidence, rain is area context
    "ndvi_drop_full": 0.40,      # NDVI 40% below normal  -> NDVI component = 100
    "rain_deficit_full": 0.60,   # rainfall 60% below normal -> rain component = 100
    "trigger1_deficit": 0.25,    # area rainfall deficit must exceed this to be scored
    "medium_threshold": 50,      # score >= 50 -> Medium priority
    "high_threshold": 70,        # score >= 70 -> High priority
    "max_cloud_cover": 40,       # above this, flag for manual review
}

PAYOUT_SHARE = {LOW: 0.0, MEDIUM: 0.5, HIGH: 1.0}
BASE_SUBSIDY = 10000  # placeholder amount per plot (Rs), replace with the real scheme amount

# NDVI trend: six 16-day MODIS composites, labelled by the date each one starts (2026)
TS_N = 6
TS_DATES = ["2026-06-26", "2026-07-12", "2026-07-28", "2026-08-13", "2026-08-29", "2026-09-14"]
TS_CUR_COLS = [f"ts_cur_{i}" for i in range(1, TS_N + 1)]
TS_NORM_COLS = [f"ts_norm_{i}" for i in range(1, TS_N + 1)]


def score_plots(df: pd.DataFrame, cfg: dict = CONFIG) -> pd.DataFrame:
    df = df.copy()
    df["ndvi_drop"] = (df["ndvi_normal"] - df["ndvi_current"]) / df["ndvi_normal"]
    df["rain_deficit"] = (df["rain_normal_mm"] - df["rain_actual_mm"]) / df["rain_normal_mm"]

    ndvi_score = (df["ndvi_drop"] / cfg["ndvi_drop_full"]).clip(0, 1) * 100
    rain_score = (df["rain_deficit"] / cfg["rain_deficit_full"]).clip(0, 1) * 100

    w = cfg["weights"]
    ndvi_pts = w["ndvi"] * ndvi_score
    rain_pts = w["rain"] * rain_score

    # Trigger-1 gate: only score plots in areas that meet the rainfall-deficit test
    df["trigger1_met"] = df["rain_deficit"] > cfg["trigger1_deficit"]
    df["ndvi_points"] = ndvi_pts.where(df["trigger1_met"], 0.0).round(1)
    df["rain_points"] = rain_pts.where(df["trigger1_met"], 0.0).round(1)
    df["drought_score"] = (ndvi_pts + rain_pts).round(1).where(df["trigger1_met"], 0.0)

    df["tier"] = pd.cut(
        df["drought_score"],
        bins=[-np.inf, cfg["medium_threshold"], cfg["high_threshold"], np.inf],
        labels=[LOW, MEDIUM, HIGH],
        right=False,
    ).astype(str)

    df["needs_manual_review"] = df["cloud_cover_pct"] > cfg["max_cloud_cover"]
    df["indicative_payout"] = df["tier"].map(PAYOUT_SHARE) * BASE_SUBSIDY
    return df


def early_warning(df: pd.DataFrame, stressed=-0.20, watch=-0.10, worsening_step=0.05, min_readings=3) -> pd.DataFrame:
    """Adds ew_status and ew_latest_pct (latest NDVI anomaly vs normal, in %).

    Worsening : latest anomaly is below -10% AND at least 5 points lower than the recent average
    Stressed  : latest anomaly is below -20% but not getting worse
    Watch     : latest anomaly is between -10% and -20%
    Stable    : otherwise
    Limited data : fewer than 3 clear satellite readings this season (monsoon cloud), so no status is given
    This is a trend signal, not a forecast.
    """
    df = df.copy()
    if not all(c in df.columns for c in TS_CUR_COLS + TS_NORM_COLS):
        df["ew_status"] = "No trend data"
        df["ew_latest_pct"] = np.nan
        return df

    cur = df[TS_CUR_COLS].to_numpy(dtype=float)
    norm = df[TS_NORM_COLS].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        anomaly = (cur - norm) / norm

    status, latest_pct = [], []
    for a in anomaly:
        ok = np.flatnonzero(np.isfinite(a))
        if len(ok) == 0:
            status.append("No trend data")
            latest_pct.append(np.nan)
            continue
        latest = a[ok[-1]]
        if len(ok) < min_readings:
            status.append("Limited data")
            latest_pct.append(round(latest * 100, 1))
            continue
        prior = a[ok[:-1]][-3:].mean()
        if latest <= watch and latest < prior - worsening_step:
            status.append("Worsening")
        elif latest <= stressed:
            status.append("Stressed")
        elif latest <= watch:
            status.append("Watch")
        else:
            status.append("Stable")
        latest_pct.append(round(latest * 100, 1))
    df["ew_status"] = status
    df["ew_latest_pct"] = latest_pct
    return df


def make_sample_data(seed: int = 42) -> pd.DataFrame:
    """Fake data: every village is 'drought-declared', but farms are hit unevenly
    (some have borewell/irrigation) - exactly the case this project targets."""
    rng = np.random.default_rng(seed)
    # village -> rainfall deficit vs normal (area-level, same for all plots in it)
    villages = {
        "Wadgaon": 0.62, "Pimpalgaon": 0.55, "Kherda": 0.48,
        "Sawargaon": 0.40, "Nimgaon": 0.30, "Dhanora": 0.22,
    }
    shape = np.array([0.35, 0.5, 0.65, 0.8, 1.0, 0.95])  # normal greening through the season
    progress = (np.arange(1, TS_N + 1) / TS_N) ** 1.5
    rows, pid = [], 1
    for i, (village, deficit) in enumerate(villages.items()):
        rain_normal = rng.uniform(550, 700)
        for _ in range(8 if i < 4 else 9):
            irrigated = rng.random() < 0.30
            drop = deficit * 0.6 + rng.normal(0, 0.06)
            if irrigated:
                drop *= 0.25
            drop = max(drop, -0.03)
            ndvi_normal = rng.uniform(0.55, 0.75)
            ts_norm = ndvi_normal * shape
            ts_cur = ts_norm * (1 - drop * progress + rng.normal(0, 0.02, TS_N))
            row = {
                "plot_id": f"MH-{pid:03d}",
                "farmer": f"Farmer {pid}",
                "village": village,
                "lat": 19.0 + rng.uniform(-0.08, 0.08),
                "lon": 75.5 + rng.uniform(-0.08, 0.08),
                "ndvi_normal": round(ndvi_normal, 3),
                "ndvi_current": round(ndvi_normal * (1 - drop), 3),
                "rain_normal_mm": round(rain_normal, 1),
                "rain_actual_mm": round(rain_normal * (1 - deficit), 1),
                "cloud_cover_pct": int(np.clip(rng.gamma(2, 10), 0, 100)),
                "soil_moisture_anomaly_pct": round(-deficit * 45 + rng.normal(0, 4), 1),
                "temp_max_anomaly_c": round(deficit * 2.5 + rng.normal(0, 0.3), 2),
                "irrigated_hidden": irrigated,  # only for checking the demo data
            }
            for k in range(TS_N):
                row[f"ts_norm_{k + 1}"] = round(float(ts_norm[k]), 3)
                row[f"ts_cur_{k + 1}"] = round(float(ts_cur[k]), 3)
            rows.append(row)
            pid += 1
    return pd.DataFrame(rows)


def impact_summary(scored: pd.DataFrame) -> dict:
    n = len(scored)
    blanket = n * BASE_SUBSIDY
    targeted = scored["indicative_payout"].sum()
    return {
        "total_farms": n,
        "high": int((scored["tier"] == HIGH).sum()),
        "medium": int((scored["tier"] == MEDIUM).sum()),
        "low": int((scored["tier"] == LOW).sum()),
        "not_scored_trigger1": int((~scored["trigger1_met"]).sum()),
        "manual_review": int(scored["needs_manual_review"].sum()),
        "blanket_cost": blanket,
        "targeted_cost": targeted,
        "saved_pct": round(100 * (blanket - targeted) / blanket, 1),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="CSV with the required columns (see top of file)")
    ap.add_argument("--output", default="scored_plots.csv")
    args = ap.parse_args()

    data = pd.read_csv(args.input) if args.input else make_sample_data()
    scored = early_warning(score_plots(data))
    scored.to_csv(args.output, index=False)

    print("\nPlots per village by priority:")
    print(pd.crosstab(scored["village"], scored["tier"])[[LOW, MEDIUM, HIGH]])

    s = impact_summary(scored)
    print(f"\n{s['total_farms']} plots -> {s['high']} high, {s['medium']} medium, {s['low']} low priority")
    print(f"{s['not_scored_trigger1']} plots not scored (area rainfall deficit below Trigger-1 level)")
    print(f"{s['manual_review']} plots flagged for manual review (cloud cover)")
    print("Early warning:", scored["ew_status"].value_counts().to_dict())
    print(f"Blanket cost: Rs {s['blanket_cost']:,.0f} | Prioritised (indicative) cost: "
          f"Rs {s['targeted_cost']:,.0f} | Difference: {s['saved_pct']}%")
    print(f"\nSaved to {args.output}")
