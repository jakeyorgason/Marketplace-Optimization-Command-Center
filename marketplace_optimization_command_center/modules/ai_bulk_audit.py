from __future__ import annotations

import json
import os
from typing import Any
import pandas as pd

AI_BULK_AUDIT_VERSION = "2026-06-30-ai-bulk-audit-v1"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _actions_sample(actions: pd.DataFrame, limit: int = 40) -> list[dict]:
    if actions is None or actions.empty:
        return []
    cols = [c for c in [
        "ad_type", "priority", "category", "issue", "recommendation", "campaign", "ad_group",
        "target", "spend", "ad_sales", "clicks", "orders", "current_bid", "suggested_bid",
        "suggested_change_pct", "reason_code", "evidence"
    ] if c in actions.columns]
    return actions[cols].head(limit).fillna("").to_dict(orient="records")


def deterministic_bulk_audit(client_name: str, cfg: dict, actions_by_ad_type: dict[str, pd.DataFrame], metrics_by_ad_type: dict[str, dict] | None = None) -> dict:
    """Fallback audit that behaves like a conservative AI reviewer without external API calls."""
    metrics_by_ad_type = metrics_by_ad_type or {}
    target_tacos = _safe_float(cfg.get("target_tacos", 0), 0)
    target_acos = _safe_float(cfg.get("target_acos", 0), 0)
    if target_tacos > 1:
        target_tacos /= 100
    if target_acos > 1:
        target_acos /= 100
    mode = str(cfg.get("growth_mode", "balanced")).lower()
    forbidden = str(cfg.get("forbidden_terms", "")).strip()

    sections = {}
    overall_notes = []
    for ad_type, actions in actions_by_ad_type.items():
        if actions is None or actions.empty:
            sections[ad_type] = {"summary": f"No {ad_type} actions generated from the current rules.", "approve_reason_codes": [], "hold_reason_codes": []}
            continue
        hi = actions[actions.get("priority", "").astype(str) == "High"] if "priority" in actions.columns else actions
        waste = int((actions.get("category", pd.Series(dtype=str)).astype(str) == "Waste Cut").sum()) if "category" in actions.columns else 0
        scale = int((actions.get("category", pd.Series(dtype=str)).astype(str) == "Scale Opportunity").sum()) if "category" in actions.columns else 0
        brand = int((actions.get("category", pd.Series(dtype=str)).astype(str) == "Brand Safety").sum()) if "category" in actions.columns else 0
        approve = ["high_click_no_order", "high_acos", "efficient_scale"]
        if forbidden:
            approve.append("brand_safety_grouped")
        if mode in ["profit", "profitability", "conservative"]:
            approve = ["high_click_no_order", "high_acos", "brand_safety_grouped"]
        elif mode in ["aggressive", "scale", "growth"]:
            approve = ["high_click_no_order", "high_acos", "efficient_scale", "brand_safety_grouped"]
        sections[ad_type] = {
            "summary": f"{ad_type}: {len(actions):,} total actions, {len(hi):,} high-priority, {waste:,} waste cuts, {scale:,} scale opportunities, {brand:,} brand-safety checks.",
            "approve_reason_codes": approve,
            "hold_reason_codes": ["low_cvr", "low_ctr", "diagnostic_only"],
        }
        overall_notes.append(sections[ad_type]["summary"])

    return {
        "mode": "deterministic_fallback",
        "client": client_name,
        "summary": (
            f"Bulk plan built for {client_name}. Target TACOS: {target_tacos:.1%} | Target ACOS: {target_acos:.1%} | Mode: {mode}. "
            "The fallback auditor approves high-confidence bid, negative, and scale actions only. "
            + " ".join(overall_notes)
        ),
        "sections": sections,
        "warnings": [
            "Review Brand Safety rows before upload; the app cannot yet tell whether a campaign is intentionally branded.",
            "Re-upload the Bulk Operations workbook after this patch so ID/template columns are preserved for cleaner exports.",
        ],
    }


def run_ai_bulk_audit(client_name: str, cfg: dict, actions_by_ad_type: dict[str, pd.DataFrame], metrics_by_ad_type: dict[str, dict] | None = None, api_key: str | None = None, model: str = "gpt-5.5-mini") -> dict:
    """Optional OpenAI audit. Falls back safely when no key/package is available."""
    api_key = api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return deterministic_bulk_audit(client_name, cfg, actions_by_ad_type, metrics_by_ad_type)

    try:
        from openai import OpenAI
    except Exception:
        out = deterministic_bulk_audit(client_name, cfg, actions_by_ad_type, metrics_by_ad_type)
        out["warnings"].append("OpenAI package is not installed, so fallback audit was used.")
        return out

    payload = {
        "client_name": client_name,
        "client_config": cfg,
        "metrics_by_ad_type": metrics_by_ad_type or {},
        "actions_by_ad_type_sample": {k: _actions_sample(v, 50) for k, v in actions_by_ad_type.items()},
        "task": "Audit the generated Amazon ad actions by client goals, target TACOS/ACOS, forbidden terms, and growth mode. Decide which reason codes should be approved for bulk upload per ad type. Return JSON only.",
    }
    schema = {
        "name": "bulk_audit_decision",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "mode": {"type": "string"},
                "client": {"type": "string"},
                "summary": {"type": "string"},
                "sections": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "summary": {"type": "string"},
                            "approve_reason_codes": {"type": "array", "items": {"type": "string"}},
                            "hold_reason_codes": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["summary", "approve_reason_codes", "hold_reason_codes"],
                    },
                },
                "warnings": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["mode", "client", "summary", "sections", "warnings"],
        },
    }
    try:
        client = OpenAI(api_key=api_key)
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": "You are a senior Amazon Ads strategist. Be conservative with upload automation; approve only high-confidence changes."},
                {"role": "user", "content": json.dumps(payload, default=str)},
            ],
            text={"format": {"type": "json_schema", "json_schema": schema}},
        )
        text = resp.output_text
        return json.loads(text)
    except Exception as exc:
        out = deterministic_bulk_audit(client_name, cfg, actions_by_ad_type, metrics_by_ad_type)
        out["warnings"].append(f"OpenAI audit failed, so fallback audit was used: {exc}")
        return out


def apply_audit_decisions(actions_by_ad_type: dict[str, pd.DataFrame], audit: dict, high_priority_only: bool = True) -> dict[str, pd.DataFrame]:
    sections = audit.get("sections", {}) if isinstance(audit, dict) else {}
    approved: dict[str, pd.DataFrame] = {}
    for ad_type, actions in actions_by_ad_type.items():
        if actions is None or actions.empty:
            approved[ad_type] = pd.DataFrame()
            continue
        codes = sections.get(ad_type, {}).get("approve_reason_codes", [])
        df = actions.copy()
        if high_priority_only and "priority" in df.columns:
            df = df[df["priority"].astype(str) == "High"].copy()
        if codes and "reason_code" in df.columns:
            df = df[df["reason_code"].astype(str).isin(codes)].copy()
        approved[ad_type] = df.reset_index(drop=True)
    return approved
