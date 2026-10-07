"""
Drought-Based Smart Subsidy System - Streamlit app
Run:  streamlit run app.py
Needs drought_score.py in the same folder. If real_plots.csv is in the same folder,
the app loads it automatically (an uploaded file overrides it).
"""
import os
import random
import zlib

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from drought_score import (
    CONFIG, HIGH, LOW, MEDIUM, PAYOUT_SHARE, TS_CUR_COLS, TS_DATES, TS_NORM_COLS,
    early_warning, make_sample_data, score_plots,
)

st.set_page_config(page_title="Drought-Based Smart Subsidy System", page_icon="🌾", layout="wide")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REAL_DATA_PATH = os.path.join(BASE_DIR, "real_plots.csv")
REQUIRED_COLUMNS = [
    "plot_id", "farmer", "village", "lat", "lon",
    "ndvi_current", "ndvi_normal", "rain_actual_mm", "rain_normal_mm", "cloud_cover_pct",
]
TIER_COLOR = {HIGH: "#d62728", MEDIUM: "#ff9f1c", LOW: "#2ca02c"}
TIER_EMOJI = {HIGH: "🔴", MEDIUM: "🟠", LOW: "🟢"}
EW_COLOR = {"Worsening": "#d62728", "Stressed": "#ff7f0e", "Watch": "#e6c200",
            "Stable": "#2ca02c", "No trend data": "#999999"}
EW_EMOJI = {"Worsening": "🔴", "Stressed": "🟠", "Watch": "🟡", "Stable": "🟢", "No trend data": "⚪"}
LAYERS = ["Priority tier", "NDVI vs normal", "Early warning (trend)"]
LAYER_LEGEND = {
    "Priority tier": "🔴 High priority · 🟠 Medium priority · 🟢 Low priority",
    "NDVI vs normal": "🟢 at or above normal · 🟡 up to 15% below · 🟠 15-30% below · 🔴 more than 30% below",
    "Early warning (trend)": "🔴 Worsening · 🟠 Stressed · 🟡 Watch · 🟢 Stable · ⚪ no trend data",
}

if "decisions" not in st.session_state:
    st.session_state.decisions = {}  # plot_id -> "Approved" / "Rejected"


@st.cache_data
def load_sample() -> pd.DataFrame:
    # drop the hidden answer-key column so the demo can't "cheat"
    return make_sample_data().drop(columns=["irrigated_hidden"])


@st.cache_data
def load_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path)


def mock_profile(plot_id: str) -> dict:
    """DEMO data only: stands in for the farmer and land registry integration."""
    rng = random.Random(zlib.crc32(plot_id.encode()))
    return {
        "Survey / Gat no.": str(rng.randint(10, 400)),
        "Farm size": f"{rng.uniform(1.0, 8.0):.1f} acres",
        "Main crop": rng.choice(["Cotton", "Soybean", "Tur (pigeon pea)", "Maize", "Bajra"]),
        "Irrigation": rng.choice(["Rain-fed", "Rain-fed", "Well / borewell"]),
        "Verification": rng.choice(["Verified", "Verified", "Pending verification"]),
    }


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Data")
    uploaded = st.file_uploader("Upload plot data (CSV)", type="csv")
    if uploaded is not None:
        st.caption("Using your uploaded file.")
    elif os.path.exists(REAL_DATA_PATH):
        st.caption("Using the bundled real_plots.csv. Upload a file to override it.")
    else:
        st.caption("No real_plots.csv found, so the app uses simulated sample data.")

    st.header("Map")
    layer = st.selectbox("Colour the map by", LAYERS)

    st.header("Scoring settings")
    w_ndvi = st.slider("NDVI weight (rainfall gets the rest)", 0.0, 1.0, 0.7, 0.05)
    trigger_pct = st.slider(
        "Trigger-1: area rainfall deficit must exceed (%)", 0, 60, int(CONFIG["trigger1_deficit"] * 100),
        help="Maharashtra Drought Manual: Trigger-1 needs a rainfall deficit above 25% plus a prolonged dry spell. "
             "Plots in areas below this level are not scored.",
    )
    medium_t = st.slider("Medium priority from score", 0, 100, CONFIG["medium_threshold"])
    high_t = st.slider("High priority from score", 0, 100, CONFIG["high_threshold"])
    cloud_t = st.slider("Flag for manual review above cloud cover %", 0, 100, CONFIG["max_cloud_cover"])
    base_subsidy = st.number_input("Indicative amount per farm (Rs)", 1000, 1_000_000, 10_000, 1000)

if high_t <= medium_t:
    st.error("'High priority' threshold must be higher than 'Medium priority' threshold.")
    st.stop()

if uploaded is not None:
    raw, data_note = pd.read_csv(uploaded), "Data: your uploaded file."
elif os.path.exists(REAL_DATA_PATH):
    raw = load_csv(REAL_DATA_PATH)
    data_note = (
        f"Data: satellite NDVI (Sentinel-2, MODIS) and rainfall (CHIRPS) for {len(raw)} sampled cropland points in "
        "Chhatrapati Sambhajinagar district, Kharif 2026. Farmer names, zones and farm profiles are placeholders."
    )
else:
    raw, data_note = load_sample(), "Data: SIMULATED sample data for demonstration only."

missing = [c for c in REQUIRED_COLUMNS if c not in raw.columns]
if missing:
    st.error(f"Your CSV is missing columns: {', '.join(missing)}")
    st.stop()

cfg = {
    **CONFIG,
    "weights": {"ndvi": w_ndvi, "rain": round(1 - w_ndvi, 2)},
    "trigger1_deficit": trigger_pct / 100,
    "medium_threshold": medium_t,
    "high_threshold": high_t,
    "max_cloud_cover": cloud_t,
}
scored = early_warning(score_plots(raw, cfg))
scored["indicative_payout"] = scored["tier"].map(PAYOUT_SHARE) * base_subsidy
has_trend = (scored["ew_status"] != "No trend data").any()
if layer == "Early warning (trend)" and not has_trend:
    st.sidebar.warning("This dataset has no NDVI trend columns, so the map is coloured by priority tier.")
    layer = "Priority tier"

# ------------------------------------------------------------------ header + impact
st.title("🌾 Drought-Based Smart Subsidy System")
st.markdown("**Farm-level drought detection and fair subsidy prioritisation**")
st.caption(
    "Satellite screening that shows officials which farms to inspect first, with the evidence, "
    "so drought relief reaches the farms that need it. Officials verify every case."
)
st.caption(data_note)

n = len(scored)
counts = scored["tier"].value_counts()
blanket = n * base_subsidy
targeted = scored["indicative_payout"].sum()
saved_pct = 100 * (blanket - targeted) / blanket if blanket else 0

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Plots in declared area", n)
c2.metric("🔴 High priority", int(counts.get(HIGH, 0)))
c3.metric("🟠 Medium priority", int(counts.get(MEDIUM, 0)))
c4.metric("🟢 Low priority", int(counts.get(LOW, 0)))
c5.metric("Indicative difference*", f"{saved_pct:.0f}%")
st.markdown(
    f"**Blanket subsidy:** Rs {blanket:,.0f} &nbsp;→&nbsp; **Prioritised (indicative):** Rs {targeted:,.0f}"
)
st.caption(
    "*Illustrative only: it depends on the thresholds, a placeholder amount per farm and a sample of plots. "
    "Low priority does not mean no loss. Officials can still add a farm after a field inspection."
)
n_gated = int((~scored["trigger1_met"]).sum())
if n_gated:
    st.caption(
        f"{n_gated} plots are in areas where the rainfall deficit is below the Trigger-1 level, so they were not scored."
    )
if has_trend:
    ew_counts = scored["ew_status"].value_counts()
    st.caption(
        f"🔔 Early warning: {int(ew_counts.get('Worsening', 0))} plots worsening, "
        f"{int(ew_counts.get('Stressed', 0))} stressed, {int(ew_counts.get('Watch', 0))} on watch "
        "(vegetation trend vs normal; a trend signal, not a forecast)."
    )

with st.expander("How the score works"):
    st.markdown(
        f"""
**Rule-based and explainable.** There is no black-box model: every score can be traced to two measurements.

1. **Trigger-1 gate.** Only plots in areas where rainfall is more than **{trigger_pct}%** below normal are scored
   (the state's Trigger-1 rule uses a rainfall deficit above 25% plus a dry spell).
2. **Drought stress score (0-100)** = **{w_ndvi:.0%}** × NDVI drop score + **{1 - w_ndvi:.0%}** × rainfall deficit score.
   NDVI 40% below normal scores 100 on its own component, and so does rainfall 60% below normal.
3. **Priority:** below {medium_t} is Low, {medium_t}-{high_t - 1} is Medium, {high_t} and above is High.
4. **Cloud cover** above {cloud_t}% flags the plot for manual inspection.
5. **Early warning** compares recent vegetation (NDVI) with normal and flags farms that keep falling further below it.

Soil moisture and temperature are shown as context and do not change the score.
Rainfall is measured at about 5 km resolution, so nearby plots share the same value; NDVI is what separates farms.
        """
    )

# ------------------------------------------------------------------ selection state
ids = scored["plot_id"].tolist()
if st.session_state.get("selected_plot") not in ids:
    st.session_state.selected_plot = (
        scored.sort_values("drought_score", ascending=False)["plot_id"].iloc[0]
    )
by_id = scored.set_index("plot_id")


def marker_color(r, layer_name: str) -> str:
    if layer_name == "NDVI vs normal":
        d = r.ndvi_drop
        return "#2ca02c" if d <= 0.0 else "#bcbd22" if d <= 0.15 else "#ff9f1c" if d <= 0.30 else "#d62728"
    if layer_name == "Early warning (trend)":
        return EW_COLOR[r.ew_status]
    return TIER_COLOR[r.tier]


def build_map(df: pd.DataFrame, selected: str, layer_name: str) -> folium.Map:
    m = folium.Map(tiles="OpenStreetMap", control_scale=True)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery",
        name="Satellite",
    ).add_to(m)
    for r in df.itertuples():
        is_sel = r.plot_id == selected
        review = bool(r.needs_manual_review)
        color = marker_color(r, layer_name)
        folium.CircleMarker(
            location=[r.lat, r.lon],
            radius=11 if is_sel else 7,
            color="black" if is_sel else ("#333333" if review else color),
            weight=3 if is_sel else (2 if review else 1),
            dash_array="4" if (review and not is_sel) else None,
            fill=True,
            fill_color=color,
            fill_opacity=0.85,
            tooltip=r.plot_id,
        ).add_to(m)
    m.fit_bounds([[df["lat"].min(), df["lon"].min()], [df["lat"].max(), df["lon"].max()]])
    folium.LayerControl().add_to(m)
    return m


# ------------------------------------------------------------------ map + evidence
left, right = st.columns([3, 2])

with left:
    st.subheader("Farm map")
    st.caption(f"{LAYER_LEGEND[layer]} · dashed dark outline = low-confidence, manual review")
    map_state = st_folium(
        build_map(scored, st.session_state.selected_plot, layer),
        height=520,
        use_container_width=True,
        returned_objects=["last_object_clicked_tooltip"],
        key="plot_map",
    )
    clicked = (map_state or {}).get("last_object_clicked_tooltip")
    if clicked:
        clicked = clicked.strip()
        if clicked in ids and clicked != st.session_state.get("last_click"):
            st.session_state.last_click = clicked
            st.session_state.selected_plot = clicked
            st.rerun()

with right:
    st.subheader("Farm profile and evidence")
    st.selectbox(
        "Select a plot (or click it on the map)",
        ids,
        key="selected_plot",
        format_func=lambda i: f"{TIER_EMOJI[by_id.loc[i, 'tier']]} {i} - {by_id.loc[i, 'farmer']}",
    )
    sel = st.session_state.selected_plot
    row = by_id.loc[sel]

    st.markdown(f"### {sel} · {row['farmer']}")
    if row["trigger1_met"]:
        score_text = f"drought stress score **{row['drought_score']:.0f} / 100**"
    else:
        score_text = "**not scored** (area rainfall deficit below Trigger-1)"
    st.markdown(f"{TIER_EMOJI[row['tier']]} **{row['tier']}** &nbsp;|&nbsp; {score_text} &nbsp;|&nbsp; {row['village']}")
    st.caption(f"Location: {row['lat']:.5f}, {row['lon']:.5f}")

    with st.expander("Farmer and farm profile (DEMO data)"):
        for k, v in mock_profile(sel).items():
            st.write(f"**{k}:** {v}")
        st.caption("Placeholder values. A real rollout would pull these from the land and farmer registry.")

    ndvi_pct = row["ndvi_drop"] * 100
    rain_pct = row["rain_deficit"] * 100
    st.write(
        f"- Crop greenness (NDVI) is **{abs(ndvi_pct):.0f}% {'below' if ndvi_pct >= 0 else 'above'}** normal for this plot.\n"
        f"- Rainfall in the area is **{abs(rain_pct):.0f}% {'below' if rain_pct >= 0 else 'above'}** normal."
    )
    if row["trigger1_met"]:
        st.caption(
            f"Why this score: NDVI contributes {row['ndvi_points']:.0f} points and rainfall {row['rain_points']:.0f} points "
            f"(total {row['drought_score']:.0f})."
        )

    ctx = []
    sm, tm = row.get("soil_moisture_anomaly_pct"), row.get("temp_max_anomaly_c")
    if pd.notna(sm):
        ctx.append(("Soil moisture vs normal", f"{sm:+.0f}%"))
    if pd.notna(tm):
        ctx.append(("Max temperature vs normal", f"{tm:+.1f} °C"))
    if ctx:
        mc = st.columns(len(ctx))
        for col, (label, val) in zip(mc, ctx):
            col.metric(label, val)
        st.caption("Context indicators (coarse resolution). They do not change the score.")

    ch1, ch2 = st.columns(2)
    with ch1:
        st.caption("NDVI (vegetation health)")
        st.bar_chart(
            pd.DataFrame({"NDVI": [row["ndvi_normal"], row["ndvi_current"]]}, index=["Normal", "This season"]),
            height=180,
        )
    with ch2:
        st.caption("Rainfall (mm)")
        st.bar_chart(
            pd.DataFrame({"mm": [row["rain_normal_mm"], row["rain_actual_mm"]]}, index=["Normal", "This season"]),
            height=180,
        )

    ew = row["ew_status"]
    if has_trend and ew != "No trend data":
        st.markdown(f"**Early warning:** {EW_EMOJI[ew]} {ew} (latest NDVI {row['ew_latest_pct']:+.0f}% vs normal)")
        trend = pd.DataFrame(
            {"This season": row[TS_CUR_COLS].astype(float).to_numpy(),
             "Normal": row[TS_NORM_COLS].astype(float).to_numpy()},
            index=pd.to_datetime(TS_DATES),
        )
        st.line_chart(trend, height=200)
        st.caption("NDVI trend through the season, 16-day periods, compared with the long-term normal.")
    else:
        st.caption("Early warning needs the NDVI trend columns (export with gee_export_v2.js).")

    if row["needs_manual_review"]:
        st.warning(
            f"Cloud cover {row['cloud_cover_pct']:.0f}% - satellite reading is low-confidence. "
            "Recommend a field inspection before approving."
        )

    if ew in ("Worsening", "Stressed") or row["tier"] == HIGH:
        with st.expander("Draft early-warning alert (demo, nothing is sent)"):
            st.code(
                f"Drought alert (DEMO)\n"
                f"Plot {sel} ({row['farmer']}, {row['village']}): {row['tier']}.\n"
                f"Crop greenness is {abs(ndvi_pct):.0f}% {'below' if ndvi_pct >= 0 else 'above'} normal; "
                f"area rainfall is {abs(rain_pct):.0f}% {'below' if rain_pct >= 0 else 'above'} normal.\n"
                f"Vegetation trend: {ew}.\n"
                f"Suggested action: field inspection (GPS {row['lat']:.5f}, {row['lon']:.5f}).",
                language="text",
            )

    if row["tier"] == LOW:
        st.info("Low priority: no strong drought signal in the satellite data. "
                "Officials can still add this farm if field evidence shows crop loss.")
    else:
        st.write(f"**Indicative payout: Rs {row['indicative_payout']:,.0f}**")
        b1, b2, b3 = st.columns(3)
        if b1.button("✅ Approve", key=f"approve_{sel}", width="stretch"):
            st.session_state.decisions[sel] = "Approved"
        if b2.button("❌ Reject", key=f"reject_{sel}", width="stretch"):
            st.session_state.decisions[sel] = "Rejected"
        if b3.button("↩️ Reset", key=f"reset_{sel}", width="stretch"):
            st.session_state.decisions.pop(sel, None)
        st.caption(f"Decision: **{st.session_state.decisions.get(sel, 'Pending')}**")

# ------------------------------------------------------------------ tables
st.divider()
tab1, tab2 = st.tabs(["Inspection priority list", "Decision summary"])

flagged = scored[scored["tier"] != LOW].copy()
flagged["decision"] = flagged["plot_id"].map(st.session_state.decisions).fillna("Pending")
flagged = flagged.sort_values("drought_score", ascending=False)

with tab1:
    if st.button("Approve all High-priority plots with high-confidence data"):
        auto = flagged[(flagged["tier"] == HIGH) & (~flagged["needs_manual_review"])]
        for pid in auto["plot_id"]:
            st.session_state.decisions[pid] = "Approved"
        st.rerun()

    cols = ["plot_id", "farmer", "village", "tier", "drought_score", "ew_status",
            "indicative_payout", "needs_manual_review", "decision"]
    st.dataframe(flagged[cols], width="stretch", hide_index=True)

    export = flagged.copy()
    export["ndvi_below_normal_pct"] = (export["ndvi_drop"] * 100).round(1)
    export["rain_deficit_pct"] = (export["rain_deficit"] * 100).round(1)
    export["google_maps"] = (
        "https://www.google.com/maps?q=" + export["lat"].round(6).astype(str) + "," + export["lon"].round(6).astype(str)
    )
    export_cols = ["plot_id", "farmer", "village", "lat", "lon", "google_maps", "tier", "drought_score",
                   "ndvi_below_normal_pct", "rain_deficit_pct", "ew_status", "cloud_cover_pct",
                   "needs_manual_review", "indicative_payout", "decision"]
    export_cols += [c for c in ("soil_moisture_anomaly_pct", "temp_max_anomaly_c") if c in export.columns]
    st.download_button(
        "Download inspection list with GPS (CSV)",
        export[export_cols].to_csv(index=False),
        "inspection_list.csv",
        "text/csv",
    )

with tab2:
    decided = flagged["decision"].value_counts()
    approved_total = flagged.loc[flagged["decision"] == "Approved", "indicative_payout"].sum()
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Approved", int(decided.get("Approved", 0)))
    d2.metric("Rejected", int(decided.get("Rejected", 0)))
    d3.metric("Pending review", int(decided.get("Pending", 0)))
    d4.metric("Approved (indicative)", f"Rs {approved_total:,.0f}")
