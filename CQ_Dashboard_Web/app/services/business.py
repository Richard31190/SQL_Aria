# *** services\business.py ***
# -*- coding: utf-8 -*-
"""
CQ Dashboard — Logique Métier Précise & Filtrée
- CQ Réalisation : logique validée (inchangée)
- Préparation : logique alignée sur l'ancien load_cq_imminent_patients_fast
  (Validation médicale dosimétrie + Finalisation dossier en draft)
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


def _fmt_dt_short(value):
    if not value:
        return "—"
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
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
    except Exception:
        db_local.session.rollback()
        return 0

    return scanned_count


# ===========================================================================
# Détection CQ Réalisation (INCHANGÉE — liste validée)
# ===========================================================================

def _is_cq_ready_task(display_focus: str, status: str) -> bool:
    """
    Vérifie qu'il s'agit bien d'une tâche de Réalisation de CQ ou Replanification.
    LOGIQUE VALIDÉE — NE PAS MODIFIER.
    """
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
    """
    Requête MySQL pour le tableau principal CQ (réalisation).
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
            rows = cursor.fetchall() or []
            print(f"[ARIA MySQL] CQ brut : {len(rows)} tâches analysées.")
            return rows
        finally:
            try:
                cursor.close()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
    except Exception as e:
        print(f"[ARIA MySQL] Erreur CQ : {e}")

    return []


# ===========================================================================
# Préparation = ancien load_cq_imminent_patients_fast
# ===========================================================================

def _is_validation_medicale_dosi(display_focus: str) -> bool:
    """display_focus ilike 'validation médicale de la dosimétrie%'"""
    n = normalize(display_focus or "")
    return n.startswith("validationmedicaledeladosimetrie") or n.startswith("validationmedicaledosimetrie")


def _is_finalisation_dossier(display_focus: str) -> bool:
    """display_focus ilike 'finalisation du dossier%'"""
    n = normalize(display_focus or "")
    return n.startswith("finalisationdudossier") or n.startswith("finalisationdossier")


def _is_scanner_tdm(display_focus: str) -> bool:
    """display_focus ilike 'scanner%'"""
    n = normalize(display_focus or "")
    return n.startswith("scanner")


def _is_appel_patient(display_focus: str) -> bool:
    n = normalize(display_focus or "")
    return n.startswith("appelerpatient") or n.startswith("appelpatient")


def _is_realisation_dosi(display_focus: str) -> bool:
    n = normalize(display_focus or "")
    return "realisationdosi" in n


def _find_tab_for_workflow(workflow: str, tabs: list):
    """
    Ancien code :
      tomo 2 / tomo 4 / tomo 7 / sinon Nova
    Ici : match Admin sur careplan.title, fallback onglet 'Nova'.
    """
    if not tabs:
        return None

    matched = find_tab_for_focus(workflow or "", tabs)
    if matched:
        return matched

    # Fallback historique : non-Tomo → Nova
    for t in tabs:
        label_n = normalize(t.label or "")
        name_n = normalize(getattr(t, "name", "") or "")
        if "nova" in label_n or "nova" in name_n:
            return t

    return None


def _fetch_imminent_prep_rows(lookback_days: int, tabs: list) -> list:
    """
    Reproduit load_cq_imminent_patients_fast de l'ancien programme :

    1) Tâches 'Validation médicale de la dosimétrie' récentes
       status ∈ completed | in-progress | ready
    2) Sur le même careplan : 'Finalisation du dossier' encore en draft
    3) Dates TDM (scanner), validation, MET (si MET > date validation)
    4) Ventilation par careplan.title (Workflow) → onglet
    """
    cfg = get_aria_mysql_config()

    # --- A. Careplans candidats via validation médicale récente ---
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

        # Filtre Python exact sur le libellé (accents / variantes)
        val_rows = []
        careplan_ids = set()
        careplan_meta = {}  # cp_id -> info patient / workflow

        for r in raw_val:
            focus = r.get("display_focus") or ""
            if not _is_validation_medicale_dosi(focus):
                continue
            cp_id = r.get("careplan_id")
            if not cp_id:
                continue
            val_rows.append(r)
            careplan_ids.add(cp_id)
            careplan_meta[cp_id] = {
                "ipp": str(r.get("ipp") or "").strip(),
                "last_name": r.get("last_name") or "",
                "first_name": r.get("first_name") or "",
                "patient_db_id": r.get("patient_db_id"),
                "careplan_title": r.get("careplan_title") or "",
            }

        if not careplan_ids:
            print("[ARIA MySQL] Prep : aucune validation médicale dosimétrie récente.")
            return []

        # --- B. TOUTES les tâches de ces careplans (finalisation, scanner, appel, dosi) ---
        placeholders = ",".join(["%s"] * len(careplan_ids))
        cp_list = list(careplan_ids)

        q_tasks = f"""
            SELECT
                careplan_id,
                display_focus,
                status,
                last_updated
            FROM tasks
            WHERE careplan_id IN ({placeholders})
        """
        cursor.execute(q_tasks, cp_list)
        all_tasks = cursor.fetchall() or []

        tasks_by_cp = defaultdict(list)
        for t in all_tasks:
            tasks_by_cp[t.get("careplan_id")].append(t)

        # --- C. MET des patients concernés ---
        patient_db_ids = [m["patient_db_id"] for m in careplan_meta.values() if m.get("patient_db_id")]
        mets_by_patient = defaultdict(list)

        if patient_db_ids:
            ph2 = ",".join(["%s"] * len(patient_db_ids))
            q_met = f"""
                SELECT
                    patient_id,
                    start_scheduled_period,
                    status
                FROM appointments
                WHERE patient_id IN ({ph2})
                  AND LOWER(COALESCE(service_type, '')) LIKE 'met%%'
                  AND LOWER(COALESCE(status, '')) NOT IN ('cancelled', 'canceled')
            """
            cursor.execute(q_met, list(patient_db_ids))
            for m in (cursor.fetchall() or []):
                mets_by_patient[m.get("patient_id")].append(m)

        # --- D. Assemblage (même règles que l'ancien programme) ---
        prep_rows = []
        seen_cp = set()

        for cp_id, meta in careplan_meta.items():
            if cp_id in seen_cp:
                continue

            c_tasks = tasks_by_cp.get(cp_id, [])

            # validation médicale dans le lookback
            v_tasks = []
            for t in c_tasks:
                if not _is_validation_medicale_dosi(t.get("display_focus") or ""):
                    continue
                st = str(t.get("status") or "").lower().strip()
                if st not in ("completed", "in-progress", "inprogress", "ready"):
                    continue
                lu = t.get("last_updated")
                v_tasks.append(t)

            if not v_tasks:
                continue

            val_dates = [t.get("last_updated") for t in v_tasks if t.get("last_updated")]
            if not val_dates:
                continue
            val_date = max(val_dates)

            # finalisation du dossier DOIT être en draft
            has_finalisation_draft = any(
                _is_finalisation_dossier(t.get("display_focus") or "")
                and str(t.get("status") or "").lower().strip() == "draft"
                for t in c_tasks
            )
            if not has_finalisation_draft:
                continue

            seen_cp.add(cp_id)

            # TDM / scanner
            tdm_dates = [
                t.get("last_updated") for t in c_tasks
                if _is_scanner_tdm(t.get("display_focus") or "") and t.get("last_updated")
            ]
            tdm_date = max(tdm_dates) if tdm_dates else None

            # Appel patient
            appel_tasks = [t for t in c_tasks if _is_appel_patient(t.get("display_focus") or "")]
            appel_status = None
            if appel_tasks:
                appel_tasks_sorted = sorted(
                    appel_tasks,
                    key=lambda x: x.get("last_updated") or datetime.min,
                    reverse=True
                )
                appel_status = appel_tasks_sorted[0].get("status")

            # Réalisation dosi (info)
            dosi_task = next(
                (t.get("display_focus") for t in c_tasks if _is_realisation_dosi(t.get("display_focus") or "")),
                None
            )

            # MET strictement APRÈS la date de validation (comme l'ancien code)
            p_db_id = meta.get("patient_db_id")
            p_mets = mets_by_patient.get(p_db_id, [])
            met_candidates = []
            for m in p_mets:
                start = m.get("start_scheduled_period")
                if start and val_date and start > val_date:
                    met_candidates.append(start)
            met_start = max(met_candidates) if met_candidates else None
            va_tomber = bool(met_start)

            workflow = meta.get("careplan_title") or ""
            matched_tab = _find_tab_for_workflow(workflow, tabs)

            ipp = meta.get("ipp") or ""
            if not ipp:
                continue

            last_name = meta.get("last_name") or ""
            first_name = meta.get("first_name") or ""

            prep_rows.append({
                "patient_id": ipp,
                "patient_name": f"{last_name} {first_name}".strip(),
                "last_name": last_name,
                "first_name": first_name,
                "task_name": "Validation médicale de la dosimétrie",
                "workflow": workflow,
                "due_date": _fmt_dt(met_start),
                "status": appel_status or "ready",
                "tdm_date": _fmt_dt_short(tdm_date),
                "validation_date": _fmt_dt_short(val_date),
                "met_start": _fmt_dt(met_start),
                "va_tomber": va_tomber,
                "realisation_dosi_task": dosi_task or "",
                "tab_label": matched_tab.label if matched_tab else None,
            })

        print(f"[ARIA MySQL] Prep imminent : {len(prep_rows)} patients (validation + finalisation draft).")
        return prep_rows

    except Exception as e:
        print(f"[ARIA MySQL] Erreur prep : {e}")
        return []
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


# ===========================================================================
# Point d'entrée dashboard
# ===========================================================================

def build_dashboard_data(tab_name=None) -> dict:
    """
    /api/data
    - main_rows / counts : CQ réalisation (INCHANGÉ)
    - prep_rows : patients imminents (validation médicale + finalisation draft)
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

    # ------------------------------------------------------------------
    # A. CQ RÉALISATION — logique validée, ne pas toucher
    # ------------------------------------------------------------------
    counts = {t.label: 0 for t in tabs}
    main_by_key = {}

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

    # ------------------------------------------------------------------
    # B. PRÉPARATION — logique ancien programme (imminent)
    # ------------------------------------------------------------------
    all_prep = _fetch_imminent_prep_rows(lookback_days, tabs)
    prep_rows = []
    for pr in all_prep:
        label = pr.get("tab_label")
        if tab_name and label != tab_name:
            continue
        prep_rows.append(pr)

    return {
        "main_rows": list(main_by_key.values()),
        "prep_rows": prep_rows,
        "counts": counts,
        "configured_extensions": configured_extensions,
    }