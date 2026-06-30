from __future__ import annotations
from pathlib import Path
from datetime import datetime
import hashlib
import json
import pandas as pd

from modules.config import UPLOADS_DIR, PROCESSED_DIR, get_client_config
from modules.report_mapper import detect_report_type, standardize_performance_table

DATA_LOADER_VERSION = "2026-06-30-id-preserving-upload-v5"

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
    rows: list[dict] = []
    with open(META_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
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
        std = standardize_performance_table(df, sheet_name=sheet_name)
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


def _latest_upload_group(reports: pd.DataFrame) -> pd.DataFrame:
    """Return records from the latest original upload/hash when possible.

    This lets the Client Dashboard combine SP + SB Search Term tabs from the same
    Bulk Operations workbook instead of selecting only one sheet.
    """
    if reports.empty:
        return reports
    reports = reports.copy()
    reports["uploaded_at"] = pd.to_datetime(reports["uploaded_at"], errors="coerce")
    latest = reports.sort_values("uploaded_at", ascending=False).iloc[0]
    if "hash" in reports.columns and pd.notna(latest.get("hash")):
        same = reports[reports["hash"].astype(str) == str(latest["hash"])]
        if not same.empty:
            return same
    # fallback: same original file from newest upload timestamp
    same_file = reports[reports["original_file"].astype(str) == str(latest.get("original_file", ""))]
    if not same_file.empty:
        return same_file
    return reports.head(1)


def get_latest_performance_source(client_name: str) -> tuple[str, dict | None, pd.DataFrame]:
    """Return the latest combined performance data for dashboards.

    Priority: combine all latest Search Term Report tabs, then targeting tabs,
    then campaign tabs. This fixes understated spend/ad sales when one Bulk Ops
    workbook contains multiple Search Term Report sheets.
    """
    priority = ["search_term_report", "targeting_report", "campaign_report", "bulk_operations_template"]
    for rt in priority:
        reports = list_reports(client_name, rt)
        if reports.empty:
            continue
        group = _latest_upload_group(reports)
        frames = []
        records = []
        for _, row in group.iterrows():
            df = load_processed_report(row)
            if df.empty:
                continue
            has_perf = ("spend" in df.columns and pd.to_numeric(df["spend"], errors="coerce").fillna(0).sum() > 0) or ("ad_sales" in df.columns and pd.to_numeric(df["ad_sales"], errors="coerce").fillna(0).sum() > 0)
            if has_perf:
                tmp = df.copy()
                tmp["_source_sheet"] = row.get("sheet_name", "")
                tmp["_source_file"] = row.get("original_file", "")
                frames.append(tmp)
                records.append(row.to_dict())
        if frames:
            combined = pd.concat(frames, ignore_index=True, sort=False)
            first = records[0] if records else group.iloc[0].to_dict()
            first["combined_sheets"] = ", ".join([str(r.get("sheet_name", "")) for r in records])
            first["combined_records"] = len(records)
            return rt, first, combined
    return "none", None, pd.DataFrame()
