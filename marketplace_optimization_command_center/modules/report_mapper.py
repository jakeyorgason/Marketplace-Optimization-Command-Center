from __future__ import annotations
import re
from typing import Any
import pandas as pd

REPORT_MAPPER_VERSION = "2026-06-30-clean-mapper-v1"

ALIASES = {
    "spend": ["spend", "cost", "ad spend", "advertising cost"],
    "ad_sales": ["sales", "7 day total sales", "14 day total sales", "total sales", "advertised sku sales", "sales 14d"],
    "impressions": ["impressions"],
    "clicks": ["clicks"],
    "orders": ["orders", "7 day total orders", "14 day total orders", "purchases", "total orders"],
    "units": ["units", "units ordered", "7 day total units", "14 day total units"],
    "cpc": ["cpc", "cost per click"],
    "acos": ["acos", "advertising cost of sales (acos)"],
    "roas": ["roas", "return on ad spend (roas)"],
    "ctr": ["ctr", "click-thru rate", "click through rate"],
    "cvr": ["cvr", "conversion rate", "order conversion rate"],
    "campaign": ["campaign", "campaign name", "campaign name (informational only)"],
    "ad_group": ["ad group", "ad group name", "ad group name (informational only)"],
    "target": ["target", "keyword", "keyword text", "targeting", "product targeting expression", "resolved product targeting expression"],
    "search_term": ["customer search term", "search term", "matched search term"],
    "match_type": ["match type", "keyword match type"],
    "bid": ["bid", "keyword bid", "targeting expression bid"],
    "budget": ["budget", "daily budget", "campaign daily budget"],
    "sku": ["sku", "seller sku", "advertised sku"],
    "asin": ["asin", "advertised asin", "targeting asin"],
    "sessions": ["sessions", "browser sessions", "mobile app sessions", "sessions - total"],
    "total_sales": ["ordered product sales", "ordered product sales - total", "sales", "total sales", "product sales"],
    "total_orders": ["total order items", "orders", "ordered items"],
}

TARGET_FIELDS = ["search_term", "target", "asin"]

def clean_col(col: Any) -> str:
    s = str(col).strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = s.replace("\n", " ").strip()
    return s

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

def standardize_performance_table(df: pd.DataFrame) -> pd.DataFrame:
    base = normalize_columns(df)
    out = pd.DataFrame(index=base.index)
    for key, aliases in ALIASES.items():
        col = find_col(base, aliases)
        if col is not None:
            out[key] = base[col]
    for col in ["spend", "ad_sales", "impressions", "clicks", "orders", "units", "cpc", "acos", "roas", "ctr", "cvr", "bid", "budget", "sessions", "total_sales", "total_orders"]:
        if col in out.columns:
            out[col] = to_number(out[col])
    # derive metrics
    if "spend" in out and "clicks" in out and "cpc" not in out:
        out["cpc"] = out["spend"] / out["clicks"].replace(0, pd.NA)
    if "spend" in out and "ad_sales" in out:
        if "acos" not in out:
            out["acos"] = out["spend"] / out["ad_sales"].replace(0, pd.NA)
        else:
            # Amazon sometimes exports ACOS as 50.5 rather than 0.505.
            out["acos"] = out["acos"].apply(lambda x: x / 100 if x and x > 3 else x)
        if "roas" not in out:
            out["roas"] = out["ad_sales"] / out["spend"].replace(0, pd.NA)
    if "clicks" in out and "impressions" in out:
        if "ctr" not in out:
            out["ctr"] = out["clicks"] / out["impressions"].replace(0, pd.NA)
        else:
            out["ctr"] = out["ctr"].apply(lambda x: x / 100 if x and x > 3 else x)
    if "orders" in out and "clicks" in out:
        if "cvr" not in out:
            out["cvr"] = out["orders"] / out["clicks"].replace(0, pd.NA)
        else:
            out["cvr"] = out["cvr"].apply(lambda x: x / 100 if x and x > 3 else x)
    for text_col in ["campaign", "ad_group", "target", "search_term", "match_type", "sku", "asin"]:
        if text_col in out.columns:
            out[text_col] = out[text_col].astype(str).fillna("").str.strip()
    return out.fillna(0)

def detect_report_type(file_name: str, sheet_name: str | None, df: pd.DataFrame) -> str:
    name = f"{file_name} {sheet_name or ''}".lower()
    cols = set(normalize_columns(df).columns)
    if "search term" in name or "customer search term" in cols or "search term" in cols:
        return "search_term_report"
    if "business" in name or ("sessions" in " ".join(cols) and "ordered product sales" in " ".join(cols)):
        return "business_report"
    if "target" in name or "targeting" in cols or "keyword text" in cols:
        return "targeting_report"
    if "campaign" in name and ("budget" in cols or "campaign daily budget" in cols):
        return "campaign_report"
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
