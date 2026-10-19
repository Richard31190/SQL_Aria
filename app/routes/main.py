from flask import Blueprint, render_template

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def index():
    return render_template('dashboard.html', title="Machines")

@main_bp.route('/calendrier')
def calendrier():
    return render_template('base.html', title="Calendrier", content="Contenu du calendrier a venir...")