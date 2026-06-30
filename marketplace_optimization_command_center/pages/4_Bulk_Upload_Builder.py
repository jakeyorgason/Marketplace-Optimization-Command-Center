from __future__ import annotations
import streamlit as st
import pandas as pd
from modules.config import load_clients, get_client_config
from modules.data_loader import latest_performance_sections, DATA_LOADER_VERSION
from modules.rules_engine import generate_actions, RULES_ENGINE_VERSION
from modules.bulk_exporter import export_bulk_prep

st.title("Bulk Upload Builder")
st.caption(f"Rules: {RULES_ENGINE_VERSION} | Loader: {DATA_LOADER_VERSION}")
st.caption("Builds one approved-action workbook across SP, SB, and SD. No per-sheet report source selection.")

clients = load_clients()
if clients.empty:
    st.warning("Add clients first.")
    st.stop()
client = st.selectbox("Client", clients["client_name"].astype(str).tolist())
cfg = get_client_config(client)

with st.expander("Recommendation settings"):
    min_spend = st.number_input("Minimum spend for waste rules", value=20.0, step=5.0)
    min_clicks = st.number_input("Minimum clicks for click-based rules", value=10, step=1)
    show_all = st.checkbox("Show medium/low priority actions too", value=False)
settings = {"min_spend": min_spend, "min_clicks": min_clicks}

sections = latest_performance_sections(client)
action_frames = []
for ad_type, payload in sections.items():
    df = payload["df"]
    if not df.empty:
        actions = generate_actions(df, cfg, settings=settings)
        if not actions.empty:
            actions["ad_type"] = ad_type
            action_frames.append(actions)
all_actions = pd.concat(action_frames, ignore_index=True) if action_frames else pd.DataFrame()
if all_actions.empty:
    st.info("No actions generated from the latest SP/SB/SD performance data.")
    st.stop()

st.subheader("Approve Actions for Bulk Prep")
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
            key=f"bulk_editor_{ad_type}",
            column_config={"approve": st.column_config.CheckboxColumn("Approve")},
        )
        approved = edited[edited["approve"] == True].drop(columns=["approve"], errors="ignore")
        if not approved.empty:
            selected_frames.append(approved)

approved_all = pd.concat(selected_frames, ignore_index=True) if selected_frames else pd.DataFrame()

st.subheader("Export")
st.write(f"Approved actions selected: **{len(approved_all):,}**")
if st.button("Build bulk prep workbook", type="primary", disabled=approved_all.empty):
    path = export_bulk_prep(client, approved_all)
    with open(path, "rb") as f:
        st.download_button("Download bulk prep workbook", f, file_name=path.name)
