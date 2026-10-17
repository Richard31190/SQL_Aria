# -*- coding: utf-8 -*-
import time
import traceback
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy import create_engine, or_, func
from sqlalchemy.orm import sessionmaker, Session
from PySide6.QtCore import QThread, Signal

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
    
    appointments = session.query(Appointments).join(Appointments.patient).filter(
        Appointments.start_scheduled_period >= today_start,
        Appointments.start_scheduled_period < today_end,
    ).all()

    machines = defaultdict(list)
    for appt in appointments:
        machine = machine_key_from_device(appt.device)
        if not machine or not appt.patient: continue

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
    appointments = session.query(Appointments).join(Appointments.patient).filter(
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

def load_cq_ready_patients_fast(session: Session, since: datetime):
    # 1. On cherche directement les tâches éligibles
    tasks = session.query(Tasks).filter(
        or_(
            Tasks.display_focus.ilike("réalisation du cq%"),
            Tasks.display_focus.ilike("réalisation des cq%"),
            Tasks.display_focus.ilike("replanif%")
        ),
        Tasks.last_updated >= since,
        Tasks.status.ilike("ready")
    ).all()

    if not tasks: return []

    careplan_ids = {t.careplan_id for t in tasks if t.careplan_id}
    careplans = session.query(Careplans).filter(Careplans.id.in_(careplan_ids)).all()
    patient_ids = {cp.patient_id for cp in careplans if cp.patient_id}
    patients = session.query(Patients).filter(Patients.id.in_(patient_ids)).all()
    patient_dict = {p.id: p for p in patients}

    # 2. On charge UNIQUEMENT les rendez-vous MET pour ces patients (évite 98% des RDV inutiles)
    mets = session.query(Appointments).filter(
        Appointments.patient_id.in_(patient_ids),
        Appointments.service_type.ilike("MET%"),
        ~func.lower(Appointments.status).in_(["cancelled"])
    ).all()
    
    patient_mets = defaultdict(list)
    for m in mets: patient_mets[m.patient_id].append(m)

    # 3. Tâches de finalisation (physicien)
    fin_tasks = session.query(Tasks).filter(
        Tasks.careplan_id.in_(careplan_ids),
        Tasks.display_focus.ilike("finalisation du dossier%")
    ).all()
    cp_physicists = {}
    for ft in fin_tasks:
        cp_physicists[ft.careplan_id] = ft.recipient or ft.recipient_id

    rows = []
    for cp in careplans:
        p = patient_dict.get(cp.patient_id)
        if not p: continue
        
        m_list = patient_mets.get(p.id, [])
        met_start = max([m.start_scheduled_period for m in m_list], default=None)
        physicist = cp_physicists.get(cp.id)

        # Filtrer les tâches du careplan
        cp_tasks = [t for t in tasks if t.careplan_id == cp.id]
        for t in cp_tasks:
            rows.append({
                "patient_obj": p,
                "ipp": p.ipp, "last_name": p.family_name_official, "first_name": p.given,
                "physicist": physicist, "patient_id": p.id,
                "task_display_focus": t.display_focus, "task_status": t.status, "task_note": t.note,
                "met_start": met_start
            })
    return rows

def load_cq_imminent_patients_fast(session: Session, since: datetime):
    # Cherche les tâches de validation médicale récentes
    val_tasks = session.query(Tasks).filter(
        Tasks.last_updated >= since,
        Tasks.display_focus.ilike("validation médicale de la dosimétrie%"),
        func.lower(Tasks.status).in_(["completed", "in-progress", "ready"])
    ).all()

    if not val_tasks: return []

    careplan_ids = {t.careplan_id for t in val_tasks if t.careplan_id}
    careplans = session.query(Careplans).filter(Careplans.id.in_(careplan_ids)).all()
    patient_ids = {cp.patient_id for cp in careplans if cp.patient_id}
    patients = session.query(Patients).filter(Patients.id.in_(patient_ids)).all()
    patient_map = {p.id: p for p in patients}

    # Charger toutes les tâches associées à ces careplans d'un coup
    all_related_tasks = session.query(Tasks).filter(Tasks.careplan_id.in_(careplan_ids)).all()
    cp_tasks_map = defaultdict(list)
    for t in all_related_tasks: cp_tasks_map[t.careplan_id].append(t)

    # Charger uniquement les METs de ces patients
    mets = session.query(Appointments).filter(
        Appointments.patient_id.in_(patient_ids),
        Appointments.service_type.ilike("MET%"),
        ~func.lower(Appointments.status).in_(["cancelled"])
    ).all()
    patient_mets = defaultdict(list)
    for m in mets: patient_mets[m.patient_id].append(m)

    rows = []
    for cp in careplans:
        p = patient_map.get(cp.patient_id)
        if not p: continue

        c_tasks = cp_tasks_map.get(cp.id, [])
        v_tasks = [t for t in c_tasks if t.display_focus and t.display_focus.lower().startswith("validation médicale") and t.last_updated >= since]
        if not v_tasks: continue
        val_date = max([t.last_updated for t in v_tasks if t.last_updated], default=None)

        if not any(t.display_focus and t.display_focus.lower().startswith("finalisation") and str(t.status or "").lower() == "draft" for t in c_tasks):
            continue

        tdm_tasks = [t for t in c_tasks if t.display_focus and t.display_focus.lower().startswith("scanner")]
        tdm_date = max([t.last_updated for t in tdm_tasks if t.last_updated], default=None)

        app_tasks = [t for t in c_tasks if t.display_focus and t.display_focus.lower().startswith("appeler patient")]
        appel_status = max(app_tasks, key=lambda x: x.last_updated).status if app_tasks else None

        p_mets = [m for m in patient_mets.get(p.id, []) if m.start_scheduled_period and m.start_scheduled_period > val_date]
        met_start = max([m.start_scheduled_period for m in p_mets], default=None)

        dosi_task = next((t.display_focus for t in c_tasks if t.display_focus and "réalisation dosi" in t.display_focus.lower()), None)

        rows.append({
            "ipp": p.ipp, "last_name": p.family_name_official, "first_name": p.given,
            "tdm_date": tdm_date, "realisation_dosi_task": dosi_task, "Workflow": cp.title,
            "appel_patient_status": appel_status, "validation_date": val_date, "met_start": met_start, "va_tomber": bool(met_start),
        })
    return rows

def build_dashboard_data() -> DashboardData:
    t0 = time.time()
    since = datetime.now() - timedelta(days=LOOKBACK_DAYS)
    
    session = SessionLocal()
    try:
        t_start = time.time()
        cq_ids_today = load_cq_patient_ids_today(session)
        ready_rows = load_cq_ready_patients_fast(session, since)
        imminent = load_cq_imminent_patients_fast(session, since)
        t_cq = time.time() - t_start

        # Imminent Split
        counts = defaultdict(int)
        details = {k: [] for k in WAITING_KEYS}
        for r in imminent:
            w_lower = str(r.get("Workflow") or "").lower()
            m = "Tomo 2" if "tomo 2" in w_lower else "Tomo 4" if "tomo 4" in w_lower else "Tomo 7" if "tomo 7" in w_lower else "Nova"
            counts[m] += 1
            details[m].append(r)

        cq_rows = []
        for r in ready_rows:
            r["Timeplanner"] = r["patient_id"] in cq_ids_today
            cq_rows.append(r)
        
        cq_rows = clean_cq_rows(cq_rows)
        nova, tomo2, tomo4, tomo7 = [], [], [], []
        for r in cq_rows:
            f = str(r.get("task_display_focus")).lower()
            if "tomo 2" in f: tomo2.append(r)
            elif "tomo 4" in f: tomo4.append(r)
            elif "tomo 7" in f: tomo7.append(r)
            elif "ruby" in f or "octa" in f: nova.append(r)

        t_planning_start = time.time()
        machines, countdown, remaining = load_today_patients_by_machine(session)
        qa = load_daily_qa(session)
        sched = load_machine_schedule(session)
        t_planning = time.time() - t_planning_start

        log(f"[BENCH SQL] Requetes CQ : {t_cq:.2f}s | Requetes Planning : {t_planning:.2f}s | Total SQL : {time.time()-t0:.2f}s")

        return DashboardData(
            nova=sort_by_met_start(nova), tomo2=sort_by_met_start(tomo2), tomo4=sort_by_met_start(tomo4), tomo7=sort_by_met_start(tomo7),
            waiting_count=dict(counts), waiting_details=details,
            qa=qa, machine_schedule=sched,
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