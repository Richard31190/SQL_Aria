# =========================================================
# Richard BOISSERON
# Dashboard de suivi des CQ
# Ce script se connecte à la base de données, extrait les tâches de réalisation des CQ prêtes et les appointments MET associés, 
# puis affiche le tout dans une interface Qt avec un code couleur selon la proximité de l'appointment MET.
# Les données sont rafraîchies automatiquement toutes les 3 minutes pour rester à jour.
# La recherche des CQ Physique ou patient est effectuée 14 jours avant la MET et après la MET
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
from sqlalchemy import func

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
from PySide6.QtCore import Qt

from pathlib import Path

import sys

# =========================================================
# Configuration for SQL databa access
# =========================================================

# Loading user information for database access
base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))

dotenv_path = os.path.join(base_path, "ATT70966.env")
load_dotenv(dotenv_path)

# Time range used for the search
two_weeks_ago = datetime.now() - timedelta(days=14)

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

#region Extraction de données complète (debug) - ID patient à adapter selon besoin
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
dump_patient_full(session, "202209726")
session.close()
"""
#endregion


def get_last_database_update(session):
    # =========================================================
    # Récupère la date et l'heure de la dernière actualisation (partielle ou complète) de la base de données (pour info dans le dashboard))
    # =========================================================
    return session.query(
        func.max(Tasks.last_updated)
    ).scalar()

def sort_by_met_start(table):
    # =========================================================
    # Fonction pour ordonner les patients par date de MET
    # =========================================================
    return sorted(
        table,
        key=lambda row: (
            row["met_start"] is None,
            row["met_start"]
        )
    )

def add_business_days(start_date, days):
    # =========================================================
    # Fonction pour exclure les week-ends dans le calcul des MET prioritaires
    # =========================================================
    current = start_date
    added = 0

    while added < days:
        current += timedelta(days=1)

        # 0 = lundi ... 6 = dimanche
        if current.weekday() < 5:  # lundi-vendredi
            added += 1

    return current

def clean_cq_rows(rows):
    # =========================================================
    # Nettoie les données brutes extraites de la base de données pour ne garder que les tâches pertinentes (filtrage des tâches annulées, etc.)
    # =========================================================
    return [
        r for r in rows
        if str(r.get("task_status") or "").lower() not in ["completed", "draft"]
    ]

def check_existing_folders(Nova, Tomo2, Tomo4, Tomo7):
    # =========================================================
    # Recherche dans les dossiers réseaux IUCT l'existence de dossiers patients correspondant aux patients du jour, et vérifie la présence de fichiers DICOM (calculs) dans ces dossiers
    # =========================================================
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
    # TOMO  (LEVEL 1 ONLY) - N'inspecte pas les sous dossiers
    # =========================
    def build_cache(path):
        cache = {}
        try:
            with os.scandir(path) as it:

                for entry in it:

                    if entry.is_dir():

                        cache[entry.name] = entry.path

        except Exception as e:
            print(f"[CACHE ERROR] {path} -> {e}")

        return cache

    # =========================
    # NOVA - Inspecte les sous dossiers
    # =========================
    def build_cache_nova(path):
        cache = {}
        year_path = os.path.join(path, current_year)

        try:
            with os.scandir(year_path) as it:

                for entry in it:

                    if entry.is_dir():

                        cache[entry.name] = entry.path

        except Exception as e:
            print(f"[NOVA CACHE ERROR] {year_path} -> {e}")

        return cache

    # =========================
    # BUILD CACHES
    # =========================
    cache_Tomo2 = build_cache(network_path_Tomo2)
    cache_Tomo4 = build_cache(network_path_Tomo4)
    cache_Tomo7 = build_cache(network_path_Tomo7)

    cache_EC = build_cache_nova(network_path_Nova["EC"])
    cache_Hyperarc = build_cache_nova(network_path_Nova["Hyperarc"])
    cache_RA = build_cache_nova(network_path_Nova["RA"])

    # =========================
    # FIND FOLDER IN CACHE
    # =========================
    def find_folder(cache, ipp):

        if not ipp:
            return None, None

        ipp = str(ipp).strip()

        for folder_name, folder_path in cache.items():

            if ipp in folder_name:

                return folder_name, folder_path

        return None, None

    # =========================
    # CHECK DICOM FILES
    # =========================
    def dicom_exists(folder_path):

        if not folder_path:
            return False

        try:

            for root, dirs, files in os.walk(folder_path):

                for file in files:

                    if file.lower().endswith(".dcm"):

                        return True

        except Exception as e:
            print(f"[DICOM ERROR] {folder_path} -> {e}")

        return False

    # =========================
    # PROCESS TOMO
    # =========================
    def process_tomo(table, cache):

        for row in table:

            folder_name, folder_path = find_folder(
                cache,
                row.get("ipp")
            )

            row["existing_folder"] = folder_name is not None
            row["existing_dicom"] = dicom_exists(folder_path)

            # mémorisation chemin trouvé
            row["folder_name"] = folder_name
            row["folder_path"] = folder_path

    # =========================
    # PROCESS NOVA
    # =========================
    def process_nova(table):

        for row in table:

            ipp = row.get("ipp")

            folder_name = None
            folder_path = None
            machine = None

            # =========================
            # EC
            # =========================
            folder_name, folder_path = find_folder(cache_EC, ipp)

            if folder_name:
                machine = "EC"

            # =========================
            # HYPERARC
            # =========================
            if not folder_name:

                folder_name, folder_path = find_folder(
                    cache_Hyperarc,
                    ipp
                )

                if folder_name:
                    machine = "Hyperarc"

            # =========================
            # RA
            # =========================
            if not folder_name:

                folder_name, folder_path = find_folder(
                    cache_RA,
                    ipp
                )

                if folder_name:
                    machine = "RA"

            row["existing_folder"] = folder_name is not None
            row["existing_dicom"] = dicom_exists(folder_path)

            # =========================
            # MEMORISATION
            # =========================
            row["folder_name"] = folder_name
            row["folder_path"] = folder_path
            row["nova_machine"] = machine

    # =========================
    # EXECUTION
    # =========================
    print("Explore folder Tomo2")
    process_tomo(Tomo2, cache_Tomo2)

    print("Explore folder Tomo4")
    process_tomo(Tomo4, cache_Tomo4)

    print("Explore folder Tomo7")
    process_tomo(Tomo7, cache_Tomo7)

    print("Explore folder Nova")
    process_nova(Nova)

def load_machine_schedule(session):
    # =========================================================
    # Recherche dans la Database des appointments de type 'Implant' programmés aujourd'hui sur les machines concernées (TOMO 2, TOMO 4, RADI 7, NOVA3, NOVA5, HALCYON6, HALCYON8), 
    # pour afficher les horaires de fin d'activité de la journée dans le dashboard
    # =========================================================
    # récupère la date du jour pour filtrer les appointments du jour
    today_start = datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    today_end = today_start + timedelta(days=1)

    # recherche les appointments du jour avec service_type 'Implant' sur les machines concernées
    rows = []
    opening_closing = (
        session.query(Appointments)
        .join(Patients)
        .options(joinedload(Appointments.patient))
        .filter(
            Appointments.service_type.ilike("Implant"),
            Appointments.device.in_([
                "TOMO 2",
                "Tomo4",
                "RADI 7",
                "0210462",
                "0210471",
                "NOVA3",
                "NOVA5",
                "HALCYON6",
                "HALCYON8"
            ]),
            Appointments.start_scheduled_period >= today_start,
            Appointments.start_scheduled_period < today_end
        )
        .all()
    )

    for appt in opening_closing:

        print(
            "FOUND:",
            appt.service_type,
            appt.device,
            appt.start_scheduled_period
        )
        patient = appt.patient

        rows.append({
            "machine": appt.device,
            "start": appt.start_scheduled_period,
            "end": appt.end_scheduled_period,
            "status": appt.status,
            "note": appt.comment or ""
        })

    return rows

def load_daily_qa(session):
    # =========================================================
    # LOAD DAILY QA TASKS
    # =========================================================
    qa_rows = []

    appointments = (
        session.query(Appointments)
        .join(Patients)
        .options(joinedload(Appointments.patient))
        .filter(
            or_(
                # NOVA
                Appointments.service_type.ilike("%cq physique%"),

                # TOMO
                (
                    Appointments.service_type.ilike("%cq patient%")
                    &
                    Appointments.device.in_(["TOMO 2", "Tomo4", "RADI 7", "0210462", "0210471"])
                )
            ),
            ~Appointments.device.ilike("HALCYON%")
        )
        .all()
    )

    print("CQ APPOINTMENTS:", len(appointments))

    for appt in appointments:

        patient = (
            session.query(Patients)
            .filter(Patients.id == appt.patient_id)
            .first()
        )

        qa_rows.append({

            "machine": appt.device,

            "task_display_focus": appt.service_type,

            "task_status": appt.status,

            "task_note": appt.comment or "",

            "met_start": appt.start_scheduled_period,

            "met_end": appt.end_scheduled_period,

            "last_name": patient.family_name_official,

            "first_name": patient.given,

            "service_type": appt.service_type,

            "ipp": patient.ipp,

            "existing_folder": False,

            "existing_dicom": False,

            "folder_path": ""
        })

    print("QA ROWS:", len(qa_rows))
    
    return qa_rows

def filter_today_qa(QA):
    # =========================================================
    # FILTER QA FOR TODAY + CURRENT + FUTURE
    # =========================================================
    now = datetime.now()

    today_start = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    today_end = today_start + timedelta(days=1)

    filtered = []

    for row in QA:

        met_start = row.get("met_start")
        met_end = row.get("met_end")

        if not met_start:
            continue

        # =========================
        # STATUS FILTER
        # =========================
        task_status = str(row.get("task_status") or "").lower()

        if task_status not in ["booked", "arrived"]:
            continue

        # =========================
        # ONLY TODAY
        # =========================
        if not (today_start <= met_start < today_end):
            continue

        # =========================
        # KEEP:
        # - FUTURE SLOT
        # - CURRENT SLOT
        # =========================
        is_future = met_start >= now

        is_current = (
            met_start <= now
            and met_end
            and now <= met_end
        )

        if is_future or is_current:
            filtered.append(row)

    # =========================
    # SORT BY START TIME
    # =========================
    filtered.sort(key=lambda x: x["met_start"])

    print("QA TODAY CURRENT/FUTURE:", len(filtered))

    return filtered

def load_data():
    # =========================================================
    # EXTRACT from database to array
    # =========================================================
    # Test database connection
    connection = engine.connect()
    print("\nDatabase connection OK")
    connection.close()

    # Recherche dans la Database des patients ayant des tâches de 'réalisation des CQ prêtes' en attente depuis moins de 14 jours
    session = SessionLocal()
    patients = (
        session.query(Patients)
        .join(Patients.careplans)
        .join(Careplans.tasks)
        .filter(
            or_(
                Tasks.display_focus.ilike("réalisation du cq%"),
                Tasks.display_focus.ilike("réalisation des cq%"),
                Tasks.display_focus.ilike("replanif%")
            ),
            Tasks.last_updated >= two_weeks_ago,
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
                        or task.display_focus.lower().startswith("replanif")
                    )
                ):

                     # FILTER STATUS 
                    task_status = task.status.lower() if task.status else ""
                    if "cancelled" in task_status:
                        continue

                    # =========================================================
                    # RECHERCHE DES 'MET' ASSOCIÉS AU PATIENT (pour info et tri)
                    # =========================================================
                    # récupérer les MET valides
                    met_appt = None
                    met_appointments = [
                        appt for appt in patient.appointments
                        if (
                            appt.service_type
                            and appt.service_type.upper().startswith("MET")
                            and appt.start_scheduled_period
                            and str(appt.status or "").lower() != "cancelled"
                        )
                    ]

                    # prendre la MET la plus récente (car le patient peut avoir eu un traitement par le passé)
                    met_appt = None
                    if met_appointments:
                        met_appt = max(
                            met_appointments,
                            key=lambda x: x.start_scheduled_period
                        )

                    # =========================================================
                    # RECHERCHE DE LA TACHE 'FINALISATION DOSSIER' => PHYSICIEN ou DOSIMETRISTE AYANT CRÉÉ LA TÂCHE CQ
                    # =========================================================
                    finalisation_tasks = [
                        t for cp in patient.careplans
                        for t in cp.tasks
                        if (
                            t.display_focus
                            and t.display_focus.lower().startswith("finalisation du dossier")
                        )
                    ]

                    # prendre la plus récente
                    latest_finalisation = None
                    if finalisation_tasks:
                        latest_finalisation = max(
                            finalisation_tasks,
                            key=lambda x: x.last_updated or datetime.min
                        )

                    # récupération du nom du physicien ou du dosimétriste
                    physicist = None
                    if latest_finalisation:
                        physicist = (
                            latest_finalisation.recipient
                            or latest_finalisation.recipient_id
                        )


                    # =========================================================
                    # RECHERCHE PROGRAMMATION CQ PATIENT DANS TIMEPLANNER
                    # =========================================================
                    # récupère la date du jour pour filtrer les tâches du jour
                    today_start = datetime.now().replace(
                        hour=0,
                        minute=0,
                        second=0,
                        microsecond=0
                    )
                    today_end = today_start + timedelta(days=1)

                    # recherche les tâches du jour avec display_focus 'CQ Patient' (pour info et tri)
                    cq_patient_today = False
                    cq_patient_appts = [
                        appt for appt in patient.appointments
                        if (
                            appt.service_type
                            and appt.service_type.strip().lower() == "cq patient"
                            and appt.start_scheduled_period
                            and today_start <= appt.start_scheduled_period < today_end
                            and str(appt.status or "").lower() != "cancelled"
                        )
                    ]

                    if cq_patient_appts:
                        cq_patient_today = True

                    # =========================================================
                    # MISE DANS UNE TABLE DES INFIRMATIONS COLLECTEES
                    # =========================================================
                    rows.append({
                        # PATIENT
                        "ipp": patient.ipp,
                        "last_name": patient.family_name_official,
                        "first_name": patient.given,
                        "physicist": physicist,

                        # CQ PATIENT
                        "cq_patient_today": cq_patient_today,

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


    # =========================================================
    # LOAD QA
    # =========================================================
    QA = load_daily_qa(session)
    QA = filter_today_qa(QA)
    MACHINE_SCHEDULE = load_machine_schedule(session)

    session.close()

    rows = clean_cq_rows(rows)
    print("\nROWS RAW:")
    print(rows)

       

    # =========================================================
    # FILTRE LES PATIENTS PAR MACHINE CONCERNEE (TOMO 2, TOMO 4, TOMO 7, NOVA)
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
    # TRIE DES PATIENTS PAR ORDRE DE PRIORITÉ DE LA MET (MET la plus proche en premier, puis les autres, les patients sans MET à la fin)
    # =========================================================
    Nova = sort_by_met_start(Nova)
    Tomo2 = sort_by_met_start(Tomo2)
    Tomo4 = sort_by_met_start(Tomo4)
    Tomo7 = sort_by_met_start(Tomo7)
    
    check_existing_folders(Nova, Tomo2, Tomo4, Tomo7)

    return Nova, Tomo2, Tomo4, Tomo7, QA, MACHINE_SCHEDULE

class MainWindow(QMainWindow):
    # =====================================================
    # INTERFACE QT
    # UPDATE QA HEADER
    # =====================================================
    def toggle_db_blink(self):
        # Clignottement du message d'alerte de la database SQL en cas de délai de refresh trop long (indication visuelle pour l'utilisateur)
        if not hasattr(self, "db_alert_level"):
            return

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

    def update_machine_footer(self, schedule):
        MACHINE_LABEL = {
            "TOMO2": "Tomo 2",
            "0210462": "Tomo 4",
            "NOVA3": "Nova 3",
            "NOVA5": "Nova 5",
            "HALCYON6": "Halcyon 6",
            "RADI7": "Radi 7",
            "HALCYON8": "Halcyon 8",
        }
        ORDER = {
            "TOMO2": 0,
            "NOVA3": 1,
            "0210462": 2,
            "NOVA5": 3,
            "HALCYON6": 4,
            "RADI7": 5,
            "HALCYON8": 6,
        }

        def normalize_machine(m):
            if not m:
                return ""
            return m.upper().replace(" ", "").replace("-", "")

        def get_priority(machine):
            return ORDER.get(normalize_machine(machine), 999)

        if not schedule:
            self.machine_label.setText("Fin de journée : aucune donnée")
            return

        schedule = sorted(
            schedule,
            key=lambda x: (
                get_priority(x.get("machine")),
                x.get("start") or datetime.max
            )
        )

        lines = []

        for row in schedule:

            machine = row.get("machine", "")
            start = row.get("start")

            if not start:
                continue

            hour = start.strftime("%H:%M")
            machine_key = normalize_machine(machine)
            machine_label = MACHINE_LABEL.get(machine_key, machine_key)
            lines.append(f"{machine_label}: {hour}")

        text = "Fin de journée :   " + "   |   ".join(lines)

        self.machine_label.setText(text)
  
    def update_qa_header(self, QA):

        if not QA:
            self.qa_label.setText("Aucun créneaux CQ trouvé aujourd'hui")
            return

        now = datetime.now()

        lines = []

        for row in QA:

            met_start = row.get("met_start")
            met_end = row.get("met_end")

            hour = met_start.strftime("%H:%M") if met_start else "--:--"

            machine = row.get("machine", "")
            service = row.get("service_type", "")

            # =========================
            # CURRENT SLOT ?
            # =========================
            is_current = (
                met_start
                and met_end
                and met_start <= now <= met_end
            )

            if is_current:

                line = (
                    f'<span style="color:red;">'
                    f'{hour} - {machine}'
                    f'</span>'
                )

            else:

                line = f"{hour} - {machine}"

            lines.append(line)

        text = (
            "Prochains créneaux CQ :   "
            + "   |   ".join(lines)
        )

        self.qa_label.setText(text)

    def refresh_data(self):
        global Tomo2, Tomo4, Tomo7, Nova, QA
        self.now = datetime.now()
        self.limit = add_business_days(self.now, 2)

        # =========================
        # SAVE CURRENT TAB
        # =========================
        current_index = self.tabs.currentIndex()

        # reload data
        Nova, Tomo2, Tomo4, Tomo7, QA, MACHINE_SCHEDULE = load_data()
        self.update_qa_header(QA)
        self.update_machine_footer(MACHINE_SCHEDULE)

        # puis update UI
        self.tabs.clear()

        self.tabs.addTab(
            self.create_table_tab(Tomo2),
            f"Tomo2 ({len(Tomo2)})"
        )

        self.tabs.addTab(
            self.create_table_tab(Tomo4),
            f"Tomo4 ({len(Tomo4)})"
        )

        self.tabs.addTab(
            self.create_table_tab(Tomo7),
            f"Tomo7 ({len(Tomo7)})"
        )

        self.tabs.addTab(
            self.create_table_tab(Nova),
            f"Nova(s) ({len(Nova)})"
        )

        # =========================
        # RESTORE TAB
        # =========================
        self.tabs.setCurrentIndex(current_index)

        # =========================
        # UPDATE LAST REFRESH TIME
        # =========================
        session = SessionLocal()
        last_db_update = get_last_database_update(session)
        session.close()
        self.last_refresh_label.setText(f"Dernier refresh (3 min) : {self.now.strftime('%Y-%m-%d %H:%M:%S')}")
        
        # =========================================================
        # Code couleur + clignottement sur l'indication du dernier refresh de la database SQL
        # =========================================================
        now = datetime.now()

        if last_db_update:
            delta_min = (now - last_db_update).total_seconds() / 60
        else:
            delta_min = None

        # =========================
        # UNKNOWN
        # =========================
        if last_db_update is None:

            self.db_alert_level = "unknown"

            self.db_label.setText(
                "⚪ SQL DataBase : inconnue"
            )

            self.db_label.setStyleSheet("""
                QLabel {
                    color: gray;
                    font-weight: bold;
                }
            """)

        # =========================
        # CRITICAL > 15 min
        # =========================
        elif delta_min > 15:

            self.db_alert_level = "critical"

            self.db_label.setText(
                f"🔴 SQL DataBase : {last_db_update.strftime('%Y-%m-%d %H:%M:%S')}"
            )

        # =========================
        # ALERT > 10 min
        # =========================
        elif delta_min > 10:

            self.db_alert_level = "alert"

            self.db_label.setText(
                f"🔴 SQL DataBase : {last_db_update.strftime('%Y-%m-%d %H:%M:%S')}"
            )

            self.db_label.setStyleSheet("""
                QLabel {
                    color: red;
                    font-weight: bold;
                }
            """)

        # =========================
        # WARNING > 5 min
        # =========================
        elif delta_min > 5:

            self.db_alert_level = "warning"

            self.db_label.setText(
                f"🟠 SQL DataBase : {last_db_update.strftime('%Y-%m-%d %H:%M:%S')}"
            )

            self.db_label.setStyleSheet("""
                QLabel {
                    color: orange;
                    font-weight: bold;
                }
            """)

        # =========================
        # OK
        # =========================
        else:

            self.db_alert_level = "ok"

            self.db_label.setText(
                f"🟢 SQL DataBase : {last_db_update.strftime('%Y-%m-%d %H:%M:%S')}"
            )

            self.db_label.setStyleSheet("""
                QLabel {
                    color: green;
                    font-weight: bold;
                }
            """)

    def __init__(self):
        super().__init__()

        self.setWindowTitle("CQ Dashboard")
        self.resize(1200, 700)

        self.now = datetime.now()
        self.limit = add_business_days(self.now, 2)

        
        # =========================
        # ROOT WIDGET
        # =========================
        root = QWidget()
        root_layout = QVBoxLayout()

        # =========================
        # QA LABEL
        # =========================
        self.qa_label = QLabel()
        self.qa_label.setStyleSheet("""
            QLabel {
                background-color: #FFF3CD;
                border: 1px solid #FFCC00;
                padding: 8px;
                font-size: 14px;
                font-weight: bold;
            }
        """)
        root_layout.addWidget(self.qa_label)

        self.machine_label = QLabel()
        self.machine_label.setStyleSheet("""
            QLabel {
                background-color: #E8F0FE;
                border: 1px solid #4A90E2;
                padding: 8px;
                font-size: 14px;
                font-weight: bold;
            }
        """)

        root_layout.addWidget(self.machine_label)


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
        # FIRST LOAD
        # =========================
        self.refresh_data()

        # =========================
        # AUTO REFRESH TIMER
        # =========================
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh_data)
        self.timer.start(180_000)  # 3 minutes

        # =========================
        # BLINK TIMER SQL STATUS
        # =========================
        self.blink_state = False

        self.blink_timer = QTimer()
        self.blink_timer.timeout.connect(self.toggle_db_blink)
        self.blink_timer.start(500)  # 500 ms
        

    def create_table_tab(self, data):

        widget = QWidget()
        layout = QVBoxLayout()

        table = QTableWidget()
        table.setColumnCount(12)

        table.setHorizontalHeaderLabels([
            "Status",
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
            "Adress"
        ])

        # info popup sur les headers
        header_tooltips = [
            "Priorité du dossier",
            "Date MET prévue",
            "Nom du patient",
            "ID patient",
            "Type de tâche CQ",
            "Statut de la tâche",
            "Personne ayant crée le CQ et fait les exports dicom",
            "Note associée à la tâche",
            "CQ Patient programmé pour aujourd'hui dans Timeplanner ?",
            "Dossier patient existant sur le réseau IUCT ?",
            "Fichiers DICOM (calculs) présents dans le dossier ?",
            "Nom du dossier où le calcul à été exporté"
        ]

        for col in range(len(header_tooltips)):
            table.horizontalHeaderItem(col).setToolTip(header_tooltips[col])
        
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
            # ICONS
            # =========================
            folder_ok = patient.get("existing_folder", False)
            dicom_ok = patient.get("existing_dicom", False)
            cq_patient_ok = patient.get("cq_patient_today", False)

            folder_icon = "✅" if folder_ok else "❌"
            dicom_icon = "✅" if dicom_ok else "❌"
            cq_patient_icon = "✅" if cq_patient_ok else "❌"

            # =========================
            # ADRESS DISPLAY
            # =========================
            folder_path = str(patient.get("folder_path") or "")

            adress = ""

            if "Hyperarc" in folder_path:
                adress = "Hyperarc"

            elif "EC_IUC" in folder_path:
                adress = "EC"

            elif "Patients_RA" in folder_path:
                adress = "RA"

            # =========================
            # CREATE ITEMS
            # =========================
            item0 = QTableWidgetItem(dot)
            item1 = QTableWidgetItem(str(met_date))
            item2 = QTableWidgetItem(f'{patient["last_name"]} {patient["first_name"]}')
            item3 = QTableWidgetItem(str(patient["ipp"]))
            item4 = QTableWidgetItem(str(patient["task_display_focus"]))
            item5 = QTableWidgetItem(str(patient["task_status"]))
            item_physicist = QTableWidgetItem(str(patient.get("physicist") or ""))
            item7 = QTableWidgetItem(str(patient.get("task_note") or ""))
            item_cq_patient = QTableWidgetItem(cq_patient_icon)

            # nouvelles colonnes
            item8 = QTableWidgetItem(folder_icon)
            item9 = QTableWidgetItem(dicom_icon)
            item10 = QTableWidgetItem(adress)

            # =========================
            # CENTER ICONS
            # =========================
            item7.setTextAlignment(Qt.AlignCenter)
            item8.setTextAlignment(Qt.AlignCenter)
            item9.setTextAlignment(Qt.AlignCenter)
            item_cq_patient.setTextAlignment(Qt.AlignCenter)

            # =========================
            # TOOLTIP + COLOR (sur toute la ligne)
            # =========================
            items = [
    item0,
    item1,
    item2,
    item3,
    item4,
    item5,
    item_physicist,
    item7,
    item_cq_patient,
    item8,
    item9,
    item10
]
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
            table.setItem(row, 6, item_physicist)
            table.setItem(row, 7, item7)
            table.setItem(row, 8, item_cq_patient)
            table.setItem(row, 9, item8)
            table.setItem(row, 10, item9)
            table.setItem(row, 11, item10)


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