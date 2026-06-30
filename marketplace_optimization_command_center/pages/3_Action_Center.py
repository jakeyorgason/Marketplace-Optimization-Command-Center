from __future__ import annotations
import streamlit as st
import pandas as pd
from modules.config import load_clients, get_client_config
from modules.data_loader import latest_performance_sections, DATA_LOADER_VERSION
from modules.rules_engine import generate_actions, summarize_actions, RULES_ENGINE_VERSION
from modules.bulk_exporter import export_action_review

st.title("Action Center")
st.caption(f"Rules: {RULES_ENGINE_VERSION} | Loader: {DATA_LOADER_VERSION}")
st.caption("Runs one combined analysis per ad type. No per-sheet report selection needed.")

clients = load_clients()
if clients.empty:
    st.warning("Add clients in Client Settings first.")
    st.stop()
client = st.selectbox("Client", clients["client_name"].astype(str).tolist())
cfg = get_client_config(client)

with st.expander("Rule settings"):
    min_spend = st.number_input("Minimum spend for waste rules", value=20.0, step=5.0)
    min_clicks = st.number_input("Minimum clicks for click-based rules", value=10, step=1)
    show_debug = st.checkbox("Show source/debug info", value=False)
settings = {"min_spend": min_spend, "min_clicks": min_clicks}

sections = latest_performance_sections(client)
action_frames = []
source_rows = []
for ad_type, payload in sections.items():
    df = payload["df"]
    source_rows.extend(payload.get("sources", []))
    if not df.empty:
        actions = generate_actions(df, cfg, settings=settings)
        if not actions.empty:
            actions["ad_type"] = ad_type
            action_frames.append(actions)
all_actions = pd.concat(action_frames, ignore_index=True) if action_frames else pd.DataFrame()

if show_debug:
    with st.expander("Source/debug info", expanded=True):
        if source_rows:
            st.dataframe(pd.DataFrame(source_rows), use_container_width=True, hide_index=True)
        else:
            st.info("No source rows found.")
        for ad_type, payload in sections.items():
            st.write(f"{ad_type}: {len(payload['df']):,} rows from {payload.get('report_type', 'none')}")
        if st.button("Clear Streamlit cache and rerun"):
            st.cache_data.clear()
            st.rerun()

st.subheader("Action Summary")
if all_actions.empty:
    st.success("No actions generated from the latest SP/SB/SD performance data.")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total actions", len(all_actions))
c2.metric("High priority", int((all_actions["priority"] == "High").sum()))
c3.metric("Waste cuts", int((all_actions["category"] == "Waste Cut").sum()))
c4.metric("Scale opportunities", int((all_actions["category"] == "Scale Opportunity").sum()))

summary = all_actions.groupby(["ad_type", "category"], dropna=False).agg(
    actions=("category", "size"),
    estimated_monthly_impact=("estimated_monthly_impact", "sum"),
    spend=("spend", "sum"),
    ad_sales=("ad_sales", "sum"),
).reset_index().sort_values(["ad_type", "actions"], ascending=[True, False])
st.dataframe(summary, use_container_width=True, hide_index=True)

st.subheader("Review Actions by Ad Type")
show_all = st.toggle("Show all actions, including lower-priority diagnosis items", value=False)
selected_frames = []
for ad_type in ["SP", "SB", "SD"]:
    subset = all_actions[all_actions["ad_type"].astype(str).str.upper() == ad_type].copy()
    if not show_all:
        subset = subset[subset["priority"] == "High"].copy()
    with st.expander(f"{ad_type} Actions ({len(subset):,})", expanded=(ad_type == "SP")):
        if subset.empty:
            st.info(f"No {ad_type} actions with the current filters.")
            continue
        edited = st.data_editor(
            subset.assign(approve=True),
            use_container_width=True,
            hide_index=True,
            key=f"editor_{ad_type}",
            column_config={"approve": st.column_config.CheckboxColumn("Approve")},
        )
        approved = edited[edited["approve"] == True].drop(columns=["approve"], errors="ignore")
        if not approved.empty:
            selected_frames.append(approved)

approved_all = pd.concat(selected_frames, ignore_index=True) if selected_frames else pd.DataFrame()
if st.button("Export approved action review", type="primary", disabled=approved_all.empty):
    path = export_action_review(client, approved_all)
    with open(path, "rb") as f:
        st.download_button("Download action review workbook", f, file_name=path.name)
