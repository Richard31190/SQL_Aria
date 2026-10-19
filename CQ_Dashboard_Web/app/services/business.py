import os
import xml.etree.ElementTree as ET
import json
from datetime import datetime, date, timedelta
from collections import defaultdict
from sqlalchemy import create_engine, func, or_
from sqlalchemy.orm import sessionmaker
from app.models_local import Setting, CQTab, CQMachine, CQSource, ScanCache, db_local
from app.models_aria import Patients, Careplans, Tasks, Appointments

# ============================================================
# Mapping device ARIA → machine_key (tiré de ton ancien config)
# ============================================================
DEVICE_TO_MACHINE = {
    "TOMO2": "TOMO2", "TOMO 2": "TOMO2",
    "0210462": "TOMO4", "TOMO4": "TOMO4", "Tomo4": "TOMO4", "TOMO 4": "TOMO4",
    "RADI7": "RADI7", "RADI 7": "RADI7", "0210471": "RADI7",
    "NOVA3": "NOVA3", "NOVA 3": "NOVA3",
    "NOVA5": "NOVA5", "NOVA 5": "NOVA5",
    "HALCYON6": "HALCYON6", "HALCYON 6": "HALCYON6",
    "HALCYON8": "HALCYON8", "HALCYON 8": "HALCYON8",
}

MACHINE_LABEL = {
    "TOMO2": "Tomo 2", "TOMO4": "Tomo 4", "RADI7": "Tomo 7",
    "NOVA3": "Nova 3", "NOVA5": "Nova 5",
    "HALCYON6": "Halcyon 6", "HALCYON8": "Halcyon 8",
}

# Mapping machine_key → noms de devices ARIA (pour le filtrage SQL)
MACHINE_TO_DEVICES = defaultdict(list)
for dev, mkey in DEVICE_TO_MACHINE.items():
    MACHINE_TO_DEVICES[mkey].append(dev)

# Mapping onglet dashboard → machine_keys
TAB_TO_MACHINES = {
    "tomo2": ["TOMO2"],
    "tomo4": ["TOMO4", "RADI7"],
    "tomo7": ["RADI7"],
    "nova": ["NOVA3", "NOVA5", "HALCYON6", "HALCYON8"],
}


def normalize_device(device):
    return str(device or "").upper().replace(" ", "").replace("-", "").strip()


def machine_key_from_device(device):
    return DEVICE_TO_MACHINE.get(str(device or "").strip())


def get_mysql_engine():
    host = Setting.get_val('MYSQL_HOST', 'localhost')
    port = Setting.get_val('MYSQL_PORT', '3306')
    user = Setting.get_val('MYSQL_USER', 'root')
    pwd = Setting.get_val('MYSQL_PASSWORD', '')
    dbname = Setting.get_val('MYSQL_DB', 'db_iuct')

    conn_str = f"mysql+pymysql://{user}:{pwd}@{host}:{port}/{dbname}"
    return create_engine(conn_str, pool_pre_ping=True, pool_recycle=1800)


def get_french_holidays(year):
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

    easter = date(year, month, day)
    easter_monday = easter + timedelta(days=1)
    ascension = easter + timedelta(days=39)
    whit_monday = easter + timedelta(days=50)

    fixed = [
        date(year, 1, 1), date(year, 5, 1), date(year, 5, 8),
        date(year, 7, 14), date(year, 8, 15), date(year, 11, 1),
        date(year, 11, 11), date(year, 12, 25)
    ]
    return set(fixed + [easter_monday, ascension, whit_monday])


def is_business_day(d):
    if d.weekday() >= 5:
        return False
    return d not in get_french_holidays(d.year)


def add_business_days(start_date, num_days):
    cur = start_date
    added = 0
    step = 1 if num_days >= 0 else -1
    abs_days = abs(num_days)
    while added < abs_days:
        cur += timedelta(days=step)
        if is_business_day(cur):
            added += 1
    return cur


def calculate_row_color(met_date):
    if not met_date:
        return 'bg-white dark:bg-slate-800'

    today = date.today()
    target_met_0 = add_business_days(today, 2)

    if met_date.date() < today:
        return 'bg-red-100 dark:bg-red-950/40 text-red-900 dark:text-red-200'
    elif met_date.date() == today:
        return 'bg-orange-100 dark:bg-orange-950/40 text-orange-900 dark:text-orange-200'
    elif met_date.date() <= target_met_0:
        return 'bg-yellow-100 dark:bg-yellow-950/40 text-yellow-900 dark:text-yellow-200'
    else:
        return 'bg-green-100 dark:bg-green-950/40 text-green-900 dark:text-green-200'


def day_bounds(reference=None):
    reference = reference or datetime.now()
    start = reference.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def build_dashboard_data(tab_name=None):
    engine = get_mysql_engine()
    Session = sessionmaker(bind=engine)
    session = Session()

    # Déterminer les devices ARIA à filtrer selon l'onglet
    selected_devices = []
    if tab_name:
        cq_tab = CQTab.query.filter_by(name=tab_name).first()
        if cq_tab:
            machine_names = cq_tab.get_machines()
            # Convertir les noms display ("Tomo 2") en devices ARIA ("TOMO 2", "TOMO2")
            for mn in machine_names:
                mn_upper = mn.upper().replace(" ", "")
                for dev, mkey in DEVICE_TO_MACHINE.items():
                    if mkey == mn_upper or dev.upper().replace(" ", "") == mn_upper:
                        if dev not in selected_devices:
                            selected_devices.append(dev)
            # Fallback : si pas de mapping trouvé, utiliser les noms tels quels
            if not selected_devices:
                selected_devices = machine_names

    main_rows = []
    prep_rows = []

    try:
        today_start, today_end = day_bounds()

        # =============================================
        # 1. Rendez-vous du jour (CQ Patient + CQ Physique)
        # =============================================
        app_query = session.query(Appointments).join(
            Appointments.patient
        ).filter(
            Appointments.start_scheduled_period >= today_start,
            Appointments.start_scheduled_period < today_end,
        )

        if selected_devices:
            app_query = app_query.filter(Appointments.device.in_(selected_devices))

        appointments = app_query.all()

        for appt in appointments:
            pat = appt.patient
            if not pat:
                continue

            machine_key = machine_key_from_device(appt.device)
            machine_display = MACHINE_LABEL.get(machine_key, appt.device or "?")

            # --- Tâche MET (display_focus contenant 'MET' ou 'réalisation du cq') ---
            met_date = None
            task_display = ''
            task_status = ''
            task_note = ''
            physicist = ''

            careplans = session.query(Careplans).filter_by(patient_id=pat.id).all()
            cp_ids = [cp.id for cp in careplans]

            if cp_ids:
                # Tâche CQ principale
                cq_task = session.query(Tasks).filter(
                    Tasks.careplan_id.in_(cp_ids),
                    or_(
                        Tasks.display_focus.ilike('%réalisation du cq%'),
                        Tasks.display_focus.ilike('%réalisation des cq%'),
                        Tasks.display_focus.ilike('%MET%'),
                    )
                ).order_by(Tasks.last_updated.desc()).first()

                if cq_task:
                    met_date = cq_task.last_updated
                    task_display = cq_task.display_focus or ''
                    task_status = cq_task.status or ''
                    task_note = cq_task.note or ''

                # Physicien (tâche finalisation)
                fin_task = session.query(Tasks).filter(
                    Tasks.careplan_id.in_(cp_ids),
                    Tasks.display_focus.ilike('%finalisation%')
                ).first()
                if fin_task:
                    physicist = fin_task.recipient or fin_task.recipient_id or ''

            # --- Scan Status from local SQLite Cache ---
            scandidos_scan = ScanCache.query.filter_by(
                patient_id=str(pat.ipp or pat.id or ''), file_type='DICOM'
            ).first()
            pdf_scan = ScanCache.query.filter_by(
                patient_id=str(pat.ipp or pat.id or ''),
                file_type='PDF'
            ).first()

            scandidos_status = '✅' if scandidos_scan else '❌'
            pdf_status = '✅' if pdf_scan else '❌'

            # --- Machine Duration rules ---
            m_config = CQMachine.query.filter_by(name=machine_display).first()
            if not m_config and machine_key:
                m_config = CQMachine.query.filter_by(name=machine_key).first()
            duration_rules = m_config.get_duration_rules() if m_config else {}
            base_duration = duration_rules.get('fixed_duration', 10)

            # --- Énergie par défaut ---
            energy = Setting.get_val('DEFAULT_TOMO_ENERGY', '6 MV')

            row = {
                'patient_id': str(pat.ipp or pat.id),
                'last_name': str(pat.family_name_official or '').upper(),
                'first_name': str(pat.given or ''),
                'machine': machine_display,
                'scheduled_time': appt.start_scheduled_period.strftime('%H:%M') if appt.start_scheduled_period else '',
                'met_date': met_date.strftime('%Y-%m-%d') if met_date else '',
                'met_color': calculate_row_color(met_date),
                'scandidos': scandidos_status,
                'pdf': pdf_status,
                'energy': energy,
                'technique': str(appt.service_type or ''),
                'status': str(appt.status or 'Booked'),
                'base_duration': base_duration,
                'comment': str(appt.comment or ''),
                'task_display': task_display,
                'task_status': task_status,
                'physicist': physicist,
            }
            main_rows.append(row)

        # Trier par heure
        main_rows.sort(key=lambda r: r['scheduled_time'] or '99:99')

        # =============================================
        # 2. Patients en préparation (tâches ready/in-progress)
        # =============================================
        since = datetime.now() - timedelta(days=14)
        prep_tasks = session.query(Tasks, Careplans, Patients).join(
            Careplans, Tasks.careplan_id == Careplans.id
        ).join(
            Patients, Careplans.patient_id == Patients.id
        ).filter(
            or_(
                Tasks.display_focus.ilike('%réalisation du cq%'),
                Tasks.display_focus.ilike('%réalisation des cq%'),
                Tasks.display_focus.ilike('%replanif%'),
            ),
            Tasks.last_updated >= since,
            func.lower(Tasks.status).in_(['ready', 'in-progress', 'in progress', 'pending']),
        ).all()

        for tsk, cp, pat in prep_tasks:
            prep_rows.append({
                'patient_id': str(pat.ipp or pat.id),
                'patient_name': f"{pat.family_name_official or ''} {pat.given or ''}",
                'task_name': tsk.display_focus or '',
                'due_date': tsk.last_updated.strftime('%Y-%m-%d') if tsk.last_updated else '',
                'status': tsk.status or ''
            })

    except Exception as e:
        print(f"Error executing ARIA MySQL query: {e}")
    finally:
        session.close()

    return {
        'main_rows': main_rows,
        'prep_rows': prep_rows
    }


def perform_network_scan():
    dicom_sources = CQSource.query.filter_by(source_type='DICOM', active=True).all()
    pdf_sources = CQSource.query.filter_by(source_type='PDF', active=True).all()

    found_count = 0
    scandidos_sig = Setting.get_val('SCANDIDOS_SIGNATURE', '114358')

    for src in dicom_sources:
        if os.path.exists(src.path):
            for root, dirs, files in os.walk(src.path):
                for f in files:
                    file_path = os.path.join(root, f)
                    if f.endswith('.xml') or f.endswith('.json'):
                        try:
                            patient_id = None
                            if f.endswith('.xml'):
                                tree = ET.parse(file_path)
                                root_xml = tree.getroot()
                                xml_str = ET.tostring(root_xml, encoding='utf-8').decode('utf-8')
                                if scandidos_sig.lower() in xml_str.lower():
                                    elem = root_xml.find('.//PatientID')
                                    if elem is not None:
                                        patient_id = elem.text
                            elif f.endswith('.json'):
                                with open(file_path, 'r', encoding='utf-8') as jf:
                                    data = json.load(jf)
                                    if scandidos_sig.lower() in json.dumps(data).lower():
                                        patient_id = data.get('PatientID') or data.get('patient_id')

                            if patient_id:
                                existing = ScanCache.query.filter_by(
                                    patient_id=patient_id, file_type='DICOM'
                                ).first()
                                if not existing:
                                    db_local.session.add(ScanCache(
                                        patient_id=patient_id,
                                        file_type='DICOM',
                                        file_name=f,
                                        file_path=file_path,
                                        last_modified=datetime.fromtimestamp(os.path.getmtime(file_path))
                                    ))
                                    found_count += 1
                        except Exception:
                            continue

    for src in pdf_sources:
        if os.path.exists(src.path):
            for root, dirs, files in os.walk(src.path):
                for f in files:
                    if f.endswith('.pdf'):
                        file_path = os.path.join(root, f)
                        patient_id = f.split('_')[0] if '_' in f else os.path.splitext(f)[0]
                        existing = ScanCache.query.filter_by(
                            patient_id=patient_id, file_type='PDF'
                        ).first()
                        if not existing:
                            db_local.session.add(ScanCache(
                                patient_id=patient_id,
                                file_type='PDF',
                                file_name=f,
                                file_path=file_path,
                                last_modified=datetime.fromtimestamp(os.path.getmtime(file_path))
                            ))
                            found_count += 1

    db_local.session.commit()
    return found_count