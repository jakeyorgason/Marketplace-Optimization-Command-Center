from __future__ import annotations
from pathlib import Path
import pandas as pd

APP_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = APP_DIR / "data"
CLIENTS_DIR = DATA_DIR / "clients"
UPLOADS_DIR = DATA_DIR / "uploads"
PROCESSED_DIR = DATA_DIR / "processed"
EXPORTS_DIR = DATA_DIR / "exports"
CLIENT_CONFIG_PATH = CLIENTS_DIR / "clients.csv"

for path in [CLIENTS_DIR, UPLOADS_DIR, PROCESSED_DIR, EXPORTS_DIR]:
    path.mkdir(parents=True, exist_ok=True)

CLIENT_COLUMNS = [
    "client_name", "target_tacos", "target_acos", "monthly_budget", "growth_mode",
    "forbidden_terms", "priority_asins", "notes"
]

def ensure_client_config() -> Path:
    if not CLIENT_CONFIG_PATH.exists():
        pd.DataFrame(columns=CLIENT_COLUMNS).to_csv(CLIENT_CONFIG_PATH, index=False)
    return CLIENT_CONFIG_PATH

def load_clients() -> pd.DataFrame:
    ensure_client_config()
    df = pd.read_csv(CLIENT_CONFIG_PATH)
    for col in CLIENT_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    return df[CLIENT_COLUMNS]

def save_clients(df: pd.DataFrame) -> None:
    ensure_client_config()
    out = df.copy()
    for col in CLIENT_COLUMNS:
        if col not in out.columns:
            out[col] = ""
    out[CLIENT_COLUMNS].to_csv(CLIENT_CONFIG_PATH, index=False)

def get_client_config(client_name: str) -> dict:
    df = load_clients()
    if df.empty or client_name not in set(df["client_name"].astype(str)):
        return {}
    row = df[df["client_name"].astype(str) == str(client_name)].iloc[0].to_dict()
    return row


def ensure_directories() -> None:
    """Compatibility helper for older loader imports."""
    for path in [CLIENTS_DIR, UPLOADS_DIR, PROCESSED_DIR, EXPORTS_DIR]:
        path.mkdir(parents=True, exist_ok=True)

