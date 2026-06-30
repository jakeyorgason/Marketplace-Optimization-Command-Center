from __future__ import annotations
import math
import pandas as pd
from .data_loader import latest_report, get_latest_performance_source

METRICS_VERSION = "2026-06-30-clean-metrics-v1"

def _num(value, default=0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default

def _pct_from_config(value, default=0.0) -> float:
    n = _num(value, default)
    return n / 100 if n > 1 else n

def summarize_business_sales(client_name: str) -> float:
    rec, df = latest_report(client_name, "business_report")
    if df.empty:
        return 0.0
    if "total_sales" in df.columns:
        return float(pd.to_numeric(df["total_sales"], errors="coerce").fillna(0).sum())
    if "ad_sales" in df.columns:
        return float(pd.to_numeric(df["ad_sales"], errors="coerce").fillna(0).sum())
    return 0.0

def summarize_performance(df: pd.DataFrame, total_sales_override: float = 0.0) -> dict:
    if df is None or df.empty:
        return {"spend": 0, "ad_sales": 0, "total_sales": total_sales_override, "acos": 0, "tacos": 0, "clicks": 0, "orders": 0, "impressions": 0, "ctr": 0, "cvr": 0, "cpc": 0}
    spend = float(pd.to_numeric(df.get("spend", 0), errors="coerce").fillna(0).sum()) if "spend" in df else 0.0
    ad_sales = float(pd.to_numeric(df.get("ad_sales", 0), errors="coerce").fillna(0).sum()) if "ad_sales" in df else 0.0
    clicks = float(pd.to_numeric(df.get("clicks", 0), errors="coerce").fillna(0).sum()) if "clicks" in df else 0.0
    orders = float(pd.to_numeric(df.get("orders", 0), errors="coerce").fillna(0).sum()) if "orders" in df else 0.0
    impressions = float(pd.to_numeric(df.get("impressions", 0), errors="coerce").fillna(0).sum()) if "impressions" in df else 0.0
    total_sales = total_sales_override if total_sales_override and total_sales_override > 0 else ad_sales
    return {
        "spend": spend,
        "ad_sales": ad_sales,
        "total_sales": float(total_sales),
        "acos": spend / ad_sales if ad_sales else 0,
        "tacos": spend / total_sales if total_sales else 0,
        "clicks": clicks,
        "orders": orders,
        "impressions": impressions,
        "ctr": clicks / impressions if impressions else 0,
        "cvr": orders / clicks if clicks else 0,
        "cpc": spend / clicks if clicks else 0,
    }

def health_score(metrics: dict, client_config: dict, action_count: int = 0) -> tuple[int, str, list[str]]:
    target_tacos = _pct_from_config(client_config.get("target_tacos", 0.12), 0.12)
    target_acos = _pct_from_config(client_config.get("target_acos", 0.5), 0.5)
    monthly_budget = _num(client_config.get("monthly_budget", 0), 0)
    score = 100
    flags = []
    if target_tacos and metrics.get("tacos", 0) > target_tacos * 1.15:
        score -= 25; flags.append(f"TACOS is {metrics['tacos']:.1%} vs {target_tacos:.1%} target")
    if target_acos and metrics.get("acos", 0) > target_acos * 1.15:
        score -= 15; flags.append(f"ACOS is {metrics['acos']:.1%} vs {target_acos:.1%} target")
    if monthly_budget and metrics.get("spend", 0) > monthly_budget:
        score -= 10; flags.append("Spend is above monthly budget")
    if metrics.get("clicks", 0) >= 100 and metrics.get("cvr", 0) < 0.05:
        score -= 10; flags.append("CVR is weak relative to traffic")
    if metrics.get("impressions", 0) >= 1000 and metrics.get("ctr", 0) < 0.002:
        score -= 10; flags.append("CTR is weak")
    if action_count > 50:
        score -= 10; flags.append(f"{action_count} high-confidence actions need review")
    elif action_count > 15:
        score -= 5; flags.append(f"{action_count} actions need review")
    score = max(0, min(100, int(score)))
    if score >= 85:
        status = "Green — Healthy / low urgency"
    elif score >= 70:
        status = "Yellow — Watch closely"
    elif score >= 50:
        status = "Orange — Needs optimization"
    else:
        status = "Red — Urgent review"
    return score, status, flags

def client_metric_snapshot(client_name: str, client_config: dict, action_count: int = 0) -> dict:
    source_type, rec, df = get_latest_performance_source(client_name)
    total_sales = summarize_business_sales(client_name)
    m = summarize_performance(df, total_sales_override=total_sales)
    score, status, flags = health_score(m, client_config, action_count=action_count)
    m.update({"health_score": score, "status": status, "flags": flags, "source_type": source_type, "source_file": rec.get("original_file") if rec else ""})
    return m
