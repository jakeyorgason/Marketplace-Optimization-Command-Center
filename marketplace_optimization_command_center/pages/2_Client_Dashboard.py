from __future__ import annotations
import streamlit as st
import pandas as pd
from modules.config import load_clients, get_client_config
from modules.data_loader import list_reports, latest_performance_sections, combined_latest_performance, DATA_LOADER_VERSION
from modules.metrics import summarize_business_sales, summarize_performance, health_score, METRICS_VERSION
from modules.rules_engine import generate_actions, RULES_ENGINE_VERSION
from modules.ai_audit import placeholder_summary

st.title("Client Dashboard")
st.caption(f"Loader: {DATA_LOADER_VERSION} | Metrics: {METRICS_VERSION} | Rules: {RULES_ENGINE_VERSION}")

clients = load_clients()
if clients.empty:
    st.warning("Add clients in Client Settings first.")
    st.stop()
client = st.selectbox("Client", clients["client_name"].astype(str).tolist())
cfg = get_client_config(client)
reports = list_reports(client)

with st.expander("Detected reports"):
    if reports.empty:
        st.info("No reports uploaded yet.")
    else:
        cols = [c for c in ["uploaded_at", "report_type", "ad_type", "original_file", "sheet_name", "rows"] if c in reports.columns]
        st.dataframe(reports[cols], use_container_width=True, hide_index=True)

sections, combined = combined_latest_performance(client)
if combined.empty:
    st.warning("Upload a Bulk Operations file or performance reports to populate the dashboard.")
    st.stop()

total_sales = summarize_business_sales(client)
metrics = summarize_performance(combined, total_sales_override=total_sales)
actions = []
for ad_type, payload in sections.items():
    df = payload["df"]
    if not df.empty:
        a = generate_actions(df, cfg)
        if not a.empty:
            a["ad_type"] = ad_type
            actions.append(a)
actions_df = pd.concat(actions, ignore_index=True) if actions else pd.DataFrame()
score, status, flags = health_score(metrics, cfg, action_count=len(actions_df))

st.subheader(f"Status: {status}")
c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
c1.metric("Health Score", f"{score}/100")
c2.metric("Spend", f"${metrics['spend']:,.0f}")
c3.metric("Ad Sales", f"${metrics['ad_sales']:,.0f}")
c4.metric("Total Sales", f"${metrics['total_sales']:,.0f}")
c5.metric("ACOS", f"{metrics['acos']:.1%}")
c6.metric("TACOS", f"{metrics['tacos']:.1%}")
c7.metric("Actions", f"{len(actions_df):,}")

if flags:
    st.warning(" | ".join(flags))
else:
    st.success("No major dashboard flags from the current data.")

st.subheader("Performance by Ad Type")
rows = []
for ad_type, payload in sections.items():
    df = payload["df"]
    m = summarize_performance(df, total_sales_override=0)
    rows.append({
        "Ad Type": ad_type,
        "Report Used": payload.get("report_type", "none"),
        "Sources": len(payload.get("sources", [])),
        "Rows": len(df),
        "Spend": m["spend"],
        "Ad Sales": m["ad_sales"],
        "ACOS": m["acos"],
        "Clicks": m["clicks"],
        "Orders": m["orders"],
        "CVR": m["cvr"],
    })
summary_df = pd.DataFrame(rows)
st.dataframe(summary_df, use_container_width=True, hide_index=True, column_config={
    "Spend": st.column_config.NumberColumn(format="$%.2f"),
    "Ad Sales": st.column_config.NumberColumn(format="$%.2f"),
    "ACOS": st.column_config.NumberColumn(format="%.1%%"),
    "CVR": st.column_config.NumberColumn(format="%.1%%"),
})

st.subheader("AI-Style Account Summary")
st.info(placeholder_summary(client, metrics, len(actions_df)))

st.subheader("Top Actions")
if actions_df.empty:
    st.success("No actions generated from the current performance data.")
else:
    cols = [c for c in ["ad_type", "priority", "category", "issue", "recommendation", "campaign", "ad_group", "target", "spend", "ad_sales", "evidence"] if c in actions_df.columns]
    st.dataframe(actions_df[cols].head(25), use_container_width=True, hide_index=True)

with st.expander("Dashboard metric debug"):
    source_rows = []
    for ad_type, payload in sections.items():
        source_rows.extend(payload.get("sources", []))
    if source_rows:
        st.dataframe(pd.DataFrame(source_rows), use_container_width=True, hide_index=True)
    else:
        st.info("No source rows found.")
    st.write("Combined shape:", combined.shape)
