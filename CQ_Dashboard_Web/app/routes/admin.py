import os
import io
import zipfile
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, send_file
from app.models_local import db_local, Setting, CQTab, CQMachine, CQSource

admin_bp = Blueprint('admin', __name__)

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
    sources = CQSource.query.all()
    return render_template('admin/index.html',
                           settings=settings,
                           tabs=[t.to_dict() for t in tabs],
                           machines=[m.to_dict() for m in machines],
                           sources=[s.to_dict() for s in sources])

@admin_bp.route('/settings', methods=['POST'])
def update_settings():
    for key in request.form:
        if key != 'csrf_token':
            Setting.set_val(key, request.form[key])
    flash('Parametres mis a jour avec succes', 'success')
    return redirect(url_for('admin.index'))

@admin_bp.route('/tabs/save', methods=['POST'])
def save_tab():
    tab_id = request.form.get('id')
    name = request.form.get('name')
    label = request.form.get('label')
    order = request.form.get('display_order', 0)

    # Lire le champ CSV du template
    machines_csv = request.form.get('machines_csv', '')
    machines = [m.strip() for m in machines_csv.split(',') if m.strip()]

    if tab_id:
        tab = CQTab.query.get(tab_id)
    else:
        tab = CQTab()

    tab.name = name
    tab.label = label
    tab.display_order = int(order)
    tab.set_machines(machines)

    db_local.session.add(tab)
    db_local.session.commit()
    flash('Onglet sauvegarde avec succes', 'success')
    return redirect(url_for('admin.index'))

@admin_bp.route('/tabs/delete/<int:tab_id>', methods=['POST'])
def delete_tab(tab_id):
    tab = CQTab.query.get_or_404(tab_id)
    db_local.session.delete(tab)
    db_local.session.commit()
    flash('Onglet supprime', 'success')
    return redirect(url_for('admin.index'))

@admin_bp.route('/machines/save', methods=['POST'])
def save_machine():
    m_id = request.form.get('id')
    name = request.form.get('name')
    label = request.form.get('label')
    order = request.form.get('display_order', 0)
    
    base_offset = int(request.form.get('base_offset', 7))
    per_target = int(request.form.get('per_target_offset', 3))
    fixed_dur = int(request.form.get('fixed_duration', 10))

    if m_id:
        m = CQMachine.query.get(m_id)
    else:
        m = CQMachine()

    m.name = name
    m.label = label
    m.display_order = int(order)
    m.set_duration_rules({
        'base_offset': base_offset,
        'per_target_offset': per_target,
        'fixed_duration': fixed_dur
    })

    db_local.session.add(m)
    db_local.session.commit()
    flash('Machine sauvegardee', 'success')
    return redirect(url_for('admin.index'))

@admin_bp.route('/machines/delete/<int:m_id>', methods=['POST'])
def delete_machine(m_id):
    m = CQMachine.query.get_or_404(m_id)
    db_local.session.delete(m)
    db_local.session.commit()
    flash('Machine meutisee / supprimee', 'success')
    return redirect(url_for('admin.index'))

@admin_bp.route('/sources/save', methods=['POST'])
def save_source():
    s_id = request.form.get('id')
    name = request.form.get('name')
    stype = request.form.get('source_type')
    path = request.form.get('path')
    active = 'active' in request.form

    if s_id:
        src = CQSource.query.get(s_id)
    else:
        src = CQSource()

    src.name = name
    src.source_type = stype
    src.path = path
    src.active = active

    db_local.session.add(src)
    db_local.session.commit()
    flash('Source reseau sauvegardee', 'success')
    return redirect(url_for('admin.index'))

@admin_bp.route('/sources/delete/<int:s_id>', methods=['POST'])
def delete_source(s_id):
    src = CQSource.query.get_or_404(s_id)
    db_local.session.delete(src)
    db_local.session.commit()
    flash('Source supprimee', 'success')
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