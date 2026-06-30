from __future__ import annotations
import inspect
import streamlit as st
import pandas as pd
from modules.config import load_clients, get_client_config
from modules.data_loader import list_reports, load_processed_report, get_latest_performance_source
import modules.rules_engine as rules_engine
from modules.rules_engine import generate_actions, summarize_actions, RULES_ENGINE_VERSION
from modules.bulk_exporter import export_action_review

st.title("Action Center")
st.caption(f"Rules engine version: {RULES_ENGINE_VERSION}")

clients = load_clients()
if clients.empty:
    st.warning("Add clients in Client Settings first.")
    st.stop()
client = st.selectbox("Client", clients["client_name"].astype(str).tolist())
cfg = get_client_config(client)
reports = list_reports(client)
if reports.empty:
    st.warning("Upload a performance report first.")
    st.stop()

perf_reports = reports[reports["report_type"].isin(["search_term_report", "targeting_report", "campaign_report", "bulk_operations_template"])]
if perf_reports.empty:
    st.warning("No performance reports found. Upload a Bulk Operations or Search Term report.")
    st.stop()

labels = [f"{r.report_type} — {r.original_file} — {r.sheet_name}" for _, r in perf_reports.iterrows()]
selected_label = st.selectbox("Performance report", labels)
selected_idx = labels.index(selected_label)
rec = perf_reports.iloc[selected_idx].to_dict()
df = load_processed_report(rec)

with st.expander("Rule settings"):
    min_spend = st.number_input("Minimum spend for waste rules", value=20.0, step=5.0)
    min_clicks = st.number_input("Minimum clicks for click-based rules", value=10, step=1)
    show_debug = st.checkbox("Show debug info", value=False)
settings = {"min_spend": min_spend, "min_clicks": min_clicks}

actions = generate_actions(df, cfg, settings=settings)
summary = summarize_actions(actions)

if show_debug:
    with st.expander("Debug: imported files and brand grouping", expanded=True):
        st.write("Imported rules engine file:")
        st.code(getattr(rules_engine, "__file__", "unknown"))
        st.write("generate_actions defined at:")
        st.code(inspect.getsourcefile(generate_actions) or "unknown")
        st.write("Selected source shape:")
        st.code(f"{df.shape[0]:,} rows x {df.shape[1]:,} columns")
        st.write("Brand Safety action count:")
        brand_count = int((actions.get("category", pd.Series(dtype=str)) == "Brand Safety").sum()) if not actions.empty else 0
        st.code(str(brand_count))
        if brand_count:
            st.dataframe(actions[actions["category"] == "Brand Safety"][["target", "spend", "ad_sales", "clicks", "orders", "priority", "evidence"]].head(50), use_container_width=True, hide_index=True)
        if st.button("Clear Streamlit cache and rerun"):
            st.cache_data.clear()
            st.rerun()

st.subheader("Action Summary")
if actions.empty:
    st.success("No actions generated from this report.")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total actions", len(actions))
c2.metric("High priority", int((actions["priority"] == "High").sum()))
c3.metric("Waste cuts", int((actions["category"] == "Waste Cut").sum()))
c4.metric("Scale opportunities", int((actions["category"] == "Scale Opportunity").sum()))
st.dataframe(summary, use_container_width=True, hide_index=True)

st.subheader("Review Actions")
show_all = st.toggle("Show all actions, including lower-priority diagnosis items", value=False)
review = actions if show_all else actions[actions["priority"] == "High"].copy()

edited = st.data_editor(
    review.assign(approve=True),
    use_container_width=True,
    hide_index=True,
    column_config={"approve": st.column_config.CheckboxColumn("Approve")},
)
approved = edited[edited["approve"] == True].drop(columns=["approve"], errors="ignore")

if st.button("Export approved action review", type="primary", disabled=approved.empty):
    path = export_action_review(client, approved)
    with open(path, "rb") as f:
        st.download_button("Download action review workbook", f, file_name=path.name)
