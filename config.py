#config.py
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Optional, Any
from dotenv import load_dotenv
from PySide6.QtGui import QColor

# =========================================================
# CHARGEMENT VARIABLES ENVIRONNEMENT
# =========================================================
base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
dotenv_path = os.path.join(base_path, "ATT70966.env")
load_dotenv(dotenv_path)

DATABASE_URL = (
    f"mysql+pymysql://"
    f"{os.getenv('DATABASE_USER')}:"
    f"{os.getenv('DATABASE_PASSWORD')}@"
    f"{os.getenv('DATABASE_HOST')}/"
    f"{os.getenv('DATABASE_NAME')}"
)

# =========================================================
# CONSTANTES METIER
# =========================================================
CQ_PHYSIQUE_PATIENT_ID = 3
PATIENT_ID_EXCLUDED_FROM_REMAINING = 4
SCANDIDOS_TOMO_SIGNATURE = "114358"
TOMO_DEFAULT_ENERGY = "6FFF"
LOOKBACK_DAYS = 14
MAX_WALK_DEPTH = 3

REFRESH_INTERVAL_MS = 180_000   # 3 minutes
BLINK_INTERVAL_MS = 500

DEVICE_TO_MACHINE = {
    "TOMO2": "TOMO2", "0210462": "TOMO4", "RADI7": "RADI7",
    "NOVA3": "NOVA3", "NOVA5": "NOVA5",
    "HALCYON6": "HALCYON6", "HALCYON8": "HALCYON8",
}

MACHINE_LABEL = {
    "TOMO2": "Tomo 2", "TOMO4": "Tomo 4", "RADI7": "Radi 7",
    "NOVA3": "Nova 3", "NOVA5": "Nova 5",
    "HALCYON6": "Halcyon 6", "HALCYON8": "Halcyon 8",
}

MACHINE_ORDER = {
    "TOMO2": 0, "NOVA3": 1, "TOMO4": 2, "NOVA5": 3,
    "HALCYON6": 4, "RADI7": 5, "HALCYON8": 6,
}

SCHEDULE_DEVICES = [
    "TOMO 2", "Tomo4", "RADI 7", "0210462", "0210471",
    "NOVA3", "NOVA5", "HALCYON6", "HALCYON8",
]
TOMO_DEVICES = ["TOMO 2", "Tomo4", "RADI 7", "0210462", "0210471"]

TAB_SPECS = [
    ("Tomo2", "tomo2", "Tomo 2"),
    ("Tomo4", "tomo4", "Tomo 4"),
    ("Tomo7", "tomo7", "Tomo 7"),
    ("Nova(s)", "nova", "Nova"),
]

WAITING_KEYS = ["Tomo 2", "Tomo 4", "Tomo 7", "Nova"]

# Colonnes du tableau
COL_STATUS = 0
COL_SELECT = 1
COL_MET = 2
COL_PATIENT = 3
COL_IPP = 4
COL_TASK = 5
COL_TASK_STATUS = 6
COL_PHYSICIST = 7
COL_NOTE = 8
COL_TIMEPLANNER = 9
COL_FOLDER = 10
COL_DICOM = 11
COL_PDF = 12
COL_ADDRESS = 13
COL_ENERGY = 14
COL_DURATION = 15
COL_LOCALISATION = 16
COLUMN_COUNT = 17

# =========================================================
# COULEURS ET STYLES
# =========================================================
PASTEL_NONE = QColor(232, 232, 236)     # ⚪ pas de date
PASTEL_LATE = QColor(255, 209, 209)     # 🔴 en retard
PASTEL_URGENT = QColor(255, 226, 183)   # 🟠 urgent
PASTEL_OK = QColor(210, 240, 214)       # 🟢 ok

APP_STYLESHEET = """
    QMainWindow { background-color: #F5F6F8; }
    QWidget { font-family: 'Segoe UI', 'Inter', sans-serif; font-size: 13px; color: #2B2D33; }
    QTabWidget::pane { border: 1px solid #E1E3E8; border-radius: 10px; background: #FFFFFF; top: -1px; }
    QTabBar::tab { background: #EDEEF2; color: #5A5D66; padding: 8px 18px; margin-right: 4px; border-top-left-radius: 8px; border-top-right-radius: 8px; font-weight: 600; }
    QTabBar::tab:selected { background: #FFFFFF; color: #2B2D33; border: 1px solid #E1E3E8; border-bottom: none; }
    QTabBar::tab:hover { background: #E4E6EC; }
    QTableWidget { background-color: #FFFFFF; gridline-color: #EDEEF2; border: none; selection-background-color: #D9E8FB; selection-color: #1A1C20; alternate-background-color: #FAFBFC; }
    QHeaderView::section { background-color: #F0F2F5; color: #4A4D55; padding: 8px; border: none; border-bottom: 2px solid #E1E3E8; font-weight: 600; }
    QTableWidget::item { padding: 4px; border-bottom: 1px solid #F0F1F4; }
    QPushButton { border-radius: 6px; padding: 6px 12px; }
    QStatusBar { background-color: #FFFFFF; border-top: 1px solid #E1E3E8; }
    QCheckBox::indicator { width: 15px; height: 15px; }
"""

# =========================================================
# DATA STRUCTURE
# =========================================================
@dataclass
class DashboardData:
    nova: list = field(default_factory=list)
    tomo2: list = field(default_factory=list)
    tomo4: list = field(default_factory=list)
    tomo7: list = field(default_factory=list)

    waiting_count: dict = field(default_factory=dict)
    waiting_details: dict = field(default_factory=dict)

    qa: list = field(default_factory=list)
    machine_schedule: list = field(default_factory=list)

    machines: dict = field(default_factory=dict)
    countdown: dict = field(default_factory=dict)
    remaining_today: dict = field(default_factory=dict)

    last_db_update: Optional[datetime] = None

# =========================================================
# UTILS
# =========================================================
def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

def normalize_device(device: Any) -> str:
    return str(device or "").upper().replace(" ", "").replace("-", "").strip()

def machine_key_from_device(device: Any) -> Optional[str]:
    return DEVICE_TO_MACHINE.get(normalize_device(device))

def machine_display(machine_key: Optional[str]) -> str:
    if not machine_key: return "?"
    return MACHINE_LABEL.get(machine_key, machine_key)

_HOLIDAY_CACHE: dict = {}
def french_holidays(year: int) -> set:
    if year in _HOLIDAY_CACHE: return _HOLIDAY_CACHE[year]
    a, b, c = year % 19, year // 100, year % 100
    d, e, f = b // 4, b % 4, (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month, day = (h + ll - 7 * m + 114) // 31, ((h + ll - 7 * m + 114) % 31) + 1
    easter = date(year, month, day)

    days = {
        date(year, 1, 1), easter + timedelta(days=1), date(year, 5, 1),
        date(year, 5, 8), easter + timedelta(days=39), easter + timedelta(days=50),
        date(year, 7, 14), date(year, 8, 15), date(year, 11, 1),
        date(year, 11, 11), date(year, 12, 25),
    }
    _HOLIDAY_CACHE[year] = days
    return days

def add_business_days(start_date: datetime, days: int) -> datetime:
    current, added = start_date, 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5 and current.date() not in french_holidays(current.year):
            added += 1
    return current

def day_bounds(reference: Optional[datetime] = None) -> tuple:
    reference = reference or datetime.now()
    start = reference.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)

def sort_by_met_start(table: list) -> list:
    return sorted(table, key=lambda row: (row["met_start"] is None, row["met_start"] or datetime.max))

def clean_cq_rows(rows: list) -> list:
    return [r for r in rows if str(r.get("task_status") or "").lower() not in ["completed", "draft"]]