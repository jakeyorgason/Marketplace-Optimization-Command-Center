from __future__ import annotations
from pathlib import Path
from datetime import datetime
import hashlib
import json
import pandas as pd

try:
    from modules.config import UPLOADS_DIR, PROCESSED_DIR, get_client_config
    from modules.report_mapper import detect_report_type, standardize_performance_table
except Exception:  # local/package fallback
    from .config import UPLOADS_DIR, PROCESSED_DIR, get_client_config
    from .report_mapper import detect_report_type, standardize_performance_table

DATA_LOADER_VERSION = "2026-06-30-adtype-loader-v2"
META_FILE = PROCESSED_DIR / "processed_reports.jsonl"
AD_TYPES = ["SP", "SB", "SD"]
PERFORMANCE_REPORT_TYPES = ["search_term_report", "targeting_report", "campaign_report"]


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


def infer_ad_type(original_file: str = "", sheet_name: str = "", report_type: str = "") -> str:
    text = f" {original_file} {sheet_name} {report_type} ".lower()
    compact = text.replace("_", " ").replace("-", " ")
    if "sponsored products" in compact or " sp " in compact or compact.strip().startswith("sp ") or "sp search" in compact:
        return "SP"
    if "sponsored brands" in compact or " sb " in compact or compact.strip().startswith("sb ") or "sb search" in compact or "multi ad group" in compact:
        return "SB"
    if "sponsored display" in compact or " sd " in compact or compact.strip().startswith("sd "):
        return "SD"
    return "Unknown"


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
    if "ad_type" not in df.columns:
        df["ad_type"] = df.apply(lambda r: infer_ad_type(r.get("original_file", ""), r.get("sheet_name", ""), r.get("report_type", "")), axis=1)
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
        ad_type = infer_ad_type(original_name, sheet_name, report_type)
        std = standardize_performance_table(df)
        std["ad_type"] = ad_type
        std["source_report_type"] = report_type
        std["source_sheet"] = sheet_name
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
            "ad_type": ad_type,
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
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    if "ad_type" not in df.columns:
        df["ad_type"] = infer_ad_type(str(record.get("original_file", "")), str(record.get("sheet_name", "")), str(record.get("report_type", "")))
    if "source_report_type" not in df.columns:
        df["source_report_type"] = str(record.get("report_type", ""))
    if "source_sheet" not in df.columns:
        df["source_sheet"] = str(record.get("sheet_name", ""))
    return df


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


def _latest_hash_for_performance(client_name: str) -> str | None:
    reports = list_reports(client_name)
    if reports.empty or "hash" not in reports.columns:
        return None
    perf = reports[reports["report_type"].isin(PERFORMANCE_REPORT_TYPES)]
    if perf.empty:
        return None
    return str(perf.sort_values("uploaded_at", ascending=False).iloc[0]["hash"])


def latest_performance_sections(client_name: str, mode: str = "search_term_first") -> dict[str, dict]:
    """Return one combined performance dataframe per ad type.

    This removes per-sheet report selection from the UI. For each ad type, the app
    uses Search Term data when present, then falls back to Targeting, then Campaign.
    That avoids double-counting metrics while still producing SP/SB/SD sections.
    """
    reports = list_reports(client_name)
    empty = {ad: {"df": pd.DataFrame(), "sources": [], "report_type": "none"} for ad in AD_TYPES}
    if reports.empty:
        return empty

    latest_hash = _latest_hash_for_performance(client_name)
    if latest_hash:
        batch = reports[reports["hash"].astype(str) == latest_hash].copy()
    else:
        batch = reports.copy()
    batch = batch[batch["report_type"].isin(PERFORMANCE_REPORT_TYPES)].copy()
    if batch.empty:
        return empty
    if "ad_type" not in batch.columns:
        batch["ad_type"] = batch.apply(lambda r: infer_ad_type(r.get("original_file", ""), r.get("sheet_name", ""), r.get("report_type", "")), axis=1)

    priority = ["search_term_report", "targeting_report", "campaign_report"]
    out = {}
    for ad_type in AD_TYPES:
        ad_rows = batch[batch["ad_type"].astype(str).str.upper() == ad_type]
        chosen_rt = None
        chosen_rows = pd.DataFrame()
        for rt in priority:
            rt_rows = ad_rows[ad_rows["report_type"].astype(str) == rt]
            if not rt_rows.empty:
                chosen_rt = rt
                chosen_rows = rt_rows.sort_values("uploaded_at", ascending=False)
                break
        frames = []
        sources = []
        if chosen_rt:
            for _, rec in chosen_rows.iterrows():
                d = load_processed_report(rec.to_dict())
                if d.empty:
                    continue
                d = d.copy()
                d["ad_type"] = ad_type
                d["source_report_type"] = chosen_rt
                d["source_sheet"] = rec.get("sheet_name", "")
                frames.append(d)
                sources.append({
                    "ad_type": ad_type,
                    "report_type": chosen_rt,
                    "original_file": rec.get("original_file", ""),
                    "sheet_name": rec.get("sheet_name", ""),
                    "rows": int(len(d)),
                    "spend": float(pd.to_numeric(d.get("spend", 0), errors="coerce").fillna(0).sum()) if "spend" in d.columns else 0.0,
                    "ad_sales": float(pd.to_numeric(d.get("ad_sales", 0), errors="coerce").fillna(0).sum()) if "ad_sales" in d.columns else 0.0,
                })
        out[ad_type] = {
            "df": pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(),
            "sources": sources,
            "report_type": chosen_rt or "none",
        }
    return out


def combined_latest_performance(client_name: str) -> tuple[dict[str, dict], pd.DataFrame]:
    sections = latest_performance_sections(client_name)
    frames = [payload["df"] for payload in sections.values() if not payload["df"].empty]
    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return sections, combined


def get_latest_performance_source(client_name: str) -> tuple[str, dict | None, pd.DataFrame]:
    sections, combined = combined_latest_performance(client_name)
    sources = []
    for ad_type, payload in sections.items():
        sources.extend(payload.get("sources", []))
    rec = {"original_file": "combined latest performance", "sheet_name": "SP/SB/SD combined", "sources": sources} if sources else None
    return "combined_by_ad_type", rec, combined
