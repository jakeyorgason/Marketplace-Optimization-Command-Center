from __future__ import annotations

from pathlib import Path
from datetime import datetime
import re
import zipfile
from io import BytesIO
import pandas as pd

try:
    from openpyxl import load_workbook
except Exception:  # pragma: no cover
    load_workbook = None

try:
    from modules.config import EXPORTS_DIR, APP_DIR
    from modules.data_loader import slugify
except Exception:
    from .config import EXPORTS_DIR, APP_DIR
    from .data_loader import slugify

BULK_EXPORTER_VERSION = "2026-06-30-brand-safety-optional-v3"

TEMPLATE_PATH = APP_DIR / "templates" / "PxP_SP_Bulk_Upload_Jun25_2026_FIXED_v2.xlsx"
DEFAULT_COLUMNS = [
    "Product", "Entity", "Operation", "Campaign ID", "Ad Group ID", "Keyword ID", "Product Targeting ID",
    "Campaign Name", "Ad Group Name", "State", "Keyword Text", "Match Type", "Bid", "Budget", "Daily Budget",
    "Placement Type", "Placement %", "Portfolio ID", "Product Targeting Expression",
]
PRODUCT_BY_AD_TYPE = {
    "SP": "Sponsored Products",
    "SB": "Sponsored Brands",
    "SD": "Sponsored Display",
}


def _out_dir(client_name: str) -> Path:
    out = EXPORTS_DIR / slugify(client_name)
    out.mkdir(parents=True, exist_ok=True)
    return out


def _safe_text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def _safe_float(value, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        text = str(value).replace("$", "").replace(",", "").strip()
        if text == "":
            return default
        return float(text)
    except Exception:
        return default


def _raw(row: pd.Series, *names: str) -> str:
    for name in names:
        candidates = [
            name,
            "raw__" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_"),
            re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_"),
        ]
        for c in candidates:
            if c in row.index:
                val = _safe_text(row.get(c))
                if val and val.lower() not in {"nan", "none", "0.0"}:
                    return val
    return ""


def _template_headers() -> list[str]:
    if TEMPLATE_PATH.exists() and load_workbook:
        try:
            wb = load_workbook(TEMPLATE_PATH, read_only=True, data_only=False)
            ws = wb["Output"] if "Output" in wb.sheetnames else wb[wb.sheetnames[0]]
            headers = [_safe_text(cell.value) for cell in next(ws.iter_rows(min_row=1, max_row=1))]
            headers = [h for h in headers if h]
            if headers:
                return headers
        except Exception:
            pass
    return DEFAULT_COLUMNS.copy()


def _target_for_negative(row: pd.Series) -> str:
    return _safe_text(row.get("target")) or _raw(row, "customer search term", "search term", "keyword text", "targeting")


def _is_product_target(row: pd.Series) -> bool:
    target = _safe_text(row.get("target")).lower()
    expr = _raw(row, "product targeting expression", "resolved product targeting expression")
    ptid = _raw(row, "product targeting id")
    asinish = bool(re.search(r"\bB0[A-Z0-9]{8}\b", target.upper())) or target.startswith("asin") or "asin=" in target
    return bool(expr or ptid or asinish)


def _bid_update_row(row: pd.Series, ad_type: str) -> dict:
    product = PRODUCT_BY_AD_TYPE.get(ad_type.upper(), PRODUCT_BY_AD_TYPE.get(_safe_text(row.get("ad_type")).upper(), "Sponsored Products"))
    suggested_bid = _safe_float(row.get("suggested_bid"), 0)
    is_pt = _is_product_target(row)
    entity = _raw(row, "entity") or ("Product Targeting" if is_pt else "Keyword")
    keyword_text = "" if is_pt else (_raw(row, "keyword text") or _safe_text(row.get("target")))
    product_expr = _raw(row, "product targeting expression", "resolved product targeting expression") if is_pt else ""
    if is_pt and not product_expr:
        tgt = _safe_text(row.get("target"))
        product_expr = tgt if tgt.lower().startswith("asin") or "=" in tgt else f'asin="{tgt}"' if re.search(r"^B0[A-Z0-9]{8}$", tgt.upper()) else tgt
    return {
        "Product": product,
        "Entity": entity,
        "Operation": "Update",
        "Campaign ID": _raw(row, "campaign id"),
        "Ad Group ID": _raw(row, "ad group id"),
        "Keyword ID": "" if is_pt else _raw(row, "keyword id"),
        "Product Targeting ID": _raw(row, "product targeting id") if is_pt else "",
        "Campaign Name": _raw(row, "campaign name") or _safe_text(row.get("campaign")),
        "Ad Group Name": _raw(row, "ad group name") or _safe_text(row.get("ad_group")),
        "State": _raw(row, "state") or "Enabled",
        "Keyword Text": keyword_text,
        "Match Type": _raw(row, "match type") or "exact",
        "Bid": suggested_bid if suggested_bid > 0 else "",
        "Budget": "",
        "Daily Budget": "",
        "Placement Type": "",
        "Placement %": "",
        "Portfolio ID": _raw(row, "portfolio id"),
        "Product Targeting Expression": product_expr,
    }


def _negative_keyword_row(row: pd.Series, ad_type: str) -> dict:
    product = PRODUCT_BY_AD_TYPE.get(ad_type.upper(), PRODUCT_BY_AD_TYPE.get(_safe_text(row.get("ad_type")).upper(), "Sponsored Products"))
    term = _target_for_negative(row)
    return {
        "Product": product,
        "Entity": "Negative Keyword",
        "Operation": "Create",
        "Campaign ID": _raw(row, "campaign id"),
        "Ad Group ID": _raw(row, "ad group id"),
        "Keyword ID": "",
        "Product Targeting ID": "",
        "Campaign Name": _raw(row, "campaign name") or _safe_text(row.get("campaign")),
        "Ad Group Name": _raw(row, "ad group name") or _safe_text(row.get("ad_group")),
        "State": "Enabled",
        "Keyword Text": term,
        "Match Type": "negative exact",
        "Bid": "",
        "Budget": "",
        "Daily Budget": "",
        "Placement Type": "",
        "Placement %": "",
        "Portfolio ID": _raw(row, "portfolio id"),
        "Product Targeting Expression": "",
    }


def _campaign_budget_row(row: pd.Series, ad_type: str) -> dict:
    product = PRODUCT_BY_AD_TYPE.get(ad_type.upper(), "Sponsored Products")
    budget = _safe_float(row.get("suggested_budget"), 0) or _safe_float(row.get("daily_budget"), 0)
    return {
        "Product": product,
        "Entity": "Campaign",
        "Operation": "Update",
        "Campaign ID": _raw(row, "campaign id"),
        "Ad Group ID": "",
        "Keyword ID": "",
        "Product Targeting ID": "",
        "Campaign Name": _raw(row, "campaign name") or _safe_text(row.get("campaign")),
        "Ad Group Name": "",
        "State": _raw(row, "state") or "Enabled",
        "Keyword Text": "",
        "Match Type": "",
        "Bid": "",
        "Budget": "",
        "Daily Budget": budget if budget > 0 else "",
        "Placement Type": "",
        "Placement %": "",
        "Portfolio ID": _raw(row, "portfolio id"),
        "Product Targeting Expression": "",
    }


def _truthy(value) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y", "include"}


def _brand_safety_uploadable(row: pd.Series) -> bool:
    # Grouped brand-safety rows are useful for review, but risky as upload rows when they span multiple campaigns.
    # Only allow them into a bulk upload when the UI explicitly marks them and they resolve to one campaign/ad group.
    if not _truthy(row.get("include_in_bulk_upload", False)):
        return False
    multi = str(row.get("is_multi_campaign_brand_safety", "")).strip().lower()
    if multi in {"true", "1", "yes"}:
        return False
    campaign = _safe_text(row.get("campaign"))
    ad_group = _safe_text(row.get("ad_group"))
    if re.match(r"^\d+ campaigns$", campaign.lower()) or re.match(r"^\d+ ad groups$", ad_group.lower()):
        return False
    return bool(campaign and ad_group)


def build_bulk_rows(actions: pd.DataFrame, ad_type: str) -> pd.DataFrame:
    headers = _template_headers()
    rows: list[dict] = []
    if actions is None or actions.empty:
        return pd.DataFrame(columns=headers)
    for _, row in actions.iterrows():
        reason = _safe_text(row.get("reason_code"))
        category = _safe_text(row.get("category"))
        suggested_bid = _safe_float(row.get("suggested_bid"), 0)
        if reason == "high_click_no_order":
            rows.append(_negative_keyword_row(row, ad_type))
        elif reason == "brand_safety_grouped":
            if _brand_safety_uploadable(row):
                rows.append(_negative_keyword_row(row, ad_type))
        if suggested_bid > 0 and reason not in {"brand_safety_grouped"}:
            rows.append(_bid_update_row(row, ad_type))
        if category.lower().startswith("budget"):
            rows.append(_campaign_budget_row(row, ad_type))
    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=headers)
    # Deduplicate the exact same upload row.
    out = out.drop_duplicates()
    for h in headers:
        if h not in out.columns:
            out[h] = ""
    return out[headers]


def _write_template_output(path: Path, rows: pd.DataFrame) -> None:
    if load_workbook and TEMPLATE_PATH.exists():
        wb = load_workbook(TEMPLATE_PATH)
        ws = wb["Output"] if "Output" in wb.sheetnames else wb[wb.sheetnames[0]]
        # Clear old data rows while preserving header/style.
        if ws.max_row > 1:
            ws.delete_rows(2, ws.max_row - 1)
        headers = [_safe_text(ws.cell(row=1, column=c).value) for c in range(1, ws.max_column + 1)]
        headers = [h for h in headers if h]
        if rows.empty:
            pass
        else:
            for r_idx, record in enumerate(rows.to_dict(orient="records"), start=2):
                for c_idx, header in enumerate(headers, start=1):
                    val = record.get(header, "")
                    ws.cell(row=r_idx, column=c_idx).value = val
        wb.save(path)
    else:
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            rows.to_excel(writer, index=False, sheet_name="Output")


def export_action_review(client_name: str, actions: pd.DataFrame) -> Path:
    path = _out_dir(client_name) / f"{slugify(client_name)}_action_review_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        actions.to_excel(writer, index=False, sheet_name="Approved_Actions")
    return path


def export_bulk_for_ad_type(client_name: str, ad_type: str, actions: pd.DataFrame) -> Path:
    ad_type = ad_type.upper()
    rows = build_bulk_rows(actions, ad_type)
    path = _out_dir(client_name) / f"{slugify(client_name)}_{ad_type}_bulk_upload_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
    _write_template_output(path, rows)
    return path


def export_bulk_review_workbook(client_name: str, actions_by_ad_type: dict[str, pd.DataFrame], audit: dict | None = None) -> Path:
    path = _out_dir(client_name) / f"{slugify(client_name)}_bulk_audit_review_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        if audit:
            pd.DataFrame([{"summary": audit.get("summary", ""), "mode": audit.get("mode", "")}]).to_excel(writer, index=False, sheet_name="AI_Audit")
            warnings = pd.DataFrame({"warnings": audit.get("warnings", [])})
            warnings.to_excel(writer, index=False, sheet_name="Warnings")
        for ad_type, actions in actions_by_ad_type.items():
            sheet = f"{ad_type}_Approved_Actions"
            (actions if actions is not None else pd.DataFrame()).to_excel(writer, index=False, sheet_name=sheet[:31])
            upload_rows = build_bulk_rows(actions, ad_type)
            upload_rows.to_excel(writer, index=False, sheet_name=f"{ad_type}_Upload_Preview"[:31])
    return path


def zip_bulk_files(paths: list[Path], client_name: str) -> Path:
    zip_path = _out_dir(client_name) / f"{slugify(client_name)}_split_bulk_uploads_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for path in paths:
            if path and Path(path).exists():
                z.write(path, arcname=Path(path).name)
    return zip_path


def export_bulk_prep(client_name: str, actions: pd.DataFrame) -> Path:
    """Legacy compatibility: creates a review workbook, not an upload file."""
    grouped = {}
    if actions is not None and not actions.empty and "ad_type" in actions.columns:
        for ad_type, df in actions.groupby(actions["ad_type"].astype(str).str.upper()):
            grouped[ad_type] = df.copy()
    else:
        grouped["SP"] = actions
    return export_bulk_review_workbook(client_name, grouped)
