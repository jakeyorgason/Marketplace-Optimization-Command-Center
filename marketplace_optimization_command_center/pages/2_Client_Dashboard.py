from __future__ import annotations
import streamlit as st
import pandas as pd
from modules.config import load_clients, get_client_config
from modules.data_loader import list_reports, get_latest_performance_source
from modules.metrics import summarize_business_sales, summarize_performance, health_score
from modules.rules_engine import generate_actions, RULES_ENGINE_VERSION
from modules.ai_audit import placeholder_summary

st.title("Client Dashboard")
st.caption(f"Rules engine: {RULES_ENGINE_VERSION}")
clients = load_clients()
if clients.empty:
    st.warning("Add clients in Client Settings first.")
    st.stop()
client = st.selectbox("Client", clients["client_name"].astype(str).tolist())
cfg = get_client_config(client)

with st.expander("Detected reports"):
    reports = list_reports(client)
    if reports.empty:
        st.info("No reports yet.")
    else:
        st.dataframe(reports[["uploaded_at", "report_type", "original_file", "sheet_name", "rows"]], use_container_width=True, hide_index=True)

source_type, rec, df = get_latest_performance_source(client)
if df.empty:
    st.warning("Upload a Bulk Operations file or Search Term/Targeting report to populate performance.")
    st.stop()

total_sales = summarize_business_sales(client)
metrics = summarize_performance(df, total_sales_override=total_sales)
actions = generate_actions(df, cfg)
score, status, flags = health_score(metrics, cfg, action_count=int((actions["priority"] == "High").sum()) if not actions.empty and "priority" in actions else 0)

st.subheader(f"Status: {status}")
source_label = rec.get("sheet_name", "") if rec else ""
source_file = rec.get("original_file", "") if rec else ""
st.caption(f"Performance source: {source_type} — {source_file} — {source_label} — {len(df):,} combined rows")
with st.expander("Dashboard metric debug"):
    st.write("Data loader source is combined across all matching sheets from the latest upload batch.")
    st.write({
        "source_type": source_type,
        "source_file": source_file,
        "source_sheet": source_label,
        "combined_rows": int(len(df)),
        "spend_sum": float(metrics.get("spend", 0)),
        "ad_sales_sum": float(metrics.get("ad_sales", 0)),
        "business_report_total_sales": float(total_sales),
    })
    if "source_sheet" in df.columns:
        sheet_summary = df.groupby("source_sheet", dropna=False).agg(
            rows=("source_sheet", "size"),
            spend=("spend", "sum"),
            ad_sales=("ad_sales", "sum"),
            clicks=("clicks", "sum"),
            orders=("orders", "sum"),
        ).reset_index().sort_values("spend", ascending=False)
        st.dataframe(sheet_summary, use_container_width=True, hide_index=True)
cols = st.columns(7)
cols[0].metric("Health Score", f"{score}/100")
cols[1].metric("Spend", f"${metrics['spend']:,.0f}")
cols[2].metric("Ad Sales", f"${metrics['ad_sales']:,.0f}")
cols[3].metric("Total Sales", f"${metrics['total_sales']:,.0f}")
cols[4].metric("ACOS", f"{metrics['acos']:.1%}")
cols[5].metric("TACOS", f"{metrics['tacos']:.1%}")
cols[6].metric("Actions", f"{len(actions):,.0f}")

if flags:
    st.warning(" | ".join(flags))
else:
    st.success("No major red flags based on current thresholds.")

st.info(placeholder_summary(client, metrics, len(actions)))

st.subheader("Top Actions")
if actions.empty:
    st.success("No actions generated yet.")
else:
    st.dataframe(actions.head(25), use_container_width=True, hide_index=True)
