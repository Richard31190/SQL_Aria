from datetime import datetime, timedelta
from collections import defaultdict

from sqlalchemy import create_engine, or_, func
from sqlalchemy.orm import sessionmaker

from ..models_aria import Patients, Careplans, Tasks, Appointments
from .config_utils import (
    LOOKBACK_DAYS, day_bounds, clean_cq_rows, sort_by_met_start,
    machine_matches_task, serialize_dt
)
from .network_service import enrich_rows_with_nas


def make_aria_session_factory(host, user, password, db_name):
    uri = f'mysql+pymysql://{user}:{password}@{host}/{db_name}'
    engine = create_engine(
        uri,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_size=2,
        max_overflow=0,
    )
    return sessionmaker(bind=engine)


def load_cq_patient_ids_today(session):
    today_start, today_end = day_bounds()
    rows = session.query(Appointments.patient_id).filter(
        Appointments.start_scheduled_period >= today_start,
        Appointments.start_scheduled_period < today_end,
        Appointments.service_type.ilike('%cq patient%'),
        ~func.lower(Appointments.status).in_(['cancelled', 'fulfilled']),
    ).all()
    return {row[0] for row in rows if row[0]}


def load_cq_ready_patients_fast(session, since: datetime):
    tasks = session.query(Tasks).filter(
        or_(
            Tasks.display_focus.ilike('realisation du cq%'),
            Tasks.display_focus.ilike('realisation des cq%'),
            Tasks.display_focus.ilike('replanif%'),
            # Variantes avec accents eventuels en base
            Tasks.display_focus.ilike('r_alisation du cq%'),
            Tasks.display_focus.ilike('r_alisation des cq%'),
        ),
        Tasks.last_updated >= since,
        Tasks.status.ilike('ready'),
    ).all()

    if not tasks:
        return []

    careplan_ids = {t.careplan_id for t in tasks if t.careplan_id}
    careplans = session.query(Careplans).filter(Careplans.id.in_(careplan_ids)).all()
    patient_ids = {cp.patient_id for cp in careplans if cp.patient_id}
    patients = session.query(Patients).filter(Patients.id.in_(patient_ids)).all()
    patient_dict = {p.id: p for p in patients}

    mets = session.query(Appointments).filter(
        Appointments.patient_id.in_(patient_ids),
        Appointments.service_type.ilike('MET%'),
        ~func.lower(Appointments.status).in_(['cancelled']),
    ).all()
    patient_mets = defaultdict(list)
    for m in mets:
        patient_mets[m.patient_id].append(m)

    fin_tasks = session.query(Tasks).filter(
        Tasks.careplan_id.in_(careplan_ids),
        or_(
            Tasks.display_focus.ilike('finalisation du dossier%'),
            Tasks.display_focus.ilike('finalisation%'),
        ),
    ).all()
    cp_physicists = {}
    for ft in fin_tasks:
        cp_physicists[ft.careplan_id] = ft.recipient or ft.recipient_id

    rows = []
    for cp in careplans:
        p = patient_dict.get(cp.patient_id)
        if not p:
            continue
        m_list = patient_mets.get(p.id, [])
        met_start = max((m.start_scheduled_period for m in m_list if m.start_scheduled_period), default=None)
        physicist = cp_physicists.get(cp.id)
        cp_tasks = [t for t in tasks if t.careplan_id == cp.id]
        for t in cp_tasks:
            rows.append({
                'ipp': p.ipp,
                'last_name': p.family_name_official,
                'first_name': p.given,
                'physicist': physicist,
                'patient_id': p.id,
                'task_display_focus': t.display_focus,
                'task_status': t.status,
                'task_note': t.note,
                'met_start': met_start,
            })
    return rows


def get_cq_for_machine(settings: dict, machine) -> dict:
    """
    settings: dict aria_db_*
    machine: objet Machine locale (name, label, dicom_path, device_ids)
    """
    host = settings.get('aria_db_host') or ''
    user = settings.get('aria_db_user') or ''
    password = settings.get('aria_db_password') or ''
    db_name = settings.get('aria_db_name') or ''

    if not all([host, user, db_name]):
        return {
            'status': 'error',
            'message': 'Configuration ARIA incomplete (Admin).',
            'data': [],
        }

    SessionFactory = make_aria_session_factory(host, user, password, db_name)
    session = SessionFactory()
    try:
        since = datetime.now() - timedelta(days=LOOKBACK_DAYS)
        cq_ids_today = load_cq_patient_ids_today(session)
        ready_rows = load_cq_ready_patients_fast(session, since)

        for r in ready_rows:
            r['Timeplanner'] = r['patient_id'] in cq_ids_today

        ready_rows = clean_cq_rows(ready_rows)

        # Filtre machine configurable
        filtered = [
            r for r in ready_rows
            if machine_matches_task(machine.name, machine.label, machine.device_ids, r.get('task_display_focus'))
        ]
        filtered = sort_by_met_start(filtered)

        name_u = str(machine.name or '').upper()
        label_u = str(machine.label or '').upper()
        is_tomo = ('TOMO' in name_u) or ('TOMO' in label_u) or ('RADI' in name_u)

        enrich_rows_with_nas(filtered, machine.dicom_path or '', is_tomo=is_tomo)

        payload = []
        for r in filtered:
            dicom_ok = bool(r.get('existing_dicom'))
            pdf_ok = bool(r.get('existing_pdf'))
            folder_ok = bool(r.get('existing_folder'))

            if dicom_ok and pdf_ok:
                nas_status = 'ok'
                nas_label = 'DICOM + PDF'
            elif dicom_ok:
                nas_status = 'partial'
                nas_label = 'DICOM seul'
            elif folder_ok:
                nas_status = 'folder'
                nas_label = 'Dossier vide'
            else:
                nas_status = 'missing'
                nas_label = 'Absent'

            # Urgence simple : MET proche (< 2 jours) ou deja planifie aujourd'hui
            urgency = 'normal'
            met = r.get('met_start')
            if r.get('Timeplanner'):
                urgency = 'today'
            elif met:
                delta = met - datetime.now()
                if delta.total_seconds() < 0:
                    urgency = 'late'
                elif delta.days <= 2:
                    urgency = 'urgent'

            payload.append({
                'patient_name': f"{r.get('last_name') or ''} {r.get('first_name') or ''}".strip(),
                'patient_id': r.get('patient_id'),
                'ipp': r.get('ipp') or '',
                'plan_name': r.get('task_display_focus') or '',
                'physicist': r.get('physicist') or '',
                'task_status': r.get('task_status') or '',
                'task_note': r.get('task_note') or '',
                'met_start': serialize_dt(r.get('met_start')),
                'timeplanner': bool(r.get('Timeplanner')),
                'energy': r.get('energy') or '',
                'tomo_fraction': r.get('tomo_fraction') or '',
                'tomo_duration_min': r.get('tomo_duration_min'),
                'folder_path': r.get('folder_path') or '',
                'nas_status': nas_status,
                'nas_label': nas_label,
                'urgency': urgency,
            })

        return {'status': 'success', 'data': payload, 'count': len(payload)}
    except Exception as exc:
        return {'status': 'error', 'message': str(exc), 'data': []}
    finally:
        session.close()