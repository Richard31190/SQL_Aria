# *** services\business.py ***
import os
import re
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy import create_engine, func, or_
from sqlalchemy.orm import sessionmaker
from app.models_local import Setting, CQTab, CQMachine, ScanCache, db_local
from app.models_aria import Patients, Careplans, Tasks, Appointments


# ============================================================
# Connexion MySQL (engine mis en cache)
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
# Utilitaires
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
    if not met:
        return 'bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300'
    if met < now:
        return 'bg-red-100 dark:bg-red-950/40 text-red-900 dark:text-red-200'
    if met <= limit:
        return 'bg-orange-100 dark:bg-orange-950/40 text-orange-900 dark:text-orange-200'
    return 'bg-green-100 dark:bg-green-950/40 text-green-900 dark:text-green-200'


def find_tab_for_focus(focus_lower, tabs):
    """Trouve l'onglet correspondant à un display_focus en cherchant
    les mots-clés ARIA de chaque onglet dans la chaîne.
    Matching insensible aux espaces : 'Tomo2' matche 'tomo 2'."""
    focus_norm = normalize(focus_lower)
    for tab in tabs:
        for kw in tab.get_aria_keywords():
            if normalize(kw) in focus_norm:
                return tab
    return None

def normalize(text):
    """Supprime les espaces et met en minuscule pour le matching."""
    return re.sub(r'\s+', '', text.lower())

def find_technique_rules(focus_lower, techniques):
    """Trouve les règles temporelles correspondant à la technique.
    Retourne : (acquisition, montage, nom_technique)"""
    focus_norm = normalize(focus_lower)
    for tech in techniques:
        if normalize(tech.name) in focus_norm:
            return tech.get_acquisition(), tech.get_mount_unmount(), tech.name
    return 15, 0, "Défaut"  # Valeurs si aucune technique ne matche

# ============================================================
# Requêtes SQL ARIA (inchangées)
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
# Construction des données — 100% DYNAMIQUE
# ============================================================
def build_dashboard_data(tab_name=None):
    now = datetime.now()
    limit = add_business_days(now, 2)
    lookback = int(Setting.get_val('LOOKBACK_DAYS', '14'))
    since = now - timedelta(days=lookback)

    tabs = CQTab.query.order_by(CQTab.display_order).all()
    techniques = CQMachine.query.order_by(CQMachine.display_order).all()

    lists = {tab.label: [] for tab in tabs}
    waiting = {tab.label: [] for tab in tabs}

    session = sessionmaker(bind=get_mysql_engine())()
    try:
        cq_ids_today = load_cq_patient_ids_today(session)
        ready_rows = load_cq_ready_patients_fast(session, since)
        imminent = load_cq_imminent_patients_fast(session, since)

        # === DEBUG ===
        print(f"[DEBUG] {len(ready_rows)} patients CQ ready trouvés dans ARIA")
        print(f"[DEBUG] {len(imminent)} patients imminents trouvés dans ARIA")
        print(f"[DEBUG] Onglets configurés :")
        for tab in tabs:
            print(f"   - label='{tab.label}' | keywords={tab.get_aria_keywords()}")
        # =============

        for r in imminent:
            w = str(r.get("Workflow") or "").lower()
            matched_tab = find_tab_for_focus(w, tabs)
            if matched_tab:
                waiting[matched_tab.label].append(r)

        for r in ready_rows:
            r["Timeplanner"] = r["patient_id"] in cq_ids_today
        cq_rows = clean_cq_rows(ready_rows)

        # === DEBUG ===
        print(f"[DEBUG] {len(cq_rows)} patients après nettoyage (clean_cq_rows)")
        # =============

        unmatched = []
        for r in cq_rows:
            focus = str(r.get("task_display_focus") or "").lower()
            matched_tab = find_tab_for_focus(focus, tabs)
            if matched_tab:
                lists[matched_tab.label].append(r)
            else:
                unmatched.append(focus)

        # === DEBUG ===
        if unmatched:
            print(f"[DEBUG] ⚠️ {len(unmatched)} patients NON RATTACHÉS à un onglet :")
            for u in unmatched[:10]:
                print(f"   - '{u}'")
        else:
            print(f"[DEBUG] ✅ Tous les patients rattachés à un onglet")
        # =============

        for label in lists:
            lists[label] = sort_by_met_start(lists[label])

    except Exception as e:
        print(f"Error executing ARIA MySQL query: {e}")
    finally:
        session.close()

    counts = {label: len(rows) for label, rows in lists.items()}
    print(f"[CQ] {datetime.now():%H:%M:%S} Counts={counts} | onglet demandé='{tab_name}'")

    active_tab = None
    if tab_name:
        for tab in tabs:
            if tab.label.lower() == tab_name.lower():
                active_tab = tab
                break
    if not active_tab and tabs:
        active_tab = tabs[0]

    if not active_tab:
        return {'main_rows': [], 'prep_rows': [], 'counts': counts}

    main_rows = []
    for r in lists[active_tab.label]:
        met = r["met_start"]
        ipp = str(r.get("ipp") or r.get("patient_id") or "")
        focus = str(r.get("task_display_focus") or "")
        focus_lower = focus.lower()

        # On récupère Acquisition, Montage et Nom de la technique
        acq_time, mount_time, tech_name = find_technique_rules(focus_lower, techniques)
        energy = active_tab.get_default_energy()

        main_rows.append({
            'patient_id': ipp,
            'last_name': str(r.get("last_name") or "").upper(),
            'first_name': str(r.get("first_name") or ""),
            'machine': active_tab.label,
            'scheduled_time': met.strftime('%H:%M') if met else '',
            'met_date': met.strftime('%Y-%m-%d %H:%M') if met else '',
            'met_color': row_color(met, now, limit),
            'scandidos': '✅' if ScanCache.query.filter_by(patient_id=ipp, file_type='DICOM').first() else '❌',
            'pdf': '✅' if ScanCache.query.filter_by(patient_id=ipp, file_type='PDF').first() else '❌',
            'energy': energy,
            'technique': focus,
            'status': str(r.get("task_status") or ""),
            'base_duration': acq_time,       # Acquisition (min)
            'mount_duration': mount_time,    # Montage (min)
            'tech_name': tech_name,          # Nom pour le regroupement
            'comment': str(r.get("task_note") or ""),
            'physicist': str(r.get("physicist") or ""),
            'timeplanner': '✅' if r.get("Timeplanner") else '❌',
        })

    prep_rows = []
    for p in waiting[active_tab.label]:
        val = p.get("validation_date")
        met = p.get("met_start")
        prep_rows.append({
            'patient_id': str(p.get("ipp") or ""),
            'patient_name': f"{p.get('last_name') or ''} {p.get('first_name') or ''}",
            'task_name': str(p.get("Workflow") or ""),
            'due_date': p["tdm_date"].strftime('%d/%m') if p.get("tdm_date") else '-',
            'status': f"Valid. méd. : {val.strftime('%d/%m') if val else '-'} | MET : {met.strftime('%d/%m') if met else '-'}",
        })

        # === DEBUG PRÉPARATION ===
        unmatched_prep = []
        for r in imminent:
            w = str(r.get("Workflow") or "").lower()
            if not find_tab_for_focus(w, tabs):
                unmatched_prep.append(w)
        if unmatched_prep:
            print(f"[DEBUG PREP] ⚠️ {len(unmatched_prep)} patients préparation NON RATTACHÉS :")
            for u in set(unmatched_prep):
                print(f"   - '{u}'")
        # =========================
    return {'main_rows': main_rows, 'prep_rows': prep_rows, 'counts': counts}


# ============================================================
# Scan réseau NAS — 100% DYNAMIQUE
# ============================================================
def perform_network_scan():
    """Scanne les dossiers réseau de chaque onglet avec ses extensions configurées."""
    tabs = CQTab.query.all()
    scan_depth = int(Setting.get_val('SCAN_DEPTH', '3'))
    scandidos_sig = Setting.get_val('SCANDIDOS_SIGNATURE', '114358')

    found_count = 0

    for tab in tabs:
        extensions = [e.strip().lower() for e in tab.get_file_extensions()]
        paths = tab.get_network_paths()

        for path in paths:
            if not path or not os.path.exists(path):
                continue
            try:
                for root, dirs, files in os.walk(path):
                    depth = root[len(path):].count(os.sep)
                    if depth >= scan_depth:
                        dirs.clear()

                    for f in files:
                        file_path = os.path.join(root, f)
                        ext = os.path.splitext(f)[1].lower()

                        # Vérifier si l'extension est dans la liste de l'onglet
                        if ext not in extensions:
                            continue

                        lower_f = f.lower()

                        # --- PDF ---
                        if ext == '.pdf':
                            patient_id = f.split('_')[0] if '_' in f else os.path.splitext(f)[0]
                            patient_id = patient_id.strip()
                            if patient_id and not ScanCache.query.filter_by(
                                    patient_id=patient_id, file_type='PDF').first():
                                db_local.session.add(ScanCache(
                                    patient_id=patient_id,
                                    file_type='PDF',
                                    file_name=f,
                                    file_path=file_path,
                                    last_modified=datetime.fromtimestamp(os.path.getmtime(file_path))
                                ))
                                found_count += 1

                        # --- DICOM / autres extensions ---
                        elif ext == '.dcm':
                            parent_name = os.path.basename(root)
                            patient_id = parent_name if parent_name and any(
                                c.isdigit() for c in parent_name) else f.split('_')[0]
                            if patient_id:
                                patient_id = str(patient_id).strip()
                                if not ScanCache.query.filter_by(
                                        patient_id=patient_id, file_type='DICOM').first():
                                    db_local.session.add(ScanCache(
                                        patient_id=patient_id,
                                        file_type='DICOM',
                                        file_name=f,
                                        file_path=file_path,
                                        last_modified=datetime.fromtimestamp(os.path.getmtime(file_path))
                                    ))
                                    found_count += 1

                        # --- XML (Scandidos) ---
                        elif ext == '.xml':
                            try:
                                tree = ET.parse(file_path)
                                root_xml = tree.getroot()
                                xml_str = ET.tostring(root_xml, encoding='utf-8').decode('utf-8', errors='ignore')
                                if scandidos_sig.lower() in xml_str.lower():
                                    elem = root_xml.find('.//PatientID')
                                    if elem is not None and elem.text:
                                        patient_id = elem.text.strip()
                                        if not ScanCache.query.filter_by(
                                                patient_id=patient_id, file_type='DICOM').first():
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

                        # --- JSON (Scandidos) ---
                        elif ext == '.json':
                            try:
                                with open(file_path, 'r', encoding='utf-8', errors='ignore') as jf:
                                    data = json.load(jf)
                                    if scandidos_sig.lower() in json.dumps(data).lower():
                                        patient_id = data.get('PatientID') or data.get('patient_id')
                                        if patient_id:
                                            patient_id = str(patient_id).strip()
                                            if not ScanCache.query.filter_by(
                                                    patient_id=patient_id, file_type='DICOM').first():
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

            except Exception as e:
                print(f"[SCAN] Erreur scan {path}: {e}")

    db_local.session.commit()
    return found_count