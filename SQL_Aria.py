# =========================================================
# Dashboard de suivi des CQ
# Ce script se connecte à la base de données, extrait les tâches de réalisation des CQ prêtes et les appointments MET associés, 
# puis affiche le tout dans une interface Qt avec un code couleur selon la proximité de l'appointment MET.
# Les données sont rafraîchies automatiquement toutes les 30 secondes pour rester à jour.
# Note
# pour faire le .exe : 
"""
Remove-Item dist -Recurse -Force
Remove-Item build -Recurse -Force
Remove-Item CQ_Dashboard.spec -Force

python -m PyInstaller SQL_Aria.py --clean --noconfirm --onedir --windowed --name CQ_Dashboard --add-data "ATT70966.env;."
"""
# =========================================================



import os
import traceback
import pandas as pd

from models import Patients, Careplans, Tasks, Appointments, Prescriptions, Plans, Events, QueryLog,Comments, CommentAssociations
from dotenv import load_dotenv

from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, joinedload
from sqlalchemy import or_

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
    QLabel,
    QStatusBar,
    QVBoxLayout
)
from PySide6.QtGui import QColor, QBrush
from PySide6.QtCore import QTimer

from pathlib import Path

import sys

# =========================================================
# Configuration for databa access
# =========================================================

# Loading user information for database access
base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))

dotenv_path = os.path.join(base_path, "ATT70966.env")
load_dotenv(dotenv_path)

# Time range used for the search
one_week_ago = datetime.now() - timedelta(days=7)

# Read environment variables
db_user = os.getenv("DATABASE_USER")
db_password = os.getenv("DATABASE_PASSWORD")
db_host = os.getenv("DATABASE_HOST")
db_name = os.getenv("DATABASE_NAME")

# Build SQLAlchemy database URL
DATABASE_URL = (
    f"mysql+pymysql://"
    f"{db_user}:"
    f"{db_password}@"
    f"{db_host}/"
    f"{db_name}"
)

# Create SQLAlchemy engine
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


"""
# =========================
# FULL DUMP FUNCTION (if needed, in order to see all available patient data)
# =========================
def dump_patient_full(session, ipp: str):

    patient = (
        session.query(Patients)
        .options(
            joinedload(Patients.appointments),
            joinedload(Patients.careplans).joinedload(Careplans.tasks),
            joinedload(Patients.prescriptions).joinedload(Prescriptions.plans),
            joinedload(Patients.plans),
            joinedload(Patients.events),
        )
        .filter(Patients.ipp == ipp)
        .first()
    )

    if not patient:
        print(f"No patient found for IPP {ipp}")
        return

    # =========================
    # PATIENT
    # =========================
    print("\n================ PATIENT ================")
    print("ID:", patient.id)
    print("IPP:", patient.ipp)
    print("Name:", patient.family_name_official)
    print("Given:", patient.given)
    print("Gender:", patient.gender)
    print("Birth date:", patient.birth_date)
    print("Created:", patient.created_at)
    print("Last updated:", patient.last_updated)

    # =========================
    # APPOINTMENTS
    # =========================
    print("\n================ APPOINTMENTS ================")
    for a in patient.appointments:
        print("ID:", a.id)
        print("Service type:", a.service_type)
        print("Status:", a.status)
        print("Start:", a.start_scheduled_period)
        print("End:", a.end_scheduled_period)
        print("Code:", a.code)
        print("User note:", a.user_note)
        print("Device:", a.device)
        print("Comment:", a.comment)
        print("---")

    # =========================
    # CAREPLANS + TASKS
    # =========================
    print("\n================ CAREPLANS / TASKS ================")
    for cp in patient.careplans:
        print("\nCAREPLAN:", cp.id, "-", cp.title)

        for t in cp.tasks:
            print("  Task ID:", t.id)
            print("  Display focus:", t.display_focus)
            print("  Status:", t.status)
            print("  Code:", t.code)
            print("  Category:", t.category)
            print("  Minutes duration:", t.minutes_duration)
            print("  Activity definition id:", t.activitydefinition_id)
            print("  Based On:", t.basedOn)
            print("  Authored On:", t.authoredOn)
            print("  Recipient:", t.recipient)
            print("  Recipient ID:", t.recipient_id)
            print("  Careplan id:", t.careplan_id)
            print("  Device:", t.device)
            print("  Created:", t.created_at)
            print("  Last updated:", t.last_updated)
            print("  Note:", t.note)
            print("  CAREPLAN Title:", cp.title)
            print("  CAREPLAN Note:", cp.note)
            print("  ---")

            # =========================
            # COMMENTS FOR THIS TASK
            # =========================
            comments = (
                session.query(Comments)
                .join(CommentAssociations, Comments.id == CommentAssociations.comment_id)
                .filter(
                    CommentAssociations.table_name == "tasks",
                    CommentAssociations.entity_id == t.id
                )
                .all()
            )

        for c in comments:
            print("    COMMENT:", c.content)
    # =========================
    # PRESCRIPTIONS + PLANS
    # =========================
    print("\n================ PRESCRIPTIONS ================")
    for p in patient.prescriptions:
        print("Prescription ID:", p.id)
        print("Status:", p.status)
        print("Technique:", p.technique)
        print("Site:", p.site)
        print("Created:", p.created_at)
        print("Last updated:", p.last_updated)

        for pl in p.plans:
            print("  Plan ID:", pl.id)
            print("  Name:", pl.name)
            print("  Technique:", pl.treatment_technique)
            print("  Last updated:", pl.last_updated)

    # =========================
    # PLANS (direct patient link)
    # =========================
    print("\n================ PATIENT PLANS ================")
    for pl in patient.plans:
        print("Plan ID:", pl.id)
        print("Name:", pl.name)
        print("Technique:", pl.treatment_technique)
        print("Last updated:", pl.last_updated)

    # =========================
    # EVENTS
    # =========================
    print("\n================ EVENTS ================")
    for e in patient.events:
        print("Event ID:", e.id)
        print("Type:", e.event_type)
        print("Timestamp:", e.timestamp)
        print("Description:", e.description)
        print("Last updated:", e.last_updated)

    # =========================
    # QUERY LOG
    # =========================
    print("\n================ QUERY LOG ================")
    log = session.query(QueryLog).first()

    if log:
        print("Last task request:", log.last_task_request)
        print("Last appointment request:", log.last_appointment_request)
        print("Last updated:", log.last_updated)


# =========================================================
# EXECUTION (Don't forget to add a stop point.)
# =========================================================
# Get all data patient (DEBUG CALL)
session = SessionLocal()
dump_patient_full(session, "201803581")
session.close()


"""
# =========================================================
# Function to sort patients by MET appointment start date (with None at the end).git --version

# =========================================================
def sort_by_met_start(table):

    return sorted(
        table,
        key=lambda row: (
            row["met_start"] is None,
            row["met_start"]
        )
    )

# =========================================================
# Function to concider business days when adding days to a date (for better estimation of QA priority).
# =========================================================
def add_business_days(start_date, days):
    current = start_date
    added = 0

    while added < days:
        current += timedelta(days=1)

        # 0 = lundi ... 6 = dimanche
        if current.weekday() < 5:  # lundi-vendredi
            added += 1

    return current




# =========================================================
# Check if patient folder already exists on network
# =========================================================

def check_existing_folders(Nova, Tomo2, Tomo4, Tomo7):

    import os
    from datetime import datetime

    # =========================
    # NETWORK PATHS TOMO
    # =========================
    network_path_Tomo2 = r"\\nasdata1\TOMO\02 - CQ Patients\0_DELTA4\TOMO2"
    network_path_Tomo4 = r"\\nasdata1\TOMO\02 - CQ Patients\0_DELTA4\TOMO4"
    network_path_Tomo7 = r"\\nasdata1\TOMO\02 - CQ Patients\0_DELTA4\RADI7"

    # =========================
    # NETWORK PATHS NOVA
    # =========================
    network_path_Nova = {
        "EC": r"\\srv015\Radiophysique_acquisition\Patients_stereo_EC_IUC",
        "Hyperarc": r"\\srv015\Radiophysique_acquisition\Patients_stereo_Hyperarc",
        "RA": r"\\srv015\Radiophysique_acquisition\Patients_RA"
    }

    current_year = str(datetime.now().year)

    # =========================
    # TOMO CACHE (LEVEL 1 ONLY)
    # =========================
    def build_cache(path):
        try:
            with os.scandir(path) as it:
                return {entry.name for entry in it if entry.is_dir()}
        except Exception as e:
            print(f"[CACHE ERROR] {path} -> {e}")
            return set()

    cache_Tomo2 = build_cache(network_path_Tomo2)
    cache_Tomo4 = build_cache(network_path_Tomo4)
    cache_Tomo7 = build_cache(network_path_Tomo7)

    # =========================
    # NOVA CACHE (LEVEL 1 ONLY + YEAR FOLDER)
    # =========================
    def build_cache_nova(path):
        year_path = os.path.join(path, current_year)

        try:
            with os.scandir(year_path) as it:
                return {entry.name for entry in it if entry.is_dir()}
        except Exception as e:
            print(f"[NOVA CACHE ERROR] {year_path} -> {e}")
            return set()

    cache_EC = build_cache_nova(network_path_Nova["EC"])
    cache_Hyperarc = build_cache_nova(network_path_Nova["Hyperarc"])
    cache_RA = build_cache_nova(network_path_Nova["RA"])

    # =========================
    # MATCH FUNCTION
    # =========================
    def folder_exists(cache, ipp):
        if not ipp:
            return False
        ipp = str(ipp).strip()
        return any(ipp in name for name in cache)

    def nova_exists(ipp):
        return (
            folder_exists(cache_EC, ipp) or
            folder_exists(cache_Hyperarc, ipp) or
            folder_exists(cache_RA, ipp)
        )

    # =========================
    # TOMO2
    # =========================
    print("Explore folder Tomo2")
    for row in Tomo2:
        row["existing_folder"] = folder_exists(cache_Tomo2, row.get("ipp"))

    # =========================
    # TOMO4
    # =========================
    print("Explore folder Tomo4")
    for row in Tomo4:
        row["existing_folder"] = folder_exists(cache_Tomo4, row.get("ipp"))

    # =========================
    # TOMO7
    # =========================
    print("Explore folder Tomo7")
    for row in Tomo7:
        row["existing_folder"] = folder_exists(cache_Tomo7, row.get("ipp"))

    # =========================
    # NOVA
    # =========================
    print("Explore folder Nova")
    for row in Nova:
        row["existing_folder"] = nova_exists(row.get("ipp"))

# =========================================================
# EXTRACT from database to array
# =========================================================
def load_data():
    # Test database connection
    connection = engine.connect()
    print("\nDatabase connection OK")
    connection.close()

    session = SessionLocal()

    patients = (
        session.query(Patients)
        .join(Patients.careplans)
        .join(Careplans.tasks)
        .filter(
            or_(
                Tasks.display_focus.ilike("réalisation du cq%"),
                Tasks.display_focus.ilike("réalisation des cq%")
            ),
            Tasks.last_updated >= one_week_ago,
            Tasks.status.ilike("ready")
        )
        .options(
            joinedload(Patients.appointments),
            joinedload(Patients.careplans).joinedload(Careplans.tasks)
        )
        .distinct()
        .all()
    )

    print(f"\nNumber of patients fetched: {len(patients)}")

    rows = []

    for patient in patients:

        for careplan in patient.careplans:
            for task in careplan.tasks:

                if (
                    task.display_focus
                    and (
                        task.display_focus.lower().startswith("réalisation du cq")
                        or task.display_focus.lower().startswith("réalisation des cq")
                    )
                ):

                     # FILTER STATUS 
                    task_status = task.status.lower() if task.status else ""
                    if "cancelled" in task_status:
                        continue

                    # récupérer appointment MET associé (si existe)
                    met_appt = None

                    for appointment in patient.appointments:
                        if (
                            appointment.service_type
                            and appointment.service_type.upper().startswith("MET")
                        ):
                            met_appt = appointment
                            break

                    # ajouter une ligne structurée
                    rows.append({
                        # PATIENT
                        "ipp": patient.ipp,
                        "last_name": patient.family_name_official,
                        "first_name": patient.given,

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

    session.close()

    print("\nROWS RAW:")
    print(rows)

       

    # =========================================================
    # Sorting of patients according to the machine concerned for QA
    # =========================================================

    Nova = []
    Tomo2 = []
    Tomo4 = []
    Tomo7 = []

    for row in rows:

        focus = row["task_display_focus"]

        if not focus:
            continue

        focus_lower = focus.lower()

        #
        # TOMO 2
        #
        if "tomo 2" in focus_lower:
            Tomo2.append(row)

        #
        # TOMO 4
        #
        elif "tomo 4" in focus_lower:
            Tomo4.append(row)

        #
        # TOMO 7
        #
        elif "tomo 7" in focus_lower:
            Tomo7.append(row)

        #
        # NOVA
        #
        elif (
            "ruby" in focus_lower
            or "octa4d" in focus_lower
        ):
            Nova.append(row)

    print("Nova:", len(Nova))
    print("Tomo2:", len(Tomo2))
    print("Tomo4:", len(Tomo4))
    print("Tomo7:", len(Tomo7))

    # =========================================================
    # Sorted by chronological priority
    # =========================================================
    Nova = sort_by_met_start(Nova)
    Tomo2 = sort_by_met_start(Tomo2)
    Tomo4 = sort_by_met_start(Tomo4)
    Tomo7 = sort_by_met_start(Tomo7)
    
    check_existing_folders(Nova, Tomo2, Tomo4, Tomo7)

    return Nova, Tomo2, Tomo4, Tomo7

    





# =====================================================
# INTERFACE QT
# =====================================================

class MainWindow(QMainWindow):

    def refresh_data(self):
        global Tomo2, Tomo4, Tomo7, Nova
        self.now = datetime.now()
        self.limit = add_business_days(self.now, 2)

        # =========================
        # SAVE CURRENT TAB
        # =========================
        current_index = self.tabs.currentIndex()

        # reload data
        Nova, Tomo2, Tomo4, Tomo7 = load_data()

        # puis update UI
        self.tabs.clear()

        self.tabs.addTab(self.create_table_tab(Tomo2), "Tomo2")
        self.tabs.addTab(self.create_table_tab(Tomo4), "Tomo4")
        self.tabs.addTab(self.create_table_tab(Tomo7), "Tomo7")
        self.tabs.addTab(self.create_table_tab(Nova), "Nova(s)")

        # =========================
        # RESTORE TAB
        # =========================
        self.tabs.setCurrentIndex(current_index)

        # =========================
        # UPDATE LAST REFRESH TIME
        # =========================
        self.last_refresh_label.setText(f"Dernier refresh (30 sec) : {self.now.strftime('%Y-%m-%d %H:%M:%S')}")

    def __init__(self):
        super().__init__()

        self.setWindowTitle("CQ Dashboard")
        self.resize(1200, 700)

        self.now = datetime.now()
        self.limit = add_business_days(self.now, 2)

        # =========================
        # UI ROOT TABS
        # =========================
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # =========================
        # STATUS BAR
        # =========================
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self.last_refresh_label = QLabel("Dernier refresh : -")
        self.status_bar.addPermanentWidget(self.last_refresh_label)
        
        # =========================
        # FIRST LOAD
        # =========================
        self.refresh_data()

        # =========================
        # AUTO REFRESH TIMER
        # =========================
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh_data)
        self.timer.start(30_000)  # 30 sec

        

    def create_table_tab(self, data):

        widget = QWidget()
        layout = QVBoxLayout()

        table = QTableWidget()
        table.setColumnCount(7)

        table.setHorizontalHeaderLabels([
            "Status",
            "MET Date",
            "Patient",
            "IPP",
            "Task",
            "Task Status",
            "Note"
        ])

        table.setRowCount(len(data))

        for row, patient in enumerate(data):

            met_date = patient["met_start"]

            # =========================
            # COLOR LOGIC
            # =========================
            color = QColor(255, 200, 200)  # rouge par défaut
            tooltip = ""
            if not met_date:
                color = QColor(220, 220, 220)
                tooltip = "Aucune date définie"

            elif met_date < self.now:
                color = QColor(255, 150, 150)
                tooltip = f"⚠️ En retard de {(self.now - met_date).days} jour(s)"

            elif met_date <= self.limit:
                color = QColor(255, 220, 150)
                tooltip = f"⏳ Urgent : {(met_date - self.now).days} jour(s) restant(s)"

            else:
                color = QColor(200, 255, 200)
                tooltip = f"OK : {(met_date - self.now).days} jour(s) restants"

           
            # =========================
            # STATUS DOT
            # =========================
            if not met_date:
                dot = "⚪"
            elif met_date < self.now:
                dot = "🔴"
            elif met_date <= self.limit:
                dot = "🟠"
            else:
                dot = "🟢"

            # =========================
            # CREATE ITEMS
            # =========================
            item0 = QTableWidgetItem(dot)
            item1 = QTableWidgetItem(str(met_date))
            item2 = QTableWidgetItem(f'{patient["last_name"]} {patient["first_name"]}')
            item3 = QTableWidgetItem(str(patient["ipp"]))
            item4 = QTableWidgetItem(str(patient["task_display_focus"]))
            item5 = QTableWidgetItem(str(patient["task_status"]))
            item6 = QTableWidgetItem(str(patient.get("task_note") or ""))

            # =========================
            # TOOLTIP + COLOR (sur toute la ligne)
            # =========================
            items = [item0, item1, item2, item3, item4, item5, item6]
            for i in items:
                i.setBackground(QBrush(color))
                i.setToolTip(tooltip)

            # =========================
            # INSERT INTO TABLE
            # =========================
            table.setItem(row, 0, item0)
            table.setItem(row, 1, item1)
            table.setItem(row, 2, item2)
            table.setItem(row, 3, item3)
            table.setItem(row, 4, item4)
            table.setItem(row, 5, item5)
            table.setItem(row, 6, item6)


        table.resizeColumnsToContents()

        layout.addWidget(table)
        widget.setLayout(layout)

        return widget



# =====================================================
# LANCEMENT APPLICATION
# =====================================================
app = QApplication(sys.argv)

window = MainWindow()
window.show()

app.exec()