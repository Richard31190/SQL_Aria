import os
import re
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy import create_engine, func, or_
from sqlalchemy.orm import sessionmaker
from app.models_local import Setting, CQTab, CQSource, ScanCache, db_local
from app.models_aria import Patients, Careplans, Tasks, Appointments

# ============================================================
# Constantes (identiques à l'ancien config.py)
# ============================================================
LOOKBACK_DAYS = 14
TAB_KEYS = ["tomo2", "tomo4", "tomo7", "nova"]
WAITING_KEYS = {"tomo2": "Tomo 2", "tomo4": "Tomo 4", "tomo7": "Tomo 7", "nova": "Nova"}
TAB_LABELS = {"tomo2": "Tomo 2", "tomo4": "Tomo 4", "tomo7": "Tomo 7", "nova": "Nova"}


# ============================================================
# Connexion MySQL (engine mis en cache, pas recréé à chaque requête)
# ============================================================
_ENGINE_CACHE = {}

def get_mysql_engine():
    host = Setting.get_val('MYSQL_HOST', 'localhost')
    port = Setting.get_val('MYSQL_PORT', '3306')
    user = Setting.get_val('MYSQL_USER', 'root')
    pwd = Setting.get_val('MYSQL_PASSWORD', '')
    dbname = Setting.get_val('MYSQL_DB', 'db_iuct')
    conn_str = f"mysql+pymysql://{user}:{pwd}@{host}:{port}/{dbname}"
    if conn_str not in _ENGINE_CACHE:
        _ENGINE_CACHE[conn_str] = create_engine(
            conn_str, pool_pre_ping=True, pool_recycle=1800, pool_size=2, max_overflow=2
        )
    return _ENGINE_CACHE[conn_str]


# ============================================================
# Utilitaires (copie de l'ancien config.py)
# ============================================================
_HOLIDAY_CACHE = {}

def french_holidays(year):
    if year in _HOLIDAY_CACHE:
        return _HOLIDAY_CACHE[year]
    a, b, c = year % 19, year // 100, year % 100
    d, e, f = b // 4, b % 4, (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    month, day = (h + ll - 7 * m + 114) // 31, ((h + ll - 7 * m + 114) % 31) + 1
    from datetime import date
    easter = date(year, month, day)
    days = {
        date(year, 1, 1), easter + timedelta(days=1), date(year, 5, 1),
        date(year, 5, 8), easter + timedelta(days=39), easter + timedelta(days=50),
        date(year, 7, 14), date(year, 8, 15), date(year, 11, 1),
        date(year, 11, 11), date(year, 12, 25),
    }
    _HOLIDAY_CACHE[year] = days
    return days


def add_business_days(start_date, days):
    current, added = start_date, 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5 and current.date() not in french_holidays(current.year):
            added += 1
    return current


def day_bounds(reference=None):
    reference = reference or datetime.now()
    start = reference.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def sort_by_met_start(table):
    return sorted(table, key=lambda r: (r["met_start"] is None, r["met_start"] or datetime.max))


def clean_cq_rows(rows):
    return [r for r in rows if str(r.get("task_status") or "").lower() not in ["completed", "draft"]]


def row_color(met, now, limit):
    """Même logique que ui.py : ⚪ pas de date, 🔴 en retard, 🟠 urgent, 🟢 ok"""
    if not met:
        return 'bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300'
    if met < now:
        return 'bg-red-100 dark:bg-red-950/40 text-red-900 dark:text-red-200'
    if met <= limit:
        return 'bg-orange-100 dark:bg-orange-950/40 text-orange-900 dark:text-orange-200'
    return 'bg-green-100 dark:bg-green-950/40 text-green-900 dark:text-green-200'


def resolve_tab_key(tab_name):
    """Convertit le nom (ou le libellé) d'un onglet en tomo2 / tomo4 / tomo7 / nova.
    Renvoie None si l'onglet n'est pas reconnu (→ liste vide, et non tous les patients)."""
    if not tab_name:
        return "tomo2"
    candidates = [tab_name]
    tab = CQTab.query.filter_by(name=tab_name).first()
    if tab:
        candidates.append(tab.label or "")
    for c in candidates:
        key = re.sub(r'[^a-z0-9]', '', str(c).lower())
        if 'tomo2' in key:
            return 'tomo2'
        if 'tomo4' in key:
            return 'tomo4'
        if 'tomo7' in key or 'radi7' in key:
            return 'tomo7'
        if any(k in key for k in ('nova', 'ruby', 'octa', 'halcyon')):
            return 'nova'
    return None


# ============================================================
# Requêtes SQL — portage EXACT de l'ancien database.py
# ============================================================
def load_cq_patient_ids_today(session):
    today_start, today_end = day_bounds()
    return {row[0] for row in session.query(Appointments.patient_id).filter(
        Appointments.start_scheduled_period >= today_start,
        Appointments.start_scheduled_period < today_end,
        Appointments.service_type.ilike("%cq patient%"),
        ~func.lower(Appointments.status).in_(["cancelled", "fulfilled"]),
    ).all() if row[0]}


def load_cq_ready_patients_fast(session, since):
    tasks = session.query(Tasks).filter(
        or_(
            Tasks.display_focus.ilike("réalisation du cq%"),
            Tasks.display_focus.ilike("réalisation des cq%"),
            Tasks.display_focus.ilike("replanif%"),
        ),
        Tasks.last_updated >= since,
        Tasks.status.ilike("ready"),
    ).all()

    if not tasks:
        return []

    careplan_ids = {t.careplan_id for t in tasks if t.careplan_id}
    careplans = session.query(Careplans).filter(Careplans.id.in_(careplan_ids)).all()
    patient_ids = {cp.patient_id for cp in careplans if cp.patient_id}
    if not patient_ids:
        return []
    patients = session.query(Patients).filter(Patients.id.in_(patient_ids)).all()
    patient_dict = {p.id: p for p in patients}

    mets = session.query(Appointments).filter(
        Appointments.patient_id.in_(patient_ids),
        Appointments.service_type.ilike("MET%"),
        ~func.lower(Appointments.status).in_(["cancelled"]),
    ).all()
    patient_mets = defaultdict(list)
    for m in mets:
        patient_mets[m.patient_id].append(m)

    fin_tasks = session.query(Tasks).filter(
        Tasks.careplan_id.in_(careplan_ids),
        Tasks.display_focus.ilike("finalisation du dossier%"),
    ).all()
    cp_physicists = {ft.careplan_id: (ft.recipient or ft.recipient_id) for ft in fin_tasks}

    rows = []
    for cp in careplans:
        p = patient_dict.get(cp.patient_id)
        if not p:
            continue
        met_dates = [m.start_scheduled_period for m in patient_mets.get(p.id, []) if m.start_scheduled_period]
        met_start = max(met_dates, default=None)
        physicist = cp_physicists.get(cp.id)

        for t in [t for t in tasks if t.careplan_id == cp.id]:
            rows.append({
                "ipp": p.ipp,
                "last_name": p.family_name_official,
                "first_name": p.given,
                "physicist": physicist,
                "patient_id": p.id,
                "task_display_focus": t.display_focus,
                "task_status": t.status,
                "task_note": t.note,
                "met_start": met_start,
            })
    return rows


def load_cq_imminent_patients_fast(session, since):
    val_tasks = session.query(Tasks).filter(
        Tasks.last_updated >= since,
        Tasks.display_focus.ilike("validation médicale de la dosimétrie%"),
        func.lower(Tasks.status).in_(["completed", "in-progress", "ready"]),
    ).all()

    if not val_tasks:
        return []

    careplan_ids = {t.careplan_id for t in val_tasks if t.careplan_id}
    careplans = session.query(Careplans).filter(Careplans.id.in_(careplan_ids)).all()
    patient_ids = {cp.patient_id for cp in careplans if cp.patient_id}
    if not patient_ids:
        return []
    patients = session.query(Patients).filter(Patients.id.in_(patient_ids)).all()
    patient_map = {p.id: p for p in patients}

    all_related = session.query(Tasks).filter(Tasks.careplan_id.in_(careplan_ids)).all()
    cp_tasks_map = defaultdict(list)
    for t in all_related:
        cp_tasks_map[t.careplan_id].append(t)

    mets = session.query(Appointments).filter(
        Appointments.patient_id.in_(patient_ids),
        Appointments.service_type.ilike("MET%"),
        ~func.lower(Appointments.status).in_(["cancelled"]),
    ).all()
    patient_mets = defaultdict(list)
    for m in mets:
        patient_mets[m.patient_id].append(m)

    rows = []
    for cp in careplans:
        p = patient_map.get(cp.patient_id)
        if not p:
            continue
        c_tasks = cp_tasks_map.get(cp.id, [])

        v_tasks = [t for t in c_tasks
                   if t.display_focus and t.display_focus.lower().startswith("validation médicale")
                   and t.last_updated and t.last_updated >= since]
        if not v_tasks:
            continue
        val_date = max(t.last_updated for t in v_tasks)

        if not any(t.display_focus and t.display_focus.lower().startswith("finalisation")
                   and str(t.status or "").lower() == "draft" for t in c_tasks):
            continue

        tdm_date = max([t.last_updated for t in c_tasks
                        if t.display_focus and t.display_focus.lower().startswith("scanner") and t.last_updated],
                       default=None)

        p_mets = [m.start_scheduled_period for m in patient_mets.get(p.id, [])
                  if m.start_scheduled_period and m.start_scheduled_period > val_date]
        met_start = max(p_mets, default=None)

        rows.append({
            "ipp": p.ipp,
            "last_name": p.family_name_official,
            "first_name": p.given,
            "tdm_date": tdm_date,
            "Workflow": cp.title,
            "validation_date": val_date,
            "met_start": met_start,
            "va_tomber": bool(met_start),
        })
    return rows


# ============================================================
# Construction des données — même découpage que l'ancien build_dashboard_data
# ============================================================
def build_dashboard_data(tab_name=None):
    now = datetime.now()
    limit = add_business_days(now, 2)
    since = now - timedelta(days=LOOKBACK_DAYS)

    tab_key = resolve_tab_key(tab_name)
    lists = {k: [] for k in TAB_KEYS}
    waiting = {k: [] for k in TAB_KEYS}

    session = sessionmaker(bind=get_mysql_engine())()
    try:
        cq_ids_today = load_cq_patient_ids_today(session)
        ready_rows = load_cq_ready_patients_fast(session, since)
        imminent = load_cq_imminent_patients_fast(session, since)

        # --- Répartition "en préparation" selon le Workflow (cp.title) ---
        for r in imminent:
            w = str(r.get("Workflow") or "").lower()
            k = "tomo2" if "tomo 2" in w else "tomo4" if "tomo 4" in w else "tomo7" if "tomo 7" in w else "nova"
            waiting[k].append(r)

        # --- Timeplanner + nettoyage ---
        for r in ready_rows:
            r["Timeplanner"] = r["patient_id"] in cq_ids_today
        cq_rows = clean_cq_rows(ready_rows)

        # --- Répartition EXCLUSIVE (if / elif) comme l'ancien programme ---
        for r in cq_rows:
            f = str(r.get("task_display_focus")).lower()
            if "tomo 2" in f:
                lists["tomo2"].append(r)
            elif "tomo 4" in f:
                lists["tomo4"].append(r)
            elif "tomo 7" in f:
                lists["tomo7"].append(r)
            elif "ruby" in f or "octa" in f:
                lists["nova"].append(r)
            # sinon : ignoré (comme avant)

        for k in TAB_KEYS:
            lists[k] = sort_by_met_start(lists[k])

    except Exception as e:
        print(f"Error executing ARIA MySQL query: {e}")
    finally:
        session.close()

    counts = {k: len(v) for k, v in lists.items()}
    print(f"[CQ] {datetime.now():%H:%M:%S} Tomo2={counts['tomo2']} Tomo4={counts['tomo4']} "
          f"Tomo7={counts['tomo7']} Nova={counts['nova']} | onglet demandé='{tab_name}' → {tab_key}")

    if tab_key is None:
        return {'main_rows': [], 'prep_rows': [], 'counts': counts}

    default_energy = Setting.get_val('DEFAULT_TOMO_ENERGY', '6FFF')

    # --- Mise en forme pour le tableau web ---
    main_rows = []
    for r in lists[tab_key]:
        met = r["met_start"]
        ipp = str(r.get("ipp") or r.get("patient_id") or "")
        focus = str(r.get("task_display_focus") or "")
        fl = focus.lower()

        # Durée estimée par ligne (même logique que on_tab_changed de ui.py, hors offset initial)
        if "tomo" in fl:
            duration = 3
        elif "ruby" in fl:
            duration = 12
        elif "octa" in fl:
            duration = 9
        else:
            duration = 15

        main_rows.append({
            'patient_id': ipp,
            'last_name': str(r.get("last_name") or "").upper(),
            'first_name': str(r.get("first_name") or ""),
            'machine': TAB_LABELS[tab_key],
            'scheduled_time': met.strftime('%H:%M') if met else '',
            'met_date': met.strftime('%Y-%m-%d %H:%M') if met else '',
            'met_color': row_color(met, now, limit),
            'scandidos': '✅' if ScanCache.query.filter_by(patient_id=ipp, file_type='DICOM').first() else '❌',
            'pdf': '✅' if ScanCache.query.filter_by(patient_id=ipp, file_type='PDF').first() else '❌',
            'energy': default_energy if tab_key != 'nova' else '',
            'technique': focus,
            'status': str(r.get("task_status") or ""),
            'base_duration': duration,
            'comment': str(r.get("task_note") or ""),
            'physicist': str(r.get("physicist") or ""),
            'timeplanner': '✅' if r.get("Timeplanner") else '❌',
        })

    prep_rows = []
    for p in waiting[tab_key]:
        val = p.get("validation_date")
        met = p.get("met_start")
        prep_rows.append({
            'patient_id': str(p.get("ipp") or ""),
            'patient_name': f"{p.get('last_name') or ''} {p.get('first_name') or ''}",
            'task_name': str(p.get("Workflow") or ""),
            'due_date': p["tdm_date"].strftime('%d/%m') if p.get("tdm_date") else '-',
            'status': f"Valid. méd. : {val.strftime('%d/%m') if val else '-'} | MET : {met.strftime('%d/%m') if met else '-'}",
        })

    return {'main_rows': main_rows, 'prep_rows': prep_rows, 'counts': counts}


# ============================================================
# Scan réseau NAS (inchangé)
# ============================================================
def perform_network_scan():
    dicom_sources = CQSource.query.filter_by(source_type='DICOM', active=True).all()
    pdf_sources = CQSource.query.filter_by(source_type='PDF', active=True).all()

    found_count = 0
    scandidos_sig = Setting.get_val('SCANDIDOS_SIGNATURE', '114358')

    for src in dicom_sources:
        if not os.path.exists(src.path):
            continue
        for root, dirs, files in os.walk(src.path):
            for f in files:
                if not (f.endswith('.xml') or f.endswith('.json')):
                    continue
                file_path = os.path.join(root, f)
                try:
                    patient_id = None
                    if f.endswith('.xml'):
                        root_xml = ET.parse(file_path).getroot()
                        if scandidos_sig.lower() in ET.tostring(root_xml, encoding='utf-8').decode('utf-8').lower():
                            elem = root_xml.find('.//PatientID')
                            if elem is not None:
                                patient_id = elem.text
                    else:
                        with open(file_path, 'r', encoding='utf-8') as jf:
                            data = json.load(jf)
                        if scandidos_sig.lower() in json.dumps(data).lower():
                            patient_id = data.get('PatientID') or data.get('patient_id')

                    if patient_id and not ScanCache.query.filter_by(patient_id=patient_id, file_type='DICOM').first():
                        db_local.session.add(ScanCache(
                            patient_id=patient_id, file_type='DICOM', file_name=f, file_path=file_path,
                            last_modified=datetime.fromtimestamp(os.path.getmtime(file_path))
                        ))
                        found_count += 1
                except Exception:
                    continue

    for src in pdf_sources:
        if not os.path.exists(src.path):
            continue
        for root, dirs, files in os.walk(src.path):
            for f in files:
                if not f.endswith('.pdf'):
                    continue
                file_path = os.path.join(root, f)
                patient_id = f.split('_')[0] if '_' in f else os.path.splitext(f)[0]
                if not ScanCache.query.filter_by(patient_id=patient_id, file_type='PDF').first():
                    db_local.session.add(ScanCache(
                        patient_id=patient_id, file_type='PDF', file_name=f, file_path=file_path,
                        last_modified=datetime.fromtimestamp(os.path.getmtime(file_path))
                    ))
                    found_count += 1

    db_local.session.commit()
    return found_count