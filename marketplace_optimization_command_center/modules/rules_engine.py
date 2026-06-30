from __future__ import annotations
import math
import re
from typing import Any
import pandas as pd

RULES_ENGINE_VERSION = "2026-06-30-brand-safety-optional-v6"
PRIORITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}

GENERIC_FORBIDDEN_PARTS = {"energy", "drink", "drinks", "water", "sparkling", "caffeine", "natural", "organic", "the", "and"}
TARGET_FIELD_CANDIDATES = ["search_term", "target", "keyword", "keyword_text", "targeting", "product_targeting_expression", "resolved_expression", "asin"]

def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default

def _safe_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()

def _term_list(raw: str) -> list[str]:
    if not raw:
        return []
    terms = []
    for chunk in str(raw).replace("\n", ",").replace(";", ",").split(","):
        term = chunk.strip().lower()
        if term:
            terms.append(term)
            for piece in term.split():
                if len(piece) >= 4 and piece not in GENERIC_FORBIDDEN_PARTS:
                    terms.append(piece)
    # unique, longest first
    return sorted(set(terms), key=lambda x: (-len(x), x))

def _matches_forbidden(target_text: str, forbidden_terms: list[str]) -> list[str]:
    text = f" {_safe_text(target_text).lower()} "
    matches = []
    for term in forbidden_terms:
        if not term:
            continue
        # word-boundary-ish matching for terms like ardor
        pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
        if re.search(pattern, text):
            matches.append(term)
    return matches

def _best_target_text(row: pd.Series) -> str:
    for col in ["search_term", "target", "keyword_text", "keyword", "targeting", "product_targeting_expression", "resolved_expression", "asin"]:
        if col in row.index:
            val = _safe_text(row.get(col))
            if val and val.lower() not in {"nan", "none", "0"}:
                return val.lower()
    return ""


def _raw_export_fields(row: pd.Series) -> dict:
    fields = {}
    wanted = [
        "raw__product", "raw__entity", "raw__operation", "raw__campaign_id", "raw__ad_group_id",
        "raw__keyword_id", "raw__product_targeting_id", "raw__campaign_name", "raw__ad_group_name",
        "raw__state", "raw__keyword_text", "raw__match_type", "raw__bid", "raw__budget",
        "raw__daily_budget", "raw__placement_type", "raw__placement", "raw__placement_percent",
        "raw__placement_percentage", "raw__portfolio_id", "raw__product_targeting_expression",
        "raw__resolved_product_targeting_expression", "raw__customer_search_term", "raw__search_term"
    ]
    for col in wanted:
        if col in row.index:
            fields[col] = _safe_text(row.get(col))
    return fields

def _campaign(row: pd.Series) -> str:
    return _safe_text(row.get("campaign", ""))

def _ad_group(row: pd.Series) -> str:
    return _safe_text(row.get("ad_group", ""))

def _round_bid(value: float) -> float:
    if value <= 0:
        return 0.0
    return round(round(value / 0.05) * 0.05, 2)

def _estimate_impact(spend: float, ad_sales: float, action_type: str) -> float:
    if action_type == "Waste Cut":
        return round(max(spend * 0.45, 0), 2)
    if action_type == "Scale Opportunity":
        return round(max(ad_sales * 0.25, spend * 0.5), 2)
    return round(max(spend, ad_sales) * 0.1, 2)

def generate_actions(performance_df: pd.DataFrame, client_config: dict | None = None, settings: dict | None = None) -> pd.DataFrame:
    client_config = client_config or {}
    settings = settings or {}
    if performance_df is None or performance_df.empty:
        return pd.DataFrame()

    df = performance_df.copy()
    for col in ["spend", "ad_sales", "clicks", "orders", "impressions", "acos", "cvr", "ctr", "bid"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
        else:
            df[col] = 0.0

    target_acos = _safe_float(client_config.get("target_acos", 0.5), 0.5)
    if target_acos > 1:
        target_acos = target_acos / 100
    growth_mode = str(client_config.get("growth_mode", "balanced")).lower()
    forbidden_terms = _term_list(str(client_config.get("forbidden_terms", "")))

    min_spend = _safe_float(settings.get("min_spend", 20), 20)
    min_clicks = int(_safe_float(settings.get("min_clicks", 10), 10))
    actions: list[dict] = []

    # Brand Safety grouped by unique shopper/target text. Never campaign/ad group names.
    brand_groups: dict[str, dict] = {}
    if forbidden_terms:
        for _, row in df.iterrows():
            target_text = _best_target_text(row)
            if not target_text:
                continue
            matches = _matches_forbidden(target_text, forbidden_terms)
            if not matches:
                continue
            key = target_text.lower().strip()
            g = brand_groups.setdefault(key, {
                "priority": "High",
                "category": "Brand Safety",
                "area": "Ads",
                "issue": "Forbidden/branded term found in shopper query or target",
                "recommendation": "Review whether this should be excluded from non-brand campaigns or moved to a controlled brand campaign.",
                "ad_type": _safe_text(row.get("ad_type", "Unknown")) or "Unknown",
                "campaign": "",
                "ad_group": "",
                "target": key,
                "spend": 0.0,
                "ad_sales": 0.0,
                "clicks": 0.0,
                "orders": 0.0,
                "current_bid": 0.0,
                "suggested_bid": 0.0,
                "suggested_change_pct": 0.0,
                "estimated_monthly_impact": 0.0,
                "confidence": "High",
                "reason_code": "brand_safety_grouped",
                "evidence": f"Matched forbidden term(s): {', '.join(matches)}",
                "rows_found": 0,
                "campaigns_found": set(),
                "ad_groups_found": set(),
                "campaign_ids_found": set(),
                "ad_group_ids_found": set(),
            })
            spend = _safe_float(row.get("spend"))
            ad_sales = _safe_float(row.get("ad_sales"))
            clicks = _safe_float(row.get("clicks"))
            orders = _safe_float(row.get("orders"))
            g["spend"] += spend; g["ad_sales"] += ad_sales; g["clicks"] += clicks; g["orders"] += orders
            g["rows_found"] += 1
            if _campaign(row): g["campaigns_found"].add(_campaign(row))
            if _ad_group(row): g["ad_groups_found"].add(_ad_group(row))
            cid = _safe_text(row.get("raw__campaign_id", "")) or _safe_text(row.get("campaign_id", ""))
            agid = _safe_text(row.get("raw__ad_group_id", "")) or _safe_text(row.get("ad_group_id", ""))
            if cid: g["campaign_ids_found"].add(cid)
            if agid: g["ad_group_ids_found"].add(agid)
        for g in brand_groups.values():
            campaigns = sorted(g.get("campaigns_found", set()))
            ad_groups = sorted(g.get("ad_groups_found", set()))
            campaign_ids = sorted(g.get("campaign_ids_found", set()))
            ad_group_ids = sorted(g.get("ad_group_ids_found", set()))
            g["estimated_monthly_impact"] = _estimate_impact(g["spend"], g["ad_sales"], "Brand Safety")
            g["affected_campaigns"] = ", ".join(campaigns[:12]) + (" ..." if len(campaigns) > 12 else "")
            g["affected_ad_groups"] = ", ".join(ad_groups[:12]) + (" ..." if len(ad_groups) > 12 else "")
            g["campaign_count"] = len(campaigns)
            g["ad_group_count"] = len(ad_groups)
            g["is_multi_campaign_brand_safety"] = len(campaigns) != 1 or len(ad_groups) != 1
            if len(campaigns) == 1:
                g["campaign"] = campaigns[0]
            else:
                g["campaign"] = f"{len(campaigns)} campaigns"
            if len(ad_groups) == 1:
                g["ad_group"] = ad_groups[0]
            else:
                g["ad_group"] = f"{len(ad_groups)} ad groups"
            if len(campaign_ids) == 1:
                g["raw__campaign_id"] = campaign_ids[0]
            if len(ad_group_ids) == 1:
                g["raw__ad_group_id"] = ad_group_ids[0]
            g["evidence"] = (
                f"{g['evidence']} | {g['rows_found']} rows, {len(campaigns)} campaigns, "
                f"{len(ad_groups)} ad groups. Affected campaigns: {g['affected_campaigns'] or 'not available'}"
            )
            g.pop("campaigns_found", None); g.pop("ad_groups_found", None)
            g.pop("campaign_ids_found", None); g.pop("ad_group_ids_found", None)
            if g["spend"] < 10 and g["clicks"] < 5:
                g["priority"] = "Medium"
            actions.append(g)

    # Row-level rules for non-brand items.
    seen_keys = set()
    for _, row in df.iterrows():
        spend = _safe_float(row.get("spend"))
        ad_sales = _safe_float(row.get("ad_sales"))
        clicks = _safe_float(row.get("clicks"))
        orders = _safe_float(row.get("orders"))
        acos = spend / ad_sales if ad_sales else 0.0
        cvr = orders / clicks if clicks else 0.0
        ctr = _safe_float(row.get("ctr"))
        current_bid = _safe_float(row.get("bid"))
        target = _best_target_text(row)
        campaign = _campaign(row)
        ad_group = _ad_group(row)
        base = {"ad_type": _safe_text(row.get("ad_type", "Unknown")) or "Unknown", "campaign": campaign, "ad_group": ad_group, "target": target, "spend": round(spend,2), "ad_sales": round(ad_sales,2), "clicks": int(clicks), "orders": int(orders), "current_bid": current_bid, **_raw_export_fields(row)}

        if spend >= min_spend and clicks >= min_clicks and orders == 0:
            key = ("waste_no_orders", campaign, ad_group, target)
            if key not in seen_keys:
                seen_keys.add(key)
                suggested = _round_bid(current_bid * 0.65) if current_bid else 0
                actions.append({**base, "priority": "High", "category": "Waste Cut", "area": "Ads", "issue": "High clicks/spend with no orders", "recommendation": "Reduce bid and review as negative exact candidate if query intent is poor.", "suggested_bid": suggested, "suggested_change_pct": -35 if current_bid else 0, "estimated_monthly_impact": _estimate_impact(spend, ad_sales, "Waste Cut"), "confidence": "High", "reason_code": "high_click_no_order", "evidence": f"{int(clicks)} clicks, ${spend:,.2f} spend, 0 orders"})
        elif ad_sales > 0 and target_acos and acos > target_acos * 1.35 and spend >= min_spend:
            key = ("high_acos", campaign, ad_group, target)
            if key not in seen_keys:
                seen_keys.add(key)
                pct = -20 if growth_mode in ["aggressive", "scale"] else -30
                suggested = _round_bid(current_bid * (1 + pct/100)) if current_bid else 0
                actions.append({**base, "priority": "High" if acos > target_acos * 1.75 else "Medium", "category": "Waste Cut", "area": "Ads", "issue": "ACOS materially above target", "recommendation": f"Reduce bid by {abs(pct)}% and review query relevance.", "suggested_bid": suggested, "suggested_change_pct": pct, "estimated_monthly_impact": _estimate_impact(spend, ad_sales, "Waste Cut"), "confidence": "Medium", "reason_code": "high_acos", "evidence": f"ACOS {acos:.1%} vs target {target_acos:.1%}"})
        elif ad_sales >= max(50, min_spend * 2) and target_acos and acos < target_acos * 0.65 and orders >= 2:
            key = ("scale", campaign, ad_group, target)
            if key not in seen_keys:
                seen_keys.add(key)
                pct = 20 if growth_mode in ["aggressive", "scale"] else 12
                suggested = _round_bid(current_bid * (1 + pct/100)) if current_bid else 0
                actions.append({**base, "priority": "High" if ad_sales >= 250 else "Medium", "category": "Scale Opportunity", "area": "Ads", "issue": "Efficient target with room to scale", "recommendation": f"Increase bid by {pct}% if budget and TACOS allow.", "suggested_bid": suggested, "suggested_change_pct": pct, "estimated_monthly_impact": _estimate_impact(spend, ad_sales, "Scale Opportunity"), "confidence": "Medium", "reason_code": "efficient_scale", "evidence": f"ACOS {acos:.1%} vs target {target_acos:.1%}; {int(orders)} orders"})
        elif clicks >= 40 and cvr < 0.04 and spend >= min_spend:
            key = ("low_cvr", campaign, ad_group, target)
            if key not in seen_keys:
                seen_keys.add(key)
                actions.append({**base, "priority": "Medium", "category": "Listing / CVR", "area": "Listing", "issue": "Traffic is not converting well", "recommendation": "Audit price, reviews, hero image, bullets, offer, and query-to-listing fit.", "suggested_bid": current_bid, "suggested_change_pct": 0, "estimated_monthly_impact": _estimate_impact(spend, ad_sales, "Listing / CVR"), "confidence": "Medium", "reason_code": "low_cvr", "evidence": f"{int(clicks)} clicks, CVR {cvr:.1%}"})

    out = pd.DataFrame(actions)
    if out.empty:
        return out
    out["priority_rank"] = out["priority"].map(PRIORITY_ORDER).fillna(9)
    out = out.sort_values(["priority_rank", "estimated_monthly_impact", "spend"], ascending=[True, False, False]).drop(columns=["priority_rank"])
    return out.reset_index(drop=True)

def summarize_actions(actions: pd.DataFrame) -> pd.DataFrame:
    if actions is None or actions.empty:
        return pd.DataFrame(columns=["category", "actions", "estimated_monthly_impact"])
    return actions.groupby("category", dropna=False).agg(actions=("category", "size"), estimated_monthly_impact=("estimated_monthly_impact", "sum")).reset_index().sort_values("actions", ascending=False)
