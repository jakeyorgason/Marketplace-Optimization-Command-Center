from pathlib import Path
from datetime import datetime
import warnings
import pandas as pd
from .config import UPLOADS_DIR, PROCESSED_DIR, ensure_directories

REPORT_TYPES = [
    "Business Report",
    "Bulk Operations",
    "Search Term Report",
    "Targeting Report",
    "Campaign Report",
    "Placement Report",
    "Inventory Report",
    "Profit Matrix",
    "Listing Copy",
]


def safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in ["-", "_"] else "_" for char in value.strip())


def save_uploaded_file(client_name: str, report_type: str, uploaded_file) -> Path:
    ensure_directories()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    client_dir = UPLOADS_DIR / safe_name(client_name) / timestamp
    client_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"{safe_name(report_type)}__{uploaded_file.name}"
    output_path = client_dir / file_name
    output_path.write_bytes(uploaded_file.getbuffer())
    return output_path


def _cell_has_value(value) -> bool:
    return value is not None and str(value).strip() != ""


def _make_unique_columns(raw_columns: list) -> list[str]:
    columns = []
    seen = {}
    for idx, col in enumerate(raw_columns):
        name = str(col).strip() if _cell_has_value(col) else f"Unnamed_{idx + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        columns.append(name)
    return columns


def _rows_to_dataframe(rows: list[tuple]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()

    # Amazon bulk exports sometimes have workbook dimensions incorrectly set to A1.
    # We read rows manually and find the first row that looks like a real header.
    header_idx = 0
    for i, row in enumerate(rows[:25]):
        non_empty = sum(_cell_has_value(v) for v in row)
        if non_empty >= 2:
            header_idx = i
            break

    header = list(rows[header_idx])
    data_rows = list(rows[header_idx + 1 :])
    max_cols = max([len(header)] + [len(r) for r in data_rows]) if data_rows else len(header)
    header = header + [None] * (max_cols - len(header))
    columns = _make_unique_columns(header)

    normalized_rows = []
    for row in data_rows:
        padded = list(row) + [None] * (max_cols - len(row))
        normalized_rows.append(padded[:max_cols])

    df = pd.DataFrame(normalized_rows, columns=columns)
    df = df.dropna(axis=0, how="all").dropna(axis=1, how="all")
    return df


def read_xlsx_sheets(path: Path) -> dict[str, pd.DataFrame]:
    """Read every useful sheet from an Excel workbook.

    This intentionally uses openpyxl's reset_dimensions() path because Amazon bulk
    operation downloads often declare the worksheet dimension as A1 even when the
    sheet contains thousands of rows. pandas/openpyxl can otherwise return only one
    blank-looking column, which causes spend/ad sales to show as zero.
    """
    import openpyxl

    sheets: dict[str, pd.DataFrame] = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)

    for ws in wb.worksheets:
        try:
            ws.reset_dimensions()
        except Exception:
            pass

        rows = []
        for row in ws.iter_rows(values_only=True):
            if any(_cell_has_value(v) for v in row):
                rows.append(row)
        df = _rows_to_dataframe(rows)
        if not df.empty and len(df.columns) > 1:
            sheets[ws.title] = df
    try:
        wb.close()
    except Exception:
        pass
    return sheets


def _concat_with_source(sheets: dict[str, pd.DataFrame], names: list[str]) -> pd.DataFrame:
    frames = []
    for name in names:
        df = sheets[name].copy()
        df["Source Sheet"] = name
        frames.append(df)
    return pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()


def _has_columns(df: pd.DataFrame, required: list[str]) -> bool:
    cols = " | ".join(str(c).strip().lower() for c in df.columns)
    return all(req.lower() in cols for req in required)


def read_table(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in [".xlsx", ".xlsm", ".xls"]:
        sheets = read_xlsx_sheets(path)
        if not sheets:
            return pd.read_excel(path)

        # For Amazon bulk workbooks, the search term tabs are the safest source
        # for account-level ad spend/ad sales because campaign tabs can duplicate
        # performance across campaign, ad group, keyword, and ad rows.
        search_term_names = [name for name in sheets if "search term" in name.lower()]
        if search_term_names:
            return _concat_with_source(sheets, search_term_names)

        # Otherwise prefer the largest sheet that actually has performance columns.
        perf_names = [
            name for name, df in sheets.items()
            if _has_columns(df, ["Spend", "Sales"]) or _has_columns(df, ["Cost", "Sales"])
        ]
        if perf_names:
            return _concat_with_source(sheets, perf_names)

        # Fallback to the largest useful sheet.
        best_name = max(sheets, key=lambda n: len(sheets[n]) * max(1, len(sheets[n].columns)))
        return sheets[best_name]

    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in [".txt", ".tsv"]:
        try:
            return pd.read_csv(path, sep="\t")
        except Exception:
            return pd.read_csv(path)
    raise ValueError(f"Unsupported file type: {suffix}")


def list_uploaded_files(client_name: str | None = None) -> pd.DataFrame:
    ensure_directories()
    root = UPLOADS_DIR if not client_name else UPLOADS_DIR / safe_name(client_name)
    rows = []
    if not root.exists():
        return pd.DataFrame(columns=["client", "timestamp", "file", "path"])
    for file_path in root.rglob("*"):
        if file_path.is_file():
            parts = file_path.relative_to(UPLOADS_DIR).parts
            rows.append({
                "client": parts[0] if len(parts) > 0 else "",
                "timestamp": parts[1] if len(parts) > 1 else "",
                "file": file_path.name,
                "path": str(file_path),
            })
    return pd.DataFrame(rows).sort_values(["client", "timestamp", "file"], ascending=[True, False, True]) if rows else pd.DataFrame(columns=["client", "timestamp", "file", "path"])
