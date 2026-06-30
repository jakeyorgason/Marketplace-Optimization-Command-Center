from __future__ import annotations

import streamlit as st
import pandas as pd
from pathlib import Path

from modules.config import load_clients, get_client_config
from modules.data_loader import latest_performance_sections, DATA_LOADER_VERSION
from modules.metrics import summarize_performance
from modules.rules_engine import generate_actions, RULES_ENGINE_VERSION
from modules.ai_bulk_audit import run_ai_bulk_audit, apply_audit_decisions, AI_BULK_AUDIT_VERSION
from modules.bulk_exporter import (
    export_bulk_for_ad_type,
    export_bulk_review_workbook,
    zip_bulk_files,
    build_bulk_rows,
    BULK_EXPORTER_VERSION,
)

st.title("Bulk Upload Builder")
st.caption(
    f"Rules: {RULES_ENGINE_VERSION} | Loader: {DATA_LOADER_VERSION} | "
    f"AI audit: {AI_BULK_AUDIT_VERSION} | Exporter: {BULK_EXPORTER_VERSION}"
)
st.caption("Build separate Amazon bulk upload files for SP, SB, and SD using the PxP bulk template.")
st.caption("Brand Safety negatives are review-only by default so branded campaigns do not accidentally receive negatives.")

clients = load_clients()
if clients.empty:
    st.warning("Add clients first.")
    st.stop()

client = st.selectbox("Client", clients["client_name"].astype(str).tolist())
cfg = get_client_config(client)

with st.expander("Client rules used for this build", expanded=False):
    st.json({
        "target_tacos": cfg.get("target_tacos", ""),
        "target_acos": cfg.get("target_acos", ""),
        "growth_mode": cfg.get("growth_mode", "balanced"),
        "monthly_budget": cfg.get("monthly_budget", ""),
        "forbidden_terms": cfg.get("forbidden_terms", ""),
        "priority_asins": cfg.get("priority_asins", ""),
    })

with st.expander("Recommendation settings", expanded=False):
    min_spend = st.number_input("Minimum spend for waste rules", value=20.0, step=5.0)
    min_clicks = st.number_input("Minimum clicks for click-based rules", value=10, step=1)
    show_all = st.checkbox("Show medium/low priority actions too", value=False)
    include_brand_safety_upload = st.checkbox(
        "Allow Brand Safety negatives in upload exports",
        value=False,
        help="Default is off. Keep this off unless you have reviewed affected campaigns and confirmed these negatives should be uploaded."
    )
    exclude_branded_campaigns = st.checkbox(
        "Exclude Brand Safety rows that appear to come from branded campaigns",
        value=True,
        help="Checks campaign/ad group/affected campaign names for the client's forbidden terms before allowing Brand Safety rows into upload exports."
    )
    st.caption("AI audit uses OPENAI_API_KEY, OPENAI_MODEL, and OPENAI_REASONING_EFFORT from Streamlit secrets.")
settings = {"min_spend": min_spend, "min_clicks": min_clicks}


def _forbidden_tokens(raw: str) -> list[str]:
    terms = []
    for chunk in str(raw or "").replace(";", ",").replace("\n", ",").split(","):
        term = chunk.strip().lower()
        if not term:
            continue
        terms.append(term)
        for piece in term.split():
            if len(piece) >= 4 and piece not in {"energy", "drink", "drinks", "water", "sparkling", "caffeine"}:
                terms.append(piece)
    return sorted(set(terms), key=lambda x: (-len(x), x))


def _contains_forbidden_campaign(action_row: pd.Series, terms: list[str]) -> bool:
    if not terms:
        return False
    text = " ".join(str(action_row.get(col, "")) for col in [
        "campaign", "ad_group", "affected_campaigns", "affected_ad_groups"
    ]).lower()
    return any(term and term in text for term in terms)


def _prepare_export_selection(df: pd.DataFrame, include_brand: bool, exclude_branded: bool, terms: list[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if "reason_code" not in out.columns:
        out["include_in_bulk_upload"] = True
        return out
    brand_mask = out["reason_code"].astype(str).eq("brand_safety_grouped")
    out["include_in_bulk_upload"] = True
    if brand_mask.any():
        out.loc[brand_mask, "include_in_bulk_upload"] = bool(include_brand)
        if include_brand and exclude_branded:
            branded_campaign_mask = out.apply(lambda r: _contains_forbidden_campaign(r, terms), axis=1)
            out.loc[brand_mask & branded_campaign_mask, "include_in_bulk_upload"] = False
    return out


forbidden_campaign_terms = _forbidden_tokens(cfg.get("forbidden_terms", ""))

sections = latest_performance_sections(client)
actions_by_ad_type: dict[str, pd.DataFrame] = {}
metrics_by_ad_type: dict[str, dict] = {}

for ad_type, payload in sections.items():
    df = payload.get("df", pd.DataFrame())
    metrics_by_ad_type[ad_type] = summarize_performance(df) if df is not None and not df.empty else {}
    if df is None or df.empty:
        actions_by_ad_type[ad_type] = pd.DataFrame()
        continue
    actions = generate_actions(df, cfg, settings=settings)
    if not actions.empty:
        actions["ad_type"] = ad_type
    actions_by_ad_type[ad_type] = actions

if all(df.empty for df in actions_by_ad_type.values()):
    st.info("No actions generated from the latest SP/SB/SD performance data.")
    st.stop()

st.subheader("AI Audit & Build Plan")
st.caption("The audit reviews all generated SP/SB/SD actions, not just high-priority actions. Final exports still require row-level approval below.")
st.caption("Brand Safety items are included in the review, but they are not added to bulk upload exports unless you enable the Brand Safety upload option above.")
if st.button("Run AI bulk audit", type="primary") or "bulk_ai_audit" not in st.session_state:
    st.session_state["bulk_ai_audit"] = run_ai_bulk_audit(
        client_name=client,
        cfg=cfg,
        actions_by_ad_type=actions_by_ad_type,
        metrics_by_ad_type=metrics_by_ad_type,
    )

audit = st.session_state.get("bulk_ai_audit", {})
st.info(audit.get("summary", "No audit summary available."))
if audit.get("warnings"):
    with st.expander("Audit warnings", expanded=False):
        for warning in audit.get("warnings", []):
            st.warning(warning)

approved_by_ai = apply_audit_decisions(actions_by_ad_type, audit, high_priority_only=False)

brand_review_count = 0
for _df in actions_by_ad_type.values():
    if _df is not None and not _df.empty and "reason_code" in _df.columns:
        brand_review_count += int(_df["reason_code"].astype(str).eq("brand_safety_grouped").sum())
if brand_review_count:
    if include_brand_safety_upload:
        st.warning(f"Brand Safety upload is ON. {brand_review_count:,} Brand Safety review rows can be included only if approved and upload-safe.")
    else:
        st.info(f"{brand_review_count:,} Brand Safety review rows detected. They will stay out of upload exports unless you turn on Brand Safety uploads.")

st.subheader("SP / SB / SD Build Sections")
final_selected: dict[str, pd.DataFrame] = {}

for ad_type in ["SP", "SB", "SD"]:
    generated = actions_by_ad_type.get(ad_type, pd.DataFrame())
    suggested = approved_by_ai.get(ad_type, pd.DataFrame())
    if generated is None or generated.empty:
        with st.expander(f"{ad_type} Actions (0)", expanded=False):
            st.info(f"No {ad_type} actions generated.")
        final_selected[ad_type] = pd.DataFrame()
        continue

    display = suggested.copy()
    # Always surface Brand Safety rows for visibility/review, even when AI keeps them out of the default export plan.
    if "reason_code" in generated.columns:
        brand_rows = generated[generated["reason_code"].astype(str).eq("brand_safety_grouped")].copy()
        if not brand_rows.empty:
            display = pd.concat([display, brand_rows], ignore_index=True).drop_duplicates()
    if show_all:
        display = generated.copy()
    if display.empty:
        with st.expander(f"{ad_type} Actions (0 AI-approved / {len(generated):,} generated)", expanded=False):
            st.info("The audit did not approve any actions for export under the current settings.")
        final_selected[ad_type] = pd.DataFrame()
        continue

    upload_preview = build_bulk_rows(display, ad_type)
    with st.expander(f"{ad_type} Actions ({len(display):,} shown, {len(upload_preview):,} upload rows)", expanded=(ad_type == "SP")):
        section_summary = audit.get("sections", {}).get(ad_type, {}).get("summary", "")
        if section_summary:
            st.write(section_summary)
        cols = [c for c in [
            "priority", "category", "campaign", "ad_group",
            "issue", "recommendation", "target",
            "affected_campaigns", "affected_ad_groups", "campaign_count", "ad_group_count",
            "spend", "ad_sales", "clicks", "orders", "current_bid", "suggested_bid", "reason_code", "evidence"
        ] if c in display.columns]
        table = display.copy().reset_index(drop=True)
        table["_row_id"] = table.index
        editor_df = table[["_row_id"] + cols].copy()
        if "reason_code" in editor_df.columns:
            approve_default = ~editor_df["reason_code"].astype(str).eq("brand_safety_grouped")
            if include_brand_safety_upload:
                approve_default = pd.Series([True] * len(editor_df), index=editor_df.index)
        else:
            approve_default = pd.Series([True] * len(editor_df), index=editor_df.index)

        # Keep the approval checkbox as the first visible column.
        editor_df.insert(0, "approve", approve_default.astype(bool))

        edited = st.data_editor(
            editor_df,
            use_container_width=True,
            hide_index=True,
            key=f"bulk_ai_editor_{ad_type}",
            column_config={"approve": st.column_config.CheckboxColumn("Approve"), "_row_id": None},
        )
        approved_ids = edited.loc[edited["approve"] == True, "_row_id"].astype(int).tolist() if "_row_id" in edited.columns else []
        selected_rows = table[table["_row_id"].isin(approved_ids)].drop(columns=["_row_id"], errors="ignore").copy() if approved_ids else pd.DataFrame()
        final_selected[ad_type] = _prepare_export_selection(
            selected_rows,
            include_brand=include_brand_safety_upload,
            exclude_branded=exclude_branded_campaigns,
            terms=forbidden_campaign_terms,
        )

        with st.expander(f"{ad_type} upload preview", expanded=False):
            preview = build_bulk_rows(final_selected[ad_type], ad_type)
            st.dataframe(preview, use_container_width=True)
            brand_selected = 0
            brand_uploadable = 0
            if not final_selected[ad_type].empty and "reason_code" in final_selected[ad_type].columns:
                brand_selected = int(final_selected[ad_type]["reason_code"].astype(str).eq("brand_safety_grouped").sum())
                if "include_in_bulk_upload" in final_selected[ad_type].columns:
                    brand_uploadable = int((final_selected[ad_type]["reason_code"].astype(str).eq("brand_safety_grouped") & final_selected[ad_type]["include_in_bulk_upload"].astype(bool)).sum())
            if brand_selected:
                st.caption(f"Brand Safety selected for review: {brand_selected:,}. Brand Safety eligible for upload after safeguards: {brand_uploadable:,}.")

st.subheader("Export separate bulk uploads")
export_cols = st.columns(4)
created_paths = []

for idx, ad_type in enumerate(["SP", "SB", "SD"]):
    selected = final_selected.get(ad_type, pd.DataFrame())
    upload_rows = build_bulk_rows(selected, ad_type)
    with export_cols[idx]:
        st.metric(f"{ad_type} upload rows", len(upload_rows))
        if st.button(f"Build {ad_type} upload", disabled=upload_rows.empty, key=f"build_{ad_type}"):
            path = export_bulk_for_ad_type(client, ad_type, selected)
            st.session_state[f"bulk_path_{ad_type}"] = str(path)
        saved = st.session_state.get(f"bulk_path_{ad_type}")
        if saved:
            with open(saved, "rb") as f:
                st.download_button(f"Download {ad_type}", f, file_name=saved.split("/")[-1], key=f"dl_{ad_type}")
            created_paths.append(saved)

with export_cols[3]:
    selected_any = any(not df.empty for df in final_selected.values())
    st.metric("Review files", int(selected_any))
    if st.button("Build review workbook", disabled=not selected_any):
        path = export_bulk_review_workbook(client, final_selected, audit=audit)
        st.session_state["bulk_review_path"] = str(path)
    saved_review = st.session_state.get("bulk_review_path")
    if saved_review:
        with open(saved_review, "rb") as f:
            st.download_button("Download review", f, file_name=saved_review.split("/")[-1], key="dl_review")

if created_paths and len(created_paths) >= 2:
    if st.button("Zip built uploads"):
        zip_path = zip_bulk_files([Path(p) for p in created_paths], client)
        st.session_state["bulk_zip_path"] = str(zip_path)
zip_saved = st.session_state.get("bulk_zip_path")
if zip_saved:
    with open(zip_saved, "rb") as f:
        st.download_button("Download zip", f, file_name=zip_saved.split("/")[-1])

st.caption("Important: re-upload the Bulk Operations workbook after installing this patch so original ID/template columns are preserved for cleaner exports.")
