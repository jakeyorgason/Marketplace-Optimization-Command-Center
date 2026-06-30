from __future__ import annotations
import streamlit as st
from modules.config import load_clients
from modules.data_loader import list_reports, load_processed_report

st.title("Listing Audit")
st.caption("Starter module. Upload listing/PLO files in Upload Center, then review copy gaps here.")
clients = load_clients()
if clients.empty:
    st.warning("Add clients first.")
    st.stop()
client = st.selectbox("Client", clients["client_name"].astype(str).tolist())
reports = list_reports(client, "listing_copy")
if reports.empty:
    st.info("No listing copy report uploaded yet.")
    st.stop()
labels = [f"{r.original_file} — {r.sheet_name}" for _, r in reports.iterrows()]
label = st.selectbox("Listing file", labels)
rec = reports.iloc[labels.index(label)].to_dict()
df = load_processed_report(rec)
st.dataframe(df.head(100), use_container_width=True)
