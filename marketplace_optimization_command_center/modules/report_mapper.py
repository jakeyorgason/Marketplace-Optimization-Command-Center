from __future__ import annotations
import re
from typing import Any
import pandas as pd

REPORT_MAPPER_VERSION = "2026-06-30-upload-id-preserving-v5"

ALIASES = {
    "product": ["product"],
    "entity": ["entity"],
    "operation": ["operation"],
    "campaign_id": ["campaign id"],
    "ad_group_id": ["ad group id"],
    "keyword_id": ["keyword id"],
    "product_targeting_id": ["product targeting id"],
    "portfolio_id": ["portfolio id"],
    "ad_id": ["ad id"],
    "spend": ["spend", "cost", "ad spend", "advertising cost"],
    "ad_sales": ["sales", "7 day total sales", "14 day total sales", "total sales", "advertised sku sales", "sales 14d"],
    "impressions": ["impressions"],
    "clicks": ["clicks"],
    "orders": ["orders", "7 day total orders", "14 day total orders", "purchases", "total orders"],
    "units": ["units", "units ordered", "7 day total units", "14 day total units"],
    "cpc": ["cpc", "cost per click"],
    "acos": ["acos", "advertising cost of sales (acos)"],
    "roas": ["roas", "return on ad spend (roas)"],
    "ctr": ["ctr", "click-thru rate", "click through rate", "click-through rate"],
    "cvr": ["cvr", "conversion rate", "order conversion rate"],
    "campaign": ["campaign name", "campaign name (informational only)", "campaign"],
    "ad_group": ["ad group name", "ad group name (informational only)", "ad group"],
    "target": ["customer search term", "search term", "keyword text", "target", "targeting", "product targeting expression", "resolved product targeting expression", "resolved product targeting expression (informational only)"],
    "search_term": ["customer search term", "search term", "matched search term"],
    "keyword_text": ["keyword text", "keyword"],
    "match_type": ["match type", "keyword match type"],
    "bid": ["bid", "keyword bid", "targeting expression bid"],
    "budget": ["budget", "daily budget", "campaign daily budget"],
    "daily_budget": ["daily budget", "campaign daily budget"],
    "state": ["state", "campaign state", "status"],
    "campaign_state": ["campaign state", "campaign state (informational only)"],
    "ad_group_state": ["ad group state", "ad group state (informational only)"],
    "sku": ["sku", "seller sku", "advertised sku"],
    "asin": ["asin", "asin (informational only)", "advertised asin", "targeting asin"],
    "product_targeting_expression": ["product targeting expression"],
    "resolved_product_targeting_expression": ["resolved product targeting expression", "resolved product targeting expression (informational only)"],
    "placement_type": ["placement type", "placement"],
    "placement_pct": ["placement %", "percentage"],
    "sessions": ["sessions", "browser sessions", "mobile app sessions", "sessions - total"],
    "total_sales": ["ordered product sales", "ordered product sales - total", "sales", "total sales", "product sales"],
    "total_orders": ["total order items", "orders", "ordered items"],
}

NUMERIC_COLS = [
    "spend", "ad_sales", "impressions", "clicks", "orders", "units", "cpc", "acos", "roas", "ctr", "cvr",
    "bid", "budget", "daily_budget", "sessions", "total_sales", "total_orders", "placement_pct"
]

TEXT_COLS = [
    "product", "entity", "operation", "campaign_id", "ad_group_id", "keyword_id", "product_targeting_id", "portfolio_id",
    "ad_id", "campaign", "ad_group", "target", "search_term", "keyword_text", "match_type", "state", "campaign_state",
    "ad_group_state", "sku", "asin", "product_targeting_expression", "resolved_product_targeting_expression", "placement_type"
]


def clean_col(col: Any) -> str:
    s = str(col).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s.replace("\n", " ").strip()


def raw_col_name(col: Any) -> str:
    s = clean_col(col)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return f"raw__{s}" if s else "raw__blank"


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [clean_col(c) for c in out.columns]
    return out


def find_col(df: pd.DataFrame, aliases: list[str]) -> str | None:
    cols = {clean_col(c): c for c in df.columns}
    for a in aliases:
        ca = clean_col(a)
        if ca in cols:
            return cols[ca]
    for a in aliases:
        ca = clean_col(a)
        for c in df.columns:
            cc = clean_col(c)
            if ca == cc or ca in cc:
                return c
    return None


def to_number(series: pd.Series) -> pd.Series:
    if series is None:
        return pd.Series(dtype="float64")
    cleaned = series.astype(str).str.replace("$", "", regex=False).str.replace(",", "", regex=False).str.replace("%", "", regex=False).str.strip()
    return pd.to_numeric(cleaned, errors="coerce").fillna(0.0)


def _ad_type_from_product(value: Any, sheet_name: str | None = None) -> str:
    text = f"{value or ''} {sheet_name or ''}".lower()
    if "sponsored brands" in text or "sb " in text or text.startswith("sb"):
        return "SB"
    if "sponsored display" in text or "sd " in text or text.startswith("sd"):
        return "SD"
    if "sponsored products" in text or "sp " in text or text.startswith("sp"):
        return "SP"
    return "Unknown"


def standardize_performance_table(df: pd.DataFrame, sheet_name: str | None = None) -> pd.DataFrame:
    base = normalize_columns(df)
    out = pd.DataFrame(index=base.index)

    # Preserve every original Amazon column under raw__ names. This is critical for bulk uploads because
    # Amazon requires IDs such as Campaign ID, Ad Group ID, Keyword ID, and Product Targeting ID.
    seen = set()
    for original_col in base.columns:
        raw_name = raw_col_name(original_col)
        if raw_name in seen:
            suffix = 2
            while f"{raw_name}_{suffix}" in seen:
                suffix += 1
            raw_name = f"{raw_name}_{suffix}"
        seen.add(raw_name)
        out[raw_name] = base[original_col]

    for key, aliases in ALIASES.items():
        col = find_col(base, aliases)
        if col is not None:
            out[key] = base[col]

    for col in NUMERIC_COLS:
        if col in out.columns:
            out[col] = to_number(out[col])
    for col in TEXT_COLS:
        if col in out.columns:
            out[col] = out[col].astype(str).replace({"nan": "", "None": ""}).fillna("").str.strip()

    if "product" in out.columns:
        out["ad_type"] = out["product"].apply(lambda x: _ad_type_from_product(x, sheet_name))
    else:
        out["ad_type"] = _ad_type_from_product("", sheet_name)

    # derive metrics
    if "spend" in out and "clicks" in out and "cpc" not in out:
        out["cpc"] = out["spend"] / out["clicks"].replace(0, pd.NA)
    if "spend" in out and "ad_sales" in out:
        if "acos" not in out:
            out["acos"] = out["spend"] / out["ad_sales"].replace(0, pd.NA)
        else:
            out["acos"] = out["acos"].apply(lambda x: x / 100 if pd.notna(x) and x > 3 else x)
        if "roas" not in out:
            out["roas"] = out["ad_sales"] / out["spend"].replace(0, pd.NA)
    if "clicks" in out and "impressions" in out:
        if "ctr" not in out:
            out["ctr"] = out["clicks"] / out["impressions"].replace(0, pd.NA)
        else:
            out["ctr"] = out["ctr"].apply(lambda x: x / 100 if pd.notna(x) and x > 3 else x)
    if "orders" in out and "clicks" in out:
        if "cvr" not in out:
            out["cvr"] = out["orders"] / out["clicks"].replace(0, pd.NA)
        else:
            out["cvr"] = out["cvr"].apply(lambda x: x / 100 if pd.notna(x) and x > 3 else x)
    return out.fillna("")


def detect_report_type(file_name: str, sheet_name: str | None, df: pd.DataFrame) -> str:
    name = f"{file_name} {sheet_name or ''}".lower()
    cols = set(normalize_columns(df).columns)
    joined = " ".join(cols)
    if "search term" in name or "customer search term" in cols or "search term" in cols:
        return "search_term_report"
    if "business" in name or ("sessions" in joined and "ordered product sales" in joined):
        return "business_report"
    if "campaign" in name and ("daily budget" in cols or "campaign name" in cols or "entity" in cols):
        # Bulk Operations campaign tabs are both campaign data and the best source for IDs/templates.
        return "campaign_report"
    if "target" in name or "targeting" in cols or "keyword text" in cols:
        return "targeting_report"
    if "bulk" in name or "operation" in name:
        return "bulk_operations_template"
    if "inventory" in name:
        return "inventory_report"
    if "profit" in name or "matrix" in name:
        return "profit_matrix"
    if "listing" in name or "plo" in name:
        return "listing_copy"
    return "unknown_report"


def report_priority_key(report_type: str) -> int:
    return {
        "search_term_report": 0,
        "targeting_report": 1,
        "campaign_report": 2,
        "business_report": 3,
        "bulk_operations_template": 4,
    }.get(report_type, 99)
