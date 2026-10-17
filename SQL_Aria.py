# =========================================================
# Richard BOISSERON
# Dashboard de suivi des CQ
#
# Ce script se connecte à la base de données, extrait les tâches de réalisation des CQ prêtes
# et les appointments MET associés, puis affiche le tout dans une interface Qt avec un code
# couleur selon la proximité de l'appointment MET.
# Les données sont rafraîchies automatiquement toutes les 3 minutes pour rester à jour.
# La recherche des CQ Physique ou patient est effectuée 14 jours avant la MET et après la MET
#
# v2 — CORRECTIONS ANTI-FREEZE :
#   - Chargement SQL + réseau déporté dans un QThread (l'UI ne gèle plus)
#   - Le worker est créé UNE SEULE FOIS et relancé à chaque cycle
#   - Signal currentChanged connecté une seule fois
#   - Destruction réelle des anciens onglets
#   - Fenêtre glissante de 14 jours recalculée à chaque refresh
#   - Fermeture propre de l'application (closeEvent)
#
# v3 — OPTIMISATIONS & FIABILITE :
#   [P1] load_daily_qa() filtré par date DIRECTEMENT en SQL (-90% de lignes)
#   [P1] patients_validation() filtré par type de tâche en SQL (-70% de lignes)
#   [P2] check_dicom_and_pdf() : 1 seul os.walk au lieu de 3
#   [P2] Profondeur de os.walk limitée (iter_files)
#   [P2] joinedload inutile retiré de load_machine_schedule()
#   [P3] load_data() découpée en sous-fonctions
#   [P3] DashboardData (dataclass) remplace le tuple de 12 éléments
#   [P3] Normalisation des noms de machines (DEVICE_TO_MACHINE)
#        => corrige un bug : compute_real_cq_slot() ne détectait jamais les
#           chevauchements sur "TOMO 2" / "RADI 7" (clé non trouvée)
#   [P3] Constantes nommées (plus de patient_id == 3 / == 4 en dur)
#   [P3] Jours fériés français pris en compte dans add_business_days()
#   [P3] Remontée des erreurs réseau (NAS injoignable) dans un bandeau UI
#   [P3] Index de colonnes nommés (COL_*) + onglets pilotés par TAB_SPECS
#
# Note pour faire le .exe :
"""
Remove-Item dist -Recurse -Force
Remove-Item build -Recurse -Force
Remove-Item CQ_Dashboard.spec -Force

python -m PyInstaller SQL_Aria.py --clean --noconfirm --onedir --windowed --name CQ_Dashboard --icon "CQ_Dashboard.ico" --add-data "ATT70966.env;."
"""
# =========================================================

import os
import re
import sys
import traceback

from dataclasses import dataclass, field
from datetime import datetime, timedelta, date
from collections import defaultdict
from typing import Any, Iterator, Optional

from dotenv import load_dotenv

from sqlalchemy import create_engine, or_, func
from sqlalchemy.orm import sessionmaker, joinedload, Session

# Imports limités aux modèles réellement utilisés
from models import (
    Patients,
    Careplans,
    Tasks,
    Appointments,
    QueryLog,
)

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
    QLabel,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QHBoxLayout,
    QCheckBox,
    QMessageBox,
)

from PySide6.QtGui import QColor, QBrush, QFont
from PySide6.QtCore import QTimer, Qt, QThread, Signal

import pydicom


# =========================================================
# CONSTANTES METIER
# =========================================================

# Patients "virtuels" ARIA utilisés pour les créneaux de contrôle qualité
CQ_PHYSIQUE_PATIENT_ID = 3            # patient support des CQ Physique
PATIENT_ID_EXCLUDED_FROM_REMAINING = 4  # exclu du décompte "patients restants"

# Signature heuristique des fichiers ScandiDos Tomo (UID-like sans extension)
SCANDIDOS_TOMO_SIGNATURE = "114358"

# Energie par défaut des Tomo (pas de lecture RP sur ces machines)
TOMO_DEFAULT_ENERGY = "6FFF"

# Fenêtre glissante d'analyse
LOOKBACK_DAYS = 14

# Profondeur maximale d'exploration des dossiers réseau
MAX_WALK_DEPTH = 3

# Intervalles des timers (ms)
REFRESH_INTERVAL_MS = 180_000   # 3 minutes
BLINK_INTERVAL_MS = 500

# =========================================================
# MACHINES — table de correspondance unique
# Les clés sont les noms de device NORMALISES (majuscules, sans espace/tiret)
# =========================================================
DEVICE_TO_MACHINE = {
    "TOMO2": "TOMO2",
    "0210462": "TOMO4",
    "RADI7": "RADI7",
    "NOVA3": "NOVA3",
    "NOVA5": "NOVA5",
    "HALCYON6": "HALCYON6",
    "HALCYON8": "HALCYON8",
    # TODO : confirmer l'ID machine "0210471" auprès du service technique
}

MACHINE_LABEL = {
    "TOMO2": "Tomo 2",
    "TOMO4": "Tomo 4",
    "RADI7": "Radi 7",
    "NOVA3": "Nova 3",
    "NOVA5": "Nova 5",
    "HALCYON6": "Halcyon 6",
    "HALCYON8": "Halcyon 8",
}

MACHINE_ORDER = {
    "TOMO2": 0,
    "NOVA3": 1,
    "TOMO4": 2,
    "NOVA5": 3,
    "HALCYON6": 4,
    "RADI7": 5,
    "HALCYON8": 6,
}

# Devices interrogés pour le planning de fin de journée
SCHEDULE_DEVICES = [
    "TOMO 2", "Tomo4", "RADI 7",
    "0210462", "0210471",
    "NOVA3", "NOVA5",
    "HALCYON6", "HALCYON8",
]

# Devices Tomo (CQ patient Delta4)
TOMO_DEVICES = ["TOMO 2", "Tomo4", "RADI 7", "0210462", "0210471"]

# =========================================================
# ONGLETS — (titre, attribut DashboardData, clé "patients en attente")
# Ajouter une machine = ajouter une ligne ici, rien d'autre.
# =========================================================
TAB_SPECS = [
    ("Tomo2", "tomo2", "Tomo 2"),
    ("Tomo4", "tomo4", "Tomo 4"),
    ("Tomo7", "tomo7", "Tomo 7"),
    ("Nova(s)", "nova", "Nova"),
]

WAITING_KEYS = ["Tomo 2", "Tomo 4", "Tomo 7", "Nova"]

# =========================================================
# COLONNES DU TABLEAU
# =========================================================
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
# PALETTE PASTEL — raccordée aux pastilles d'urgence (⚪ 🔴 🟠 🟢)
# =========================================================
PASTEL_NONE = QColor(232, 232, 236)     # ⚪ pas de date
PASTEL_LATE = QColor(255, 209, 209)     # 🔴 en retard
PASTEL_URGENT = QColor(255, 226, 183)   # 🟠 urgent
PASTEL_OK = QColor(210, 240, 214)       # 🟢 ok

# =========================================================
# FEUILLE DE STYLE GLOBALE — look moderne, clair, aéré
# =========================================================
APP_STYLESHEET = """
    QMainWindow {
        background-color: #F5F6F8;
    }

    QWidget {
        font-family: 'Segoe UI', 'Inter', sans-serif;
        font-size: 13px;
        color: #2B2D33;
    }

    QTabWidget::pane {
        border: 1px solid #E1E3E8;
        border-radius: 10px;
        background: #FFFFFF;
        top: -1px;
    }

    QTabBar::tab {
        background: #EDEEF2;
        color: #5A5D66;
        padding: 8px 18px;
        margin-right: 4px;
        border-top-left-radius: 8px;
        border-top-right-radius: 8px;
        font-weight: 600;
    }

    QTabBar::tab:selected {
        background: #FFFFFF;
        color: #2B2D33;
        border: 1px solid #E1E3E8;
        border-bottom: none;
    }

    QTabBar::tab:hover {
        background: #E4E6EC;
    }

    QTableWidget {
        background-color: #FFFFFF;
        gridline-color: #EDEEF2;
        border: none;
        selection-background-color: #D9E8FB;
        selection-color: #1A1C20;
        alternate-background-color: #FAFBFC;
    }

    QHeaderView::section {
        background-color: #F0F2F5;
        color: #4A4D55;
        padding: 8px;
        border: none;
        border-bottom: 2px solid #E1E3E8;
        font-weight: 600;
    }

    QTableWidget::item {
        padding: 4px;
        border-bottom: 1px solid #F0F1F4;
    }

    QPushButton {
        border-radius: 6px;
        padding: 6px 12px;
    }

    QStatusBar {
        background-color: #FFFFFF;
        border-top: 1px solid #E1E3E8;
    }

    QCheckBox::indicator {
        width: 15px;
        height: 15px;
    }
"""


# =========================================================
# LOGGING SIMPLE (horodaté)
# =========================================================
def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")


# =========================================================
# Configuration for SQL database access
# =========================================================

base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))

dotenv_path = os.path.join(base_path, "ATT70966.env")
load_dotenv(dotenv_path)

db_user = os.getenv("DATABASE_USER")
db_password = os.getenv("DATABASE_PASSWORD")
db_host = os.getenv("DATABASE_HOST")
db_name = os.getenv("DATABASE_NAME")

DATABASE_URL = (
    f"mysql+pymysql://"
    f"{db_user}:"
    f"{db_password}@"
    f"{db_host}/"
    f"{db_name}"
)

# pool_pre_ping  : évite "MySQL server has gone away" sur une appli qui tourne la journée
# pool_recycle   : recycle les connexions avant le wait_timeout serveur
# pool_size / max_overflow : appli mono-utilisateur, 1 seule session à la fois
#                            => on évite les connexions fantômes côté serveur
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_size=2,
    max_overflow=0,
)

SessionLocal = sessionmaker(bind=engine)


# =========================================================
# STRUCTURE DE DONNEES RENVOYEE PAR LE WORKER
# (remplace l'ancien tuple de 12 éléments, illisible et fragile)
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
    network_errors: list = field(default_factory=list)


# =========================================================
# OUTILS GENERIQUES
# =========================================================

def normalize_device(device: Any) -> str:
    # =========================================================
    # Normalise un nom de device ARIA : "TOMO 2" / "Tomo-2" -> "TOMO2"
    # =========================================================
    return str(device or "").upper().replace(" ", "").replace("-", "").strip()


def machine_key_from_device(device: Any) -> Optional[str]:
    # =========================================================
    # Device ARIA -> clé machine canonique (TOMO2, TOMO4, ...)
    # =========================================================
    return DEVICE_TO_MACHINE.get(normalize_device(device))


def machine_display(machine_key: Optional[str]) -> str:
    if not machine_key:
        return "?"
    return MACHINE_LABEL.get(machine_key, machine_key)


def _easter_sunday(year: int) -> date:
    # =========================================================
    # Algorithme de Butcher (comput grégorien)
    # =========================================================
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451

    month = (h + ll - 7 * m + 114) // 31
    day = ((h + ll - 7 * m + 114) % 31) + 1

    return date(year, month, day)


_HOLIDAY_CACHE: dict = {}


def french_holidays(year: int) -> set:
    # =========================================================
    # Jours fériés français (métropole), mis en cache par année
    # =========================================================
    if year in _HOLIDAY_CACHE:
        return _HOLIDAY_CACHE[year]

    easter = _easter_sunday(year)

    days = {
        date(year, 1, 1),                   # Jour de l'an
        easter + timedelta(days=1),         # Lundi de Pâques
        date(year, 5, 1),                   # Fête du travail
        date(year, 5, 8),                   # Victoire 1945
        easter + timedelta(days=39),        # Ascension
        easter + timedelta(days=50),        # Lundi de Pentecôte
        date(year, 7, 14),                  # Fête nationale
        date(year, 8, 15),                  # Assomption
        date(year, 11, 1),                  # Toussaint
        date(year, 11, 11),                 # Armistice
        date(year, 12, 25),                 # Noël
    }

    _HOLIDAY_CACHE[year] = days

    return days


def add_business_days(start_date: datetime, days: int) -> datetime:
    # =========================================================
    # Exclut les week-ends ET les jours fériés dans le calcul
    # des MET prioritaires.
    # =========================================================
    current = start_date
    added = 0

    while added < days:
        current += timedelta(days=1)

        if current.weekday() >= 5:          # samedi / dimanche
            continue

        if current.date() in french_holidays(current.year):
            continue

        added += 1

    return current


def iter_files(root_path: str, max_depth: int = MAX_WALK_DEPTH) -> Iterator[tuple]:
    # =========================================================
    # Variante de os.walk avec profondeur limitée.
    # Evite de descendre indéfiniment dans les arborescences NAS.
    # Yield : (dossier_courant, nom_de_fichier)
    # =========================================================
    root_path = os.path.abspath(root_path)

    for current, dirs, files in os.walk(root_path):

        depth = current[len(root_path):].count(os.sep)

        if depth >= max_depth:
            dirs.clear()  # empêche la descente plus profonde

        for file_name in files:
            yield current, file_name


def sort_by_met_start(table: list) -> list:
    # =========================================================
    # Ordonne les patients par date de MET
    # =========================================================
    return sorted(
        table,
        key=lambda row: (
            row["met_start"] is None,
            row["met_start"] or datetime.max
        )
    )


def clean_cq_rows(rows: list) -> list:
    # =========================================================
    # Nettoie les données brutes (filtrage des tâches non pertinentes)
    # =========================================================
    return [
        r for r in rows
        if str(r.get("task_status") or "").lower() not in ["completed", "draft"]
    ]


def day_bounds(reference: Optional[datetime] = None) -> tuple:
    # =========================================================
    # Retourne (début de journée, début du lendemain)
    # =========================================================
    reference = reference or datetime.now()

    start = reference.replace(hour=0, minute=0, second=0, microsecond=0)

    return start, start + timedelta(days=1)


def create_centered_checkbox(checked: bool = True) -> QWidget:
    # =========================================================
    # Affiche la case à cocher de la colonne 'Select' au milieu de la cellule
    # =========================================================
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)

    checkbox = QCheckBox()
    checkbox.setChecked(checked)

    layout.addWidget(checkbox, alignment=Qt.AlignCenter)

    widget.checkbox = checkbox

    return widget


# =========================================================
# ACCES BASE DE DONNEES
# =========================================================

def print_query_log(session: Session) -> None:
    # =========================================================
    # Affiche les dernières requêtes ARIA ayant alimenté la DB
    # =========================================================
    entry = (
        session.query(QueryLog)
        .order_by(QueryLog.id.desc())
        .first()
    )

    if not entry:
        log("Aucune entrée dans query_log")
        return

    log(
        "QUERY LOG "
        f"| id={entry.id} "
        f"| task={entry.last_task_request} "
        f"| appt={entry.last_appointment_request} "
        f"| maj={entry.last_updated}"
    )


def get_last_database_update(session: Session) -> Optional[datetime]:
    # =========================================================
    # Récupère la date/heure de la dernière actualisation de la base
    # =========================================================
    return session.query(func.max(Tasks.last_updated)).scalar()


def load_today_patients_by_machine(session: Session) -> tuple:
    # =========================================================
    # Patients programmés aujourd'hui, regroupés par machine.
    # Retourne : (machines, compte_down, remaining_today)
    # =========================================================
    now = datetime.now()
    today_start, today_end = day_bounds(now)

    appointments = (
        session.query(Appointments)
        .join(Appointments.patient)
        .options(joinedload(Appointments.patient))
        .filter(
            Appointments.start_scheduled_period >= today_start,
            Appointments.start_scheduled_period < today_end,
        )
        .all()
    )

    machines = {key: [] for key in MACHINE_LABEL.keys()}

    for appt in appointments:

        machine = machine_key_from_device(appt.device)

        if machine is None:
            continue

        # =========================
        # EXCLUSION CQ
        # =========================
        service_type = str(appt.service_type or "").lower()
        last_name = str(appt.patient.family_name_official or "").upper()

        if "CQ HEBDOMADAIRE" in last_name:
            # =========================
            # GARDER CQ HEBDOMADAIRE
            # =========================
            pass

        elif "cq patient" in service_type or "cq physique" in service_type:
            continue

        patient = appt.patient

        machines[machine].append({
            "id": patient.id,
            "patient_id": appt.patient_id,
            "last_name": patient.family_name_official,
            "first_name": patient.given,

            "start": appt.start_scheduled_period,
            "end": appt.end_scheduled_period,
            "status": appt.status,
            "device": normalize_device(appt.device),
            "service_type": appt.service_type,
        })

    # ==========================================
    # Tri chronologique croissant
    # ==========================================
    for machine_patients in machines.values():
        machine_patients.sort(key=lambda p: p["start"] or datetime.max)

    # ==========================================
    # DECOMPTE DU NOMBRE DE PATIENTS AVANT LE CRENEAU CQ HEBDOMADAIRE
    # ==========================================
    compte_down = {}

    for machine, patients in machines.items():

        countdown = None
        index = 0

        for p in patients:

            status = str(p.get("status") or "").lower()
            last_name = str(p.get("last_name") or "").upper()
            service_type = str(p.get("service_type") or "").lower()

            # =========================
            # EXCLUSIONS
            # =========================
            if status not in ["arrived", "booked"]:
                continue

            if "TOP " in last_name:
                continue

            if (
                "consultation" in service_type
                or "interne" in service_type
                or "sang" in service_type
            ):
                continue

            # =========================
            # CQ HEBDOMADAIRE CHECK
            # =========================
            if "CQ HEBDOMADAIRE" in last_name:
                countdown = index
                break

            index += 1

        compte_down[machine] = "none" if countdown is None else countdown

    # =========================
    # NOMBRE DE PATIENTS RESTANTS AVANT LA FIN DE LA JOURNEE
    # =========================
    remaining_today = {}

    for machine, patients in machines.items():

        count = 0

        for p in patients:

            if p.get("id") == PATIENT_ID_EXCLUDED_FROM_REMAINING:
                continue

            service_type = str(p.get("service_type") or "").lower()

            if (
                "consultation" in service_type
                or "interne" in service_type
                or "sang" in service_type
            ):
                continue

            status = str(p.get("status") or "").lower()

            if status in ["cancelled", "fulfilled"]:
                continue

            if p["start"] and p["start"] >= now:
                count += 1

        remaining_today[machine] = count

    return machines, compte_down, remaining_today


def load_machine_schedule(session: Session) -> list:
    # =========================================================
    # Appointments de type 'Implant' programmés aujourd'hui sur les machines
    # concernées, pour afficher les horaires de fin d'activité de la journée.
    #
    # [P2] joinedload(Appointments.patient) retiré : l'objet patient n'était
    #      jamais utilisé en aval.
    # =========================================================
    today_start, today_end = day_bounds()

    rows = []

    opening_closing = (
        session.query(Appointments)
        .filter(
            Appointments.service_type.ilike("Implant"),
            Appointments.device.in_(SCHEDULE_DEVICES),
            Appointments.start_scheduled_period >= today_start,
            Appointments.start_scheduled_period < today_end,
        )
        .all()
    )

    for appt in opening_closing:

        rows.append({
            "machine_key": machine_key_from_device(appt.device),
            "machine_raw": appt.device,
            "start": appt.start_scheduled_period,
            "end": appt.end_scheduled_period,
            "status": appt.status,
            "note": appt.comment or "",
        })

    log(f"Planning machines (Implant) : {len(rows)} créneau(x)")

    return rows


def load_daily_qa(session: Session) -> list:
    # =========================================================
    # Créneaux CQ du jour.
    #
    # [P1] CORRECTION MAJEURE : le filtre sur la date est désormais fait
    #      en SQL. Auparavant, toute l'historique des CQ était chargée en
    #      mémoire puis filtrée en Python (plusieurs dizaines de milliers
    #      de lignes pour n'en conserver que 2 ou 3).
    # =========================================================
    today_start, today_end = day_bounds()

    appointments = (
        session.query(Appointments)
        .options(joinedload(Appointments.patient))
        .filter(
            or_(
                # NOVA
                Appointments.service_type.ilike("%cq physique%"),

                # TOMO
                (
                    Appointments.service_type.ilike("%cq patient%")
                    &
                    Appointments.device.in_(TOMO_DEVICES)
                ),
            ),
            ~Appointments.device.ilike("HALCYON%"),

            # ---- filtre temporel (nouveau) ----
            Appointments.start_scheduled_period >= today_start,
            Appointments.start_scheduled_period < today_end,

            # ---- statuts exploitables uniquement ----
            func.lower(Appointments.status).in_(["booked", "arrived"]),
        )
        .all()
    )

    log(f"CQ appointments du jour : {len(appointments)}")

    qa_rows = []

    for appt in appointments:

        patient = appt.patient

        if patient is None:
            continue

        machine_key = machine_key_from_device(appt.device)

        qa_rows.append({
            # Machine : clé canonique (pour les recoupements) + libellé (affichage)
            "machine_key": machine_key,
            "machine_label": machine_display(machine_key) if machine_key
            else str(appt.device or "?"),
            "machine_raw": appt.device,

            "task_display_focus": appt.service_type,
            "task_status": appt.status,
            "task_note": appt.comment or "",

            "met_start": appt.start_scheduled_period,
            "met_end": appt.end_scheduled_period,

            "last_name": patient.family_name_official,
            "first_name": patient.given,
            "service_type": appt.service_type,
            "ipp": patient.ipp,
            "patient_id": appt.patient_id,
            "family_name_official": patient.family_name_official,

            "existing_folder": False,
            "existing_dicom": False,
            "existing_pdf": False,
            "folder_path": "",
        })

    return qa_rows


def filter_today_qa(qa_rows: list) -> list:
    # =========================================================
    # Ne conserve que les créneaux CQ en cours ou à venir.
    # (Le filtrage date/statut est maintenant fait en SQL.)
    # =========================================================
    now = datetime.now()

    filtered = []

    for row in qa_rows:

        met_start = row.get("met_start")
        met_end = row.get("met_end")

        if not met_start:
            continue

        # =========================
        # FILTRE TYPE DE CQ
        # =========================
        family_name = str(row.get("family_name_official") or "").upper()

        if (
            "CQ HEBDOMADAIRE" not in family_name
            and row.get("patient_id") != CQ_PHYSIQUE_PATIENT_ID
        ):
            continue

        # =========================
        # CRENEAU EN COURS OU A VENIR
        # =========================
        is_future = met_start >= now
        is_current = met_start <= now and met_end and now <= met_end

        if is_future or is_current:
            filtered.append(row)

    filtered.sort(key=lambda x: x["met_start"])

    log(f"Créneaux CQ en cours / à venir : {len(filtered)}")

    return filtered


def load_cq_patient_ids_today(session: Session) -> set:
    # =========================================================
    # Patients ayant un CQ Patient programmé dans Timeplanner aujourd'hui
    # =========================================================
    today_start, today_end = day_bounds()

    appts = (
        session.query(Appointments.patient_id)
        .filter(
            Appointments.start_scheduled_period >= today_start,
            Appointments.start_scheduled_period < today_end,
            Appointments.service_type.ilike("%cq patient%"),
            ~func.lower(Appointments.status).in_(["cancelled", "fulfilled"]),
        )
        .all()
    )

    ids = {row[0] for row in appts if row[0] is not None}

    log(f"CQ patient programmés aujourd'hui : {len(ids)}")

    return ids


def load_cq_ready_patients(session: Session, since: datetime) -> list:
    # =========================================================
    # Patients ayant des tâches 'réalisation des CQ' prêtes (< 14 jours)
    # =========================================================
    patients = (
        session.query(Patients)
        .join(Patients.careplans)
        .join(Careplans.tasks)
        .filter(
            or_(
                Tasks.display_focus.ilike("réalisation du cq%"),
                Tasks.display_focus.ilike("réalisation des cq%"),
                Tasks.display_focus.ilike("replanif%"),
            ),
            Tasks.last_updated >= since,
            Tasks.status.ilike("ready"),
        )
        .options(
            joinedload(Patients.appointments),
            joinedload(Patients.careplans).joinedload(Careplans.tasks),
        )
        .distinct()
        .all()
    )

    log(f"Patients avec CQ prêt : {len(patients)}")

    return patients


def load_cq_imminent_patients(session: Session, since: datetime) -> list:
    # =========================================================
    # Patients avec :
    #   - Validation Médicale de la dosimétrie = completed / ready / in-progress
    #   - Finalisation du dossier = draft
    # => CQ imminent
    #
    # [P1] CORRECTION MAJEURE : le filtre sur le type de tâche est fait en SQL.
    #      Auparavant, TOUS les patients ayant n'importe quelle tâche modifiée
    #      dans les 14 derniers jours étaient chargés (avec eager load complet
    #      des careplans et tâches), puis filtrés en Python.
    # =========================================================
    candidates = (
        session.query(Patients)
        .join(Patients.careplans)
        .join(Careplans.tasks)
        .filter(
            Tasks.last_updated >= since,
            or_(
                Tasks.display_focus.ilike("validation médicale de la dosimétrie%"),
                Tasks.display_focus.ilike("finalisation du dossier%"),
            ),
        )
        .options(
            joinedload(Patients.appointments),
            joinedload(Patients.careplans).joinedload(Careplans.tasks),
        )
        .distinct()
        .all()
    )

    log(f"Patients potentiellement éligibles CQ imminent : {len(candidates)}")

    rows = []
    seen_patient_ids = set()

    for patient in candidates:

        if patient.id in seen_patient_ids:
            continue

        all_tasks = [t for cp in patient.careplans for t in cp.tasks]

        # ==========================================
        # Validation Médicale de la dosimétrie
        # ==========================================
        validation_tasks = [
            t for t in all_tasks
            if (
                t.display_focus
                and t.display_focus.lower().startswith(
                    "validation médicale de la dosimétrie"
                )
                and str(t.status or "").lower() in ["completed", "in-progress", "ready"]
                and t.last_updated
                and t.last_updated >= since
            )
        ]

        if not validation_tasks:
            continue

        latest_validation = max(validation_tasks, key=lambda x: x.last_updated)
        validation_date = latest_validation.last_updated

        # ==========================================
        # Finalisation du dossier = draft
        # ==========================================
        finalisation_tasks = [
            t for t in all_tasks
            if (
                t.display_focus
                and t.display_focus.lower().startswith("finalisation du dossier")
                and str(t.status or "").lower() == "draft"
                and t.last_updated
                and t.last_updated >= since
            )
        ]

        if not finalisation_tasks:
            continue

        # ==========================================
        # Réalisation du TDM (scanner de simulation)
        # ==========================================
        tdm_tasks = [
            t for t in all_tasks
            if (
                t.display_focus
                and t.display_focus.lower().startswith("scanner de simulation")
                and t.last_updated
            )
        ]

        tdm_date = (
            max(tdm_tasks, key=lambda x: x.last_updated).last_updated
            if tdm_tasks else None
        )

        # ==========================================
        # Appeler patient (status)
        # ==========================================
        appel_tasks = [
            t for t in all_tasks
            if (
                t.display_focus
                and t.display_focus.lower().startswith("appeler patient")
                and t.last_updated
                and t.last_updated >= since
            )
        ]

        appel_patient_status = (
            max(appel_tasks, key=lambda x: x.last_updated).status
            if appel_tasks else None
        )

        # ==========================================
        # MET (la plus récente après la validation médicale)
        # ==========================================
        met_appointments = [
            appt for appt in patient.appointments
            if (
                appt.service_type
                and appt.service_type.upper().startswith("MET")
                and appt.start_scheduled_period
                and appt.start_scheduled_period > validation_date
                and str(appt.status or "").lower() != "cancelled"
            )
        ]

        met_start = (
            max(met_appointments, key=lambda x: x.start_scheduled_period)
            .start_scheduled_period
            if met_appointments else None
        )

        # ==========================================
        # Réalisation dosi (nom exact de la tâche)
        # ==========================================
        realisation_dosi_task_name = None

        for t in all_tasks:
            if (
                t.display_focus
                and "réalisation dosi" in t.display_focus.lower()
                and t.last_updated
                and t.last_updated >= since
            ):
                realisation_dosi_task_name = t.display_focus
                break

        seen_patient_ids.add(patient.id)

        workflow_title = patient.careplans[-1].title if patient.careplans else None

        rows.append({
            "ipp": patient.ipp,
            "last_name": patient.family_name_official,
            "first_name": patient.given,
            "patient_id": patient.id,
            "tdm_date": tdm_date,
            "realisation_dosi_task": realisation_dosi_task_name,
            "Workflow": workflow_title,
            "appel_patient_status": appel_patient_status,
            "validation_date": validation_date,
            "met_start": met_start,
            "va_tomber": bool(met_start),
        })

    log(f"Patients CQ imminent trouvés : {len(rows)}")

    return rows


def split_waiting_patients_by_machine(rows: list) -> tuple:
    # =========================================================
    # Classement des patients en attente par machine (depuis le workflow)
    # =========================================================
    counts = defaultdict(int)
    details = {key: [] for key in WAITING_KEYS}

    for row in rows:

        workflow = row.get("Workflow")

        if not workflow:
            continue

        workflow_lower = workflow.lower()

        if "tomo 2" in workflow_lower:
            machine = "Tomo 2"
        elif "tomo 4" in workflow_lower:
            machine = "Tomo 4"
        elif "tomo 7" in workflow_lower:
            machine = "Tomo 7"
        else:
            machine = "Nova"

        counts[machine] += 1

        details[machine].append({
            "ipp": row["ipp"],
            "last_name": row["last_name"],
            "first_name": row["first_name"],
            "tdm_date": row["tdm_date"],
            "task": row["realisation_dosi_task"],
            "called": row["appel_patient_status"],
            "validation_date": row["validation_date"],
            "MET": row["met_start"],
            "workflow": row["Workflow"],
            "va_tomber": row["va_tomber"],
        })

    log("Répartition des patients en préparation : " + ", ".join(
        f"{m}={c}" for m, c in counts.items()
    ) or "aucune")

    return dict(counts), details


def build_cq_rows(patients: list, cq_patient_ids_today: set) -> list:
    # =========================================================
    # Transforme les patients "CQ prêt" en lignes de tableau
    # =========================================================
    rows = []

    for patient in patients:

        # =========================================================
        # MET ASSOCIÉS AU PATIENT
        # =========================================================
        met_appointments = [
            appt for appt in patient.appointments
            if (
                appt.service_type
                and appt.service_type.upper().startswith("MET")
                and appt.start_scheduled_period
                and str(appt.status or "").lower() != "cancelled"
            )
        ]

        met_appt = (
            max(met_appointments, key=lambda x: x.start_scheduled_period)
            if met_appointments else None
        )

        # =========================================================
        # 'FINALISATION DOSSIER' => PHYSICIEN / DOSIMETRISTE
        # =========================================================
        finalisation_tasks = [
            t for cp in patient.careplans
            for t in cp.tasks
            if (
                t.display_focus
                and t.display_focus.lower().startswith("finalisation du dossier")
            )
        ]

        physicist = None

        if finalisation_tasks:
            latest = max(
                finalisation_tasks,
                key=lambda x: x.last_updated or datetime.min
            )
            physicist = latest.recipient or latest.recipient_id

        cq_patient_today = patient.id in cq_patient_ids_today

        for careplan in patient.careplans:

            for task in careplan.tasks:

                focus = (task.display_focus or "").lower()

                if not (
                    focus.startswith("réalisation du cq")
                    or focus.startswith("réalisation des cq")
                    or focus.startswith("replanif")
                ):
                    continue

                if "cancelled" in str(task.status or "").lower():
                    continue

                rows.append({
                    # PATIENT
                    "ipp": patient.ipp,
                    "last_name": patient.family_name_official,
                    "first_name": patient.given,
                    "physicist": physicist,

                    # CQ PATIENT
                    "Timeplanner": cq_patient_today,
                    "patient_id": patient.id,

                    # TASK
                    "task_display_focus": task.display_focus,
                    "task_code": task.code,
                    "task_status": task.status,
                    "task_note": task.note,

                    # CAREPLAN
                    "careplan_id": careplan.id,

                    # APPOINTMENT MET
                    "met_service_type": met_appt.service_type if met_appt else None,
                    "met_start": met_appt.start_scheduled_period if met_appt else None,
                    "met_status": met_appt.status if met_appt else None,
                })

    return rows


def split_cq_rows_by_machine(rows: list) -> tuple:
    # =========================================================
    # Répartition des CQ par machine depuis le libellé de la tâche
    # =========================================================
    nova, tomo2, tomo4, tomo7 = [], [], [], []

    for row in rows:

        focus = row.get("task_display_focus")

        if not focus:
            continue

        focus_lower = focus.lower()

        if "tomo 2" in focus_lower:
            tomo2.append(row)

        elif "tomo 4" in focus_lower:
            tomo4.append(row)

        elif "tomo 7" in focus_lower:
            tomo7.append(row)

        elif "ruby" in focus_lower or "octa4d" in focus_lower:
            nova.append(row)

    log(
        f"Répartition CQ : Nova={len(nova)} "
        f"Tomo2={len(tomo2)} Tomo4={len(tomo4)} Tomo7={len(tomo7)}"
    )

    return nova, tomo2, tomo4, tomo7


# =========================================================
# EXPLORATION RESEAU
# =========================================================

def _read_rp_energy(file_path: str) -> Optional[str]:
    # =========================================================
    # Lecture de l'énergie nominale dans un fichier RTPLAN (RP)
    # =========================================================
    try:
        ds = pydicom.dcmread(file_path, stop_before_pixels=True)

        if not getattr(ds, "BeamSequence", None):
            return None

        beam = ds.BeamSequence[0]

        energy_val = str(beam.ControlPointSequence[0].NominalBeamEnergy)

        mode = "X"

        try:
            fluence_id = beam.PrimaryFluenceModeSequence[0].FluenceModeID

            if fluence_id and "FFF" in str(fluence_id).upper():
                mode = "FFF"

        except Exception:
            mode = "X"

        return f"{energy_val}{mode}"

    except Exception as exc:
        log(f"[RP READ ERROR] {file_path} -> {exc}")
        return None


def check_dicom_and_pdf(folder_path: Optional[str], read_energy: bool = False) -> tuple:
    # =========================================================
    # [P2] OPTIMISATION MAJEURE : un SEUL parcours du dossier réseau
    #      (auparavant : 3 os.walk complets par patient).
    #
    # Retourne : (dicom_ok, pdf_ok, pdf_date, energy)
    #
    # Note : on retient désormais le PDF le PLUS RECENT (et non le premier
    #        trouvé), ce qui est plus pertinent pour un rapport de CQ.
    # =========================================================
    if not folder_path:
        return False, False, None, None

    dcm_date = None
    energy = None
    latest_pdf_date = None

    try:
        for root, file_name in iter_files(folder_path):

            lower = file_name.lower()
            full_path = os.path.join(root, file_name)

            # =========================
            # DICOM
            # =========================
            if lower.endswith(".dcm"):

                if dcm_date is None:
                    try:
                        dcm_date = datetime.fromtimestamp(
                            os.path.getmtime(full_path)
                        )
                    except OSError:
                        pass

                if read_energy and energy is None and "RP" in file_name.upper():
                    energy = _read_rp_energy(full_path)

            # =========================
            # PDF
            # =========================
            elif lower.endswith(".pdf"):

                try:
                    pdf_date = datetime.fromtimestamp(os.path.getmtime(full_path))
                except OSError:
                    continue

                if latest_pdf_date is None or pdf_date > latest_pdf_date:
                    latest_pdf_date = pdf_date

        # =========================
        # SYNTHESE
        # =========================
        if dcm_date is None:
            return False, False, None, None

        pdf_ok = bool(
            latest_pdf_date
            and latest_pdf_date.date() >= dcm_date.date()
        )

        return True, pdf_ok, (latest_pdf_date if pdf_ok else None), energy

    except Exception as exc:
        log(f"[DICOM/PDF ERROR] {folder_path} -> {exc}")
        return False, False, None, None


# =========================
# LECTURE FICHIER SCANDIDOS (TOMO)
# =========================

def extract_tokens(raw_bytes: bytes) -> list:

    text = raw_bytes.decode("latin-1", errors="ignore")

    # 1. remplacer NULL + contrôles
    text = text.replace("\x00", " ")

    # 2. garder uniquement les chunks exploitables
    return re.findall(r"[A-Za-z0-9_.\-\/\^]+", text)


def is_tomo_scandidos_file(file_name: str) -> bool:
    """
    Détecte le fichier ScandiDos Tomo (sans extension standard)
    """
    name, ext = os.path.splitext(file_name)

    if ext.lower() in [".dcm", ".pdf", ".xml", ".json"]:
        return False

    # heuristique Tomo UID-like
    if name.count(".") >= 5 and name.replace(".", "").isdigit():
        return True

    # fallback : parfois pas totalement numérique
    if SCANDIDOS_TOMO_SIGNATURE in name:
        return True

    return False


def parse_tomo_scandi_tokens(tokens: list) -> tuple:
    # =========================================================
    # Extraction (plan, fraction, durée) depuis les tokens ScandiDos
    # =========================================================
    plan = None
    fraction = None
    duration = None

    for i, token in enumerate(tokens):

        # =========================
        # FRACTION
        # =========================
        if "_QA" in token:

            parts = []
            j = i

            # Remonte jusqu'au séparateur "0".
            # La boucle sort AVANT d'insérer tokens[j] : ce token est donc
            # ajouté juste après (comportement identique à la v2).
            while j > 0 and tokens[j - 1] != "0":

                current = (
                    tokens[j]
                    .replace("_QA", "")
                    .replace("_OK", "")
                    .strip()
                )

                parts.insert(0, current)
                j -= 1

            if j > 0:
                first = (
                    tokens[j]
                    .replace("_QA", "")
                    .replace("_OK", "")
                    .strip()
                )

                if first:
                    parts.insert(0, first)

            fraction = " ".join(parts)

        # =========================
        # PLAN (heuristique)
        # =========================
        if plan is None:
            if "split" in token.lower() or "course" in token.lower():
                plan = token

        # =========================
        # VERIFICATION BLOCK -> durée
        # =========================
        if token == "VERIFICATION":

            for j in range(i, min(i + 20, len(tokens))):

                if re.match(r"\d+\.\d{4,}", tokens[j]):
                    duration = float(tokens[j])
                    break

    return plan, fraction, duration


def read_tomo_scandidos_file(folder_path: Optional[str]) -> tuple:

    if not folder_path:
        return None, None, None

    try:
        for root, file_name in iter_files(folder_path):

            if not is_tomo_scandidos_file(file_name):
                continue

            with open(os.path.join(root, file_name), "rb") as handle:
                raw = handle.read()

            return parse_tomo_scandi_tokens(extract_tokens(raw))

    except Exception as exc:
        log(f"[SCANDIDOS READ ERROR] {folder_path} -> {exc}")

    return None, None, None


def check_existing_folders(nova: list, tomo2: list, tomo4: list, tomo7: list) -> list:
    # =========================================================
    # Recherche dans les dossiers réseaux IUCT l'existence de dossiers patients
    # et vérifie la présence de fichiers DICOM / PDF.
    #
    # ⚠️ Fonction lourde (accès réseau) : exécutée dans le thread de travail.
    #
    # Retourne la liste des erreurs réseau rencontrées, afin de prévenir
    # l'utilisateur plutôt que d'afficher silencieusement des ❌ partout.
    # =========================================================
    errors = []

    # =========================
    # NETWORK PATHS TOMO
    # =========================
    path_tomo2 = r"\\nasdata1\TOMO\02 - CQ Patients\0_DELTA4\TOMO2"
    path_tomo4 = r"\\nasdata1\TOMO\02 - CQ Patients\0_DELTA4\TOMO4"
    path_tomo7 = r"\\nasdata1\TOMO\02 - CQ Patients\0_DELTA4\RADI7"

    # =========================
    # NETWORK PATHS NOVA
    # =========================
    path_nova = {
        "EC": r"\\srv015\Radiophysique_acquisition\Patients_stereo_EC_IUC",
        "Hyperarc": r"\\srv015\Radiophysique_acquisition\Patients_stereo_Hyperarc",
        "RA": r"\\srv015\Radiophysique_acquisition\Patients_RA",
    }

    current_year = str(datetime.now().year)

    # =========================
    # CACHE NIVEAU 1
    # =========================
    def build_cache(path: str, subfolder: Optional[str] = None) -> dict:

        target = os.path.join(path, subfolder) if subfolder else path
        cache = {}

        try:
            with os.scandir(target) as entries:
                for entry in entries:
                    if entry.is_dir():
                        cache[entry.name] = entry.path

        except Exception as exc:
            message = f"{target} -> {exc}"
            log(f"[CACHE ERROR] {message}")
            errors.append(message)

        return cache

    # =========================
    # BUILD CACHES
    # =========================
    cache_tomo2 = build_cache(path_tomo2)
    cache_tomo4 = build_cache(path_tomo4)
    cache_tomo7 = build_cache(path_tomo7)

    cache_ec = build_cache(path_nova["EC"], current_year)
    cache_hyperarc = build_cache(path_nova["Hyperarc"], current_year)
    cache_ra = build_cache(path_nova["RA"], current_year)

    # =========================
    # FIND FOLDER IN CACHE
    # =========================
    def find_folder(cache: dict, ipp: Any) -> tuple:

        if not ipp:
            return None, None

        ipp = str(ipp).strip()

        for folder_name, folder_path in cache.items():
            if ipp in folder_name:
                return folder_name, folder_path

        return None, None

    # =========================
    # PROCESS TOMO
    # =========================
    def process_tomo(table: list, cache: dict) -> None:

        for row in table:

            folder_name, folder_path = find_folder(cache, row.get("ipp"))

            dicom_ok, pdf_ok, pdf_date, _ = check_dicom_and_pdf(folder_path)
            _, fraction, duration = read_tomo_scandidos_file(folder_path)

            row["existing_folder"] = folder_name is not None
            row["existing_dicom"] = dicom_ok
            row["existing_pdf"] = pdf_ok
            row["pdf_date"] = pdf_date

            row["tomo_fraction"] = fraction
            row["tomo_duration_min"] = duration
            row["tomo_duration_sec"] = duration * 60 if duration else None

            # Les Tomo fonctionnent toutes en 6 MV FFF
            row["energy"] = TOMO_DEFAULT_ENERGY

            row["folder_name"] = folder_name
            row["folder_path"] = folder_path

    # =========================
    # PROCESS NOVA
    # =========================
    def process_nova(table: list) -> None:

        sources = [
            ("EC", cache_ec),
            ("Hyperarc", cache_hyperarc),
            ("RA", cache_ra),
        ]

        for row in table:

            ipp = row.get("ipp")

            folder_name = None
            folder_path = None
            machine = None

            for label, cache in sources:

                folder_name, folder_path = find_folder(cache, ipp)

                if folder_name:
                    machine = label
                    break

            dicom_ok, pdf_ok, pdf_date, energy = check_dicom_and_pdf(
                folder_path,
                read_energy=True
            )

            row["existing_folder"] = folder_name is not None
            row["existing_dicom"] = dicom_ok
            row["existing_pdf"] = pdf_ok
            row["pdf_date"] = pdf_date
            row["energy"] = energy

            row["folder_name"] = folder_name
            row["folder_path"] = folder_path
            row["nova_machine"] = machine

    # =========================
    # EXECUTION
    # =========================
    log("Exploration réseau : Tomo2")
    process_tomo(tomo2, cache_tomo2)

    log("Exploration réseau : Tomo4")
    process_tomo(tomo4, cache_tomo4)

    log("Exploration réseau : Tomo7")
    process_tomo(tomo7, cache_tomo7)

    log("Exploration réseau : Nova")
    process_nova(nova)

    return errors


# =========================================================
# POINT D'ENTREE DU CHARGEMENT (exécuté dans le worker)
# =========================================================

def load_data() -> DashboardData:
    # =========================================================
    # ⚠️ FONCTION LOURDE : exécutée uniquement dans le thread de travail.
    # Découpée en sous-fonctions pour rester lisible et testable.
    # =========================================================

    since = datetime.now() - timedelta(days=LOOKBACK_DAYS)

    # Test de la connexion
    try:
        with engine.connect():
            log("Connexion base de données OK")

    except Exception:
        log("ERREUR DE CONNEXION A LA BASE DE DONNEES")
        raise

    session = SessionLocal()

    try:
        print_query_log(session)

        # ---- CQ Patient programmés aujourd'hui (Timeplanner) ----
        cq_patient_ids_today = load_cq_patient_ids_today(session)

        # ---- Patients avec tâches CQ prêtes ----
        ready_patients = load_cq_ready_patients(session, since)

        # ---- Patients CQ imminent ----
        imminent_rows = load_cq_imminent_patients(session, since)
        waiting_count, waiting_details = split_waiting_patients_by_machine(imminent_rows)

        # ---- Construction des lignes CQ ----
        cq_rows = build_cq_rows(ready_patients, cq_patient_ids_today)
        cq_rows = clean_cq_rows(cq_rows)

        nova, tomo2, tomo4, tomo7 = split_cq_rows_by_machine(cq_rows)

        # ---- Tri par priorité de MET ----
        nova = sort_by_met_start(nova)
        tomo2 = sort_by_met_start(tomo2)
        tomo4 = sort_by_met_start(tomo4)
        tomo7 = sort_by_met_start(tomo7)

        # ---- QA + planning machines ----
        qa = filter_today_qa(load_daily_qa(session))
        machine_schedule = load_machine_schedule(session)

        machines, countdown, remaining_today = load_today_patients_by_machine(session)

        last_db_update = get_last_database_update(session)

    finally:
        session.close()

    # ---- Exploration réseau (hors session SQL : évite de la garder ouverte) ----
    network_errors = check_existing_folders(nova, tomo2, tomo4, tomo7)

    return DashboardData(
        nova=nova,
        tomo2=tomo2,
        tomo4=tomo4,
        tomo7=tomo7,
        waiting_count=waiting_count,
        waiting_details=waiting_details,
        qa=qa,
        machine_schedule=machine_schedule,
        machines=machines,
        countdown=countdown,
        remaining_today=remaining_today,
        last_db_update=last_db_update,
        network_errors=network_errors,
    )


# =========================================================
# WORKER THREAD — exécute load_data() en arrière-plan
# =========================================================
class DataLoaderWorker(QThread):

    data_loaded = Signal(object)
    error_occurred = Signal(str)

    def run(self) -> None:
        try:
            self.data_loaded.emit(load_data())

        except Exception as exc:
            self.error_occurred.emit(
                "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            )


# =========================================================
# WIDGET REPLIABLE
# =========================================================
class CollapsibleWidget(QWidget):

    def __init__(self, title: str = "Titre"):
        super().__init__()

        self._title = title

        self.toggle_button = QPushButton(f"▶ {title}")
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(False)

        self.toggle_button.setStyleSheet("""
            QPushButton {
                text-align: left;
                padding: 9px 12px;
                font-weight: 600;
                background-color: #EEF3EE;
                color: #355E3B;
                border: 1px solid #CFE3CF;
                border-radius: 8px;
            }

            QPushButton:hover {
                background-color: #E3EEE3;
            }
        """)

        self.content = QLabel("")
        self.content.setVisible(False)  # fermé par défaut

        self.content.setStyleSheet("""
            QLabel {
                padding: 10px;
                background-color: #FFFFFF;
                border-left: 1px solid #CFE3CF;
                border-right: 1px solid #CFE3CF;
                border-bottom: 1px solid #CFE3CF;
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toggle_button)
        layout.addWidget(self.content)

        self.toggle_button.clicked.connect(self.toggle)

    def set_title(self, title: str) -> None:
        # =========================================================
        # Met à jour le titre sans manipuler la flèche par remplacement
        # de chaîne (plus robuste que str.replace)
        # =========================================================
        self._title = title

        arrow = "▼" if self.toggle_button.isChecked() else "▶"
        self.toggle_button.setText(f"{arrow} {title}")

    def toggle(self) -> None:
        self.content.setVisible(self.toggle_button.isChecked())
        self.set_title(self._title)


# =========================================================
# FENETRE PRINCIPALE
# =========================================================
class MainWindow(QMainWindow):

    TIME_RULES = {
        "tomo": {
            "fixed": 7,     # montage + démontage
            "per_case": 12  # mesure + exploitation
        },
        "ruby": {
            "fixed": 8,     # montage + démontage + calibrage
            "per_case": 12  # mesure + exploitation
        },
        "octa": {
            "fixed": 11,    # montage + démontage + calibrage
            "per_case": 9   # mesure + exploitation
        }
    }

    DEFAULT_TIME = {
        "fixed": 10,
        "per_case": 15
    }

    # Par patient Tomo sélectionné : 3 min de manipulation + le beam-on
    TOMO_PER_CASE_OVERHEAD = 3

    # =====================================================
    # INIT
    # =====================================================
    def __init__(self):
        super().__init__()

        self.setWindowTitle("CQ Dashboard")
        self.resize(1400, 500)

        self.now = datetime.now()
        self.limit = add_business_days(self.now, 2)

        self.db_error_shown = False
        self.db_alert_level = "unknown"

        self.waiting_count = {}
        self.waiting_details = {}

        # =========================
        # ROOT WIDGET
        # =========================
        root = QWidget()
        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(12, 12, 12, 8)
        root_layout.setSpacing(8)

        # =========================
        # QA LABEL
        # =========================
        self.qa_label = QLabel()
        self.qa_label.setStyleSheet("""
            QLabel {
                background-color: #FFF6DE;
                border: 1px solid #F5D98B;
                border-radius: 8px;
                padding: 10px 14px;
                font-size: 14px;
                font-weight: 600;
                color: #6B5215;
            }
        """)
        root_layout.addWidget(self.qa_label)

        # =========================
        # MACHINE LABEL
        # =========================
        self.machine_label = QLabel()
        self.machine_label.setStyleSheet("""
            QLabel {
                background-color: #E9F1FF;
                border: 1px solid #AECBF5;
                border-radius: 8px;
                padding: 10px 14px;
                font-size: 14px;
                font-weight: 600;
                color: #204E8C;
            }
        """)
        root_layout.addWidget(self.machine_label)

        # =========================
        # NETWORK ALERT (masqué par défaut)
        # =========================
        self.network_label = QLabel()
        self.network_label.setWordWrap(True)
        self.network_label.setVisible(False)
        self.network_label.setStyleSheet("""
            QLabel {
                background-color: #FDE7E7;
                border: 1px solid #F0A9A9;
                border-radius: 8px;
                padding: 8px 12px;
                font-weight: 600;
                color: #8C1F1F;
            }
        """)
        root_layout.addWidget(self.network_label)

        self.qa_legend = QLabel("* : patients restants")
        self.qa_legend.setStyleSheet("""
            QLabel {
                color: #9497A0;
                font-size: 11px;
                padding: 2px 4px;
            }
        """)
        root_layout.addWidget(self.qa_legend)

        # =========================
        # TABS
        # =========================
        self.tabs = QTabWidget()
        root_layout.addWidget(self.tabs)

        root.setLayout(root_layout)
        self.setCentralWidget(root)

        # =========================
        # STATUS BAR
        # =========================
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.last_refresh_label = QLabel("Dernier refresh : -")
        self.status_bar.addPermanentWidget(self.last_refresh_label)

        self.db_label = QLabel("DB : -")
        self.db_label.setMinimumWidth(320)
        self.status_bar.addPermanentWidget(self.db_label)

        # =========================
        # SIGNAL ONGLETS — connecté UNE SEULE FOIS
        # =========================
        self.tabs.currentChanged.connect(self.on_tab_changed)

        # =========================
        # WORKER THREAD — créé UNE SEULE FOIS et réutilisé
        # =========================
        self.worker = DataLoaderWorker(self)
        self.worker.data_loaded.connect(self.on_data_loaded)
        self.worker.error_occurred.connect(self.on_data_error)

        # =========================
        # FIRST LOAD (asynchrone)
        # =========================
        self.trigger_refresh()

        # =========================
        # AUTO REFRESH TIMER
        # =========================
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.trigger_refresh)
        self.timer.start(REFRESH_INTERVAL_MS)

        # =========================
        # BLINK TIMER SQL STATUS
        # =========================
        self.blink_state = False

        self.blink_timer = QTimer(self)
        self.blink_timer.timeout.connect(self.toggle_db_blink)
        self.blink_timer.start(BLINK_INTERVAL_MS)

    # =====================================================
    # REFRESH ASYNCHRONE
    # =====================================================
    def trigger_refresh(self) -> None:

        # Un chargement est encore en cours (réseau lent...) → on saute ce cycle
        if self.worker.isRunning():
            log("Refresh déjà en cours, cycle ignoré")
            return

        log("Lancement du rafraîchissement asynchrone")
        self.last_refresh_label.setText("Actualisation en cours (arrière-plan)...")

        # Un QThread terminé peut être relancé avec start()
        self.worker.start()

    def on_data_loaded(self, data: DashboardData) -> None:

        self.now = datetime.now()
        self.limit = add_business_days(self.now, 2)

        self.waiting_count = data.waiting_count
        self.waiting_details = data.waiting_details

        # La connexion revient après une panne
        if self.db_error_shown:

            QMessageBox.information(
                self,
                "Connexion rétablie",
                "La connexion à la base SQL est de nouveau disponible."
            )

            self.db_error_shown = False

        # =========================
        # HEADERS
        # =========================
        self.update_qa_header(data.qa, data.countdown, data.machines)
        self.update_machine_footer(data.machine_schedule, data.remaining_today)
        self.update_network_alert(data.network_errors)

        # =========================
        # SAVE CURRENT TAB
        # =========================
        current_index = self.tabs.currentIndex()

        # =========================
        # SUPPRESSION REELLE DES ANCIENS ONGLETS (anti fuite mémoire)
        # =========================
        self.tabs.blockSignals(True)

        while self.tabs.count():
            old_widget = self.tabs.widget(0)
            self.tabs.removeTab(0)
            old_widget.setParent(None)
            old_widget.deleteLater()

        # =========================
        # REBUILD TABS (piloté par TAB_SPECS)
        # =========================
        for tab_name, attribute, waiting_key in TAB_SPECS:

            rows = getattr(data, attribute)

            tab = self.create_table_tab(rows)
            tab.tab_name = tab_name
            tab.waiting_key = waiting_key

            self.tabs.addTab(tab, f"{tab_name} ({len(rows)})")

        self.tabs.blockSignals(False)

        # =========================
        # RESTORE TAB
        # =========================
        if 0 <= current_index < self.tabs.count():
            self.tabs.setCurrentIndex(current_index)
        else:
            self.tabs.setCurrentIndex(0)

        # force la mise à jour du pied de page / patients en attente
        self.on_tab_changed(self.tabs.currentIndex())

        # =========================
        # UPDATE LAST REFRESH TIME
        # =========================
        self.last_refresh_label.setText(
            f"Dernier refresh (3 min) : {self.now.strftime('%Y-%m-%d %H:%M:%S')}"
        )

        self.update_db_status(data.last_db_update)

    def on_data_error(self, err_msg: str) -> None:

        log("ERREUR LORS DU CHARGEMENT DES DONNEES")
        print(err_msg)

        self.last_refresh_label.setText(
            f"Échec du refresh : {datetime.now().strftime('%H:%M:%S')}"
        )

        if not self.db_error_shown:

            QMessageBox.critical(
                self,
                "Erreur SQL / Réseau",
                "Impossible de se connecter à la base de données "
                "ou d'accéder au réseau.\n\n"
                f"{err_msg}"
            )

            self.db_error_shown = True

    # =====================================================
    # FERMETURE PROPRE
    # =====================================================
    def closeEvent(self, event) -> None:

        self.timer.stop()
        self.blink_timer.stop()

        if self.worker.isRunning():

            # on laisse au thread jusqu'à 15 s pour finir proprement
            if not self.worker.wait(15000):
                self.worker.terminate()
                self.worker.wait()

        super().closeEvent(event)

    # =====================================================
    # BANDEAUX D'ETAT
    # =====================================================
    def update_network_alert(self, errors: list) -> None:
        # =========================================================
        # Avertit explicitement lorsque le NAS est injoignable, au lieu
        # d'afficher silencieusement des ❌ sur toutes les lignes.
        # =========================================================
        if not errors:
            self.network_label.setVisible(False)
            self.network_label.clear()
            return

        detail = "<br>".join(f"• {e}" for e in errors[:4])

        if len(errors) > 4:
            detail += f"<br>• ... et {len(errors) - 4} autre(s)"

        self.network_label.setText(
            "⚠️ Accès réseau partiellement indisponible — "
            "les colonnes Folder / Dicom / PDF peuvent être fausses.<br>"
            f"<span style='font-weight:400;'>{detail}</span>"
        )

        self.network_label.setVisible(True)

    def update_db_status(self, last_db_update: Optional[datetime]) -> None:
        # =========================================================
        # Code couleur + clignotement sur la dernière MAJ de la DB
        # =========================================================
        if last_db_update is None:

            self.db_alert_level = "unknown"
            self.db_label.setText("⚪ SQL DataBase : inconnue")
            self.db_label.setStyleSheet(
                "QLabel { color: gray; font-weight: bold; }"
            )
            return

        delta_min = (self.now - last_db_update).total_seconds() / 60
        stamp = last_db_update.strftime('%Y-%m-%d %H:%M:%S')

        # =========================
        # CRITICAL > 15 min (clignotant, style géré par toggle_db_blink)
        # =========================
        if delta_min > 15:
            self.db_alert_level = "critical"
            self.db_label.setText(f"🔴 SQL DataBase : {stamp}")

        # =========================
        # ALERT > 10 min
        # =========================
        elif delta_min > 10:
            self.db_alert_level = "alert"
            self.db_label.setText(f"🔴 SQL DataBase : {stamp}")
            self.db_label.setStyleSheet(
                "QLabel { color: red; font-weight: bold; }"
            )

        # =========================
        # WARNING > 5 min
        # =========================
        elif delta_min > 5:
            self.db_alert_level = "warning"
            self.db_label.setText(f"🟠 SQL DataBase : {stamp}")
            self.db_label.setStyleSheet(
                "QLabel { color: orange; font-weight: bold; }"
            )

        # =========================
        # OK
        # =========================
        else:
            self.db_alert_level = "ok"
            self.db_label.setText(f"🟢 SQL DataBase : {stamp}")
            self.db_label.setStyleSheet(
                "QLabel { color: green; font-weight: bold; }"
            )

    def toggle_db_blink(self) -> None:
        # Clignotement du message d'alerte de la DB SQL si délai trop long
        if self.db_alert_level != "critical":
            return

        self.blink_state = not self.blink_state

        if self.blink_state:
            self.db_label.setStyleSheet("""
                QLabel {
                    color: white;
                    background-color: red;
                    font-weight: bold;
                    padding-left: 5px;
                    padding-right: 5px;
                    min-width: 320px;
                }
            """)

        else:
            self.db_label.setStyleSheet("""
                QLabel {
                    color: red;
                    background-color: #2b2b2b;
                    font-weight: bold;
                    padding-left: 5px;
                    padding-right: 5px;
                    min-width: 320px;
                }
            """)

    # =====================================================
    # CALCULS / AFFICHAGE
    # =====================================================
    def refresh_current_tab_footer(self) -> None:
        self.on_tab_changed(self.tabs.currentIndex())

    def calculate_estimated_time(self, table_widget: QTableWidget) -> int:
        # =========================================================
        # Calcul du temps estimé pour la réalisation des CQ sélectionnés
        # =========================================================
        used_categories = {"tomo": 0, "ruby": 0, "octa": 0}

        total_minutes = 0
        tomo_beam_on_values = []

        # =========================
        # 1) COMPTER LES CASES COCHEES
        # =========================
        for row in range(table_widget.rowCount()):

            cell_widget = table_widget.cellWidget(row, COL_SELECT)

            if not cell_widget:
                continue

            checkbox = getattr(cell_widget, "checkbox", None)

            if not checkbox or not checkbox.isChecked():
                continue

            task_item = table_widget.item(row, COL_TASK)
            task_text = (task_item.text() if task_item else "").lower()

            matched = False

            for key in list(used_categories.keys()):
                if key in task_text:
                    used_categories[key] += 1
                    matched = True
                    break

            if not matched:
                used_categories["other"] = used_categories.get("other", 0) + 1

            # récupération du Beam On pour les Tomo
            if "tomo" in task_text:

                beam_item = table_widget.item(row, COL_DURATION)

                if beam_item:
                    try:
                        tomo_beam_on_values.append(float(beam_item.text()))
                    except ValueError:
                        pass

        # =========================
        # 2) CALCUL TEMPS
        # =========================
        for key, count in used_categories.items():

            if count == 0:
                continue

            rule = self.TIME_RULES.get(key, self.DEFAULT_TIME)

            if key == "tomo":

                # 1 seul setup (fixed)
                total_minutes += rule["fixed"]

                # par patient sélectionné : overhead + beam-on
                for beam in tomo_beam_on_values:
                    total_minutes += self.TOMO_PER_CASE_OVERHEAD + beam

            else:
                total_minutes += rule["fixed"]
                total_minutes += count * rule["per_case"]

        return round(total_minutes)

    def on_tab_changed(self, index: int) -> None:

        # removeTab() peut émettre currentChanged(-1)
        if index is None or index < 0:
            return

        widget = self.tabs.widget(index)

        if widget is None:
            return

        tab_name = getattr(widget, "tab_name", "")
        waiting_key = getattr(widget, "waiting_key", None)

        # =========================
        # FOOTER TEMPS
        # =========================
        if hasattr(widget, "footer_label") and hasattr(widget, "table"):

            total = self.calculate_estimated_time(widget.table)

            widget.footer_label.setText(
                f"Temps estimé ({tab_name}) suivant sélection : {total} min"
            )

        # =========================
        # PATIENTS EN ATTENTE
        # =========================
        if not hasattr(widget, "patient_widget"):
            return

        count = self.waiting_count.get(waiting_key, 0)

        widget.patient_widget.set_title(
            f"Patients en cours de préparation : {waiting_key} = {count}"
        )

        patients = self.waiting_details.get(waiting_key, [])

        if not patients:
            widget.patient_widget.content.setText("Aucun patient en préparation")
            return

        html = """
        <table cellspacing="6">
            <tr>
                <th align="left">IPP</th>
                <th align="left">Patient</th>
                <th align="left">TDM</th>
                <th align="left">Valid. Méd.</th>
                <th align="left">Called</th>
                <th align="left">MET</th>
                <th align="left">Workflow</th>
            </tr>
        """

        for p in patients:

            color = "red" if p.get("va_tomber") else "black"

            met = (
                p["MET"].strftime("%d/%m/%Y %H:%M")
                if p.get("MET") else "-"
            )

            validation_date = (
                p["validation_date"].strftime("%d/%m/%Y")
                if p.get("validation_date") else "-"
            )

            tdm_date = (
                p["tdm_date"].strftime("%d/%m/%Y")
                if p.get("tdm_date") else "-"
            )

            html += f"""
            <tr style="color:{color};">
                <td>{p['ipp']}</td>
                <td>{p['last_name']} {p['first_name']}</td>
                <td>{tdm_date}</td>
                <td>{validation_date}</td>
                <td>{p.get('called') or '-'}</td>
                <td>{met}</td>
                <td>{p.get('workflow') or '-'}</td>
            </tr>
            """

        html += "</table>"

        widget.patient_widget.content.setText(html)

    def update_machine_footer(self, schedule: list, remaining_today: dict) -> None:

        if not schedule:
            self.machine_label.setText("Fin de journée : aucune donnée")
            return

        schedule = sorted(
            schedule,
            key=lambda x: (
                MACHINE_ORDER.get(x.get("machine_key"), 999),
                x.get("start") or datetime.max
            )
        )

        lines = []

        for row in schedule:

            start = row.get("start")

            if not start:
                continue

            machine_key = row.get("machine_key")

            label = (
                machine_display(machine_key)
                if machine_key
                else str(row.get("machine_raw") or "?")
            )

            count = remaining_today.get(machine_key)

            extra = "" if count in (None, "none") else f" ({count})"

            lines.append(f"{label}: {start.strftime('%H:%M')}{extra}*")

        self.machine_label.setText(
            "Fin de journée :   " + "   |   ".join(lines)
        )

    def compute_real_cq_slot(self, qa_row: dict, machines: dict) -> tuple:
        # =========================================================
        # Détermine le plus grand créneau réellement libre à l'intérieur
        # du créneau CQ théorique.
        # =========================================================
        machine = qa_row.get("machine_key")
        cq_start = qa_row.get("met_start")
        cq_end = qa_row.get("met_end")

        if not machine or not cq_start or not cq_end:
            return None, None, False

        intervals = []

        # =========================
        # toutes les tâches qui empiètent
        # =========================
        for task in machines.get(machine, []):

            start = task.get("start")
            end = task.get("end")

            if not start or not end:
                continue

            service_type = str(task.get("service_type") or "").lower()

            if "cq patient" in service_type or "cq physique" in service_type:
                continue

            if end <= cq_start or start >= cq_end:
                continue

            intervals.append((max(start, cq_start), min(end, cq_end)))

        # =========================
        # aucune coupure → créneau intact
        # =========================
        if not intervals:
            return cq_start, cq_end, True

        intervals.sort()

        free_slots = []
        cursor = cq_start

        for start, end in intervals:

            if start > cursor:
                free_slots.append((cursor, start))

            cursor = max(cursor, end)

        if cursor < cq_end:
            free_slots.append((cursor, cq_end))

        if not free_slots:
            return None, None, False

        best_slot = max(free_slots, key=lambda x: (x[1] - x[0]))

        return best_slot[0], best_slot[1], False

    def check_qa_overlap(self, qa_row: dict, machines: dict) -> int:

        machine = qa_row.get("machine_key")
        met_start = qa_row.get("met_start")
        met_end = qa_row.get("met_end")

        if not machine or not met_start or not met_end:
            return 0

        overlap_minutes = 0

        for task in machines.get(machine, []):

            task_start = task.get("start")
            task_end = task.get("end")

            if not task_start or not task_end:
                continue

            # =========================
            # EXCLUSION DE LA TACHE CQ ELLE-MEME
            # =========================
            if (
                abs((task_start - met_start).total_seconds()) < 60
                and abs((task_end - met_end).total_seconds()) < 60
            ):
                continue

            # =========================
            # CHEVAUCHEMENT
            # =========================
            overlap_start = max(task_start, met_start)
            overlap_end = min(task_end, met_end)

            if overlap_start < overlap_end:
                overlap_minutes += (
                    overlap_end - overlap_start
                ).total_seconds() / 60

        return round(overlap_minutes)

    def update_qa_header(self, qa: list, countdown: dict, machines: dict) -> None:

        if not qa:
            self.qa_label.setText("Aucun créneau CQ trouvé aujourd'hui")
            return

        now = datetime.now()
        lines = []

        for row in qa:

            met_start = row.get("met_start")
            met_end = row.get("met_end")
            machine_key = row.get("machine_key")
            label = row.get("machine_label") or "?"

            # =========================
            # CRENEAU CQ REELLEMENT DISPONIBLE
            # =========================
            real_start, real_end, is_full = self.compute_real_cq_slot(row, machines)

            if real_start and real_end:
                hour = f"{real_start.strftime('%H:%M')} → {real_end.strftime('%H:%M')}"
            else:
                hour = met_start.strftime("%H:%M") if met_start else "--:--"

            # =========================
            # COUNTDOWN CQ
            # =========================
            remaining = countdown.get(machine_key)

            extra = "" if remaining in (None, "none") else f" ({remaining})*"

            # =========================
            # OVERLAP CHECK CQ
            # =========================
            reduction = self.check_qa_overlap(row, machines)

            if reduction > 0:
                extra += f" ⚠️ -{reduction} min"

            # =========================
            # STATUS CQ SLOT
            # =========================
            if real_start and real_end:
                extra += " ✅" if is_full else " ⚠️"

            # =========================
            # CRENEAU EN COURS ?
            # =========================
            is_current = met_start and met_end and met_start <= now <= met_end

            if is_current:
                lines.append(
                    f'<span style="color:red;">{hour} - {label}{extra}</span>'
                )
            else:
                lines.append(f"{hour} - {label}{extra}")

        self.qa_label.setText(
            "Prochains créneaux CQ :   " + "   |   ".join(lines)
        )

    # =====================================================
    # CONSTRUCTION D'UN ONGLET
    # =====================================================
    def create_table_tab(self, data: list) -> QWidget:

        widget = QWidget()
        layout = QVBoxLayout()

        table = QTableWidget()
        table.setColumnCount(COLUMN_COUNT)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(28)
        table.setShowGrid(False)

        footer_label = QLabel("Temps estimé suivant sélection :")
        footer_label.setStyleSheet("""
            QLabel {
                background-color: #F3F4F7;
                color: #3D3F46;
                padding: 8px 10px;
                font-weight: 600;
                border-top: 1px solid #E1E3E8;
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
            }
        """)

        patient_widget = CollapsibleWidget("Patients en cours de préparation")

        # =========================
        # HEADERS
        # =========================
        headers = [
            "Status",
            "Select",
            "MET Date",
            "Patient",
            "IPP",
            "Task",
            "Task Status",
            "Physicist / Dosimetrist",
            "Note",
            "Time Planner",
            "Folder",
            "Dicom",
            "PDF",
            "Adress",
            "Energy",
            "Duration (min)",
            "Localisation",
        ]

        header_tooltips = [
            "Priorité du dossier",
            "Sélection pour le calcul du temps",
            "Date MET prévue",
            "Nom du patient",
            "ID patient",
            "Type de tâche CQ",
            "Statut de la tâche",
            "Personne ayant créé le CQ et fait les exports dicom",
            "Note associée à la tâche",
            "CQ Patient programmé pour aujourd'hui dans Timeplanner ?",
            "Dossier patient existant sur le réseau IUCT ?",
            "Fichiers DICOM (calculs) présents dans le dossier ?",
            "Rapport PDF présent et daté du même jour ou après les exports DICOM",
            "Nom du dossier où le calcul a été exporté",
            "Energie du faisceau",
            "Durée (beam-on) en minutes",
            "Fraction",
        ]

        table.setHorizontalHeaderLabels(headers)

        for col, tooltip in enumerate(header_tooltips):
            item = table.horizontalHeaderItem(col)
            if item:
                item.setToolTip(tooltip)

        table.setRowCount(len(data))

        # =========================
        # ROW LOOP
        # =========================
        for row, patient in enumerate(data):

            met_date = patient.get("met_start")

            # =========================
            # COLOR / STATUS LOGIC
            # =========================
            if not met_date:
                color = PASTEL_NONE
                dot = "⚪"
                tooltip = "Aucune date définie"

            elif met_date < self.now:
                color = PASTEL_LATE
                dot = "🔴"
                tooltip = f"⚠️ En retard de {(self.now - met_date).days} jour(s)"

            elif met_date <= self.limit:
                color = PASTEL_URGENT
                dot = "🟠"
                tooltip = f"⏳ Urgent : {(met_date - self.now).days} jour(s) restant(s)"

            else:
                color = PASTEL_OK
                dot = "🟢"
                tooltip = f"OK : {(met_date - self.now).days} jour(s) restants"

            # =========================
            # ICONS
            # =========================
            folder_icon = "✅" if patient.get("existing_folder") else "❌"
            dicom_icon = "✅" if patient.get("existing_dicom") else "❌"
            pdf_icon = "✅" if patient.get("existing_pdf") else "❌"
            cq_patient_icon = "✅" if patient.get("Timeplanner") else "❌"

            # =========================
            # ADDRESS
            # =========================
            folder_path = str(patient.get("folder_path") or "")

            if "Hyperarc" in folder_path:
                adress = "Hyperarc"
            elif "EC_IUC" in folder_path:
                adress = "EC"
            elif "Patients_RA" in folder_path:
                adress = "RA"
            else:
                adress = ""

            # =========================
            # TOMO DATA
            # =========================
            tomo_beam_on = patient.get("tomo_duration_min")
            tomo_fraction = patient.get("tomo_fraction")

            # =========================
            # ITEMS
            # =========================
            item_status = QTableWidgetItem(dot)

            select_widget = create_centered_checkbox(True)
            select_widget.checkbox.stateChanged.connect(
                self.refresh_current_tab_footer
            )

            item_met = QTableWidgetItem(
                met_date.strftime("%Y-%m-%d %H:%M") if met_date else ""
            )
            item_patient = QTableWidgetItem(
                f'{patient["last_name"]} {patient["first_name"]}'
            )
            item_ipp = QTableWidgetItem(str(patient["ipp"]))
            item_task = QTableWidgetItem(str(patient["task_display_focus"]))
            item_task_status = QTableWidgetItem(str(patient["task_status"]))
            item_physicist = QTableWidgetItem(str(patient.get("physicist") or ""))
            item_note = QTableWidgetItem(str(patient.get("task_note") or ""))
            item_timeplanner = QTableWidgetItem(cq_patient_icon)
            item_folder = QTableWidgetItem(folder_icon)
            item_dicom = QTableWidgetItem(dicom_icon)
            item_pdf = QTableWidgetItem(pdf_icon)
            item_adress = QTableWidgetItem(adress)
            item_energy = QTableWidgetItem(str(patient.get("energy") or ""))

            item_beam_on = QTableWidgetItem(
                "" if tomo_beam_on is None else str(tomo_beam_on)
            )
            item_fraction = QTableWidgetItem(
                "" if tomo_fraction is None else str(tomo_fraction)
            )

            pdf_date = patient.get("pdf_date")

            if pdf_date:
                item_pdf.setToolTip(
                    f"PDF validé : {pdf_date.strftime('%d/%m/%Y %H:%M')}"
                )

            # =========================
            # ALIGNMENT
            # =========================
            for centered in (
                item_note, item_timeplanner, item_folder,
                item_dicom, item_pdf, item_energy,
                item_beam_on, item_fraction,
            ):
                centered.setTextAlignment(Qt.AlignCenter)

            # =========================
            # COLOR + TOOLTIP
            # =========================
            items = [
                item_status, item_met, item_patient, item_ipp, item_task,
                item_task_status, item_physicist, item_note,
                item_timeplanner, item_folder, item_dicom,
                item_pdf, item_adress, item_energy,
                item_beam_on, item_fraction,
            ]

            for item in items:
                item.setBackground(QBrush(color))
                if item is not item_pdf:
                    item.setToolTip(tooltip)

            # =========================
            # INSERT TABLE
            # =========================
            table.setItem(row, COL_STATUS, item_status)
            table.setCellWidget(row, COL_SELECT, select_widget)
            table.setItem(row, COL_MET, item_met)
            table.setItem(row, COL_PATIENT, item_patient)
            table.setItem(row, COL_IPP, item_ipp)
            table.setItem(row, COL_TASK, item_task)
            table.setItem(row, COL_TASK_STATUS, item_task_status)
            table.setItem(row, COL_PHYSICIST, item_physicist)
            table.setItem(row, COL_NOTE, item_note)
            table.setItem(row, COL_TIMEPLANNER, item_timeplanner)
            table.setItem(row, COL_FOLDER, item_folder)
            table.setItem(row, COL_DICOM, item_dicom)
            table.setItem(row, COL_PDF, item_pdf)
            table.setItem(row, COL_ADDRESS, item_adress)
            table.setItem(row, COL_ENERGY, item_energy)
            table.setItem(row, COL_DURATION, item_beam_on)
            table.setItem(row, COL_LOCALISATION, item_fraction)

        table.resizeColumnsToContents()

        layout.addWidget(table)
        layout.addWidget(footer_label)
        layout.addWidget(patient_widget)

        widget.setLayout(layout)

        widget.footer_label = footer_label
        widget.patient_widget = patient_widget
        widget.table = table

        return widget


# =====================================================
# LANCEMENT APPLICATION
# =====================================================
if __name__ == "__main__":

    app = QApplication(sys.argv)
    app.setStyleSheet(APP_STYLESHEET)
    app.setFont(QFont("Segoe UI", 10))

    window = MainWindow()
    window.show()

    sys.exit(app.exec())