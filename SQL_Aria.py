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
    QPushButton,
    QStatusBar,
    QVBoxLayout
)
from PySide6.QtGui import QColor, QBrush
from PySide6.QtCore import QTimer
from PySide6.QtCore import Qt

from pathlib import Path
from PySide6.QtWidgets import QWidget, QCheckBox, QHBoxLayout
from PySide6.QtCore import Qt

from PySide6.QtWidgets import QMessageBox
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
    print("family name official:", patient.family_name_official)
    print("Given:", patient.given)
    print("Birth date:", patient.birth_date)
    print("family name mainden:", patient.family_name_maiden)
    print("Gender:", patient.gender)
    print("IPP:", patient.ipp)
    print("Telecom:", patient.telecom)
    print("Adress:", patient.address)
    print("Created at:", patient.created_at)
    print("Last updated:", patient.last_updated)

    # =========================
    # APPOINTMENTS
    # =========================
    print("\n================ APPOINTMENTS ================")
    for a in patient.appointments:
        print("ID:", a.id)
        print("Status:", a.status)
        print("Code:", a.code)
        print("Service type:", a.service_type)
        print("Service category:", a.service_category)
        print("Is active:", a.is_active)
        print("Start:", a.start_scheduled_period)
        print("End:", a.end_scheduled_period)
        print("Instance:", a.instance)
        print("User note:", a.user_note)
        print("Minutes duration:", a.minutes_duration)
        print("Comment:", a.comment)
        print("Device:", a.device)
        print("Physician id:", a.physician_id)
        print("Physician:", a.physician)
        print("Patient id:", a.patient_id)
        print("Created at:", a.created_at)
        print("last updated:", a.last_updated)
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
            print("  Minutes duration:", t.minutes_duration)
            print("  Activity definition id:", t.activitydefinition_id)
            print("  Based On:", t.basedOn)
            print("  Restriction period end:", t.restriction_period_end)
            print("  ExecutionPeriod:", t.executionPeriod)
            print("  LastModified:", t.lastModified)
            print("  Authored On:", t.authoredOn)
            print("  Category:", t.category)
            print("  Note:", t.note)
            print("  Recipient:", t.recipient)
            print("  Recipient ID:", t.recipient_id)
            print("  Careplan id:", t.careplan_id)
            print("  Created at:", t.created_at)
            print("  Last updated:", t.last_updated)
            print("  Device:", t.device)
            print("  CAREPLAN id:", cp.id)
            print("  CAREPLAN Title:", cp.title)
            print("  CAREPLAN Note:", cp.note)
            print("  CAREPLAN Patient id:", cp.patient_id)
            print("  CAREPLAN Created at:", cp.created_at)
            print("  CAREPLAN Last_updated:", cp.last_updated)
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
        print("Id:", log.id)
        print("Last task request:", log.last_task_request)
        print("Last appointment request:", log.last_appointment_request)
        print("Created at:", log.created_at)
        print("Last updated:", log.last_updated)


# =========================================================
# EXECUTION (Don't forget to add a stop point.)
# =========================================================
# Get all data patient (DEBUG CALL)
session = SessionLocal()
dump_patient_full(session, "201600480")
session.close()
"""
#endregion

def load_today_patients_by_machine(session):
    remaining_today = {}
    now = datetime.now()

    today_start = datetime.now().replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    today_end = today_start + timedelta(days=1)

    appointments = (
        session.query(Appointments)
        .join(Appointments.patient)
        .filter(
            Appointments.start_scheduled_period >= today_start,
            Appointments.start_scheduled_period < today_end
        )
        .all()
    )

    machines = {
        "TOMO2": [],
        "0210462": [],
        "RADI7": [],
        "NOVA3": [],
        "NOVA5": [],
        "HALCYON6": [],
        "HALCYON8": []
    }

    for appt in appointments:

        device = (
            str(appt.device).upper().strip()
            if appt.device
            else ""
        )

        if device == "TOMO 2":
            machine = "TOMO2"

        elif device == "0210462":
            machine = "0210462"

        elif device == "RADI 7":
            machine = "RADI7"

        elif device == "NOVA3":
            machine = "NOVA3"

        elif device == "NOVA5":
            machine = "NOVA5"

        elif device == "HALCYON6":
            machine = "HALCYON6"

        elif device == "HALCYON8":
            machine = "HALCYON8"

        else:
            continue

        # =========================
        # EXCLUSION CQ
        # =========================
        service_type = str(appt.service_type or "").lower()
        last_name = str(appt.patient.family_name_official or "").lower()
        # =========================
        # GARDER CQ HEBDOMADAIRE
        # =========================
        if "cq hebdomadaire" in last_name:
            pass  # on laisse passer cette tâche

        # =========================
        # EXCLUSION CQ PATIENT / PHYSIQUE
        # =========================
        elif (
            "cq patient" in service_type
            or "cq physique" in service_type
        ):
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
            "device": device,
            "service_type": appt.service_type,
        })

    # ==========================================
    # Tri chronologique croissant
    # ==========================================
    for machine_patients in machines.values():

        machine_patients.sort(
            key=lambda p: p["start"] or datetime.max
        )

    # ==========================================
    # DECOMPTE DU NOMBRE DE PATIENT AVANT CRENEAU CQ HEBDOMADAIRE
    # ==========================================
    compte_down = {}

    for machine, patients in machines.items():

        countdown = None
        index = 0

        for p in patients:

            status = str(p.get("status") or "").lower()
            last_name = str(p.get("last_name") or "")
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

        # =========================
        # RESULT
        # =========================
        if countdown is None:
            compte_down[machine] = "none"
        else:
            compte_down[machine] = countdown
    

    # =========================
    # CALCUL DU NOMBRE DE PATIENTS RESTANT AVANT LA FIN DE LA JOURNEE
    # =========================

    for machine, patients in machines.items():

        count = 0

        for p in patients:
            if p.get("id") == 4:
                continue

            service_type = str(p.get("service_type") or "").lower()
            if (
                #"cq" in service_type
                "consultation" in service_type
                #or "cq patient" in service_type
                #or "cq physique" in service_type
                or "interne" in service_type
                or "sang" in service_type
            ):
                continue

            status = str(p.get("status") or "").lower()

            if status in ["cancelled", "fulfilled"]:
                continue

            if p["start"] >= now:
                count += 1

        remaining_today[machine] = count

    return machines, compte_down, remaining_today

def check_qa_overlap(qa_row, machines):

    machine = qa_row.get("machine")
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
        # EXCLUSION DE LA TACHE CQ
        # =========================
        if (
            abs((task_start - met_start).total_seconds()) < 60
            and
            abs((task_end - met_end).total_seconds()) < 60
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

def create_centered_checkbox(checked=True):
    # =========================================================
    # Affiche la case à cocher de la colonne 'Select' au milieu de la cellule du tableau
    # =========================================================
    widget = QWidget()
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)

    checkbox = QCheckBox()
    checkbox.setChecked(checked)

    layout.addWidget(checkbox, alignment=Qt.AlignCenter)

    widget.checkbox = checkbox   # 👈 IMPORTANT

    return widget

def print_query_log(session):
    # =========================================================
    # Affiche les dernières requêtes ARIA ayant alimenté la DB
    # =========================================================

    log = (
        session.query(QueryLog)
        .order_by(QueryLog.id.desc())
        .first()
    )

    if not log:
        print("Aucune entrée dans query_log")
        return

    print("\n================ QUERY LOG ================")
    print("ID :", log.id)
    print("last_task_request :", log.last_task_request)
    print("last_appointment_request :", log.last_appointment_request)
    print("created_at :", log.created_at)
    print("last_updated :", log.last_updated)
    print("==========================================\n")

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
    # CHECK DICOM AND PDF FILES
    # =========================
    def check_dicom_and_pdf(folder_path):

        if not folder_path:
            return False, False, None

        try:

            dcm_date = None

            # =========================
            # RECHERCHE DICOM
            # =========================
            for root, dirs, files in os.walk(folder_path):

                for file in files:

                    if file.lower().endswith(".dcm"):

                        full_path = os.path.join(root, file)

                        dcm_date = datetime.fromtimestamp(
                            os.path.getmtime(full_path)
                        )

                        break

                if dcm_date:
                    break

            # aucun DICOM
            if not dcm_date:
                return False, False, None

            # =========================
            # RECHERCHE PDF
            # =========================
            pdf_ok = False
            valid_pdf_date = None

            for root, dirs, files in os.walk(folder_path):

                for file in files:

                    if file.lower().endswith(".pdf"):

                        pdf_path = os.path.join(root, file)

                        pdf_date = datetime.fromtimestamp(
                            os.path.getmtime(pdf_path)
                        )

                        # même jour ou plus récent
                        if pdf_date.date() >= dcm_date.date():

                            pdf_ok = True
                            valid_pdf_date = pdf_date
                            break

                if pdf_ok:
                    break

            return True, pdf_ok, valid_pdf_date

        except Exception as e:

            print(f"[DICOM/PDF ERROR] {folder_path} -> {e}")

            return False, False, None

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

            dicom_ok, pdf_ok, pdf_date = check_dicom_and_pdf(folder_path)

            row["existing_dicom"] = dicom_ok
            row["existing_pdf"] = pdf_ok
            row["pdf_date"] = pdf_date

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
            dicom_ok, pdf_ok, pdf_date = check_dicom_and_pdf(folder_path)


            row["existing_dicom"] = dicom_ok
            row["existing_pdf"] = pdf_ok
            row["pdf_date"] = pdf_date

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

            "patient_id": patient.id,

            "patient_id": appt.patient_id,

            "family_name_official": patient.family_name_official,

            "existing_folder": False,

            "existing_dicom": False,

            "existing_pdf": False,

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
        # FILTER CQ TYPE
        # Affiche seulement :
        # - family_name_official contenant "CQ HEBDOMADAIRE"
        # - OU patient_id == 3
        # =========================
        family_name = str(
            row.get("family_name_official") or ""
        ).upper()

        patient_id = row.get("patient_id")

        if (
            "CQ HEBDOMADAIRE" not in family_name
            and patient_id != 3
        ):
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
    try:
        connection = engine.connect()
        print("\nDatabase connection OK")
        connection.close()

    except Exception as e:
        print("DATABASE CONNECTION ERROR")
        print(e)
        raise
    #connection = engine.connect()
    #print("\nDatabase connection OK")
    #connection.close()

    session = SessionLocal()
    print_query_log(session)
    rows = []
    rows2=[]
    # =========================================================
    # RECHERCHE DE TOUS LES CQ PATIENT PROGRAMME DANS TIMEPLANNER (POUR AUJOURD'HUI) - ETAPE 1/2
    # =========================================================
    today_start = datetime.now().replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    today_end = today_start + timedelta(days=1)

    all_cq_patient_appts = (
    session.query(Appointments)
    .filter(
        Appointments.start_scheduled_period >= today_start,
        Appointments.start_scheduled_period < today_end,
        Appointments.service_type.ilike("%cq patient%"),
        ~func.lower(Appointments.status).in_([
            "cancelled",
            "fulfilled"
        ])
    )
    .all()
)

    print("CQ patient appointments found:", len(all_cq_patient_appts))

    # =========================================================
    # Recherche dans la Database des patients ayant des tâches de 'réalisation des CQ prêtes' en attente depuis moins de 14 jours
    # =========================================================
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

    # =========================================================
    # Recherche dans la Database des patients avec :
    # - Validation Médicale de la dosimétrie = completed ou ready
    # - Finalisation du dossier = draft
    # Correspond aux patients dont le CQ est imminent
    # =========================================================

    rows2 = []

    patients_validation = (
        session.query(Patients)
        .join(Patients.careplans)
        .join(Careplans.tasks)
        .filter(
            Tasks.last_updated >= two_weeks_ago
        )
        .options(
            joinedload(Patients.careplans)
            .joinedload(Careplans.tasks)
        )
        .distinct()
        .all()
    )

    print(
        f"Patients potentiellement éligibles CQ imminent : "
        f"{len(patients_validation)}"
    )

    added_patient_ids = set()

    for patient in patients_validation:

        # ==========================================
        # Validation Médicale de la dosimétrie = completed ou ready ou in-progress
        # ==========================================
        validation_tasks = [
            t
            for cp in patient.careplans
            for t in cp.tasks
            if (
                t.display_focus
                and t.display_focus.lower().startswith(
                    "validation médicale de la dosimétrie"
                )
                and str(t.status or "").lower() in [
                    "completed",
                    "in-progress",
                    "ready"
                ]
                and t.last_updated
                and t.last_updated >= two_weeks_ago
            )
        ]

        if not validation_tasks:
            continue

        # validation la plus récente
        latest_validation = max(
            validation_tasks,
            key=lambda x: x.last_updated
        )

        validation_date = latest_validation.last_updated

        # ==========================================
        # Finalisation du dossier = draft
        # ==========================================
        finalisation_tasks = [
            t
            for cp in patient.careplans
            for t in cp.tasks
            if (
                t.display_focus
                and t.display_focus.lower().startswith(
                    "finalisation du dossier"
                )
                and str(t.status or "").lower() == "draft"
                and t.last_updated
                and t.last_updated >= two_weeks_ago
            )
        ]

        if not finalisation_tasks:
            continue

        # ==========================================
        # Réalisation du TDM
        # ==========================================
       
        tdm_tasks = [
            t
            for cp in patient.careplans
            for t in cp.tasks
            if (
                t.display_focus
                and t.display_focus.lower().startswith("scanner de simulation")
                and t.last_updated
            )
        ]

        latest_tdm = (
            max(tdm_tasks, key=lambda x: x.last_updated)
            if tdm_tasks
            else None
        )

        tdm_date = (
            latest_tdm.last_updated
            if latest_tdm
            else None
        )


        # ==========================================
        # Appeler patient (on récupère le status)
        # ==========================================
        
        appel_patient_tasks = [
            t
            for cp in patient.careplans
            for t in cp.tasks
            if (
                t.display_focus
                and t.display_focus.lower().startswith("appeler patient")
                and t.last_updated
                and t.last_updated >= two_weeks_ago
            )
        ]

        latest_appel = (
            max(appel_patient_tasks, key=lambda x: x.last_updated)
            if appel_patient_tasks
            else None
        )

        appel_patient_status = latest_appel.status if latest_appel else None

        # ==========================================
        # MET (si existant on prend la plus récente après la validation médicale)
        # ==========================================
        met_appointments = [
            appt
            for appt in patient.appointments
            if (
                appt.service_type
                and appt.service_type.upper().startswith("MET")
                and appt.start_scheduled_period
                and appt.start_scheduled_period > validation_date
                and str(appt.status or "").lower() != "cancelled"
            )
        ]

        met_appt = None

        if met_appointments:
            met_appt = max(
                met_appointments,
                key=lambda x: x.start_scheduled_period
            )

        met_start = (
            met_appt.start_scheduled_period
            if met_appt else None
        )

        # ==========================================
        # Réalisation dosi (récupération du nom exact)
        # ==========================================
        realisation_dosi_task_name = None

        for cp in patient.careplans:
            for t in cp.tasks:
                if (
                    t.display_focus
                    and "réalisation dosi" in t.display_focus.lower()
                    and t.last_updated
                    and t.last_updated >= two_weeks_ago
                ):
                    realisation_dosi_task_name = t.display_focus
                    break
            if realisation_dosi_task_name:
                break

        # ==========================================
        # Evite les doublons patients
        # ==========================================
        if patient.id in added_patient_ids:
            continue

        added_patient_ids.add(patient.id)

        # ==========================================
        # Récupération device depuis Tasks
        # ==========================================
        devices = list({
            t.device.name
            for cp in patient.careplans
            for t in cp.tasks
            if getattr(t, "device", None)
            and getattr(t.device, "name", None)
        })

        # ==========================================
        # Patient susceptible de tomber prochainement (si validation_tasks = completed ou ready ou in-progress et date de MET existante)
        # ==========================================
        va_tomber = bool(validation_tasks and met_start)

        # ==========================================
        # Construction de rows2
        # ==========================================
        rows2.append({

            "ipp": patient.ipp,
            "last_name": patient.family_name_official,
            "first_name": patient.given,
            "patient_id": patient.id,
            "tdm_date": tdm_date,
            "realisation_dosi_task": realisation_dosi_task_name,
            "Workflow": cp.title,
            "appel_patient_status": appel_patient_status,
            "validation_date": validation_date,
            "met_start": met_start,
            "va_tomber": va_tomber
        })

    print(f"Patients CQ imminent trouvés : {len(rows2)}")

    # ==========================================
    # Classement des patients en attente par machine d'attribution
    # ==========================================
    from collections import defaultdict

    Patient_EnAttente_count = defaultdict(int)
    Patient_EnAttente_details = {
        "Tomo 2": [],
        "Tomo 4": [],
        "Tomo 7": [],
        "Nova": []
    }

    for row in rows2:

        task_name = row.get("Workflow")

        if not task_name:
            continue

        task_name_lower = task_name.lower()

        if "tomo 2" in task_name_lower:
            machine = "Tomo 2"

        elif "tomo 4" in task_name_lower:
            machine = "Tomo 4"

        elif "tomo 7" in task_name_lower:
            machine = "Tomo 7"

        else:
            machine = "Nova"

        Patient_EnAttente_count[machine] += 1

        Patient_EnAttente_details[machine].append({
            "ipp": row["ipp"],
            "last_name": row["last_name"],
            "first_name": row["first_name"],
            "tdm_date": row["tdm_date"],
            "task": row["realisation_dosi_task"],
            "called": row["appel_patient_status"],
            "validation_date": row["validation_date"],
            "MET": row["met_start"],
            "workflow": row["Workflow"]
        })

    print("\nRépartition des machines :\n")

    for machine, count in Patient_EnAttente_count.items():
        print(f"{machine} = {count}")

    # =========================
    # DETERMINE SI LE CQ PATIENT EST CREE SUR TIMEPLANNER
    # On récupère dans la table 'all_cq_patient_appts' tous les numéro contenus dans 'patient_id' puis,
    # on vérifie dans toute la table 'patient' si il existe un 'id' = 'patient_id'.
    # si oui alors on ajoute 'patient_id' à 'cq_patient_ids' 
    # Etape 2/2
    # =========================

    # 1) récupère les numéros contenues dans 'patient_id' de la table 'all_cq_patient_appts'
    cq_patient_appt_ids = {
        appt.patient_id
        for appt in all_cq_patient_appts
        if appt.patient_id is not None
    }

    # 2) récupère les numéros d'ID de tous les patients de la table 'patients'
    patient_table_ids = {
        p.id for p in patients
    }

    # 3) retient UNIQUEMENT les numéros d'ID qui sont présents dans les 2 tables (cq_patient_appt_ids et patient_table_ids))
    cq_patient_ids = cq_patient_appt_ids.intersection(patient_table_ids)

    print("CQ patient IDs:", cq_patient_ids)

   

    for patient in patients:

        # =========================================================
        # RECHERCHE DES 'MET' ASSOCIÉS AU PATIENT (pour info et tri)
        # =========================================================
        # récupérer les MET valides
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
        cq_patient_today = patient.id in cq_patient_ids

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
                    # MISE DANS UNE TABLE DES INFIRMATIONS COLLECTEES
                    # =========================================================
                    cq_patient_today = patient.id in cq_patient_ids
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


    # =========================================================
    # RECUPERE LES PATIENTS PROGRAMMES PAR MACHINE
    # =========================================================
    machines, compte_down, remaining_today = load_today_patients_by_machine(session)

    return Nova, Tomo2, Tomo4, Tomo7, Patient_EnAttente_count, Patient_EnAttente_details, QA, MACHINE_SCHEDULE, machines, compte_down, remaining_today

from PySide6.QtWidgets import (
    QWidget,
    QPushButton,
    QLabel,
    QVBoxLayout
)

class CollapsibleWidget(QWidget):

    def __init__(self, title="Titre"):
        super().__init__()

        self.toggle_button = QPushButton()
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(False)

        self.toggle_button.setText(f"▶ {title}")

        self.toggle_button.setStyleSheet("""
            QPushButton {
                text-align: left;
                padding: 8px;
                font-weight: bold;
                background-color: #eaf7ea;
                border: 1px solid #bcdcbc;
                border-radius: 6px;
            }

            QPushButton:hover {
                background-color: #dff0df;
            }
        """)

        self.content = QLabel("")
        self.content.setVisible(False)  # fermé par défaut

        self.content.setStyleSheet("""
            QLabel {
                padding: 8px;
                background-color: white;
                border-left: 1px solid #bcdcbc;
                border-right: 1px solid #bcdcbc;
                border-bottom: 1px solid #bcdcbc;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.toggle_button)
        layout.addWidget(self.content)

        self.toggle_button.clicked.connect(self.toggle)

    def toggle(self):
        is_open = self.toggle_button.isChecked()

        self.content.setVisible(is_open)

        # changement du symbole ▶ / ▼
        if is_open:
            self.toggle_button.setText(self.toggle_button.text().replace("▶", "▼"))
        else:
            self.toggle_button.setText(self.toggle_button.text().replace("▼", "▶"))

class MainWindow(QMainWindow):
    # =====================================================
    # INTERFACE QT
    # UPDATE QA HEADER
    # =====================================================
    TIME_RULES = {
        "tomo": {
            "fixed": 7,   # montage + démontage 
            "per_case": 12 # mesure + exploitation
        },
        "ruby": {
            "fixed": 8,   # montage + démontage + calibrage 
            "per_case": 12 # mesure + exploitation
        },
        "octa": {
            "fixed": 11,   # montage + démontage + calibrage
            "per_case": 9 # mesure + exploitation
        }
    }

    DEFAULT_TIME = {
        "fixed": 10,
        "per_case": 15
    }

    def refresh_current_tab_footer(self):
        index = self.tabs.currentIndex()
        self.on_tab_changed(index)

    def calculate_estimated_time(self, table_widget, tab_name):
        # =========================================================
        # Calcul du temps estimé pour la réalisation des CQ en fonction du nombre de patients sélectionnés dans le tableau, et de la machine concernée (TOMO 2, TOMO 4, TOMO 7, NOVA)
        # =========================================================
        used_categories = {
            "tomo": 0,
            "ruby": 0,
            "octa": 0
        }

        total_minutes = 0

        # =========================
        # 1) COMPTER LES CASES
        # =========================
        for row in range(table_widget.rowCount()):

            cell_widget = table_widget.cellWidget(row, 1)
            if not cell_widget:
                continue

            checkbox = getattr(cell_widget, "checkbox", None)
            if not checkbox or not checkbox.isChecked():
                continue

            task_item = table_widget.item(row, 5)
            task_text = (task_item.text() if task_item else "").lower()

            matched = False

            for key in used_categories.keys():
                if key in task_text:
                    used_categories[key] += 1
                    matched = True
                    break

            if not matched:
                # catégorie inconnue → fallback
                used_categories.setdefault("other", 0)
                used_categories["other"] += 1

        # =========================
        # 2) CALCUL TEMPS
        # =========================
        for key, count in used_categories.items():

            if count == 0:
                continue

            rule = self.TIME_RULES.get(key, self.DEFAULT_TIME)

            # 1 seul montage/démontage par catégorie utilisée
            total_minutes += rule["fixed"]

            # temps par CQ
            total_minutes += count * rule["per_case"]

        return total_minutes
    
    def on_tab_changed(self, index):

        tab_text = self.tabs.tabText(index)
        tab_name = tab_text.split("(")[0].strip()

        widget = self.tabs.widget(index)

        # =========================
        # FOOTER TEMPS
        # =========================
        if hasattr(widget, "footer_label") and hasattr(widget, "table"):

            total = self.calculate_estimated_time(
                widget.table,
                tab_name
            )

            widget.footer_label.setText(
                f"Temps estimé ({tab_name}) suivant sélection : {total} min"
            )

        # =========================
        # PATIENTS EN ATTENTE
        # =========================
        if hasattr(widget, "patient_widget"):

            counts = getattr(
                self,
                "Patient_EnAttente_count",
                {}
            )

            details = getattr(
                self,
                "Patient_EnAttente_details",
                {}
            )

            # -------------------------
            # Détermination machine
            # -------------------------
            machine = None

            if tab_name == "Tomo2":
                machine = "Tomo 2"

            elif tab_name == "Tomo4":
                machine = "Tomo 4"

            elif tab_name == "Tomo7":
                machine = "Tomo 7"

            elif "Nova" in tab_name:
                machine = "Nova"

            # -------------------------
            # Titre du widget
            # -------------------------
            count = counts.get(machine, 0)

            title = (
                f"Patients en cours de préparation : "
                f"{machine} = {count}"
            )

            widget.patient_widget.toggle_button.setText(
                f"▼ {title}"
            )

            # -------------------------
            # Contenu du widget
            # -------------------------
            patients = details.get(machine, [])

            if not patients:

                widget.patient_widget.content.setText(
                    "Aucun patient en préparation"
                )

            else:

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
                        p.get("MET").strftime("%d/%m/%Y %H:%M")
                        if p.get("MET")
                        else "-"
                    )
                    called = p.get("called") or "-"
                    workflow = p.get("workflow") or "-"
                    validation_date = (
                        p.get("validation_date").strftime("%d/%m/%Y")
                        if p.get("validation_date")
                        else "-"
                    )
                    tdm_date = (
                        p.get("tdm_date").strftime("%d/%m/%Y")
                        if p.get("tdm_date")
                        else "-"
                    )

                    html += f"""
                    <tr style="color:{color};">
                        <td>{p['ipp']}</td>
                        <td>{p['last_name']} {p['first_name']}</td>
                        <td>{tdm_date}</td>
                        <td>{validation_date}</td>
                        <td>{called}</td>
                        <td>{met}</td>
                        <td>{workflow}</td>
                    </tr>
                    """

                html += "</table>"

                widget.patient_widget.content.setText(html)

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

    def update_machine_footer(self, schedule, remaining_today):
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

            count = remaining_today.get(machine_key, None)
            
            if count is None or count == "none":
                extra = ""
            else:
                extra = f" ({count})"

            lines.append(f"{machine_label}: {hour}{extra}*")

        text = "Fin de journée :   " + "   |   ".join(lines)

        self.machine_label.setText(text)
    
    def check_qa_overlap(self, qa_row, machines):

        machine = qa_row.get("machine")
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
            # EXCLUSION DE LA TACHE CQ
            # =========================
            if (
                abs((task_start - met_start).total_seconds()) < 60
                and
                abs((task_end - met_end).total_seconds()) < 60
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

    def update_qa_header(self, QA, compte_down, machines):

        if not QA:
            self.qa_label.setText("Aucun créneaux CQ trouvé aujourd'hui")
            return

        now = datetime.now()
        lines = []

        for row in QA:

            met_start = row.get("met_start")
            met_end = row.get("met_end")
            machine = row.get("machine", "")

            hour = met_start.strftime("%H:%M") if met_start else "--:--"

            # =========================
            # COUNTDOWN CQ
            # =========================
            comptedown = compte_down.get(machine, None)

            if comptedown is None or comptedown == "none":
                extra = ""
            else:
                extra = f" ({comptedown})*"

            # =========================
            # OVERLAP CHECK CQ
            # =========================
            reduction = self.check_qa_overlap(row, machines)

            if reduction > 0:
                extra += f" ⚠️ -{reduction} min"

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
                    f'{hour} - {machine}{extra}'
                    f'</span>'
                )
            else:
                line = f"{hour} - {machine}{extra}"

            lines.append(line)

        self.qa_label.setText(
            "Prochains créneaux CQ :   " + "   |   ".join(lines)
        )

    def refresh_data(self):
        print("Tentative de refresh :", datetime.now())
        global Tomo2, Tomo4, Tomo7, Nova, QA
        self.now = datetime.now()
        self.limit = add_business_days(self.now, 2)

        # =========================
        # SAVE CURRENT TAB
        # =========================
        current_index = self.tabs.currentIndex()

        # reload data
        try:

            Nova, Tomo2, Tomo4, Tomo7, Patient_EnAttente_count, Patient_EnAttente_details, QA, MACHINE_SCHEDULE, machines, compte_down, remaining_today = load_data()
            self.Patient_EnAttente_count = Patient_EnAttente_count
            self.Patient_EnAttente_details = Patient_EnAttente_details

            # La connexion revient après une panne
            if self.db_error_shown:

                QMessageBox.information(
                    self,
                    "Connexion rétablie",
                    "La connexion à la base SQL est de nouveau disponible."
                )

            self.db_error_shown = False

        except Exception as e:

            if not self.db_error_shown:

                QMessageBox.critical(
                    self,
                    "Erreur SQL",
                    f"Impossible de se connecter à la base de données.\n\n{e}"
                )

                self.db_error_shown = True

            return
        
        self.update_qa_header(QA,compte_down,machines)
        self.update_machine_footer(MACHINE_SCHEDULE,remaining_today)

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
        self.resize(1400, 500)

        self.now = datetime.now()
        self.limit = add_business_days(self.now, 2)
        self.db_error_shown = False
        
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

        self.qa_legend = QLabel("* : patients restants")
        self.qa_legend.setStyleSheet("""
            QLabel {
                color: gray;
                font-size: 11px;
                padding-left: 5px;
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
        table.setColumnCount(14)

        footer_label = QLabel("Temps estimé suivant sélection :")
        footer_label.setStyleSheet("""
            QLabel {
                background-color: #f0f0f0;
                padding: 6px;
                font-weight: bold;
                border-top: 1px solid #ccc;
            }
        """)

        patient_widget = CollapsibleWidget("Patients en cours de préparation")

        table.setHorizontalHeaderLabels([
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
            "Personne ayant créé le CQ et fait les exports dicom",
            "Note associée à la tâche",
            "CQ Patient programmé pour aujourd'hui dans Timeplanner ?",
            "Dossier patient existant sur le réseau IUCT ?",
            "Fichiers DICOM (calculs) présents dans le dossier ?",
            "Rapport PDF présent et daté du même jour ou après les exports DICOM",
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
            cq_patient_ok = patient.get("Timeplanner", False)
            pdf_ok = patient.get("existing_pdf", False)


            folder_icon = "✅" if folder_ok else "❌"
            dicom_icon = "✅" if dicom_ok else "❌"
            cq_patient_icon = "✅" if cq_patient_ok else "❌"
            pdf_icon = "✅" if pdf_ok else "❌"

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
            item_select_widget = create_centered_checkbox(True)
            item_select_widget.checkbox.stateChanged.connect(
                lambda _: self.refresh_current_tab_footer()
            )
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
            item_pdf = QTableWidgetItem(pdf_icon)
            pdf_date = patient.get("pdf_date")

            if pdf_date:
                item_pdf.setToolTip(
                    f"PDF validé : {pdf_date.strftime('%d/%m/%Y %H:%M')}"
                )
            item10 = QTableWidgetItem(adress)

            # =========================
            # CENTER ICONS
            # =========================
            item7.setTextAlignment(Qt.AlignCenter)
            item8.setTextAlignment(Qt.AlignCenter)
            item9.setTextAlignment(Qt.AlignCenter)
            item_cq_patient.setTextAlignment(Qt.AlignCenter)
            item_pdf.setTextAlignment(Qt.AlignCenter)
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
    item_pdf,
    item10
]
            for i in items:
                i.setBackground(QBrush(color))

                if i is not item_pdf:
                    i.setToolTip(tooltip)

            # =========================
            # INSERT INTO TABLE
            # =========================
            table.setItem(row, 0, item0)
            table.setCellWidget(row, 1, item_select_widget)
            table.setItem(row, 2, item1)
            table.setItem(row, 3, item2)
            table.setItem(row, 4, item3)
            table.setItem(row, 5, item4)
            table.setItem(row, 6, item5)
            table.setItem(row, 7, item_physicist)
            table.setItem(row, 8, item7)
            table.setItem(row, 9, item_cq_patient)
            table.setItem(row, 10, item8)
            table.setItem(row, 11, item9)
            table.setItem(row, 12, item_pdf)
            table.setItem(row, 13, item10)


        table.resizeColumnsToContents()

        self.tabs.currentChanged.connect(self.on_tab_changed)

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
app = QApplication(sys.argv)

window = MainWindow()
window.show()

app.exec()