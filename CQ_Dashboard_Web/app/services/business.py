# *** services\business.py ***
# -*- coding: utf-8 -*-
"""
CQ Dashboard — Logique Métier Précise & Filtrée
- Connexion MySQL ARIA (tasks, careplans, patients, appointments, prescriptions)
- Config SQLite (CQTab, CQMachine, ScanCache, Setting)
- Anti-doublons SQL & Détection stricte des mots-clés
"""
import os
import re
import json
import unicodedata
from datetime import datetime, date, timedelta

from app.models_local import db_local, CQTab, CQMachine, ScanCache, Setting

# Driver MySQL
_MYSQL_DRIVER = None
try:
    import mysql.connector as _mysql_connector
    _MYSQL_DRIVER = "connector"
except ImportError:
    try:
        import pymysql as _pymysql
        from pymysql.cursors import DictCursor as _DictCursor
        _MYSQL_DRIVER = "pymysql"
    except ImportError:
        _MYSQL_DRIVER = None


GLOBAL_KEYS = [
    "ADMIN_PASSWORD",
    "LOOKBACK_DAYS",
    "SCAN_DEPTH",
    "SCANDIDOS_SIGNATURE",
    "YEAR_OVERLAP_DAYS",
]


# ===========================================================================
# Helpers & Normalisation
# ===========================================================================

def normalize(text: str) -> str:
    """Minuscules, sans accents, sans espaces ni ponctuation."""
    if not text:
        return ""
    text = str(text).lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^a-z0-9]", "", text)
    return text


def match_tab_keyword(keyword: str, text: str) -> bool:
    """
    Vérifie si 'keyword' est présent dans 'text'.
    Si keyword fait 3 lettres ou moins (ex: 'ra', 'stec'), exige un mot entier
    pour éviter que 'ra' ne matche 'radiothérapie'.
    """
    if not keyword or not text:
        return False
    
    norm_kw = normalize(keyword)
    norm_text = normalize(text)
    if not norm_kw or not norm_text:
        return False

    # Mot-clé très court (ex: 'ra', 't2') -> Mot entier requis
    if len(norm_kw) <= 3:
        clean_text = unicodedata.normalize("NFD", text.lower())
        clean_text = "".join(c for c in clean_text if unicodedata.category(c) != "Mn")
        words = re.findall(r"\b[a-z0-9]+\b", clean_text)
        return norm_kw in words

    return norm_kw in norm_text


def get_global_settings() -> dict:
    defaults = {
        'ADMIN_PASSWORD': 'admin',
        'LOOKBACK_DAYS': '14',
        'SCAN_DEPTH': '3',
        'SCANDIDOS_SIGNATURE': '114358',
        'YEAR_OVERLAP_DAYS': '15',
        'MYSQL_HOST': 'localhost',
        'MYSQL_PORT': '3306',
        'MYSQL_USER': 'root',
        'MYSQL_PASSWORD': '',
        'MYSQL_DB': 'db_iuct',
    }
    rows = Setting.query.all()
    setting_map = {r.key: r.value for r in rows if r.key}
    for k, v in defaults.items():
        if k not in setting_map or setting_map[k] is None:
            setting_map[k] = v
    return setting_map


def get_aria_mysql_config() -> dict:
    s = get_global_settings()
    return {
        'host': s.get('MYSQL_HOST', 'localhost'),
        'port': int(s.get('MYSQL_PORT', 3306) or 3306),
        'user': s.get('MYSQL_USER', 'root'),
        'password': s.get('MYSQL_PASSWORD', ''),
        'database': s.get('MYSQL_DB', 'db_iuct'),
    }


def get_mysql_connection(cfg: dict = None):
    if cfg is None:
        cfg = get_aria_mysql_config()

    if _MYSQL_DRIVER == "connector":
        conn = _mysql_connector.connect(
            host=cfg.get("host"),
            user=cfg.get("user"),
            password=cfg.get("password"),
            database=cfg.get("database"),
            port=int(cfg.get("port", 3306)),
        )
        return conn, conn.cursor(dictionary=True)

    if _MYSQL_DRIVER == "pymysql":
        conn = _pymysql.connect(
            host=cfg.get("host"),
            user=cfg.get("user"),
            password=cfg.get("password"),
            database=cfg.get("database"),
            port=int(cfg.get("port", 3306)),
            cursorclass=_DictCursor,
            charset="utf8mb4",
        )
        return conn, conn.cursor()

    raise RuntimeError("Aucun driver MySQL (mysql-connector-python ou pymysql).")


def extract_ipp_candidates(text: str) -> list:
    if not text:
        return []
    return list(set(re.findall(r"\b\d{5,}\b", str(text))))


def find_tab_for_focus(focus_text: str, tabs: list):
    """Associe une tâche à un onglet selon les mots-clés ARIA de l'onglet."""
    if not focus_text:
        return None
    for tab in tabs:
        aria_names = tab.get_aria_keywords() if hasattr(tab, 'get_aria_keywords') else [x.strip() for x in (tab.name or "").split(",") if x.strip()]
        for kw in aria_names:
            if match_tab_keyword(kw, focus_text):
                return tab
    return None


def find_technique_rules(task_text: str, machines: list) -> dict:
    norm_task = normalize(task_text)
    for m in machines:
        kw = normalize(m.name)
        if kw and kw in norm_task:
            return {
                "technique_name": m.name,
                "mount_unmount": int(m.get_mount_unmount() or 0),
                "acquisition": int(m.get_acquisition() or 15),
            }
    return {
        "technique_name": (task_text or "CQ Standard")[:80],
        "mount_unmount": 10,
        "acquisition": 15,
    }


def get_configured_extensions(tab_label=None) -> list:
    """Retourne la liste unique des extensions définies en Admin (.dcm, .pdf...)."""
    tabs = CQTab.query.order_by(CQTab.display_order).all()
    ordered = []
    seen = set()

    for tab in tabs:
        if tab_label and tab.label != tab_label:
            continue
        exts = tab.get_file_extensions()
        if isinstance(exts, str):
            exts = [e.strip() for e in exts.split(",") if e.strip()]
        for ext in exts:
            e = str(ext).strip().lower()
            if not e:
                continue
            if not e.startswith("."):
                e = "." + e
            if e not in seen:
                seen.add(e)
                ordered.append(e)

    return ordered if ordered else [".dcm", ".pdf"]


def _icon_ok():
    return '<i class="fa-solid fa-circle-check text-emerald-500"></i>'


def _icon_no():
    return '<span class="text-slate-400">-</span>'


def _met_color_class(met_dt):
    if not met_dt or isinstance(met_dt, str):
        return ""
    try:
        now = datetime.now()
        if met_dt.tzinfo:
            now = datetime.now(met_dt.tzinfo)
        delta_h = (met_dt - now).total_seconds() / 3600.0
        if delta_h < 0:
            return "bg-slate-100 dark:bg-slate-900/40"
        if delta_h <= 24:
            return "bg-amber-50 dark:bg-amber-950/30"
        if delta_h <= 72:
            return "bg-cyan-50 dark:bg-cyan-950/20"
    except Exception:
        pass
    return ""


def _fmt_dt(value):
    if not value:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M")
    return str(value)


def _scans_for_patient(ipp: str) -> list:
    return ScanCache.query.filter_by(patient_id=str(ipp)).all()


def _ext_status_for_scans(scans, configured_extensions: list) -> dict:
    found = {(s.file_type or "").lower() for s in scans}
    status = {}
    for ext in configured_extensions:
        status[ext] = _icon_ok() if ext in found else _icon_no()
    return status


def _folder_from_scans(scans, fallback: str = "") -> str:
    for s in scans:
        raw = s.extra_data_json
        if raw:
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(data, dict) and data.get("folder"):
                    return data["folder"]
            except Exception:
                pass
    return fallback or ""


# ===========================================================================
# Scan Réseau IUCT (100% Admin Driven)
# ===========================================================================

def perform_network_scan() -> int:
    print("=== DEBUT perform_network_scan ===")

    try:
        ScanCache.query.delete()
        db_local.session.commit()
    except Exception as e:
        db_local.session.rollback()

    settings = get_global_settings()
    try:
        scan_depth = int(settings.get("SCAN_DEPTH", 3) or 3)
    except ValueError:
        scan_depth = 3
    try:
        year_overlap_days = int(settings.get("YEAR_OVERLAP_DAYS", 15) or 15)
    except ValueError:
        year_overlap_days = 15

    current_year = date.today().year
    today = date.today()
    allowed_years = {current_year}
    
    try:
        if today <= date(current_year, 1, min(year_overlap_days, 28)):
            allowed_years.add(current_year - 1)
    except ValueError:
        pass
    try:
        last_day = date(current_year, 12, 31)
        if (last_day - today).days < year_overlap_days:
            allowed_years.add(current_year + 1)
    except ValueError:
        pass

    tabs = CQTab.query.order_by(CQTab.display_order).all()
    scanned_count = 0
    seen_keys = set()

    for tab in tabs:
        cfg = tab._get_config() if hasattr(tab, '_get_config') else {}
        network_paths = tab.get_network_paths() if hasattr(tab, 'get_network_paths') else cfg.get("network_paths", [])
        if isinstance(network_paths, str):
            network_paths = [p.strip() for p in network_paths.replace("\r", "").split("\n") if p.strip()]

        file_exts = tab.get_file_extensions() if hasattr(tab, 'get_file_extensions') else cfg.get("file_extensions", [".dcm", ".pdf"])
        if isinstance(file_exts, str):
            file_exts = [e.strip() for e in file_exts.split(",") if e.strip()]
        
        valid_exts = tuple(
            e.lower() if e.startswith(".") else f".{e.lower()}"
            for e in file_exts if e
        )
        if not valid_exts:
            valid_exts = (".dcm", ".pdf")

        grouped_by_year = cfg.get("grouped_by_year", False)

        for base_path in network_paths:
            if not os.path.isdir(base_path):
                print(f"[SCAN] Chemin inaccessible: {base_path}")
                continue

            folder_basename = os.path.basename(os.path.normpath(base_path))

            root_paths_to_scan = []
            if grouped_by_year:
                for y in sorted(allowed_years):
                    ypath = os.path.join(base_path, str(y))
                    if os.path.isdir(ypath):
                        root_paths_to_scan.append(ypath)
                if not root_paths_to_scan:
                    root_paths_to_scan.append(base_path)
            else:
                root_paths_to_scan.append(base_path)

            for root_path in root_paths_to_scan:
                print(f"[SCAN] Ingestion de {root_path} (Depth={scan_depth})")
                for dirpath, dirnames, filenames in os.walk(root_path):
                    rel = os.path.relpath(dirpath, root_path)
                    depth = 0 if rel == "." else len(rel.split(os.sep))
                    if depth > scan_depth:
                        dirnames[:] = []
                        continue

                    dir_name = os.path.basename(dirpath)
                    ipp_candidates = extract_ipp_candidates(dir_name)
                    if not ipp_candidates:
                        continue

                    for ipp in ipp_candidates:
                        for fname in filenames:
                            f_ext = os.path.splitext(fname)[1].lower()
                            if f_ext not in valid_exts:
                                continue
                            full_file_path = os.path.join(dirpath, fname)
                            key = (str(ipp), full_file_path)
                            if key in seen_keys:
                                continue
                            seen_keys.add(key)

                            extra_data = json.dumps({
                                "folder": folder_basename,
                                "base_path": base_path,
                                "tab_id": tab.id
                            })

                            sc = ScanCache(
                                patient_id=str(ipp),
                                file_type=f_ext,
                                file_name=fname,
                                file_path=full_file_path,
                                extra_data_json=extra_data
                            )
                            db_local.session.add(sc)
                            scanned_count += 1

    try:
        db_local.session.commit()
        print(f"=== FIN SCAN : {scanned_count} fichiers indexés ===")
    except Exception as e:
        db_local.session.rollback()
        return 0

    return scanned_count


# ===========================================================================
# Détection exacte des Tâches CQ Réalisation vs Préparation
# ===========================================================================

def _is_cq_ready_task(display_focus: str, status: str) -> bool:
    """
    Vérifie qu'il s'agit bien d'une tâche de Réalisation de CQ ou Replanification.
    """
    norm_f = normalize(display_focus)
    norm_s = normalize(status)

    if norm_s not in ('ready', 'inprogress', 'in-progress', 'booked', 'arrived'):
        return False

    # Doit contenir "realisation" + "cq" OU "replanif" + "cq" OU ("cq" + machine/patient)
    if ("realisation" in norm_f and "cq" in norm_f) or ("replanif" in norm_f and "cq" in norm_f):
        return True

    if "cq" in norm_f and any(k in norm_f for k in ("patient", "ruby", "octa", "tomo", "hyperarc", "machine")):
        return True

    return False


def _is_prep_task(display_focus: str, status: str) -> bool:
    """
    Vérifie s'il s'agit d'une tâche de Préparation CQ (stec, stic, ra, dosim, validation liés au CQ).
    Empêche les faux positifs massifs de la clinique générale.
    """
    norm_f = normalize(display_focus)
    norm_s = normalize(status)

    if norm_s in ('cancelled', 'fulfilled', 'completed'):
        return False

    if "realisation" in norm_f and "cq" in norm_f:
        return False

    # La tâche de préparation doit explicitement faire référence au CQ, contrôle ou qualité
    has_cq_context = any(k in norm_f for k in ("cq", "controle", "qualite", "dosim", "stec", "stic", "ra"))
    if not has_cq_context:
        return False

    prep_keywords = ("wfstec", "wfstic", "wfra", "stec", "stic", "preparat", "validation", "dosim")
    return any(match_tab_keyword(k, display_focus) for k in prep_keywords)


def _fetch_aria_activity_rows(lookback_days: int) -> list:
    """
    Requête MySQL optimisée sans produit cartésien (sous-requêtes scalars pour MET et Prescription).
    """
    query = """
        SELECT 
            t.id                                            AS task_id,
            COALESCE(p.ipp, CAST(p.id AS CHAR))              AS patient_id,
            COALESCE(p.family_name_official, '')            AS last_name,
            COALESCE(p.given, '')                          AS first_name,
            COALESCE(t.display_focus, '')                   AS display_focus,
            COALESCE(t.status, 'ready')                     AS status,
            COALESCE(t.note, '')                            AS comment,
            (
                SELECT a.start_scheduled_period 
                FROM appointments a 
                WHERE a.patient_id = p.id 
                  AND LOWER(COALESCE(a.service_type, '')) LIKE 'met%%' 
                  AND LOWER(COALESCE(a.status, '')) NOT IN ('cancelled')
                ORDER BY a.start_scheduled_period DESC 
                LIMIT 1
            ) AS scheduled_start,
            (
                SELECT a.id 
                FROM appointments a 
                WHERE a.patient_id = p.id 
                  AND LOWER(COALESCE(a.service_type, '')) LIKE 'met%%' 
                  AND LOWER(COALESCE(a.status, '')) NOT IN ('cancelled')
                ORDER BY a.start_scheduled_period DESC 
                LIMIT 1
            ) AS appointment_id,
            (
                SELECT COALESCE(pr.site, pr.bodysite, '') 
                FROM prescriptions pr 
                WHERE pr.patient_id = p.id 
                ORDER BY pr.id DESC 
                LIMIT 1
            ) AS localisation
        FROM tasks t
        JOIN careplans cp ON t.careplan_id = cp.id
        JOIN patients p ON cp.patient_id = p.id
        WHERE (t.last_updated >= NOW() - INTERVAL %s DAY OR t.created_at >= NOW() - INTERVAL %s DAY)
          AND LOWER(COALESCE(t.status, '')) IN ('ready', 'in-progress', 'inprogress', 'booked', 'arrived')
        ORDER BY t.last_updated DESC
    """

    cfg = get_aria_mysql_config()

    try:
        conn, cursor = get_mysql_connection(cfg)
        try:
            cursor.execute(query, (lookback_days, lookback_days))
            rows = cursor.fetchall() or []
            print(f"[ARIA MySQL] Succès : {len(rows)} tâches brutes analysées (sans doublons).")
            return rows
        finally:
            try: cursor.close()
            except Exception: pass
            try: conn.close()
            except Exception: pass
    except Exception as e:
        print(f"[ARIA MySQL] Erreur : {e}")

    return []


def build_dashboard_data(tab_name=None) -> dict:
    """
    Point d'entrée principal /api/data.
    Ventile les patients dans les onglets avec vérification exacte des mots-clés.
    """
    settings = get_global_settings()
    try:
        lookback_days = int(settings.get("LOOKBACK_DAYS", 14) or 14)
    except ValueError:
        lookback_days = 14

    tabs = CQTab.query.order_by(CQTab.display_order).all()
    machines = CQMachine.query.order_by(CQMachine.display_order).all()

    configured_extensions = get_configured_extensions(tab_name if tab_name else None)
    raw_rows = _fetch_aria_activity_rows(lookback_days)

    counts = {t.label: 0 for t in tabs}
    main_by_key = {}  # CORRECTION ANTI-ÉCRASEMENT : Clé unique par IPP + Task ID
    prep_rows = []
    prep_seen = set()

    for row in raw_rows:
        ipp = str(row.get("patient_id") or "").strip()
        if not ipp:
            continue

        task_id = str(row.get("task_id") or "0")
        last_name = row.get("last_name") or ""
        first_name = row.get("first_name") or ""
        display_focus = row.get("display_focus") or ""
        status = str(row.get("status") or "ready")
        localisation = row.get("localisation") or "Non spécifié"
        comment = row.get("comment") or ""
        scheduled = row.get("scheduled_start")
        has_appointment = bool(row.get("appointment_id"))

        # 1. Traitement des Préparations
        if _is_prep_task(display_focus, status):
            matched_tab = find_tab_for_focus(display_focus, tabs)
            key = (ipp, display_focus)
            if key not in prep_seen:
                prep_seen.add(key)
                if (not tab_name) or (matched_tab and matched_tab.label == tab_name):
                    prep_rows.append({
                        "patient_id": ipp,
                        "patient_name": f"{last_name} {first_name}".strip(),
                        "task_name": display_focus,
                        "due_date": _fmt_dt(scheduled),
                        "status": status,
                    })
            continue

        # 2. Filtrage CQ : ignorer si ce n'est PAS une réalisation de CQ
        if not _is_cq_ready_task(display_focus, status):
            continue

        matched_tab = find_tab_for_focus(display_focus, tabs)
        if matched_tab is None:
            continue

        counts[matched_tab.label] = counts.get(matched_tab.label, 0) + 1

        if tab_name and matched_tab.label != tab_name:
            continue

        rules = find_technique_rules(display_focus, machines)
        default_energy = matched_tab.get_default_energy() if hasattr(matched_tab, 'get_default_energy') else ""

        scans = _scans_for_patient(ipp)
        tab_exts = get_configured_extensions(matched_tab.label)
        exts_for_row = configured_extensions or tab_exts
        ext_status = _ext_status_for_scans(scans, exts_for_row)

        folder_name = _folder_from_scans(scans, fallback=matched_tab.label)

        timeplanner_icon = (
            '<i class="fa-solid fa-calendar-check text-emerald-500 text-base" title="Rendez-vous planifié"></i>'
            if has_appointment or scheduled
            else '<span class="text-slate-400" title="Aucun créneau planifié">—</span>'
        )

        met_date = _fmt_dt(scheduled) if scheduled else "—"
        met_color = _met_color_class(scheduled) if isinstance(scheduled, datetime) else ""

        row_unique_key = f"{ipp}_{task_id}"
        main_by_key[row_unique_key] = {
            "patient_id": ipp,
            "last_name": last_name,
            "first_name": first_name,
            "localisation": localisation if localisation else "Non spécifié",
            "ext_status": ext_status,
            "dcm": ext_status.get(".dcm", _icon_no()),
            "pdf": ext_status.get(".pdf", _icon_no()),
            "folder": folder_name or "—",
            "met_date": met_date,
            "met_color": met_color,
            "energy": default_energy or "—",
            "technique": rules["technique_name"],
            "tech_name": rules["technique_name"],
            "base_duration": rules["acquisition"],
            "mount_duration": rules["mount_unmount"],
            "timeplanner": timeplanner_icon,
            "status": status,
            "comment": comment or "",
            "tab_label": matched_tab.label,
        }

    return {
        "main_rows": list(main_by_key.values()),
        "prep_rows": prep_rows,
        "counts": counts,
        "configured_extensions": configured_extensions,
    }