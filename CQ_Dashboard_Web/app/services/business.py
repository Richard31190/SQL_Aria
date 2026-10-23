# *** services\business.py ***
# -*- coding: utf-8 -*-
"""
CQ Dashboard — Logique Métier Précise & Filtrée
- Tri par priorité (Date MET)
- Calcul jours ouvrés (Urgence de la tâche)
- Code couleur de criticité dynamique
"""
import os
import re
import json
import unicodedata
from datetime import datetime, date, timedelta
from collections import defaultdict

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
    "URGENCY_DAYS",  # <-- Nouveau paramètre
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


def _clean_ipp(ipp_str: str) -> str:
    """Supprime les zéros inutiles en-tête (ex: '00012345' -> '12345')."""
    if not ipp_str:
        return ""
    cleaned = str(ipp_str).strip().lstrip("0")
    return cleaned if cleaned else "0"


def match_tab_keyword(keyword: str, text: str) -> bool:
    """
    Vérifie si 'keyword' est présent dans 'text'.
    Si keyword fait 3 lettres ou moins (ex: 'ra', 'stec'), exige un mot entier.
    """
    if not keyword or not text:
        return False

    norm_kw = normalize(keyword)
    norm_text = normalize(text)
    if not norm_kw or not norm_text:
        return False

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
        'URGENCY_DAYS': '1',  # 1 jour ouvré par défaut pour l'alerte orange
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
    """Associe une tâche/workflow à un onglet selon les mots-clés ARIA de l'onglet."""
    if not focus_text:
        return None
    for tab in tabs:
        aria_names = tab.get_aria_keywords() if hasattr(tab, 'get_aria_keywords') else [x.strip() for x in (tab.name or "").split(",") if x.strip()]
        for kw in aria_names:
            if match_tab_keyword(kw, focus_text):
                return tab
    return None


def find_technique_rules(task_text: str, machines: list) -> dict:
    """
    Retourne :
      - technique_full  : libellé ARIA complet (pour l'affichage)
      - technique_name  : nom de technique Admin matché (pour les durées)
      - mount_unmount / acquisition
    """
    full_text = (task_text or "").strip()
    norm_task = normalize(full_text)

    for m in machines:
        kw = normalize(m.name)
        if kw and kw in norm_task:
            return {
                "technique_full": full_text or m.name,
                "technique_name": m.name,  # nom court Admin → regroupement montages
                "mount_unmount": int(m.get_mount_unmount() or 0),
                "acquisition": int(m.get_acquisition() or 15),
            }

    return {
        "technique_full": full_text or "CQ Standard",
        "technique_name": full_text or "CQ Standard",
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
    return '<i class="fa-solid fa-circle-check text-emerald-500 text-lg"></i>'


def _icon_no():
    return '<span class="text-slate-400 text-sm">-</span>'


def _fmt_dt(value):
    if not value:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M")
    return str(value)


def _fmt_dt_short(value):
    if not value:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    return str(value)


def _scans_for_patient(ipp: str) -> list:
    """
    Recherche souple : teste l'IPP brut (ex: '00012345') ET l'IPP nettoyé (ex: '12345').
    """
    if not ipp:
        return []
    raw_ipp = str(ipp).strip()
    clean_ipp = _clean_ipp(raw_ipp)

    return ScanCache.query.filter(
        (ScanCache.patient_id == raw_ipp) | (ScanCache.patient_id == clean_ipp)
    ).all()


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


def business_days_between(d1: datetime, d2: datetime) -> int:
    """
    Calcule le nombre de jours ouvrés (Lundi au Vendredi) entre deux dates.
    """
    if not d1 or not d2:
        return 0
    days = 0
    current = d1.date()
    end = d2.date()

    if current > end:
        return -business_days_between(d2, d1)

    while current < end:
        # 0 = Lundi, 1 = Mardi, ..., 4 = Vendredi
        if current.weekday() < 5:
            days += 1
        current += timedelta(days=1)
    return days


# ===========================================================================
# Scan Réseau IUCT 
# ===========================================================================

def perform_network_scan() -> int:
    print("=== DEBUT perform_network_scan ===")

    try:
        ScanCache.query.delete()
        db_local.session.commit()
    except Exception:
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

                    for raw_ipp in ipp_candidates:
                        clean_ipp = _clean_ipp(raw_ipp)
                        for fname in filenames:
                            f_ext = os.path.splitext(fname)[1].lower()
                            if f_ext not in valid_exts:
                                continue
                            full_file_path = os.path.join(dirpath, fname)
                            key = (clean_ipp, full_file_path)
                            if key in seen_keys:
                                continue
                            seen_keys.add(key)

                            extra_data = json.dumps({
                                "folder": folder_basename,
                                "base_path": base_path,
                                "tab_id": tab.id
                            })

                            sc = ScanCache(
                                patient_id=clean_ipp,
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
    except Exception:
        db_local.session.rollback()
        return 0

    return scanned_count


# ===========================================================================
# Détection CQ Réalisation
# ===========================================================================

def _is_cq_ready_task(display_focus: str, status: str) -> bool:
    norm_f = normalize(display_focus)
    norm_s = normalize(status)

    if norm_s not in ('ready', 'inprogress', 'in-progress', 'booked', 'arrived'):
        return False
    if ("realisation" in norm_f and "cq" in norm_f) or ("replanif" in norm_f):
        return True
    if "cq" in norm_f and any(k in norm_f for k in ("patient", "ruby", "octa", "tomo", "hyperarc")):
        return True

    return False


def _fetch_aria_activity_rows(lookback_days: int) -> list:
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
                  AND LOWER(COALESCE(a.status, '')) NOT IN ('cancelled', 'canceled')
                ORDER BY a.start_scheduled_period DESC 
                LIMIT 1
            ) AS scheduled_start,
            (
                SELECT a.id 
                FROM appointments a 
                WHERE a.patient_id = p.id 
                  AND LOWER(COALESCE(a.service_type, '')) LIKE 'met%%' 
                  AND LOWER(COALESCE(a.status, '')) NOT IN ('cancelled', 'canceled')
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
            return cursor.fetchall() or []
        finally:
            try: cursor.close()
            except Exception: pass
            try: conn.close()
            except Exception: pass
    except Exception as e:
        print(f"[ARIA MySQL] Erreur CQ : {e}")

    return []


# ===========================================================================
# Préparation (Validation Médicale Dosimétrie + Finalisation Draft)
# ===========================================================================

def _is_validation_medicale_dosi(display_focus: str) -> bool:
    n = normalize(display_focus or "")
    return n.startswith("validationmedicaledeladosimetrie") or n.startswith("validationmedicaledosimetrie")

def _is_finalisation_dossier(display_focus: str) -> bool:
    n = normalize(display_focus or "")
    return n.startswith("finalisationdudossier") or n.startswith("finalisationdossier")

def _is_scanner_tdm(display_focus: str) -> bool:
    return normalize(display_focus or "").startswith("scanner")

def _is_appel_patient(display_focus: str) -> bool:
    n = normalize(display_focus or "")
    return n.startswith("appelerpatient") or n.startswith("appelpatient")

def _is_realisation_dosi(display_focus: str) -> bool:
    return "realisationdosi" in normalize(display_focus or "")

def _find_tab_for_workflow(workflow: str, tabs: list):
    if not tabs:
        return None
    matched = find_tab_for_focus(workflow or "", tabs)
    if matched:
        return matched
    for t in tabs:
        label_n = normalize(t.label or "")
        name_n = normalize(getattr(t, "name", "") or "")
        if "nova" in label_n or "nova" in name_n:
            return t
    return None

def _fetch_imminent_prep_rows(lookback_days: int, tabs: list) -> list:
    cfg = get_aria_mysql_config()
    q_val = """
        SELECT
            t.id                 AS task_id,
            t.careplan_id        AS careplan_id,
            t.display_focus      AS display_focus,
            t.status             AS status,
            t.last_updated       AS last_updated,
            COALESCE(cp.title, '') AS careplan_title,
            cp.patient_id        AS patient_db_id,
            COALESCE(p.ipp, CAST(p.id AS CHAR)) AS ipp,
            COALESCE(p.family_name_official, '') AS last_name,
            COALESCE(p.given, '') AS first_name
        FROM tasks t
        JOIN careplans cp ON t.careplan_id = cp.id
        JOIN patients p ON cp.patient_id = p.id
        WHERE t.last_updated >= NOW() - INTERVAL %s DAY
          AND LOWER(COALESCE(t.status, '')) IN ('completed', 'in-progress', 'inprogress', 'ready')
    """
    try:
        conn, cursor = get_mysql_connection(cfg)
    except Exception as e:
        print(f"[ARIA MySQL] Erreur prep connect : {e}")
        return []

    try:
        cursor.execute(q_val, (lookback_days,))
        raw_val = cursor.fetchall() or []
        careplan_ids = set()
        careplan_meta = {}

        for r in raw_val:
            if not _is_validation_medicale_dosi(r.get("display_focus") or ""):
                continue
            cp_id = r.get("careplan_id")
            if not cp_id:
                continue
            careplan_ids.add(cp_id)
            careplan_meta[cp_id] = {
                "ipp": str(r.get("ipp") or "").strip(),
                "last_name": r.get("last_name") or "",
                "first_name": r.get("first_name") or "",
                "patient_db_id": r.get("patient_db_id"),
                "careplan_title": r.get("careplan_title") or "",
            }

        if not careplan_ids:
            return []

        placeholders = ",".join(["%s"] * len(careplan_ids))
        q_tasks = f"SELECT careplan_id, display_focus, status, last_updated FROM tasks WHERE careplan_id IN ({placeholders})"
        cursor.execute(q_tasks, list(careplan_ids))
        tasks_by_cp = defaultdict(list)
        for t in (cursor.fetchall() or []):
            tasks_by_cp[t.get("careplan_id")].append(t)

        patient_db_ids = [m["patient_db_id"] for m in careplan_meta.values() if m.get("patient_db_id")]
        mets_by_patient = defaultdict(list)
        if patient_db_ids:
            ph2 = ",".join(["%s"] * len(patient_db_ids))
            q_met = f"SELECT patient_id, start_scheduled_period, status FROM appointments WHERE patient_id IN ({ph2}) AND LOWER(COALESCE(service_type, '')) LIKE 'met%%' AND LOWER(COALESCE(status, '')) NOT IN ('cancelled', 'canceled')"
            cursor.execute(q_met, list(patient_db_ids))
            for m in (cursor.fetchall() or []):
                mets_by_patient[m.get("patient_id")].append(m)

        prep_rows = []
        for cp_id, meta in careplan_meta.items():
            c_tasks = tasks_by_cp.get(cp_id, [])
            v_tasks = [t for t in c_tasks if _is_validation_medicale_dosi(t.get("display_focus") or "") and str(t.get("status") or "").lower().strip() in ("completed", "in-progress", "inprogress", "ready")]
            if not v_tasks: continue
            val_date = max([t.get("last_updated") for t in v_tasks if t.get("last_updated")] or [None])
            
            if not any(_is_finalisation_dossier(t.get("display_focus") or "") and str(t.get("status") or "").lower().strip() == "draft" for t in c_tasks):
                continue

            tdm_dates = [t.get("last_updated") for t in c_tasks if _is_scanner_tdm(t.get("display_focus") or "") and t.get("last_updated")]
            tdm_date = max(tdm_dates) if tdm_dates else None

            app_tasks = sorted([t for t in c_tasks if _is_appel_patient(t.get("display_focus") or "")], key=lambda x: x.get("last_updated") or datetime.min, reverse=True)
            dosi_task = next((t.get("display_focus") for t in c_tasks if _is_realisation_dosi(t.get("display_focus") or "")), None)

            p_mets = mets_by_patient.get(meta.get("patient_db_id"), [])
            met_candidates = [m.get("start_scheduled_period") for m in p_mets if m.get("start_scheduled_period") and val_date and m.get("start_scheduled_period") > val_date]
            met_start = max(met_candidates) if met_candidates else None

            matched_tab = _find_tab_for_workflow(meta.get("careplan_title") or "", tabs)
            if not meta.get("ipp"): continue

            prep_rows.append({
                "patient_id": meta.get("ipp"),
                "patient_name": f"{meta.get('last_name')} {meta.get('first_name')}".strip(),
                "task_name": "Validation médicale",
                "workflow": meta.get("careplan_title") or "",
                "due_date": _fmt_dt(met_start),
                "status": app_tasks[0].get("status") if app_tasks else "ready",
                "tdm_date": _fmt_dt_short(tdm_date),
                "validation_date": _fmt_dt_short(val_date),
                "met_start": _fmt_dt(met_start),
                "va_tomber": bool(met_start),
                "tab_label": matched_tab.label if matched_tab else None,
            })

        return prep_rows
    except Exception as e:
        print(f"[ARIA MySQL] Erreur prep : {e}")
        return []
    finally:
        try: cursor.close()
        except Exception: pass
        try: conn.close()
        except Exception: pass


# ===========================================================================
# Point d'entrée Dashboard (Avec Tri par Date et Code Couleur)
# ===========================================================================

def build_dashboard_data(tab_name=None) -> dict:
    """
    Point d'entrée principal /api/data.
    """
    settings = get_global_settings()
    try:
        lookback_days = int(settings.get("LOOKBACK_DAYS", 14) or 14)
    except ValueError:
        lookback_days = 14
    
    try:
        urgency_days = int(settings.get("URGENCY_DAYS", 1) or 1)
    except ValueError:
        urgency_days = 1

    tabs = CQTab.query.order_by(CQTab.display_order).all()
    machines = CQMachine.query.order_by(CQMachine.display_order).all()

    configured_extensions = get_configured_extensions(tab_name if tab_name else None)
    
    # Auto-scan au premier chargement si la table ScanCache est encore vide
    if ScanCache.query.count() == 0:
        perform_network_scan()

    raw_rows = _fetch_aria_activity_rows(lookback_days)

    counts = {t.label: 0 for t in tabs}
    main_by_key = {}
    now = datetime.now()

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

        # ---------------------------------------------------------
        # CALCUL DU CODE COULEUR D'URGENCE (Jours Ouvrés) ET DU TRI
        # ---------------------------------------------------------
        sort_val = datetime.max  # Par défaut, mis tout en bas si pas de date
        raw_met_date_str = None
        met_color = "bg-slate-50 text-slate-500 border-slate-200 dark:bg-slate-800 dark:text-slate-400 dark:border-slate-700"

        if scheduled and isinstance(scheduled, datetime):
            sort_val = scheduled
            raw_met_date_str = scheduled.isoformat()
            
            # Calcul des jours ouvrés de différence
            bdays = business_days_between(now, scheduled)

            # Passé ou Aujourd'hui -> ROUGE
            if bdays < 0 or (bdays == 0 and scheduled.date() <= now.date()):
                met_color = "bg-red-50 text-red-900 border-red-200 dark:bg-red-900/30 dark:text-red-300 dark:border-red-900"
            # Urgent (selon le seuil d'administration, ex: 1 jour ouvré) -> ORANGE
            elif bdays <= urgency_days:
                met_color = "bg-orange-50 text-orange-900 border-orange-200 dark:bg-orange-900/30 dark:text-orange-300 dark:border-orange-900"
            # Plus tard -> VERT
            else:
                met_color = "bg-emerald-50 text-emerald-900 border-emerald-200 dark:bg-emerald-900/10 dark:text-emerald-300 dark:border-emerald-900"
        # ---------------------------------------------------------

        row_unique_key = f"{ipp}_{task_id}"
        main_by_key[row_unique_key] = {
            "patient_id": ipp,
            "task_id": task_id,
            "last_name": last_name,
            "first_name": first_name,
            "localisation": localisation if localisation else "Non spécifié",
            "ext_status": ext_status,
            "dcm": ext_status.get(".dcm", _icon_no()),
            "pdf": ext_status.get(".pdf", _icon_no()),
            "folder": folder_name or "—",
            "met_date": _fmt_dt(scheduled),
            "met_color": met_color,
            "raw_met_date": raw_met_date_str, # Utile pour le calendrier Javascript
            "_sort_val": sort_val,            # Utilisé pour le tri
            "energy": default_energy or "—",
            "technique": rules.get("technique_full") or rules.get("technique_name") or "",
            "tech_name": rules.get("technique_name") or "",
            "base_duration": rules["acquisition"],
            "mount_duration": rules["mount_unmount"],
            "timeplanner": timeplanner_icon,
            "status": status,
            "comment": comment or "",
            "tab_label": matched_tab.label,
        }

    # ---------------------------------------------------------
    # TRI DES PATIENTS PAR ORDRE D'URGENCE DE LA MET
    # ---------------------------------------------------------
    sorted_main_rows = sorted(list(main_by_key.values()), key=lambda x: x["_sort_val"])
    
    # Nettoyage de la clé de tri temporaire
    for r in sorted_main_rows:
        r.pop("_sort_val", None)

    all_prep = _fetch_imminent_prep_rows(lookback_days, tabs)
    prep_rows = []
    for pr in all_prep:
        label = pr.get("tab_label")
        if tab_name and label != tab_name:
            continue
        prep_rows.append(pr)

    return {
        "main_rows": sorted_main_rows,
        "prep_rows": prep_rows,
        "counts": counts,
        "configured_extensions": configured_extensions,
    }