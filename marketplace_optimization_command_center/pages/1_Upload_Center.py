from __future__ import annotations
import streamlit as st
from modules.config import load_clients
from modules.data_loader import save_uploaded_file, list_reports, DATA_LOADER_VERSION
from modules.report_mapper import REPORT_MAPPER_VERSION

st.title("Upload Center")
st.caption(f"Loader: {DATA_LOADER_VERSION} | Mapper: {REPORT_MAPPER_VERSION}")
clients = load_clients()
if clients.empty:
    st.warning("Add at least one client in Client Settings first.")
    st.stop()

client = st.selectbox("Client", clients["client_name"].astype(str).tolist())
uploads = st.file_uploader("Upload Amazon reports", type=["xlsx", "xlsm", "xls", "csv", "txt"], accept_multiple_files=True)

if uploads and st.button("Process uploads", type="primary"):
    all_records = []
    for uf in uploads:
        with st.spinner(f"Processing {uf.name}..."):
            all_records.extend(save_uploaded_file(client, uf))
    st.success(f"Processed {len(all_records)} report tab(s).")
    st.dataframe(all_records, use_container_width=True)

st.subheader("Detected reports")
reports = list_reports(client)
if reports.empty:
    st.info("No reports uploaded yet for this client.")
else:
    show_cols = [c for c in ["uploaded_at", "report_type", "original_file", "sheet_name", "rows", "columns"] if c in reports.columns]
    st.dataframe(reports[show_cols], use_container_width=True, hide_index=True)
