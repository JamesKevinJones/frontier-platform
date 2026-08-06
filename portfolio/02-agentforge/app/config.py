from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RUNS_DIR = DATA_DIR / "runs"
MAX_COST_UNITS = float(os.getenv("MAX_COST_UNITS", "0.5"))
HOST_PORT = 8002
