# -*- coding: utf-8 -*-
import traceback
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy import create_engine, or_, func
from sqlalchemy.orm import sessionmaker, joinedload, Session
from PySide6.QtCore import QThread, Signal

# Import du modele ORM
from models import Patients, Careplans, Tasks, Appointments, QueryLog

from config import (
    DATABASE_URL, LOOKBACK_DAYS, PATIENT_ID_EXCLUDED_FROM_REMAINING,
    CQ_PHYSIQUE_PATIENT_ID, SCHEDULE_DEVICES, TOMO_DEVICES, WAITING_KEYS,
    DashboardData, log, day_bounds, machine_key_from_device, machine_display,
    normalize_device, sort_by_met_start, clean_cq_rows
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=1800, pool_size=2, max_overflow=0)
SessionLocal = sessionmaker(bind=engine)

def get_last_database_update(session: Session):
    return session.query(func.max(Tasks.last_updated)).scalar()

def load_today_patients_by_machine(session: Session):
    now = datetime.now()
    today_start, today_end = day_bounds(now)
    appointments = session.query(Appointments).join(Appointments.patient).options(joinedload(Appointments.patient)).filter(
        Appointments.start_scheduled_period >= today_start,
        Appointments.start_scheduled_period < today_end,
    ).all()

    machines = defaultdict(list)
    for appt in appointments:
        machine = machine_key_from_device(appt.device)
        if not machine: continue

        service_type = str(appt.service_type or "").lower()
        last_name = str(appt.patient.family_name_official or "").upper()
        
        if "CQ HEBDOMADAIRE" not in last_name and ("cq patient" in service_type or "cq physique" in service_type):
            continue

        machines[machine].append({
            "id": appt.patient.id,
            "patient_id": appt.patient_id,
            "last_name": appt.patient.family_name_official,
            "first_name": appt.patient.given,
            "start": appt.start_scheduled_period,
            "end": appt.end_scheduled_period,
            "status": appt.status,
            "device": normalize_device(appt.device),
            "service_type": appt.service_type,
        })

    for m in machines.values(): m.sort(key=lambda p: p["start"] or datetime.max)

    compte_down = {}
    for machine, patients in machines.items():
        countdown = next((i for i, p in enumerate(patients) if "CQ HEBDOMADAIRE" in str(p.get("last_name") or "").upper() and p.get("status") in ["arrived", "booked"]), None)
        compte_down[machine] = "none" if countdown is None else countdown

    remaining_today = {}
    for machine, patients in machines.items():
        count = sum(1 for p in patients if p.get("id") != PATIENT_ID_EXCLUDED_FROM_REMAINING and "consultation" not in str(p.get("service_type") or "").lower() and str(p.get("status") or "").lower() not in ["cancelled", "fulfilled"] and p["start"] and p["start"] >= now)
        remaining_today[machine] = count

    return dict(machines), compte_down, remaining_today

def load_machine_schedule(session: Session):
    today_start, today_end = day_bounds()
    appointments = session.query(Appointments).filter(
        Appointments.service_type.ilike("Implant"),
        Appointments.device.in_(SCHEDULE_DEVICES),
        Appointments.start_scheduled_period >= today_start,
        Appointments.start_scheduled_period < today_end,
    ).all()
    
    return [{
        "machine_key": machine_key_from_device(a.device),
        "machine_raw": a.device,
        "start": a.start_scheduled_period,
        "end": a.end_scheduled_period,
        "status": a.status,
        "note": a.comment or "",
    } for a in appointments]

def load_daily_qa(session: Session):
    now = datetime.now()
    today_start, today_end = day_bounds(now)
    appointments = session.query(Appointments).options(joinedload(Appointments.patient)).filter(
        or_(Appointments.service_type.ilike("%cq physique%"), (Appointments.service_type.ilike("%cq patient%") & Appointments.device.in_(TOMO_DEVICES))),
        ~Appointments.device.ilike("HALCYON%"),
        Appointments.start_scheduled_period >= today_start,
        Appointments.start_scheduled_period < today_end,
        func.lower(Appointments.status).in_(["booked", "arrived"]),
    ).all()

    qa_rows = []
    for appt in appointments:
        if not appt.patient: continue
        machine_key = machine_key_from_device(appt.device)
        met_start = appt.start_scheduled_period

        if ("CQ HEBDOMADAIRE" not in str(appt.patient.family_name_official or "").upper() and appt.patient_id != CQ_PHYSIQUE_PATIENT_ID):
            continue

        if not met_start or (met_start < now and (not appt.end_scheduled_period or appt.end_scheduled_period < now)):
            continue

        qa_rows.append({
            "machine_key": machine_key,
            "machine_label": machine_display(machine_key) if machine_key else str(appt.device or "?"),
            "machine_raw": appt.device,
            "met_start": met_start,
            "met_end": appt.end_scheduled_period,
            "last_name": appt.patient.family_name_official,
            "first_name": appt.patient.given,
            "service_type": appt.service_type,
            "ipp": appt.patient.ipp,
            "patient_id": appt.patient_id,
        })
    qa_rows.sort(key=lambda x: x["met_start"])
    return qa_rows

def load_cq_patient_ids_today(session: Session):
    today_start, today_end = day_bounds()
    return {row[0] for row in session.query(Appointments.patient_id).filter(
        Appointments.start_scheduled_period >= today_start,
        Appointments.start_scheduled_period < today_end,
        Appointments.service_type.ilike("%cq patient%"),
        ~func.lower(Appointments.status).in_(["cancelled", "fulfilled"]),
    ).all() if row[0]}

def load_cq_ready_patients(session: Session, since: datetime):
    return session.query(Patients).join(Patients.careplans).join(Careplans.tasks).filter(
        or_(Tasks.display_focus.ilike("réalisation du cq%"), Tasks.display_focus.ilike("réalisation des cq%"), Tasks.display_focus.ilike("replanif%")),
        Tasks.last_updated >= since,
        Tasks.status.ilike("ready"),
    ).options(joinedload(Patients.appointments), joinedload(Patients.careplans).joinedload(Careplans.tasks)).distinct().all()

def load_cq_imminent_patients(session: Session, since: datetime):
    candidates = session.query(Patients).join(Patients.careplans).join(Careplans.tasks).filter(
        Tasks.last_updated >= since,
        or_(Tasks.display_focus.ilike("validation médicale de la dosimétrie%"), Tasks.display_focus.ilike("finalisation du dossier%"))
    ).options(joinedload(Patients.appointments), joinedload(Patients.careplans).joinedload(Careplans.tasks)).distinct().all()

    rows = []
    for patient in candidates:
        all_tasks = [t for cp in patient.careplans for t in cp.tasks]
        val_tasks = [t for t in all_tasks if t.display_focus and t.display_focus.lower().startswith("validation médicale") and str(t.status or "").lower() in ["completed", "in-progress", "ready"] and t.last_updated >= since]
        if not val_tasks: continue
        val_date = max(val_tasks, key=lambda x: x.last_updated).last_updated

        if not any(t.display_focus and t.display_focus.lower().startswith("finalisation") and str(t.status or "").lower() == "draft" for t in all_tasks): continue
        
        tdm_tasks = [t for t in all_tasks if t.display_focus and t.display_focus.lower().startswith("scanner")]
        tdm_date = max(tdm_tasks, key=lambda x: x.last_updated).last_updated if tdm_tasks else None
        
        app_tasks = [t for t in all_tasks if t.display_focus and t.display_focus.lower().startswith("appeler patient")]
        appel_status = max(app_tasks, key=lambda x: x.last_updated).status if app_tasks else None

        mets = [a for a in patient.appointments if a.service_type and a.service_type.upper().startswith("MET") and a.start_scheduled_period and a.start_scheduled_period > val_date and str(a.status or "").lower() != "cancelled"]
        met_start = max(mets, key=lambda x: x.start_scheduled_period).start_scheduled_period if mets else None

        dosi_task = next((t.display_focus for t in all_tasks if t.display_focus and "réalisation dosi" in t.display_focus.lower()), None)
        workflow = patient.careplans[-1].title if patient.careplans else None

        rows.append({
            "ipp": patient.ipp, "last_name": patient.family_name_official, "first_name": patient.given,
            "tdm_date": tdm_date, "realisation_dosi_task": dosi_task, "Workflow": workflow,
            "appel_patient_status": appel_status, "validation_date": val_date, "met_start": met_start, "va_tomber": bool(met_start),
        })
    return rows

def build_dashboard_data() -> DashboardData:
    since = datetime.now() - timedelta(days=LOOKBACK_DAYS)
    try:
        with engine.connect(): log("Connexion DB SQL OK")
    except Exception: raise

    session = SessionLocal()
    try:
        cq_ids_today = load_cq_patient_ids_today(session)
        ready = load_cq_ready_patients(session, since)
        imminent = load_cq_imminent_patients(session, since)

        counts = defaultdict(int)
        details = {k: [] for k in WAITING_KEYS}
        for r in imminent:
            w_lower = str(r.get("Workflow") or "").lower()
            m = "Tomo 2" if "tomo 2" in w_lower else "Tomo 4" if "tomo 4" in w_lower else "Tomo 7" if "tomo 7" in w_lower else "Nova"
            counts[m] += 1
            details[m].append(r)

        cq_rows = []
        for p in ready:
            mets = [a for a in p.appointments if a.service_type and a.service_type.upper().startswith("MET") and a.start_scheduled_period and str(a.status or "").lower() != "cancelled"]
            met_appt = max(mets, key=lambda x: x.start_scheduled_period) if mets else None
            fin = [t for cp in p.careplans for t in cp.tasks if t.display_focus and t.display_focus.lower().startswith("finalisation")]
            physicist = (max(fin, key=lambda x: x.last_updated or datetime.min).recipient or max(fin, key=lambda x: x.last_updated or datetime.min).recipient_id) if fin else None

            for cp in p.careplans:
                for t in cp.tasks:
                    focus = (t.display_focus or "").lower()
                    if (focus.startswith("réalisation du cq") or focus.startswith("réalisation des cq") or focus.startswith("replanif")) and "cancelled" not in str(t.status or "").lower():
                        cq_rows.append({
                            "ipp": p.ipp, "last_name": p.family_name_official, "first_name": p.given, "physicist": physicist,
                            "Timeplanner": p.id in cq_ids_today, "patient_id": p.id,
                            "task_display_focus": t.display_focus, "task_status": t.status, "task_note": t.note,
                            "met_start": met_appt.start_scheduled_period if met_appt else None,
                        })
        
        cq_rows = clean_cq_rows(cq_rows)
        nova, tomo2, tomo4, tomo7 = [], [], [], []
        for r in cq_rows:
            f = str(r.get("task_display_focus")).lower()
            if "tomo 2" in f: tomo2.append(r)
            elif "tomo 4" in f: tomo4.append(r)
            elif "tomo 7" in f: tomo7.append(r)
            elif "ruby" in f or "octa" in f: nova.append(r)

        machines, countdown, remaining = load_today_patients_by_machine(session)

        return DashboardData(
            nova=sort_by_met_start(nova), tomo2=sort_by_met_start(tomo2), tomo4=sort_by_met_start(tomo4), tomo7=sort_by_met_start(tomo7),
            waiting_count=dict(counts), waiting_details=details,
            qa=load_daily_qa(session), machine_schedule=load_machine_schedule(session),
            machines=machines, countdown=countdown, remaining_today=remaining,
            last_db_update=get_last_database_update(session),
        )
    finally:
        session.close()

class SqlWorkerThread(QThread):
    data_loaded = Signal(object)
    error_occurred = Signal(str)

    def run(self):
        try:
            self.data_loaded.emit(build_dashboard_data())
        except Exception as exc:
            self.error_occurred.emit("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))