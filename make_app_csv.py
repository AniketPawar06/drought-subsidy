"""
Turns the Earth Engine export into a CSV the Streamlit app can use.

Usage:
    python make_app_csv.py sambhajinagar_plots_2026_v2.csv
Creates real_plots.csv (keep it next to app.py and the app loads it automatically).

Works with the v1 export (core columns only) and the v2 export (adds NDVI trend,
soil moisture and temperature columns). Farmer names are MOCK. Zones are coarse
map grid cells, not real villages.
"""
import sys

import pandas as pd

MIN_NORMAL_NDVI = 0.25  # drop fallow/bare points: their NDVI ratio is too noisy to trust
ZONE_SIZE_DEG = 0.15    # about 16 km grid cells

CORE = ["lat", "lon", "ndvi_current", "ndvi_normal", "rain_actual_mm", "rain_normal_mm", "cloud_cover_pct"]
TREND = [f"ts_cur_{i}" for i in range(1, 7)] + [f"ts_norm_{i}" for i in range(1, 7)]
CONTEXT = ["soil_moisture_anomaly_pct", "temp_max_anomaly_c"]

src = sys.argv[1] if len(sys.argv) > 1 else "sambhajinagar_plots_2026_v2.csv"
df = pd.read_csv(src)

missing = [c for c in CORE if c not in df.columns]
if missing:
    sys.exit(f"Missing columns in {src}: {missing}")

before = len(df)
df = df.dropna(subset=CORE)
df = df[(df["ndvi_normal"] >= MIN_NORMAL_NDVI) & (df["rain_normal_mm"] > 0)].reset_index(drop=True)
print(f"Kept {len(df)} of {before} points (dropped empty or low-vegetation points)")

# group points into coarse zones so the app's 'village' column is meaningful
cell = list(zip((df["lat"] // ZONE_SIZE_DEG).astype(int), (df["lon"] // ZONE_SIZE_DEG).astype(int)))
zone_ids = {c: i + 1 for i, c in enumerate(sorted(set(cell)))}
df["village"] = [f"Zone {zone_ids[c]}" for c in cell]

df["plot_id"] = [f"CS-{i + 1:03d}" for i in range(len(df))]
df["farmer"] = [f"Farmer {i + 1}" for i in range(len(df))]
df["cloud_cover_pct"] = df["cloud_cover_pct"].clip(0, 100).round(0)

# optional columns are kept only if the export has them
trend_cols = [c for c in TREND if c in df.columns]
context_cols = [c for c in CONTEXT if c in df.columns]
if len(trend_cols) == len(TREND):
    df[trend_cols] = df[trend_cols].round(3)
else:
    trend_cols = []
    print("No NDVI trend columns found, so early warning will be off (use gee_export_v2.js to add them).")
if "soil_moisture_anomaly_pct" in context_cols:
    df["soil_moisture_anomaly_pct"] = df["soil_moisture_anomaly_pct"].round(1)
if "temp_max_anomaly_c" in context_cols:
    df["temp_max_anomaly_c"] = df["temp_max_anomaly_c"].round(2)

cols = ["plot_id", "farmer", "village"] + CORE + context_cols + trend_cols
df[cols].to_csv("real_plots.csv", index=False)

ndvi_drop = (df["ndvi_normal"] - df["ndvi_current"]) / df["ndvi_normal"]
rain_def = (df["rain_normal_mm"] - df["rain_actual_mm"]) / df["rain_normal_mm"]
print("Saved real_plots.csv with", len(cols), "columns")
print(f"NDVI drop vs normal:   median {ndvi_drop.median():.0%}, 10th-90th pct {ndvi_drop.quantile(.1):.0%} to {ndvi_drop.quantile(.9):.0%}")
print(f"Rainfall deficit:      median {rain_def.median():.0%}, 10th-90th pct {rain_def.quantile(.1):.0%} to {rain_def.quantile(.9):.0%}")
if trend_cols:
    latest_ok = df[trend_cols[:6]].notna().sum().to_dict()
    print("Plots with an NDVI trend reading per period (this season):", latest_ok)
    print("If the last periods show 0, MODIS has not published them yet; early warning uses what exists.")
