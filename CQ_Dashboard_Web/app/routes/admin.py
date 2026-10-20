# *** routes\admin.py ***
import os
import io
import zipfile
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, send_file, jsonify
from sqlalchemy import text
from app.models_local import db_local, Setting, CQTab, CQMachine

admin_bp = Blueprint('admin', __name__)

# Clés affectées à chaque section de l'admin
DB_KEYS = ['MYSQL_HOST', 'MYSQL_PORT', 'MYSQL_USER', 'MYSQL_PASSWORD', 'MYSQL_DB']
GLOBAL_KEYS = ['ADMIN_PASSWORD']


@admin_bp.before_request
def check_admin_auth():
    if request.endpoint and 'admin.login' not in request.endpoint:
        if not session.get('admin_logged_in'):
            return redirect(url_for('admin.login'))


@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        pwd = request.form.get('password')
        admin_pwd = Setting.get_val('ADMIN_PASSWORD', 'admin')
        if pwd == admin_pwd:
            session['admin_logged_in'] = True
            return redirect(url_for('admin.index'))
        else:
            flash('Mot de passe incorrect', 'danger')
    return render_template('admin/login.html')


@admin_bp.route('/logout')
def logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('admin.login'))


@admin_bp.route('/')
def index():
    settings = Setting.query.all()
    tabs = CQTab.query.order_by(CQTab.display_order).all()
    machines = CQMachine.query.order_by(CQMachine.display_order).all()
    return render_template('admin/index.html',
                           settings=settings,
                           tabs=[t.to_dict() for t in tabs],
                           machines=[m.to_dict() for m in machines],
                           db_keys=DB_KEYS,
                           global_keys=GLOBAL_KEYS)


@admin_bp.route('/settings', methods=['POST'])
def update_settings():
    for key in request.form:
        if key != 'csrf_token':
            Setting.set_val(key, request.form[key])
    flash('Paramètres mis à jour avec succès', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/test-db', methods=['POST'])
def test_db():
    """Teste la connexion MySQL avec les paramètres actuellement enregistrés."""
    try:
        from app.services.business import get_mysql_engine
        engine = get_mysql_engine()
        with engine.connect() as conn:
            # Test simple
            conn.execute(text("SELECT 1"))
            # On tente de compter les patients pour valider que la base est bien celle d'ARIA
            try:
                res = conn.execute(text("SELECT COUNT(*) FROM patients")).scalar()
                return jsonify({
                    'status': 'success',
                    'message': f'Connexion réussie — {res} patients trouvés dans la base.'
                })
            except Exception:
                return jsonify({
                    'status': 'success',
                    'message': 'Connexion réussie (table "patients" non trouvée, vérifier le nom de la base).'
                })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 200


@admin_bp.route('/tabs/save', methods=['POST'])
def save_tab():
    tab_id = request.form.get('id')
    name = request.form.get('name')
    label = request.form.get('label')
    order = request.form.get('display_order', 0)

    network_paths_csv = request.form.get('network_paths_csv', '')
    file_extensions_csv = request.form.get('file_extensions_csv', '.dcm, .pdf')

    network_paths = [p.strip() for p in network_paths_csv.split(',') if p.strip()]
    file_extensions = [e.strip().lower() if e.strip().startswith('.') else '.' + e.strip().lower()
                       for e in file_extensions_csv.split(',') if e.strip()]

    if tab_id:
        tab = CQTab.query.get(tab_id)
    else:
        tab = CQTab()

    tab.name = name
    tab.label = label
    tab.display_order = int(order)
    tab.set_config(network_paths=network_paths, file_extensions=file_extensions)

    db_local.session.add(tab)
    db_local.session.commit()
    flash('Onglet sauvegardé avec succès', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/tabs/delete/<int:tab_id>', methods=['POST'])
def delete_tab(tab_id):
    tab = CQTab.query.get_or_404(tab_id)
    db_local.session.delete(tab)
    db_local.session.commit()
    flash('Onglet supprimé', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/machines/save', methods=['POST'])
def save_machine():
    m_id = request.form.get('id')
    name = request.form.get('name')
    label = request.form.get('label')  # devient "Montage/Démontage (min)"
    order = request.form.get('display_order', 0)
    base_offset = int(request.form.get('base_offset', 7))  # devient "Acquisition (min)"

    if m_id:
        m = CQMachine.query.get(m_id)
    else:
        m = CQMachine()

    m.name = name
    m.label = str(label)
    m.display_order = int(order)
    m.set_duration_rules({
        'base_offset': base_offset,  # Acquisition
        'mount_unmount': int(label) if str(label).isdigit() else 0  # Montage/Démontage
    })

    db_local.session.add(m)
    db_local.session.commit()
    flash('Type de CQ sauvegardé', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/machines/delete/<int:m_id>', methods=['POST'])
def delete_machine(m_id):
    m = CQMachine.query.get_or_404(m_id)
    db_local.session.delete(m)
    db_local.session.commit()
    flash('Type de CQ supprimé', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/backup')
def backup_config():
    db_path = os.path.join(admin_bp.root_path, '..', '..', 'instance', 'local.db')
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        if os.path.exists(db_path):
            zf.write(db_path, os.path.basename(db_path))
    memory_file.seek(0)
    return send_file(memory_file, download_name='cq_dashboard_backup.zip', as_attachment=True)