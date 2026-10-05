"""
Drought-Based Smart Subsidy System - Streamlit app
Run:  streamlit run app.py
Needs drought_score.py in the same folder.
"""
import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from drought_score import CONFIG, PAYOUT_SHARE, make_sample_data, score_plots

st.set_page_config(page_title="Drought Smart Subsidy", page_icon="🌾", layout="wide")

REQUIRED_COLUMNS = [
    "plot_id", "farmer", "village", "lat", "lon",
    "ndvi_current", "ndvi_normal", "rain_actual_mm", "rain_normal_mm", "cloud_cover_pct",
]
TIER_COLOR = {"Full": "#d62728", "Partial": "#ff9f1c", "No subsidy": "#2ca02c"}
TIER_EMOJI = {"Full": "🔴", "Partial": "🟠", "No subsidy": "🟢"}

if "decisions" not in st.session_state:
    st.session_state.decisions = {}  # plot_id -> "Approved" / "Rejected"


@st.cache_data
def load_sample() -> pd.DataFrame:
    # drop the hidden answer-key column so the demo can't "cheat"
    return make_sample_data().drop(columns=["irrigated_hidden"])


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("Data")
    uploaded = st.file_uploader("Upload plot data (CSV)", type="csv")
    st.caption("No file? The app uses sample data for 50 plots in 6 villages.")

    st.header("Scoring settings")
    w_ndvi = st.slider("NDVI weight (rainfall gets the rest)", 0.0, 1.0, 0.7, 0.05)
    partial_t = st.slider("Partial aid from score", 0, 100, CONFIG["partial_threshold"])
    full_t = st.slider("Full relief from score", 0, 100, CONFIG["full_threshold"])
    cloud_t = st.slider("Flag for manual review above cloud cover %", 0, 100, CONFIG["max_cloud_cover"])
    base_subsidy = st.number_input("Subsidy per farm (Rs)", 1000, 1_000_000, 10_000, 1000)

if full_t <= partial_t:
    st.error("'Full relief' threshold must be higher than 'Partial aid' threshold.")
    st.stop()

if uploaded is not None:
    raw = pd.read_csv(uploaded)
    missing = [c for c in REQUIRED_COLUMNS if c not in raw.columns]
    if missing:
        st.error(f"Your CSV is missing columns: {', '.join(missing)}")
        st.stop()
else:
    raw = load_sample()

cfg = {
    **CONFIG,
    "weights": {"ndvi": w_ndvi, "rain": round(1 - w_ndvi, 2)},
    "partial_threshold": partial_t,
    "full_threshold": full_t,
    "max_cloud_cover": cloud_t,
}
scored = score_plots(raw, cfg)
scored["recommended_payout"] = scored["tier"].map(PAYOUT_SHARE) * base_subsidy

# ------------------------------------------------------------------ header + impact
st.title("🌾 Drought-Based Smart Subsidy System")
st.caption("Farm-level drought evidence from satellite and rainfall data, so relief reaches the farms that need it.")

n = len(scored)
counts = scored["tier"].value_counts()
blanket = n * base_subsidy
targeted = scored["recommended_payout"].sum()
saved_pct = 100 * (blanket - targeted) / blanket if blanket else 0

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Farms in declared area", n)
c2.metric("🔴 Full relief", int(counts.get("Full", 0)))
c3.metric("🟠 Partial aid", int(counts.get("Partial", 0)))
c4.metric("🟢 No subsidy", int(counts.get("No subsidy", 0)))
c5.metric("Budget saved", f"{saved_pct:.0f}%")
st.markdown(
    f"**Blanket subsidy:** Rs {blanket:,.0f} &nbsp;→&nbsp; **Targeted subsidy:** Rs {targeted:,.0f}"
)

# ------------------------------------------------------------------ selection state
ids = scored["plot_id"].tolist()
if st.session_state.get("selected_plot") not in ids:
    st.session_state.selected_plot = (
        scored.sort_values("drought_score", ascending=False)["plot_id"].iloc[0]
    )
by_id = scored.set_index("plot_id")


def build_map(df: pd.DataFrame, selected: str) -> folium.Map:
    m = folium.Map(tiles="OpenStreetMap", control_scale=True)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery",
        name="Satellite",
    ).add_to(m)
    for r in df.itertuples():
        is_sel = r.plot_id == selected
        review = bool(r.needs_manual_review)
        folium.CircleMarker(
            location=[r.lat, r.lon],
            radius=11 if is_sel else 7,
            color="black" if is_sel else ("#333333" if review else TIER_COLOR[r.tier]),
            weight=3 if is_sel else (2 if review else 1),
            dash_array="4" if (review and not is_sel) else None,
            fill=True,
            fill_color=TIER_COLOR[r.tier],
            fill_opacity=0.85,
            tooltip=r.plot_id,
        ).add_to(m)
    m.fit_bounds([[df["lat"].min(), df["lon"].min()], [df["lat"].max(), df["lon"].max()]])
    folium.LayerControl().add_to(m)
    return m


# ------------------------------------------------------------------ map + evidence
left, right = st.columns([3, 2])

with left:
    st.subheader("Farm plots")
    st.caption("🔴 Full relief · 🟠 Partial aid · 🟢 No subsidy · dashed dark outline = low-confidence, manual review")
    map_state = st_folium(
        build_map(scored, st.session_state.selected_plot),
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
    st.subheader("Evidence")
    st.selectbox(
        "Select a plot (or click it on the map)",
        ids,
        key="selected_plot",
        format_func=lambda i: f"{TIER_EMOJI[by_id.loc[i, 'tier']]} {i} - {by_id.loc[i, 'farmer']}",
    )
    sel = st.session_state.selected_plot
    row = by_id.loc[sel]

    st.markdown(f"### {sel} · {row['farmer']}")
    st.markdown(
        f"{TIER_EMOJI[row['tier']]} **{row['tier']}** &nbsp;|&nbsp; "
        f"drought score **{row['drought_score']:.0f} / 100** &nbsp;|&nbsp; {row['village']}"
    )

    ndvi_pct = row["ndvi_drop"] * 100
    rain_pct = row["rain_deficit"] * 100
    st.write(
        f"- Crop greenness (NDVI) is **{abs(ndvi_pct):.0f}% {'below' if ndvi_pct >= 0 else 'above'}** normal for this plot.\n"
        f"- Rainfall in the area is **{abs(rain_pct):.0f}% {'below' if rain_pct >= 0 else 'above'}** normal."
    )

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

    if row["needs_manual_review"]:
        st.warning(
            f"Cloud cover {row['cloud_cover_pct']:.0f}% - satellite reading is low-confidence. "
            "Recommend a field inspection before approving."
        )

    if row["tier"] == "No subsidy":
        st.info("No drought payout recommended for this farm.")
    else:
        st.write(f"**Recommended payout: Rs {row['recommended_payout']:,.0f}**")
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
tab1, tab2 = st.tabs(["Recommended farms", "Decision summary"])

flagged = scored[scored["tier"] != "No subsidy"].copy()
flagged["decision"] = flagged["plot_id"].map(st.session_state.decisions).fillna("Pending")

with tab1:
    if st.button("Approve all Full-tier farms with high-confidence data"):
        auto = flagged[(flagged["tier"] == "Full") & (~flagged["needs_manual_review"])]
        for pid in auto["plot_id"]:
            st.session_state.decisions[pid] = "Approved"
        st.rerun()
    cols = ["plot_id", "farmer", "village", "tier", "drought_score",
            "recommended_payout", "needs_manual_review", "decision"]
    view = flagged.sort_values("drought_score", ascending=False)[cols]
    st.dataframe(view, width="stretch", hide_index=True)
    st.download_button("Download list (CSV)", view.to_csv(index=False), "recommended_farms.csv", "text/csv")

with tab2:
    decided = flagged["decision"].value_counts()
    approved_total = flagged.loc[flagged["decision"] == "Approved", "recommended_payout"].sum()
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Approved", int(decided.get("Approved", 0)))
    d2.metric("Rejected", int(decided.get("Rejected", 0)))
    d3.metric("Pending review", int(decided.get("Pending", 0)))
    d4.metric("Approved payout", f"Rs {approved_total:,.0f}")
