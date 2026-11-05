### 1. Fichier : `app/services/business.py`
# -*- coding: utf-8 -*-
"""
CQ Dashboard — Logique Métier Précise & Filtrée
- Découpage automatique & intelligent des extensions Admin (.dcm, .pdf, JSON, etc.)
- Traversal d'arborescence : IPP détecté dans le dossier parent
- Scan réseau atomique & non-bloquant en arrière-plan (la table ne se vide jamais)
- Lecture de l'énergie du faisceau dans les fichiers DICOM (.dcm)
- Tri par priority (Date MET) + Calcul jours ouvrés (Urgence)
- Timeplanner = CQ Patient programmé AUJOURD'HUI
"""
import os
import re
import json
import threading
import unicodedata
from datetime import datetime, date, timedelta
from collections import defaultdict
from sqlalchemy import text
from flask import current_app

from app.models_local import db_local, CQTab, CQMachine, ScanCache, Setting

# Horodatage et verrous du scan réseau
_LAST_SCAN_TIME = None
_SCAN_LOCK = threading.Lock()
_IS_SCANNING = False

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
    "URGENCY_DAYS",
    "NETWORK_SCAN_INTERVAL_MIN",
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
    """Supprime les zéros inutiles en tête (ex: '00012345' -> '12345')."""
    if not ipp_str:
        return ""
    cleaned = str(ipp_str).strip().lstrip("0")
    return cleaned if cleaned else "0"


def _normalize_ext(ext: str) -> str:
    """Consolide une extension : ' PDF ' -> '.pdf'"""
    if not ext:
        return ""
    e = str(ext).strip().lower()
    if not e:
        return ""
    if not e.startswith("."):
        e = "." + e
    return e


def _parse_extensions(raw) -> list:
    if raw is None:
        return []

    items = []
    if isinstance(raw, str):
        s = raw.strip()
        if (s.startswith("[") and s.endswith("]")) or (s.startswith("(") and s.endswith(")")):
            try:
                parsed = json.loads(s)
                return _parse_extensions(parsed)
            except Exception:
                pass
        items = re.split(r"[,;|\s]+", s)
    elif isinstance(raw, (list, tuple, set)):
        for item in raw:
            if isinstance(item, str) and any(c in item for c in (",", ";", "|", " ")):
                items.extend(re.split(r"[,;|\s]+", item))
            else:
                items.append(item)
    else:
        items = [str(raw)]

    out = []
    seen = set()
    for item in items:
        cleaned = _normalize_ext(item)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return out


def match_tab_keyword(keyword: str, text: str) -> bool:
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
        "ADMIN_PASSWORD": os.getenv("ADMIN_PASSWORD", "admin"),
        "LOOKBACK_DAYS": os.getenv("LOOKBACK_DAYS", "14"),
        "SCAN_DEPTH": os.getenv("SCAN_DEPTH", "3"),
        "SCANDIDOS_SIGNATURE": os.getenv("SCANDIDOS_SIGNATURE", "114358"),
        "YEAR_OVERLAP_DAYS": os.getenv("YEAR_OVERLAP_DAYS", "15"),
        "URGENCY_DAYS": os.getenv("URGENCY_DAYS", "1"),
        "NETWORK_SCAN_INTERVAL_MIN": os.getenv("NETWORK_SCAN_INTERVAL_MIN", "3"),
        "MYSQL_HOST": os.getenv("MYSQL_HOST") or os.getenv("DATABASE_HOST", "localhost"),
        "MYSQL_PORT": os.getenv("MYSQL_PORT") or os.getenv("DATABASE_PORT", "3306"),
        "MYSQL_USER": os.getenv("MYSQL_USER") or os.getenv("DATABASE_USER", "root"),
        "MYSQL_PASSWORD": os.getenv("MYSQL_PASSWORD") or os.getenv("DATABASE_PASSWORD", ""),
        "MYSQL_DB": os.getenv("MYSQL_DB") or os.getenv("DATABASE_NAME", "db_iuct"),
    }
    rows = Setting.query.all()
    setting_map = {r.key: r.value for r in rows if r.key and r.value is not None}
    for k, v in defaults.items():
        if k not in setting_map or not setting_map[k]:
            setting_map[k] = v
    return setting_map


def get_aria_mysql_config() -> dict:
    s = get_global_settings()
    return {
        "host": s.get("MYSQL_HOST", "localhost"),
        "port": int(s.get("MYSQL_PORT", 3306) or 3306),
        "user": s.get("MYSQL_USER", "root"),
        "password": s.get("MYSQL_PASSWORD", ""),
        "database": s.get("MYSQL_DB", "db_iuct"),
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

    matches = re.findall(r"(?<!\d)\d{3,10}(?!\d)", str(text))
    out = []
    for m in matches:
        if len(m) == 4 and 1990 <= int(m) <= 2035 and str(text).strip() == m:
            continue
        out.append(m)

    return list(set(out))


def find_patient_folder_info(dirpath: str, root_path: str):
    try:
        rel = os.path.relpath(dirpath, root_path)
    except Exception:
        rel = dirpath

    if rel == ".":
        parts = []
    else:
        parts = [p for p in rel.replace("/", os.sep).split(os.sep) if p and p != "."]

    for part in parts:
        ipps = extract_ipp_candidates(part)
        if ipps:
            clean_ipps = list(set(_clean_ipp(i) for i in ipps if _clean_ipp(i)))
            if clean_ipps:
                return part, clean_ipps

    return None, []


def find_tab_for_focus(focus_text: str, tabs: list):
    if not focus_text:
        return None
    for tab in tabs:
        aria_names = (
            tab.get_aria_keywords()
            if hasattr(tab, "get_aria_keywords")
            else [x.strip() for x in (tab.name or "").split(",") if x.strip()]
        )
        for kw in aria_names:
            if match_tab_keyword(kw, focus_text):
                return tab
    return None

def _get_tab_keywords(tab) -> list:
    """Extrait tous les mots-clés d'un CQTab pour le matching ARIA (device)."""
    if not tab:
        return []
    kws = []
    seen = set()

    if hasattr(tab, "get_aria_keywords"):
        for k in (tab.get_aria_keywords() or []):
            k = (k or "").strip()
            if k and k.lower() not in seen:
                seen.add(k.lower())
                kws.append(k)

    if hasattr(tab, "get_keywords"):
        for k in (tab.get_keywords() or []):
            k = (k or "").strip()
            if k and k.lower() not in seen:
                seen.add(k.lower())
                kws.append(k)

    raw_name = getattr(tab, "name", None) or ""
    for k in str(raw_name).split(","):
        k = k.strip()
        if k and k.lower() not in seen:
            seen.add(k.lower())
            kws.append(k)

    lab = (getattr(tab, "label", None) or "").strip()
    if lab and lab.lower() not in seen:
        seen.add(lab.lower())
        kws.append(lab)

    return kws

def find_technique_rules(task_text: str, machines: list) -> dict:
    full_text = (task_text or "").strip()
    norm_task = normalize(full_text)

    for m in machines:
        kw = normalize(m.name)
        if kw and kw in norm_task:
            return {
                "technique_full": full_text or m.name,
                "technique_name": m.name,
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
    tabs = CQTab.query.order_by(CQTab.display_order).all()
    ordered = []
    seen = set()

    for tab in tabs:
        if tab_label and tab.label != tab_label:
            continue
        exts = tab.get_file_extensions() if hasattr(tab, "get_file_extensions") else None
        for e in _parse_extensions(exts):
            if e not in seen:
                seen.add(e)
                ordered.append(e)

    return ordered if ordered else [".dcm", ".pdf"]


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


def _ext_status_for_scans(scans, configured_extensions: list) -> dict:
    found = set()
    for s in scans:
        ft = _normalize_ext(s.file_type or "")
        if ft:
            found.add(ft)
        if getattr(s, "file_name", None):
            ft2 = _normalize_ext(os.path.splitext(s.file_name)[1])
            if ft2:
                found.add(ft2)
        if getattr(s, "file_path", None):
            ft3 = _normalize_ext(os.path.splitext(s.file_path)[1])
            if ft3:
                found.add(ft3)

    parsed_configured = _parse_extensions(configured_extensions) or [".dcm", ".pdf"]
    status = {}
    for ext in parsed_configured:
        is_found = ext in found
        status[ext] = is_found
        status[ext.lstrip(".")] = is_found
        status[ext.upper()] = is_found
        status[ext.lstrip(".").upper()] = is_found
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
    return fallback or "-"

def _full_path_from_scans(scans, fallback: str = "") -> str:
    """Reconstitue le chemin complet (base_path + dossier patient) pour l'infobulle."""
    for s in scans:
        raw = s.extra_data_json
        if raw:
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(data, dict):
                    base = data.get("base_path", "")
                    pf = data.get("patient_folder", "")
                    if base and pf:
                        return os.path.join(base, pf)
                    elif base:
                        return base
            except Exception:
                pass
    return fallback or "-"

def _extract_dicom_energy(file_path: str) -> str:
    """
    Lit l'énergie du faisceau depuis les métadonnées d'un fichier DICOM RP/Plan.
    Exemples de retour : '6X', '6FFF', '10X', '10FFF', '18X'.
    """
    if not file_path or not file_path.lower().endswith(".dcm"):
        return ""
    try:
        import pydicom
        ds = pydicom.dcmread(file_path, stop_before_pixels=True, force=True)
        
        beam_seq = getattr(ds, "BeamSequence", None)
        if beam_seq and len(beam_seq) > 0:
            b = beam_seq[0]
            cp_seq = getattr(b, "ControlPointSequence", None)
            if cp_seq and len(cp_seq) > 0:
                cp0 = cp_seq[0]
                if hasattr(cp0, "NominalBeamEnergy"):
                    raw_e = str(cp0.NominalBeamEnergy).strip()
                    if raw_e.endswith(".0"):
                        raw_e = raw_e[:-2]
                    
                    mode = "X"
                    f_seq = getattr(b, "PrimaryFluenceModeSequence", None)
                    if f_seq and len(f_seq) > 0:
                        f_mode = getattr(f_seq[0], "FluenceModeID", "")
                        if "FFF" in str(f_mode).upper():
                            mode = "FFF"
                    return f"{raw_e}{mode}"
    except Exception:
        pass
    return ""


def _energy_from_scans(scans, fallback: str = "") -> str:
    """Récupère l'énergie de faisceau DICOM extraite lors du scan réseau."""
    for s in scans:
        raw = s.extra_data_json
        if raw:
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(data, dict) and data.get("energy"):
                    return data["energy"]
            except Exception:
                pass
    return fallback or "—"


def business_days_between(d1: datetime, d2: datetime) -> int:
    if not d1 or not d2:
        return 0
    current = d1.date()
    end = d2.date()

    if current > end:
        return -business_days_between(d2, d1)

    days = 0
    while current < end:
        if current.weekday() < 5:  # Lundi-Vendredi
            days += 1
        current += timedelta(days=1)
    return days


# ===========================================================================
# Scan Réseau Atomique & Non-Bloquant
# ===========================================================================

def perform_network_scan() -> int:
    global _LAST_SCAN_TIME
    print("=== DEBUT perform_network_scan ===")

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
    seen_keys = set()
    patient_energies = {}
    new_scans = []

    for tab in tabs:
        cfg = tab._get_config() if hasattr(tab, "_get_config") else {}
        network_paths = (
            tab.get_network_paths()
            if hasattr(tab, "get_network_paths")
            else cfg.get("network_paths", [])
        )
        if isinstance(network_paths, str):
            network_paths = [
                p.strip()
                for p in network_paths.replace("\r", "").split("\n")
                if p.strip()
            ]

        file_exts_raw = (
            tab.get_file_extensions()
            if hasattr(tab, "get_file_extensions")
            else cfg.get("file_extensions", [".dcm", ".pdf"])
        )

        valid_exts = tuple(_parse_extensions(file_exts_raw) or [".dcm", ".pdf"])

        grouped_by_year = cfg.get("grouped_by_year", False)
        if hasattr(tab, "grouped_by_year"):
            grouped_by_year = bool(getattr(tab, "grouped_by_year", False) or grouped_by_year)

        for base_path in network_paths:
            if not os.path.isdir(base_path):
                continue

            display_root = os.path.basename(os.path.normpath(base_path))

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

                    _folder_name, path_ipps = find_patient_folder_info(dirpath, root_path)

                    for fname in filenames:
                        f_ext = _normalize_ext(os.path.splitext(fname)[1])
                        if f_ext not in valid_exts:
                            continue

                        target_ipps = path_ipps
                        if not target_ipps:
                            target_ipps = [
                                _clean_ipp(i)
                                for i in extract_ipp_candidates(fname)
                                if _clean_ipp(i)
                            ]

                        if not target_ipps:
                            continue

                        full_file_path = os.path.join(dirpath, fname)

                        # Extraction DICOM Énergie
                        if f_ext == ".dcm":
                            for clean_ipp in target_ipps:
                                if clean_ipp not in patient_energies:
                                    e_found = _extract_dicom_energy(full_file_path)
                                    if e_found:
                                        patient_energies[clean_ipp] = e_found

                        for clean_ipp in target_ipps:
                            key = (clean_ipp, full_file_path)
                            if key in seen_keys:
                                continue
                            seen_keys.add(key)

                            extra_data = json.dumps(
                                {
                                    "folder": display_root,
                                    "base_path": base_path,
                                    "patient_folder": _folder_name or "",
                                    "tab_id": tab.id,
                                    "energy": patient_energies.get(clean_ipp, ""),
                                }
                            )

                            sc = ScanCache(
                                patient_id=clean_ipp,
                                file_type=f_ext,
                                file_name=fname,
                                file_path=full_file_path,
                                extra_data_json=extra_data,
                            )
                            new_scans.append(sc)

    # MISE À JOUR ATOMIQUE DU CACHE EN BASE DE DONNÉES
    try:
        ScanCache.query.delete()
        db_local.session.add_all(new_scans)
        db_local.session.commit()
        _LAST_SCAN_TIME = datetime.now()
        print(f"=== FIN SCAN ATOMIQUE : {len(new_scans)} fichiers indexés ===")
        return len(new_scans)
    except Exception as e:
        print(f"[ScanCache Error] {e}")
        db_local.session.rollback()
        return 0


def trigger_async_network_scan():
    """Déclenche le scan réseau en arrière-plan sans bloquer Flask."""
    global _IS_SCANNING
    with _SCAN_LOCK:
        if _IS_SCANNING:
            return
        _IS_SCANNING = True

    app = None
    try:
        from flask import current_app
        app = current_app._get_current_object()
    except Exception:
        pass

    def _worker():
        global _IS_SCANNING
        try:
            if app:
                with app.app_context():
                    perform_network_scan()
            else:
                perform_network_scan()
        except Exception as e:
            print(f"[Scan Thread Error] {e}")
        finally:
            with _SCAN_LOCK:
                _IS_SCANNING = False

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


# ===========================================================================
# ARIA — Tâches CQ + date MET
# ===========================================================================

def _is_cq_ready_task(display_focus: str, status: str) -> bool:
    norm_f = normalize(display_focus)
    norm_s = normalize(status)

    # 1. Vérification du statut ARIA
    if norm_s not in ("ready", "inprogress", "in-progress", "booked", "arrived"):
        return False

    # 2. Exclusion explicite des tâches de création, préparation ou planification initiale
    if "creation" in norm_f or "preparation" in norm_f:
        return False

    # 3. Tâches de réalisation effectives
    if ("realisation" in norm_f and "cq" in norm_f) or ("replanif" in norm_f):
        return True

    # 4. Tâches CQ spécifiques (ex: CQ Patient, CQ Ruby, etc.) hors création
    if "cq" in norm_f and any(
        k in norm_f for k in ("patient", "ruby", "octa", "tomo", "hyperarc")
    ):
        return True

    return False

def _fetch_aria_activity_rows(lookback_days: int) -> list:
    query = """
        SELECT
            t.id                                            AS task_id,
            COALESCE(p.ipp, CAST(p.id AS CHAR))             AS patient_id,
            COALESCE(p.family_name_official, '')            AS last_name,
            COALESCE(p.given, '')                           AS first_name,
            COALESCE(t.display_focus, '')                   AS display_focus,
            COALESCE(t.status, 'ready')                     AS status,
            COALESCE(t.note, '')                            AS comment,
            (
                SELECT a.start_scheduled_period
                FROM appointments a
                WHERE a.patient_id = p.id
                  AND LOWER(COALESCE(a.service_type, '')) LIKE 'met%%'
                  AND LOWER(COALESCE(a.status, '')) NOT IN ('cancelled', 'canceled', 'deleted')
                ORDER BY
                    CASE
                        WHEN a.start_scheduled_period >= CURDATE() THEN 0
                        ELSE 1
                    END,
                    CASE
                        WHEN a.start_scheduled_period >= CURDATE() THEN a.start_scheduled_period
                        ELSE NULL
                    END ASC,
                    a.start_scheduled_period DESC
                LIMIT 1
            ) AS scheduled_start,
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
        WHERE (t.last_updated >= NOW() - INTERVAL %s DAY
               OR t.created_at >= NOW() - INTERVAL %s DAY)
          AND LOWER(COALESCE(t.status, '')) IN (
              'ready', 'in-progress', 'inprogress', 'booked', 'arrived'
          )
        ORDER BY t.last_updated DESC
    """

    cfg = get_aria_mysql_config()
    try:
        conn, cursor = get_mysql_connection(cfg)
        try:
            cursor.execute(query, (lookback_days, lookback_days))
            rows = cursor.fetchall() or []
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
# Timeplanner = programme du JOUR (CQ Patient sur machine)
# ===========================================================================

def _is_cq_patient_activity(text: str) -> bool:
    n = normalize(text or "")
    if not n:
        return False
    if "cqpatient" in n:
        return True
    if "cq" in n and "patient" in n:
        return True
    if "realisationcq" in n or ("realisation" in n and "cq" in n):
        return True
    if n in ("cq", "controlequalite", "controlqualite"):
        return True
    return False


def _fetch_timeplanner_cq_ipps_today(tabs: list) -> dict:
    """Retourne un dictionnaire { ipp: device_name } des patients avec RDV CQ aujourd'hui."""
    cfg = get_aria_mysql_config()
    ipps = {}
    queries = [
        """
        SELECT
            COALESCE(p.ipp, CAST(p.id AS CHAR)) AS ipp,
            COALESCE(a.service_type, '')        AS service_type,
            COALESCE(a.status, '')              AS status,
            COALESCE(a.notes, '')               AS notes,
            COALESCE(a.device, '')              AS device
        FROM appointments a
        JOIN patients p ON a.patient_id = p.id
        WHERE DATE(a.start_scheduled_period) = CURDATE()
          AND LOWER(COALESCE(a.status, '')) NOT IN ('cancelled', 'canceled', 'deleted', 'noshow')
        """,
        """
        SELECT
            COALESCE(p.ipp, CAST(p.id AS CHAR)) AS ipp,
            COALESCE(a.service_type, '')        AS service_type,
            COALESCE(a.status, '')              AS status,
            ''                                  AS notes,
            COALESCE(a.device, '')              AS device
        FROM appointments a
        JOIN patients p ON a.patient_id = p.id
        WHERE DATE(a.start_scheduled_period) = CURDATE()
          AND LOWER(COALESCE(a.status, '')) NOT IN ('cancelled', 'canceled', 'deleted', 'noshow')
        """,
    ]

    rows = []
    conn = cursor = None
    try:
        conn, cursor = get_mysql_connection(cfg)
        for q in queries:
            try:
                cursor.execute(q)
                rows = cursor.fetchall() or []
                break
            except Exception:
                rows = []
                continue

        for r in rows:
            service = str(r.get("service_type") or "")
            notes = str(r.get("notes") or "")
            device = str(r.get("device") or "")

            if not _is_cq_patient_activity(f"{service} {notes}".strip()):
                continue

            if device and tabs:
                if find_tab_for_focus(device, tabs) is None:
                    continue

            ipp = _clean_ipp(str(r.get("ipp") or "").strip())
            if ipp:
                ipps[ipp] = device if device else "la machine"

        print(f"[Timeplanner] {len(ipps)} patient(s) CQ programmé(s) aujourd'hui")
        return ipps
    except Exception as e:
        print(f"[ARIA MySQL] Erreur Timeplanner : {e}")
        return {}
    finally:
        try:
            if cursor: cursor.close()
        except: pass
        try:
            if conn: conn.close()
        except: pass


# ===========================================================================
# Préparation (Validation médicale + Finalisation draft)
# ===========================================================================

def _is_validation_medicale_dosi(display_focus: str) -> bool:
    n = normalize(display_focus or "")
    return n.startswith("validationmedicaledeladosimetrie") or n.startswith(
        "validationmedicaledosimetrie"
    )


def _is_finalisation_dossier(display_focus: str) -> bool:
    n = normalize(display_focus or "")
    return n.startswith("finalisationdudossier") or n.startswith("finalisationdossier")


def _is_scanner_tdm(display_focus: str) -> bool:
    return normalize(display_focus or "").startswith("scanner")


def _is_appel_patient(display_focus: str) -> bool:
    n = normalize(display_focus or "")
    return n.startswith("appelerpatient") or n.startswith("appelpatient")


def _find_tab_for_workflow(workflow: str, tabs: list):
    if not tabs:
        return None
    matched = find_tab_for_focus(workflow or "", tabs)
    return matched  


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
        q_tasks = (
            f"SELECT careplan_id, display_focus, status, last_updated "
            f"FROM tasks WHERE careplan_id IN ({placeholders})"
        )
        cursor.execute(q_tasks, list(careplan_ids))
        tasks_by_cp = defaultdict(list)
        for t in cursor.fetchall() or []:
            tasks_by_cp[t.get("careplan_id")].append(t)

        patient_db_ids = [
            m["patient_db_id"] for m in careplan_meta.values() if m.get("patient_db_id")
        ]
        mets_by_patient = defaultdict(list)
        if patient_db_ids:
            ph2 = ",".join(["%s"] * len(patient_db_ids))
            q_met = (
                f"SELECT patient_id, start_scheduled_period, status "
                f"FROM appointments WHERE patient_id IN ({ph2}) "
                f"AND LOWER(COALESCE(service_type, '')) LIKE 'met%%' "
                f"AND LOWER(COALESCE(status, '')) NOT IN ('cancelled', 'canceled')"
            )
            cursor.execute(q_met, list(patient_db_ids))
            for m in cursor.fetchall() or []:
                mets_by_patient[m.get("patient_id")].append(m)

        prep_rows = []
        for cp_id, meta in careplan_meta.items():
            c_tasks = tasks_by_cp.get(cp_id, [])
            v_tasks = [
                t
                for t in c_tasks
                if _is_validation_medicale_dosi(t.get("display_focus") or "")
                and str(t.get("status") or "").lower().strip()
                in ("completed", "in-progress", "inprogress", "ready")
            ]
            if not v_tasks:
                continue
            val_date = max(
                [t.get("last_updated") for t in v_tasks if t.get("last_updated")] or [None]
            )

            if not any(
                _is_finalisation_dossier(t.get("display_focus") or "")
                and str(t.get("status") or "").lower().strip() == "draft"
                for t in c_tasks
            ):
                continue

            tdm_dates = [
                t.get("last_updated")
                for t in c_tasks
                if _is_scanner_tdm(t.get("display_focus") or "") and t.get("last_updated")
            ]
            tdm_date = max(tdm_dates) if tdm_dates else None

            app_tasks = sorted(
                [t for t in c_tasks if _is_appel_patient(t.get("display_focus") or "")],
                key=lambda x: x.get("last_updated") or datetime.min,
                reverse=True,
            )

            p_mets = mets_by_patient.get(meta.get("patient_db_id"), [])
            met_candidates = [
                m.get("start_scheduled_period")
                for m in p_mets
                if m.get("start_scheduled_period")
                and val_date
                and m.get("start_scheduled_period") > val_date
            ]
            met_start = max(met_candidates) if met_candidates else None

            matched_tab = _find_tab_for_workflow(meta.get("careplan_title") or "", tabs)
            if not meta.get("ipp"):
                continue

            prep_rows.append(
                {
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
                }
            )

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
# Point d'entrée Dashboard
# ===========================================================================

def _fetch_next_cq_slot(tab_name):
    """
    Récupère l'heure du prochain créneau de CQ physique/patient pour la machine ciblée.
    Totalement dynamique : se base sur la configuration des onglets enregistrée en Administration.
    """
    if not tab_name:
        return None

    tab = CQTab.query.filter_by(label=tab_name).first()
    if not tab:
        return None

    cfg = get_aria_mysql_config()

    # Structure exacte vérifiée : appointments.device, appointments.start_scheduled_period
    sql = """
        SELECT 
            a.start_scheduled_period AS start_time,
            COALESCE(a.device, '') AS device
        FROM appointments a
        JOIN patients p ON a.patient_id = p.id
        WHERE a.start_scheduled_period >= NOW()
          AND a.start_scheduled_period < CURDATE() + INTERVAL 1 DAY
          AND (
              p.family_name_official LIKE '%CQ HEBDOMADAIRE%' 
              OR p.id = 3
          )
          AND LOWER(COALESCE(a.status, '')) IN ('booked', 'arrived')
        ORDER BY a.start_scheduled_period ASC
    """

    conn = cursor = None
    rows = []
    try:
        conn, cursor = get_mysql_connection(cfg)
        cursor.execute(sql)
        rows = cursor.fetchall() or []
    except Exception as e:
        print(f"[ARIA MySQL] Erreur créneau CQ : {e}")
        return None
    finally:
        try:
            if cursor: cursor.close()
        except Exception: pass
        try:
            if conn: conn.close()
        except Exception: pass

    for r in rows:
        device_name = str(r.get("device") if isinstance(r, dict) else (r[1] if len(r) > 1 else "")).strip()
        start_time = r.get("start_time") if isinstance(r, dict) else r[0]

        if device_name and find_tab_for_focus(device_name, [tab]):
            return start_time

    return None


def _fmt_time_only(val) -> str:
    """Formate proprement n'importe quel objet date/time/timedelta/str MySQL en HH:MM."""
    if not val:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%H:%M")
    if hasattr(val, "strftime"):  # Gère datetime.time
        return val.strftime("%H:%M")
    s = str(val).strip()
    # Si string "YYYY-MM-DD HH:MM:SS" ou "HH:MM:SS" ou timedelta
    m = re.search(r"(\d{2}:\d{2})", s)
    if m:
        return m.group(1)
    return s


def _fetch_machine_schedule_today(tab_name):
    if not tab_name:
        return []

    tabs = CQTab.query.order_by(CQTab.display_order).all()
    tab = None
    for t in tabs:
        if (
            t.label == tab_name
            or normalize(t.label) == normalize(tab_name)
            or normalize(getattr(t, "name", "")) == normalize(tab_name)
        ):
            tab = t
            break

    if not tab:
        return []

    cfg = get_aria_mysql_config()

    sql = """
        SELECT 
            a.start_scheduled_period AS start_time,
            a.end_scheduled_period AS end_time,
            COALESCE(a.device, '') AS device,
            COALESCE(p.family_name_official, '') AS last_name,
            COALESCE(p.given, '') AS first_name,
            COALESCE(a.service_type, '') AS service_type,
            COALESCE(a.status, '') AS status,
            p.id AS patient_db_id
        FROM appointments a
        JOIN patients p ON a.patient_id = p.id
        WHERE a.start_scheduled_period >= CURDATE()
          AND a.start_scheduled_period < CURDATE() + INTERVAL 1 DAY
          AND LOWER(COALESCE(a.status, '')) NOT IN ('cancelled', 'canceled', 'deleted', 'noshow')
        ORDER BY a.start_scheduled_period ASC
    """

    conn = cursor = None
    rows = []
    try:
        conn, cursor = get_mysql_connection(cfg)
        cursor.execute(sql)
        rows = cursor.fetchall() or []
    finally:
        try:
            if cursor: cursor.close()
        except: pass
        try:
            if conn: conn.close()
        except: pass

    tab_kw = _get_tab_keywords(tab)
    raw_entries = []

    for r in rows:
        if isinstance(r, dict):
            device_name = str(r.get("device") or "").strip()
            start_time = r.get("start_time")
            end_time = r.get("end_time")
            patient = f"{r.get('last_name', '')} {r.get('first_name', '')}".strip()
            service = str(r.get("service_type") or "")
            status = str(r.get("status") or "")
            patient_db_id = r.get("patient_db_id")
        else:
            start_time, end_time, device_name, last_name, first_name, service, status, patient_db_id = r
            device_name = str(device_name or "").strip()
            patient = f"{last_name} {first_name}".strip()

        if not device_name or not find_tab_for_focus(device_name, [tab]):
            continue

        service_norm = normalize(service)
        if "pasdeseance" in service_norm:
            continue

        start_str = _fmt_time_only(start_time)
        end_str = _fmt_time_only(end_time)
        is_cq = _is_cq_patient_activity(service) or "cq" in patient.lower()

        entry = {
            "device": device_name,  # On mémorise la vraie machine
            "patient_key": str(patient_db_id) if patient_db_id else patient,
            "patient": patient,
            "start": start_str,
            "end": end_str,
            "service": service,
            "status": status.lower(),
            "is_cq": is_cq,
            "end_of_day": False,
        }
        raw_entries.append(entry)

        if "implant" in service_norm:
            entry["end_of_day"] = True
            break

    # Fusion par patient ET par machine
    status_priority = {"in-progress": 3, "inprogress": 3, "arrived": 3, "booked": 2, "ready": 2, "completed": 1, "done": 1}
    merged = {}
    order = []

    for e in raw_entries:
        key = f"{e['device']}_{e['patient_key']}"
        if key not in merged:
            merged[key] = {
                "device": e["device"],
                "start": e["start"],
                "end": e["end"],
                "patient": e["patient"],
                "services": [e["service"]] if e["service"] else [],
                "status": e["status"],
                "_status_priority": status_priority.get(e["status"], 0),
                "is_cq": e["is_cq"],
                "end_of_day": e["end_of_day"],
            }
            order.append(key)
        else:
            m = merged[key]
            if e["start"] and (not m["start"] or e["start"] < m["start"]): m["start"] = e["start"]
            if e["end"] and (not m["end"] or e["end"] > m["end"]): m["end"] = e["end"]
            if e["service"] and e["service"] not in m["services"]: m["services"].append(e["service"])
            p = status_priority.get(e["status"], 0)
            if p > m["_status_priority"]:
                m["status"] = e["status"]
                m["_status_priority"] = p
            m["is_cq"] = m["is_cq"] or e["is_cq"]
            if e["end_of_day"]: m["end_of_day"] = True

    schedule = []
    for key in order:
        m = merged[key]
        service_joined = " / ".join(m["services"])
        schedule.append({
            "device": m["device"], # Ajout de la machine pour le JS
            "start": m["start"],
            "end": m["end"],
            "start_time": m["start"],
            "end_time": m["end"],
            "patient": m["patient"],
            "patient_name": m["patient"],
            "service": service_joined,
            "service_type": service_joined,
            "status": m["status"],
            "is_cq": m["is_cq"],
            "end_of_day": m["end_of_day"],
        })

    return schedule

def build_dashboard_data(tab_name=None) -> dict:
    global _LAST_SCAN_TIME

    settings = get_global_settings()
    try:
        lookback_days = int(settings.get("LOOKBACK_DAYS", 14) or 14)
    except ValueError:
        lookback_days = 14

    try:
        urgency_days = int(settings.get("URGENCY_DAYS", 1) or 1)
    except ValueError:
        urgency_days = 1

    try:
        scan_interval_min = int(settings.get("NETWORK_SCAN_INTERVAL_MIN", 3) or 3)
    except ValueError:
        scan_interval_min = 3

    # ---------------------------------------------------------------
    # GESTION DES SCANS RÉSEAU (Synchrone si 0 ligne, Asynchrone ensuite)
    # ---------------------------------------------------------------
    cache_count = ScanCache.query.count()
    if cache_count == 0:
        perform_network_scan()
    else:
        if _LAST_SCAN_TIME is None:
            _LAST_SCAN_TIME = datetime.now()
            trigger_async_network_scan()
        else:
            time_since_last = datetime.now() - _LAST_SCAN_TIME
            if time_since_last > timedelta(minutes=scan_interval_min):
                trigger_async_network_scan()

    tabs = CQTab.query.order_by(CQTab.display_order).all()
    machines = CQMachine.query.order_by(CQMachine.display_order).all()

    # Programme du jour (Timeplanner)
    timeplanner_ipps = _fetch_timeplanner_cq_ipps_today(tabs)

    configured_extensions = get_configured_extensions(tab_name if tab_name else None)

    raw_rows = _fetch_aria_activity_rows(lookback_days)

    # INDEXATION EN MÉMOIRE DE LA BASE CACHE
    all_scans = ScanCache.query.all()
    scans_by_ipp = defaultdict(list)
    for sc in all_scans:
        c_ipp = _clean_ipp(sc.patient_id)
        if c_ipp:
            scans_by_ipp[c_ipp].append(sc)
        if sc.patient_id and sc.patient_id != c_ipp:
            scans_by_ipp[sc.patient_id].append(sc)

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
        scheduled = row.get("scheduled_start")  # = date MET

        if not _is_cq_ready_task(display_focus, status):
            continue

        matched_tab = find_tab_for_focus(display_focus, tabs)
        if matched_tab is None:
            continue

        counts[matched_tab.label] = counts.get(matched_tab.label, 0) + 1

        if tab_name and matched_tab.label != tab_name:
            continue

        rules = find_technique_rules(display_focus, machines)
        
        default_energy = ""
        if hasattr(matched_tab, "get_default_energy"):
            default_energy = matched_tab.get_default_energy() or ""
        # sinon éventuel champ config libre
        if not default_energy and hasattr(matched_tab, "_get_config"):
            default_energy = (matched_tab._get_config() or {}).get("default_energy", "") or ""

        clean_ipp_val = _clean_ipp(ipp)
        scans = scans_by_ipp.get(clean_ipp_val, []) or scans_by_ipp.get(ipp, [])
        tab_exts = get_configured_extensions(matched_tab.label)
        exts_for_row = configured_extensions or tab_exts
        ext_status = _ext_status_for_scans(scans, exts_for_row)

        folder_name = _folder_from_scans(scans, fallback="") if scans else "-"
        full_path = _full_path_from_scans(scans, fallback="") if scans else ""
        detected_energy = _energy_from_scans(scans, fallback=default_energy)

        tp_device = timeplanner_ipps.get(clean_ipp_val) or timeplanner_ipps.get(ipp)
        
        if tp_device:
            timeplanner_icon = (
                f'<i class="fa-solid fa-calendar-check text-emerald-500 text-base" '
                f'title="CQ de ce patient programmé aujourd\'hui sur : {tp_device}"></i>'
            )
        else:
            timeplanner_icon = (
                '<span class="text-slate-400 font-normal" '
                'title="CQ non programmé aujourd\'hui">—</span>'
            )

        sort_val = datetime.max
        raw_met_date_str = None
        met_color = (
            "bg-slate-50 text-slate-500 border-slate-200 "
            "dark:bg-slate-800 dark:text-slate-400 dark:border-slate-700"
        )

        if scheduled and isinstance(scheduled, datetime):
            sort_val = scheduled
            raw_met_date_str = scheduled.isoformat()
            bdays = business_days_between(now, scheduled)

            if bdays < 0 or (bdays == 0 and scheduled.date() <= now.date()):
                met_color = "bg-red-50 text-red-900 border-red-200 dark:bg-red-900/30 dark:text-red-300 dark:border-red-900"
            elif bdays <= urgency_days:
                met_color = "bg-orange-50 text-orange-900 border-orange-200 dark:bg-orange-900/30 dark:text-orange-300 dark:border-orange-900"
            else:
                met_color = "bg-emerald-50 text-emerald-900 border-emerald-200 dark:bg-emerald-900/10 dark:text-emerald-300 dark:border-emerald-900"

        row_unique_key = f"{ipp}_{task_id}"
        main_by_key[row_unique_key] = {
            "patient_id": ipp,
            "task_id": task_id,
            "last_name": last_name,
            "first_name": first_name,
            "localisation": localisation if localisation else "Non spécifié",
            "ext_status": ext_status,
            "folder": folder_name,
            "full_path": full_path, 
            "met_date": _fmt_dt(scheduled),
            "met_color": met_color,
            "raw_met_date": raw_met_date_str,
            "_sort_val": sort_val,
            "energy": detected_energy or "—",
            "technique": rules.get("technique_full") or rules.get("technique_name") or "",
            "tech_name": rules.get("technique_name") or "",
            "base_duration": rules["acquisition"],
            "mount_duration": rules["mount_unmount"],
            "timeplanner": timeplanner_icon,
            "status": status,
            "comment": comment or "",
            "tab_label": matched_tab.label,
        }

    sorted_main_rows = sorted(list(main_by_key.values()), key=lambda x: x["_sort_val"])
    for r in sorted_main_rows:
        r.pop("_sort_val", None)

    all_prep = _fetch_imminent_prep_rows(lookback_days, tabs)
    prep_rows = []
    for pr in all_prep:
        label = pr.get("tab_label")
        if tab_name and label != tab_name:
            continue
        prep_rows.append(pr)

    next_slot_dt = _fetch_next_cq_slot(tab_name)
    next_slot_str = _fmt_time_only(next_slot_dt) if next_slot_dt else None

    # === AJOUT DU PLANNING DU JOUR ===
    machine_schedule = _fetch_machine_schedule_today(tab_name)

    return {
        "main_rows": sorted_main_rows,
        "prep_rows": prep_rows,
        "counts": counts,
        "configured_extensions": configured_extensions,
        "scan_interval_min": scan_interval_min,
        "next_cq_slot": next_slot_str,
        "machine_schedule": machine_schedule,
    }