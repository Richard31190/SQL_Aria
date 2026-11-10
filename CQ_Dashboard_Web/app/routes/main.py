# -*- coding: utf-8 -*-
# *** routes/main.py ***
import os
from flask import Blueprint, render_template, jsonify, request
from app.models_local import CQTab, CQMachine, Setting, ScanCache
from app.services.business import (
    build_dashboard_data,
    perform_network_scan,
    get_configured_extensions,
    get_db_last_update_time,  # <--- Ajouté ici
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
        next_cq_slot=initial_data.get("next_cq_slots"),
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
            "db_last_update": get_db_last_update_time(),
            "main_rows": data.get("main_rows", []),
            "prep_rows": data.get("prep_rows", []),
            "counts": data.get("counts", {}),
            "configured_extensions": data.get("configured_extensions", []),
            "scan_interval_min": data.get("scan_interval_min", 3),
            "next_cq_slots": data.get("next_cq_slots"),
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
    """Dump DICOM complet avec priorité au fichier RTPLAN et support multi-fichiers."""
    ipp = request.args.get("ipp")
    target_path = request.args.get("path")

    if not ipp and not target_path:
        return jsonify({"error": "IPP ou chemin de fichier manquant"}), 400

    import pydicom
    from pydicom.datadict import keyword_for_tag, dictionary_description
    from pydicom.sequence import Sequence
    from pydicom.dataelem import DataElement

    # 1. Lister tous les fichiers DICOM du patient
    available_scans = []
    if ipp:
        scans = ScanCache.query.filter(
            ScanCache.file_type == ".dcm",
            (ScanCache.patient_id == ipp) | (ScanCache.patient_id.like(f"%{ipp.lstrip('0')}%"))
        ).all()
        
        for s in scans:
            if s.file_path and os.path.exists(s.file_path):
                available_scans.append({
                    "path": s.file_path,
                    "name": os.path.basename(s.file_path)
                })

    if not available_scans and not target_path:
        return jsonify({"error": "Aucun fichier DICOM introuvable sur le réseau pour ce patient"}), 404

    # 2. Sélectionner le fichier : Priorité absolue au RTPLAN (fichiers commencant par RP ou de modalité RTPLAN)
    selected_path = target_path

    if not selected_path and available_scans:
        # Chercher un fichier RP... (RT Plan)
        rp_file = next((s["path"] for s in available_scans if os.path.basename(s["path"]).upper().startswith("RP")), None)
        if rp_file:
            selected_path = rp_file
        else:
            # Sinon tester la modalité dans l'en-tête
            for s in available_scans:
                try:
                    hdr = pydicom.dcmread(s["path"], stop_before_pixels=True, specific_tags=['Modality'])
                    if str(getattr(hdr, 'Modality', '')).upper() == 'RTPLAN':
                        selected_path = s["path"]
                        break
                except Exception:
                    pass
            # Sinon prendre le premier disponible
            if not selected_path:
                selected_path = available_scans[0]["path"]

    if not selected_path or not os.path.exists(selected_path):
        return jsonify({"error": "Fichier DICOM sélectionné introuvable"}), 404

    try:
        ds = pydicom.dcmread(selected_path, stop_before_pixels=True, force=True)

        def _tag_str(tag) -> str:
            try:
                return f"({int(tag.group):04X},{int(tag.element):04X})"
            except Exception:
                return str(tag)

        def _keyword(tag) -> str:
            try:
                kw = keyword_for_tag(tag)
                if kw: return kw
            except Exception: pass
            try:
                desc = dictionary_description(tag)
                if desc: return desc
            except Exception: pass
            return "Private" if getattr(tag, "is_private", False) else "Unknown"

        def _fmt_value(val, max_len: int = 500):
            if val is None: return ""
            if isinstance(val, (bytes, bytearray)):
                return f"<binary {len(val)} bytes>"
            if isinstance(val, pydicom.valuerep.PersonName):
                return str(val).replace("^", " ")
            if isinstance(val, (list, pydicom.multival.MultiValue)):
                parts = [f"<binary {len(v)} bytes>" if isinstance(v, (bytes, bytearray)) else str(v) for v in val]
                s = " \\ ".join(parts)
            else:
                s = str(val)
            s = s.replace("\x00", "").strip()
            return s[:max_len] + f" … (+{len(s) - max_len} car.)" if len(s) > max_len else s

        def _walk_dataset(dataset, path_prefix: str = "") -> list:
            rows = []
            try:
                elements = list(dataset)
            except Exception:
                return rows

            for elem in elements:
                if not isinstance(elem, DataElement):
                    continue

                tag = elem.tag
                try:
                    if int(tag.group) == 0x7FE0: continue
                except Exception: pass

                tag_s = _tag_str(tag)
                kw = _keyword(tag)
                vr = str(elem.VR) if elem.VR else ""
                name_path = f"{path_prefix}{kw}" if path_prefix else kw

                if vr == "SQ" or isinstance(elem.value, Sequence):
                    seq = elem.value if isinstance(elem.value, Sequence) else []
                    n = len(seq) if seq is not None else 0
                    rows.append({
                        "tag": tag_s, "keyword": kw, "path": name_path, "vr": "SQ",
                        "value": f"<Sequence of {n} item(s)>", "is_seq": True,
                        "depth": path_prefix.count(" › ")
                    })
                    if seq:
                        for i, item in enumerate(seq):
                            item_path = f"{name_path}[{i}] › "
                            rows.append({
                                "tag": tag_s, "keyword": f"Item {i}", "path": f"{name_path}[{i}]",
                                "vr": "ITEM", "value": f"— item #{i} —", "is_seq": True,
                                "depth": path_prefix.count(" › ") + 1
                            })
                            rows.extend(_walk_dataset(item, item_path))
                else:
                    rows.append({
                        "tag": tag_s, "keyword": kw, "path": name_path, "vr": vr,
                        "value": _fmt_value(elem.value), "is_seq": False,
                        "depth": path_prefix.count(" › ")
                    })
            return rows

        elements = _walk_dataset(ds)
        meta_rows = _walk_dataset(ds.file_meta, "FileMeta › ") if hasattr(ds, "file_meta") and ds.file_meta else []

        # Recherche directe de l'énergie pour rassurer l'utilisateur
        detected_energy = None
        for r in elements:
            if r.get("keyword") == "NominalBeamEnergy" or "(300A,0114)" in r.get("tag", ""):
                detected_energy = r.get("value")
                break

        return jsonify({
            "status": "success",
            "file_name": os.path.basename(selected_path),
            "file_path": selected_path,
            "modality": str(getattr(ds, "Modality", "") or ""),
            "sop_class": str(getattr(ds, "SOPClassUID", "") or ""),
            "n_elements": len(elements) + len(meta_rows),
            "detected_energy": detected_energy,
            "available_files": available_scans,
            "file_meta": meta_rows,
            "elements": elements,
        })
    except Exception as e:
        return jsonify({"error": f"Erreur de lecture DICOM : {str(e)}"}), 500