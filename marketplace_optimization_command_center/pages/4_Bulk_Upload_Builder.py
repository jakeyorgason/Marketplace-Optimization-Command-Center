from __future__ import annotations
import streamlit as st
import pandas as pd
from modules.config import load_clients, get_client_config
from modules.data_loader import list_reports, load_processed_report
from modules.rules_engine import generate_actions
from modules.bulk_exporter import export_bulk_prep

st.title("Bulk Upload Builder")
st.caption("Builds a review workbook from approved actions. Full Amazon template matching comes next.")
clients = load_clients()
if clients.empty:
    st.warning("Add clients first.")
    st.stop()
client = st.selectbox("Client", clients["client_name"].astype(str).tolist())
cfg = get_client_config(client)
reports = list_reports(client)
perf = reports[reports["report_type"].isin(["search_term_report", "targeting_report", "campaign_report", "bulk_operations_template"])] if not reports.empty else pd.DataFrame()
if perf.empty:
    st.warning("Upload a performance report first.")
    st.stop()
labels = [f"{r.report_type} — {r.original_file} — {r.sheet_name}" for _, r in perf.iterrows()]
label = st.selectbox("Recommendation source", labels)
rec = perf.iloc[labels.index(label)].to_dict()
df = load_processed_report(rec)
actions = generate_actions(df, cfg)
if actions.empty:
    st.info("No actions generated from this report.")
    st.stop()
review = actions[actions["priority"] == "High"].copy()
edited = st.data_editor(review.assign(approve=True), use_container_width=True, hide_index=True, column_config={"approve": st.column_config.CheckboxColumn("Approve")})
approved = edited[edited["approve"] == True].drop(columns=["approve"], errors="ignore")
if st.button("Build bulk prep workbook", type="primary", disabled=approved.empty):
    path = export_bulk_prep(client, approved)
    with open(path, "rb") as f:
        st.download_button("Download bulk prep workbook", f, file_name=path.name)
