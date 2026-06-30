from pathlib import Path
import streamlit as st

st.set_page_config(page_title="Marketplace Optimization Command Center", page_icon="📈", layout="wide")

APP_DIR = Path(__file__).resolve().parent

pages = [
    st.Page(str(APP_DIR / "pages" / "1_Upload_Center.py"), title="Upload Center", icon="📤"),
    st.Page(str(APP_DIR / "pages" / "2_Client_Dashboard.py"), title="Client Dashboard", icon="📊"),
    st.Page(str(APP_DIR / "pages" / "3_Action_Center.py"), title="Action Center", icon="✅"),
    st.Page(str(APP_DIR / "pages" / "4_Bulk_Upload_Builder.py"), title="Bulk Upload Builder", icon="🧾"),
    st.Page(str(APP_DIR / "pages" / "5_Listing_Audit.py"), title="Listing Audit", icon="📝"),
    st.Page(str(APP_DIR / "pages" / "6_Client_Settings.py"), title="Client Settings", icon="⚙️"),
]

pg = st.navigation(pages, expanded=True)
pg.run()
