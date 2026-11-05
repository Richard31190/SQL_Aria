# *** routes/main.py ***
# -*- coding: utf-8 -*-
# *** routes/main.py ***
from flask import Blueprint, render_template, jsonify, request
from app.models_local import CQTab, CQMachine, Setting
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
            "phantom": data.get("phantom", ""),  # <-- C'ÉTAIT CETTE LIGNE QUI MANQUAIT !
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