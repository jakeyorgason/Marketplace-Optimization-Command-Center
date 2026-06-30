from __future__ import annotations
import streamlit as st
import pandas as pd
from modules.config import load_clients, save_clients

st.title("Client Settings")
st.caption("Add/edit client goals. TACOS/ACOS can be entered as 12 or 0.12.")

clients = load_clients()
edited = st.data_editor(
    clients,
    num_rows="dynamic",
    use_container_width=True,
    column_config={
        "client_name": st.column_config.TextColumn("Client", required=True),
        "target_tacos": st.column_config.NumberColumn("Target TACOS", help="Use 12 for 12% or 0.12"),
        "target_acos": st.column_config.NumberColumn("Target ACOS", help="Use 50 for 50% or 0.50"),
        "monthly_budget": st.column_config.NumberColumn("Monthly Budget"),
        "growth_mode": st.column_config.SelectboxColumn("Growth Mode", options=["conservative", "balanced", "aggressive", "scale", "profit"]),
        "forbidden_terms": st.column_config.TextColumn("Forbidden Terms", help="Comma-separated, e.g. Ardor, Ardor Energy"),
    },
)

if st.button("Save client settings", type="primary"):
    edited = edited.dropna(how="all")
    edited = edited[edited["client_name"].astype(str).str.strip() != ""]
    save_clients(edited)
    st.success("Saved client settings.")
    st.rerun()
