from __future__ import annotations
from pathlib import Path
from datetime import datetime
import hashlib
import json
import shutil
import pandas as pd
import streamlit as st

from .config import UPLOADS_DIR, PROCESSED_DIR, get_client_config
from .report_mapper import detect_report_type, standardize_performance_table

DATA_LOADER_VERSION = "2026-06-30-clean-loader-v1"

META_FILE = PROCESSED_DIR / "processed_reports.jsonl"

def slugify(value: str) -> str:
    out = "".join(ch if ch.isalnum() else "_" for ch in str(value).strip().lower())
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_") or "unknown"

def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:12]

def _append_meta(record: dict) -> None:
    META_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(META_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")

def load_meta() -> pd.DataFrame:
    if not META_FILE.exists():
        return pd.DataFrame()
    rows = []
    with open(META_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "uploaded_at" in df.columns:
        df["uploaded_at"] = pd.to_datetime(df["uploaded_at"], errors="coerce")
    return df

def _read_excel_sheets(path: Path) -> list[tuple[str, pd.DataFrame]]:
    sheets: list[tuple[str, pd.DataFrame]] = []
    try:
        xls = pd.ExcelFile(path, engine="openpyxl")
        for sheet_name in xls.sheet_names:
            try:
                df = pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl")
                if df is not None and not df.empty:
                    df = df.dropna(how="all")
                    if not df.empty:
                        sheets.append((sheet_name, df))
            except Exception:
                continue
    except Exception:
        pass
    return sheets

def _read_uploaded_file(path: Path) -> list[tuple[str, pd.DataFrame]]:
    suffix = path.suffix.lower()
    if suffix in [".xlsx", ".xlsm", ".xls"]:
        return _read_excel_sheets(path)
    if suffix == ".csv":
        return [("csv", pd.read_csv(path))]
    if suffix == ".txt":
        try:
            return [("txt", pd.read_csv(path, sep="\t"))]
        except Exception:
            return [("txt", pd.read_csv(path))]
    return []

def save_uploaded_file(client_name: str, uploaded_file) -> list[dict]:
    client_slug = slugify(client_name)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    upload_dir = UPLOADS_DIR / client_slug / ts
    processed_dir = PROCESSED_DIR / client_slug
    upload_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)

    original_name = uploaded_file.name
    raw_path = upload_dir / original_name
    with open(raw_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    fhash = file_hash(raw_path)
    outputs: list[dict] = []
    for sheet_name, df in _read_uploaded_file(raw_path):
        report_type = detect_report_type(original_name, sheet_name, df)
        std = standardize_performance_table(df)
        safe_sheet = slugify(sheet_name)
        processed_name = f"{ts}_{fhash}_{report_type}_{safe_sheet}.parquet"
        processed_path = processed_dir / processed_name
        try:
            std.to_parquet(processed_path, index=False)
        except Exception:
            processed_path = processed_path.with_suffix(".csv")
            std.to_csv(processed_path, index=False)
        record = {
            "client_name": client_name,
            "client_slug": client_slug,
            "uploaded_at": datetime.utcnow().isoformat(),
            "original_file": original_name,
            "raw_path": str(raw_path),
            "sheet_name": sheet_name,
            "report_type": report_type,
            "processed_path": str(processed_path),
            "rows": int(len(std)),
            "columns": int(len(std.columns)),
            "hash": fhash,
        }
        _append_meta(record)
        outputs.append(record)
    return outputs

def load_processed_report(record: dict | pd.Series) -> pd.DataFrame:
    path = Path(record["processed_path"])
    if not path.exists():
        return pd.DataFrame()
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)

def list_reports(client_name: str | None = None, report_type: str | None = None) -> pd.DataFrame:
    df = load_meta()
    if df.empty:
        return df
    if client_name:
        df = df[df["client_name"].astype(str) == str(client_name)]
    if report_type:
        df = df[df["report_type"].astype(str) == str(report_type)]
    if not df.empty and "uploaded_at" in df.columns:
        df = df.sort_values("uploaded_at", ascending=False)
    return df.reset_index(drop=True)

def latest_report(client_name: str, report_type: str) -> tuple[dict | None, pd.DataFrame]:
    reports = list_reports(client_name, report_type)
    if reports.empty:
        return None, pd.DataFrame()
    rec = reports.iloc[0].to_dict()
    return rec, load_processed_report(rec)

def get_latest_performance_source(client_name: str) -> tuple[str, dict | None, pd.DataFrame]:
    for rt in ["search_term_report", "targeting_report", "campaign_report", "bulk_operations_template"]:
        rec, df = latest_report(client_name, rt)
        if rec and not df.empty and {"spend", "ad_sales"}.intersection(df.columns):
            return rt, rec, df
    return "none", None, pd.DataFrame()
