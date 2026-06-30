from __future__ import annotations
from pathlib import Path
from datetime import datetime
import pandas as pd
from .config import EXPORTS_DIR
from .data_loader import slugify

BULK_EXPORTER_VERSION = "2026-06-30-clean-exporter-v1"

def export_action_review(client_name: str, actions: pd.DataFrame) -> Path:
    out_dir = EXPORTS_DIR / slugify(client_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{slugify(client_name)}_action_review_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        actions.to_excel(writer, index=False, sheet_name="Approved_Actions")
    return path

def export_bulk_prep(client_name: str, actions: pd.DataFrame) -> Path:
    out_dir = EXPORTS_DIR / slugify(client_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{slugify(client_name)}_bulk_prep_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
    bid_actions = actions[actions.get("suggested_bid", 0).astype(float) > 0].copy() if not actions.empty and "suggested_bid" in actions.columns else pd.DataFrame()
    negatives = actions[actions.get("reason_code", "").astype(str).isin(["high_click_no_order", "brand_safety_grouped"])].copy() if not actions.empty and "reason_code" in actions.columns else pd.DataFrame()
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        bid_actions.to_excel(writer, index=False, sheet_name="Bid_Update_Review")
        negatives.to_excel(writer, index=False, sheet_name="Negative_Review")
        actions.to_excel(writer, index=False, sheet_name="All_Approved_Actions")
    return path
