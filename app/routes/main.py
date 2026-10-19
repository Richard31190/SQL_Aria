from flask import Blueprint, render_template, current_app, jsonify
from ..models_local import get_all_machines, get_all_settings, Machine
from ..services.aria_service import get_cq_for_machine

main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    db_session = current_app.config['LOCAL_SESSION']()
    machines = get_all_machines(db_session, active_only=True)
    db_session.close()
    return render_template(
        'dashboard.html',
        title='Machines',
        active_page='machines',
        machines=machines,
    )


@main_bp.route('/api/cq_data/<int:machine_id>')
def api_cq_data(machine_id):
    db_session = current_app.config['LOCAL_SESSION']()
    try:
        machine = db_session.query(Machine).filter_by(id=machine_id, is_active=1).first()
        if not machine:
            return jsonify({'status': 'error', 'message': 'Machine introuvable', 'data': []}), 404

        settings = get_all_settings(db_session)
        result = get_cq_for_machine(settings, machine)
        return jsonify(result)
    finally:
        db_session.close()