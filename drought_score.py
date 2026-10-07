"""
Drought-Based Smart Subsidy System - scoring engine

Run with sample data:     python drought_score.py
Run with your own data:   python drought_score.py --input real_plots.csv

Required CSV columns:
  plot_id, farmer, village, lat, lon,
  ndvi_current, ndvi_normal,          # plot-level vegetation (e.g. Sentinel-2)
  rain_actual_mm, rain_normal_mm,     # area-level rainfall (e.g. CHIRPS/IMD)
  cloud_cover_pct                     # how cloudy the satellite pass was

How it works
  1. Trigger-1 gate: plots whose area rainfall deficit is NOT above the Trigger-1 level
     (Maharashtra Drought Manual: more than 25% deficit) are not scored.
  2. Score (0-100) = weighted NDVI drop + rainfall deficit.
  3. Priority for field inspection: Low / Medium / High.
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


def score_plots(df: pd.DataFrame, cfg: dict = CONFIG) -> pd.DataFrame:
    df = df.copy()
    df["ndvi_drop"] = (df["ndvi_normal"] - df["ndvi_current"]) / df["ndvi_normal"]
    df["rain_deficit"] = (df["rain_normal_mm"] - df["rain_actual_mm"]) / df["rain_normal_mm"]

    ndvi_score = (df["ndvi_drop"] / cfg["ndvi_drop_full"]).clip(0, 1) * 100
    rain_score = (df["rain_deficit"] / cfg["rain_deficit_full"]).clip(0, 1) * 100

    w = cfg["weights"]
    score = (w["ndvi"] * ndvi_score + w["rain"] * rain_score).round(1)

    # Trigger-1 gate: only score plots in areas that meet the rainfall-deficit test
    df["trigger1_met"] = df["rain_deficit"] > cfg["trigger1_deficit"]
    df["drought_score"] = score.where(df["trigger1_met"], 0.0)

    df["tier"] = pd.cut(
        df["drought_score"],
        bins=[-np.inf, cfg["medium_threshold"], cfg["high_threshold"], np.inf],
        labels=[LOW, MEDIUM, HIGH],
        right=False,
    ).astype(str)

    df["needs_manual_review"] = df["cloud_cover_pct"] > cfg["max_cloud_cover"]
    df["indicative_payout"] = df["tier"].map(PAYOUT_SHARE) * BASE_SUBSIDY
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
            rows.append({
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
                "irrigated_hidden": irrigated,  # only for checking the demo data
            })
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
    scored = score_plots(data)
    scored.to_csv(args.output, index=False)

    print("\nPlots per village by priority:")
    print(pd.crosstab(scored["village"], scored["tier"])[[LOW, MEDIUM, HIGH]])

    s = impact_summary(scored)
    print(f"\n{s['total_farms']} plots -> {s['high']} high, {s['medium']} medium, {s['low']} low priority")
    print(f"{s['not_scored_trigger1']} plots not scored (area rainfall deficit below Trigger-1 level)")
    print(f"{s['manual_review']} plots flagged for manual review (cloud cover)")
    print(f"Blanket cost: Rs {s['blanket_cost']:,.0f} | Prioritised (indicative) cost: "
          f"Rs {s['targeted_cost']:,.0f} | Difference: {s['saved_pct']}%")
    print(f"\nSaved to {args.output}")
