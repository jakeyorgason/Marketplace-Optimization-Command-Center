from __future__ import annotations

import json
import os
from typing import Any

import pandas as pd

AI_BULK_AUDIT_VERSION = "2026-06-30-brand-safety-optional-v3"


ACTION_COLUMNS = [
    "ad_type", "priority", "category", "issue", "recommendation", "campaign", "ad_group",
    "target", "affected_campaigns", "affected_ad_groups", "campaign_count", "ad_group_count",
    "is_multi_campaign_brand_safety", "spend", "ad_sales", "clicks", "orders", "current_bid", "suggested_bid",
    "suggested_change_pct", "reason_code", "evidence", "match_type", "entity", "operation",
]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def _get_secret(name: str, default: str | None = None) -> str | None:
    """Read Streamlit secrets when available, then environment variables."""
    try:
        import streamlit as st  # imported lazily so this module can be tested outside Streamlit
        value = st.secrets.get(name, None)
        if value not in [None, ""]:
            return str(value)
    except Exception:
        pass
    value = os.getenv(name)
    if value not in [None, ""]:
        return str(value)
    return default


def _clean_actions(actions: pd.DataFrame, max_rows: int = 1200) -> tuple[list[dict], bool]:
    """Return action rows for the AI audit. Uses all rows up to a large safety cap."""
    if actions is None or actions.empty:
        return [], False
    cols = [c for c in ACTION_COLUMNS if c in actions.columns]
    if not cols:
        cols = list(actions.columns[:25])
    trimmed = actions[cols].copy()
    truncated = len(trimmed) > max_rows
    if truncated:
        trimmed = trimmed.head(max_rows).copy()
    return trimmed.fillna("").to_dict(orient="records"), truncated


def _action_summary(actions: pd.DataFrame) -> dict[str, Any]:
    if actions is None or actions.empty:
        return {"total_actions": 0}
    out: dict[str, Any] = {"total_actions": int(len(actions))}
    for col in ["priority", "category", "reason_code"]:
        if col in actions.columns:
            out[f"by_{col}"] = actions[col].astype(str).value_counts(dropna=False).head(30).to_dict()
    for col in ["spend", "ad_sales", "clicks", "orders"]:
        if col in actions.columns:
            out[f"total_{col}"] = float(pd.to_numeric(actions[col], errors="coerce").fillna(0).sum())
    return out


def _target_tacos_acos(cfg: dict) -> tuple[float, float]:
    target_tacos = _safe_float(cfg.get("target_tacos", 0), 0)
    target_acos = _safe_float(cfg.get("target_acos", 0), 0)
    if target_tacos > 1:
        target_tacos /= 100
    if target_acos > 1:
        target_acos /= 100
    return target_tacos, target_acos


def deterministic_bulk_audit(
    client_name: str,
    cfg: dict,
    actions_by_ad_type: dict[str, pd.DataFrame],
    metrics_by_ad_type: dict[str, dict] | None = None,
) -> dict:
    """Fallback audit that reviews all actions using deterministic account rules."""
    metrics_by_ad_type = metrics_by_ad_type or {}
    target_tacos, target_acos = _target_tacos_acos(cfg)
    mode = str(cfg.get("growth_mode", "balanced")).lower()
    forbidden = str(cfg.get("forbidden_terms", "")).strip()

    sections = {}
    overall_notes = []
    for ad_type, actions in actions_by_ad_type.items():
        if actions is None or actions.empty:
            sections[ad_type] = {
                "summary": f"No {ad_type} actions generated from the current rules.",
                "approve_reason_codes": [],
                "hold_reason_codes": [],
                "audit_scope": "all_actions",
            }
            continue

        waste = int((actions.get("category", pd.Series(dtype=str)).astype(str) == "Waste Cut").sum()) if "category" in actions.columns else 0
        scale = int((actions.get("category", pd.Series(dtype=str)).astype(str) == "Scale Opportunity").sum()) if "category" in actions.columns else 0
        brand = int((actions.get("category", pd.Series(dtype=str)).astype(str) == "Brand Safety").sum()) if "category" in actions.columns else 0

        # Brand Safety is review-only by default. The Bulk Upload Builder can opt it into upload after human review.
        approve = ["high_click_no_order", "high_acos", "efficient_scale"]
        hold = ["low_cvr", "low_ctr", "diagnostic_only", "brand_safety_grouped"]

        if not forbidden and "brand_safety_grouped" in hold:
            hold.remove("brand_safety_grouped")
        if mode in ["profit", "profitability", "conservative"]:
            approve = [code for code in approve if code in ["high_click_no_order", "high_acos", "brand_safety_grouped"]]
        elif mode in ["aggressive", "scale", "growth"]:
            approve = [code for code in approve if code in ["high_click_no_order", "high_acos", "efficient_scale", "brand_safety_grouped"]]

        sections[ad_type] = {
            "summary": (
                f"{ad_type}: audited all {len(actions):,} generated actions. "
                f"Found {waste:,} waste cuts, {scale:,} scale opportunities, and {brand:,} brand-safety checks."
            ),
            "approve_reason_codes": approve,
            "hold_reason_codes": hold,
            "audit_scope": "all_actions",
        }
        overall_notes.append(sections[ad_type]["summary"])

    return {
        "mode": "deterministic_fallback",
        "client": client_name,
        "summary": (
            f"Bulk plan built for {client_name}. Target TACOS: {target_tacos:.1%} | "
            f"Target ACOS: {target_acos:.1%} | Mode: {mode}. "
            "Fallback audit reviewed all generated actions and selected high-confidence reason-code groups for export. "
            + " ".join(overall_notes)
        ),
        "sections": sections,
        "warnings": [
            "Fallback audit used deterministic rules. Add valid OpenAI secrets to use the reasoning audit.",
            "Review Brand Safety rows before upload; confirm whether any branded traffic is intentionally separated into brand-defense campaigns.",
        ],
    }


def _build_payload(
    client_name: str,
    cfg: dict,
    actions_by_ad_type: dict[str, pd.DataFrame],
    metrics_by_ad_type: dict[str, dict] | None,
) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    actions_payload: dict[str, list[dict]] = {}
    summaries: dict[str, dict] = {}
    for ad_type, actions in actions_by_ad_type.items():
        rows, truncated = _clean_actions(actions)
        actions_payload[ad_type] = rows
        summaries[ad_type] = _action_summary(actions)
        if truncated:
            warnings.append(f"{ad_type} had more than 1,200 actions; only the first 1,200 rows were sent to the AI for token safety.")

    payload = {
        "client_name": client_name,
        "client_config": cfg,
        "metrics_by_ad_type": metrics_by_ad_type or {},
        "action_summaries_by_ad_type": summaries,
        "actions_by_ad_type": actions_payload,
        "audit_scope": "Audit all generated actions, not only high-priority actions. Decide export eligibility by reason_code and strategic fit.",
        "instructions": [
            "Act as a senior Amazon Ads strategist preparing separate SP, SB, and SD bulk uploads.",
            "Use the client's target TACOS/ACOS, growth mode, forbidden terms, priority ASINs, and budget constraints.",
            "Audit every action row provided. Do not restrict analysis to high-priority rows.",
            "Approve reason codes only when they should be included in that ad type's export.",
            "Hold diagnostic actions that should be reviewed but not uploaded directly.",
            "Be more aggressive only when the client's growth mode and TACOS room support it.",
            "Protect against branded leakage when forbidden terms appear in shopper search terms or targets.",
            "Treat Brand Safety actions as review-only by default. Do not include brand_safety_grouped in approve_reason_codes unless there is clear evidence it belongs in non-brand campaigns and is safe to upload.",
            "If campaign names appear branded, warn the user rather than recommending automatic negatives for those campaigns.",
        ],
    }
    return payload, warnings


def run_ai_bulk_audit(
    client_name: str,
    cfg: dict,
    actions_by_ad_type: dict[str, pd.DataFrame],
    metrics_by_ad_type: dict[str, dict] | None = None,
    api_key: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> dict:
    """Run the reasoning audit using Streamlit secrets/env. Falls back safely."""
    api_key = api_key or _get_secret("OPENAI_API_KEY")
    model = model or _get_secret("OPENAI_MODEL", "gpt-5.5-thinking")
    reasoning_effort = reasoning_effort or _get_secret("OPENAI_REASONING_EFFORT", "high")

    payload, payload_warnings = _build_payload(client_name, cfg, actions_by_ad_type, metrics_by_ad_type)

    if not api_key:
        out = deterministic_bulk_audit(client_name, cfg, actions_by_ad_type, metrics_by_ad_type)
        out["warnings"] = payload_warnings + out.get("warnings", [])
        return out

    try:
        from openai import OpenAI
    except Exception:
        out = deterministic_bulk_audit(client_name, cfg, actions_by_ad_type, metrics_by_ad_type)
        out["warnings"] = payload_warnings + out.get("warnings", []) + ["OpenAI package is not installed, so fallback audit was used."]
        return out

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
                            "audit_scope": {"type": "string"},
                        },
                        "required": ["summary", "approve_reason_codes", "hold_reason_codes", "audit_scope"],
                    },
                },
                "warnings": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["mode", "client", "summary", "sections", "warnings"],
        },
    }

    try:
        client = OpenAI(api_key=api_key)
        kwargs = {
            "model": model,
            "input": [
                {
                    "role": "system",
                    "content": (
                        "You are a senior Amazon Ads strategist and bulk-operations QA reviewer. "
                        "You audit all provided actions, not only high-priority actions. "
                        "Brand Safety actions are review-only by default; do not approve brand_safety_grouped unless clearly safe. "
                        "Return only valid JSON matching the schema."
                    ),
                },
                {"role": "user", "content": json.dumps(payload, default=str)},
            ],
            "text": {"format": {"type": "json_schema", "json_schema": schema}},
        }
        if reasoning_effort:
            kwargs["reasoning"] = {"effort": reasoning_effort}
        resp = client.responses.create(**kwargs)
        out = json.loads(resp.output_text)
        out["mode"] = f"openai:{model}:reasoning_{reasoning_effort}"
        out["warnings"] = payload_warnings + out.get("warnings", [])
        return out
    except Exception as exc:
        out = deterministic_bulk_audit(client_name, cfg, actions_by_ad_type, metrics_by_ad_type)
        out["warnings"] = payload_warnings + out.get("warnings", []) + [f"OpenAI audit failed, so fallback audit was used: {exc}"]
        return out


def apply_audit_decisions(
    actions_by_ad_type: dict[str, pd.DataFrame],
    audit: dict,
    high_priority_only: bool = False,
) -> dict[str, pd.DataFrame]:
    """Filter actions based on audit-approved reason codes. Defaults to all priorities."""
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
