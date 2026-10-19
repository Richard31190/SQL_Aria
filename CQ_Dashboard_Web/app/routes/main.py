from flask import Blueprint, render_template, jsonify, request
from app.models_local import CQTab, CQMachine, Setting
from app.services.business import build_dashboard_data, perform_network_scan

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def index():
    tabs = CQTab.query.order_by(CQTab.display_order).all()
    default_tab = tabs[0].name if tabs else ''
    return render_template('dashboard.html', tabs=[t.to_dict() for t in tabs], default_tab=default_tab)

@main_bp.route('/api/data')
def get_data():
    tab_name = request.args.get('tab')
    data = build_dashboard_data(tab_name)
    return jsonify({
        'status': 'success',
        'main_rows': data['main_rows'],
        'prep_rows': data['prep_rows'],
        'timestamp': request.args.get('_t')
    })

@main_bp.route('/api/scan', methods=['POST'])
def trigger_scan():
    try:
        found = perform_network_scan()
        return jsonify({'status': 'success', 'found_new': found})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500