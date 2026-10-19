from flask import Blueprint, render_template, current_app, jsonify
from ..models_local import get_all_machines

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def index():
    # 1. On recupere les machines actives depuis la base locale
    db_session = current_app.config['LOCAL_SESSION']()
    machines = get_all_machines(db_session, active_only=True)
    db_session.close()
    
    return render_template('dashboard.html', title="Machines", active_page="machines", machines=machines)

@main_bp.route('/api/cq_data/<int:machine_id>')
def api_cq_data(machine_id):
    """
    ROUTE API : Sera appelee en arriere-plan par le navigateur pour chaque machine.
    A l'etape suivante, c'est ici qu'on mettra la requete SQL ARIA et le scan NAS.
    """
    import time
    time.sleep(1) # Simulation du temps de recherche (ARIA + NAS)
    
    # Fausses donnees pour visualiser le rendu
    fake_data = [
        {
            "patient_name": "DUPONT Jean",
            "patient_id": "123456",
            "plan_name": "Prostate VMAT",
            "status_aria": "A faire",
            "status_nas": "Trouve", # DICOM present
            "urgency": "urgent"
        },
        {
            "patient_name": "MARTIN Sophie",
            "patient_id": "654321",
            "plan_name": "Sein gauche",
            "status_aria": "A faire",
            "status_nas": "Manquant", # DICOM absent
            "urgency": "normal"
        }
    ]
    
    return jsonify({"status": "success", "data": fake_data})