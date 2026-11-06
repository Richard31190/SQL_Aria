# -*- coding: utf-8 -*-
# *** routes/main.py ***
import os
from flask import Blueprint, render_template, jsonify, request
from app.models_local import CQTab, CQMachine, Setting, ScanCache
from app.services.business import (
    build_dashboard_data,
    perform_network_scan,
    get_configured_extensions,
)

GLOBAL_KEYS = [
    "ADMIN_PASSWORD",
    "LOOKBACK_DAYS",
    "SCAN_DEPTH",
    "SCANDIDOS_SIGNATURE",
    "YEAR_OVERLAP_DAYS",
]

main_bp = Blueprint("main", __name__)

@main_bp.route("/")
def index():
    tabs = CQTab.query.order_by(CQTab.display_order).all()
    default_tab = tabs[0].label if tabs else ""
    configured_extensions = get_configured_extensions()
    
    initial_data = build_dashboard_data(default_tab) if default_tab else {}

    return render_template(
        "dashboard.html",
        tabs=[t.to_dict() for t in tabs],
        default_tab=default_tab,
        configured_extensions=configured_extensions,
        machine_schedule=initial_data.get("machine_schedule", []),
        next_cq_slot=initial_data.get("next_cq_slot"),
    )

@main_bp.route("/api/data")
def get_data():
    tab_name = request.args.get("tab") or request.args.get("tab_id")
    data = build_dashboard_data(tab_name)
    
    return jsonify(
        {
            "status": "success",
            "timestamp": request.args.get("_t"),
            "phantom": data.get("phantom", ""),
            "main_rows": data.get("main_rows", []),
            "prep_rows": data.get("prep_rows", []),
            "counts": data.get("counts", {}),
            "configured_extensions": data.get("configured_extensions", []),
            "scan_interval_min": data.get("scan_interval_min", 3),
            "next_cq_slot": data.get("next_cq_slot"),
            "machine_schedule": data.get("machine_schedule", []),
        }
    )

@main_bp.route("/api/scan", methods=["POST"])
def trigger_scan():
    try:
        found = perform_network_scan()
        return jsonify({"status": "success", "found_new": found})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@main_bp.route("/api/dicom")
def get_dicom_info():
    """Dump DICOM complet (style éditeur) — lecture seule."""
    ipp = request.args.get("ipp")
    if not ipp:
        return jsonify({"error": "IPP manquant"}), 400

    # Cherche le .dcm du patient (IPP peut être stocké nettoyé ou brut)
    scan = (
        ScanCache.query.filter_by(patient_id=ipp, file_type=".dcm").first()
        or ScanCache.query.filter(
            ScanCache.file_type == ".dcm",
            ScanCache.patient_id.like(f"%{ipp.lstrip('0')}%"),
        ).first()
    )
    if not scan or not scan.file_path or not os.path.exists(scan.file_path):
        return jsonify({"error": "Fichier DICOM introuvable sur le réseau"}), 404

    try:
        import pydicom
        from pydicom.datadict import keyword_for_tag, dictionary_description
        from pydicom.sequence import Sequence
        from pydicom.dataelem import DataElement

        ds = pydicom.dcmread(scan.file_path, stop_before_pixels=True, force=True)

        def _tag_str(tag) -> str:
            try:
                return f"({int(tag.group):04X},{int(tag.element):04X})"
            except Exception:
                return str(tag)

        def _keyword(tag) -> str:
            try:
                kw = keyword_for_tag(tag)
                if kw:
                    return kw
            except Exception:
                pass
            try:
                desc = dictionary_description(tag)
                if desc:
                    return desc
            except Exception:
                pass
            return "Private" if getattr(tag, "is_private", False) else "Unknown"

        def _fmt_value(val, max_len: int = 500):
            """Formate une valeur atomique pour l'affichage."""
            if val is None:
                return ""
            # Évite d'exploser l'UI avec des bytes / OB / OW
            if isinstance(val, (bytes, bytearray)):
                return f"<binary {len(val)} bytes>"
            if isinstance(val, pydicom.valuerep.PersonName):
                return str(val).replace("^", " ")
            if isinstance(val, (list, pydicom.multival.MultiValue)):
                parts = []
                for v in val:
                    if isinstance(v, (bytes, bytearray)):
                        parts.append(f"<binary {len(v)} bytes>")
                    else:
                        parts.append(str(v))
                s = " \\ ".join(parts)
            else:
                s = str(val)
            s = s.replace("\x00", "").strip()
            if len(s) > max_len:
                return s[:max_len] + f" … (+{len(s) - max_len} car.)"
            return s

        def _walk_dataset(dataset, path_prefix: str = "") -> list:
            """Parcourt récursivement tout le dataset (y compris Sequence)."""
            rows = []
            try:
                elements = list(dataset)
            except Exception:
                return rows

            for elem in elements:
                if not isinstance(elem, DataElement):
                    continue

                tag = elem.tag
                # Pixel Data / bulk : déjà exclus via stop_before_pixels, mais au cas où
                try:
                    if int(tag.group) == 0x7FE0:
                        continue
                except Exception:
                    pass

                tag_s = _tag_str(tag)
                kw = _keyword(tag)
                try:
                    vr = str(elem.VR) if elem.VR else ""
                except Exception:
                    vr = ""

                name_path = f"{path_prefix}{kw}" if path_prefix else kw

                # Séquence → un en-tête + items enfants
                if vr == "SQ" or isinstance(elem.value, Sequence):
                    seq = elem.value if isinstance(elem.value, Sequence) else []
                    n = len(seq) if seq is not None else 0
                    rows.append({
                        "tag": tag_s,
                        "keyword": kw,
                        "path": name_path,
                        "vr": "SQ",
                        "value": f"<Sequence of {n} item(s)>",
                        "is_seq": True,
                        "depth": path_prefix.count(" › "),
                    })
                    if seq:
                        for i, item in enumerate(seq):
                            item_path = f"{name_path}[{i}] › "
                            rows.append({
                                "tag": tag_s,
                                "keyword": f"Item {i}",
                                "path": f"{name_path}[{i}]",
                                "vr": "ITEM",
                                "value": f"— item #{i} —",
                                "is_seq": True,
                                "depth": path_prefix.count(" › ") + 1,
                            })
                            rows.extend(_walk_dataset(item, item_path))
                else:
                    rows.append({
                        "tag": tag_s,
                        "keyword": kw,
                        "path": name_path,
                        "vr": vr,
                        "value": _fmt_value(elem.value),
                        "is_seq": False,
                        "depth": path_prefix.count(" › "),
                    })
            return rows

        elements = _walk_dataset(ds)

        # Infos fichier + file_meta
        meta_rows = []
        if hasattr(ds, "file_meta") and ds.file_meta:
            meta_rows = _walk_dataset(ds.file_meta, "FileMeta › ")

        return jsonify({
            "status": "success",
            "file_name": os.path.basename(scan.file_path),
            "file_path": scan.file_path,
            "modality": str(getattr(ds, "Modality", "") or ""),
            "sop_class": str(getattr(ds, "SOPClassUID", "") or ""),
            "n_elements": len(elements) + len(meta_rows),
            "file_meta": meta_rows,
            "elements": elements,
        })
    except ImportError:
        return jsonify({"error": "Bibliothèque pydicom non installée (pip install pydicom)"}), 500
    except Exception as e:
        return jsonify({"error": f"Erreur de lecture DICOM : {str(e)}"}), 500