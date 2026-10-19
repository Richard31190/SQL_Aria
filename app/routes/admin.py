import os
import zipfile
import io
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, session, current_app, flash, send_file
)
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy import create_engine, text
from ..models_local import Setting, Machine, get_all_settings, get_all_machines

admin_bp = Blueprint('admin', __name__)


def _require_admin():
    return bool(session.get('is_admin'))


@admin_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password')
        db_session = current_app.config['LOCAL_SESSION']()
        admin_pwd_setting = db_session.query(Setting).filter_by(key='admin_password').first()
        db_session.close()

        if admin_pwd_setting and check_password_hash(admin_pwd_setting.value, password):
            session['is_admin'] = True
            flash('Connexion reussie.', 'success')
            return redirect(url_for('admin.settings'))
        flash('Mot de passe incorrect.', 'danger')

    return render_template(
        'admin_login.html',
        title='Connexion Administration',
        active_page='admin'
    )


@admin_bp.route('/logout')
def logout():
    session.pop('is_admin', None)
    flash('Vous etes deconnecte.', 'info')
    return redirect(url_for('main.index'))


@admin_bp.route('/settings', methods=['GET', 'POST'])
def settings():
    if not _require_admin():
        return redirect(url_for('admin.login'))

    db_session = current_app.config['LOCAL_SESSION']()

    if request.method == 'POST':
        action = request.form.get('action')

        # --- Config generale ---
        if action == 'save_settings':
            form_fields = [
                'aria_db_host', 'aria_db_user', 'aria_db_password', 'aria_db_name',
                'outlook_email', 'outlook_server', 'outlook_domain',
                'radmachine_token'
            ]
            for field in form_fields:
                val = request.form.get(field, '').strip()
                item = db_session.query(Setting).filter_by(key=field).first()
                if item:
                    item.value = val
                else:
                    db_session.add(Setting(key=field, value=val))
            db_session.commit()
            flash('Configuration sauvegardee.', 'success')

        elif action == 'change_password':
            old_pwd = request.form.get('old_password')
            new_pwd = request.form.get('new_password')
            confirm_pwd = request.form.get('confirm_password')
            pwd_setting = db_session.query(Setting).filter_by(key='admin_password').first()

            if not check_password_hash(pwd_setting.value, old_pwd):
                flash('Ancien mot de passe incorrect.', 'danger')
            elif new_pwd != confirm_pwd:
                flash('Les nouveaux mots de passe ne correspondent pas.', 'warning')
            elif len(new_pwd) < 4:
                flash('Le mot de passe doit contenir au moins 4 caracteres.', 'warning')
            else:
                pwd_setting.value = generate_password_hash(new_pwd)
                db_session.commit()
                flash('Mot de passe administrateur modifie.', 'success')

        elif action == 'test_aria':
            host = request.form.get('aria_db_host')
            user = request.form.get('aria_db_user')
            password = request.form.get('aria_db_password')
            dbname = request.form.get('aria_db_name')
            try:
                uri = f'mysql+pymysql://{user}:{password}@{host}/{dbname}'
                test_engine = create_engine(uri, connect_args={'connect_timeout': 5})
                with test_engine.connect() as conn:
                    conn.execute(text('SELECT 1'))
                flash('Connexion ARIA reussie.', 'success')
            except Exception as e:
                flash(f'Echec connexion ARIA : {e}', 'danger')

        # --- Machines CRUD ---
        elif action == 'add_machine':
            name = request.form.get('name', '').strip().upper()
            label = request.form.get('label', '').strip()
            dicom_path = request.form.get('dicom_path', '').strip()
            device_ids = request.form.get('device_ids', '').strip()
            order_index = request.form.get('order_index', '0').strip() or '0'

            if not name or not label:
                flash('Nom technique et libelle obligatoires.', 'warning')
            elif db_session.query(Machine).filter_by(name=name).first():
                flash(f'La machine {name} existe deja.', 'warning')
            else:
                try:
                    order_val = int(order_index)
                except ValueError:
                    order_val = 0
                db_session.add(Machine(
                    name=name,
                    label=label,
                    dicom_path=dicom_path,
                    device_ids=device_ids,
                    order_index=order_val,
                    is_active=1
                ))
                db_session.commit()
                flash(f'Machine {label} ajoutee.', 'success')

        elif action == 'update_machine':
            machine_id = request.form.get('machine_id')
            machine = db_session.query(Machine).filter_by(id=machine_id).first()
            if not machine:
                flash('Machine introuvable.', 'danger')
            else:
                machine.label = request.form.get('label', machine.label).strip()
                machine.dicom_path = request.form.get('dicom_path', '').strip()
                machine.device_ids = request.form.get('device_ids', '').strip()
                try:
                    machine.order_index = int(request.form.get('order_index', machine.order_index) or 0)
                except ValueError:
                    pass
                machine.is_active = 1 if request.form.get('is_active') == '1' else 0
                db_session.commit()
                flash(f'Machine {machine.label} mise a jour.', 'success')

        elif action == 'delete_machine':
            machine_id = request.form.get('machine_id')
            machine = db_session.query(Machine).filter_by(id=machine_id).first()
            if machine:
                label = machine.label
                db_session.delete(machine)
                db_session.commit()
                flash(f'Machine {label} supprimee.', 'success')
            else:
                flash('Machine introuvable.', 'danger')

    cfg = get_all_settings(db_session)
    machines = get_all_machines(db_session, active_only=False)
    db_session.close()

    return render_template(
        'admin.html',
        title='Administration',
        active_page='admin',
        cfg=cfg,
        machines=machines
    )


@admin_bp.route('/export-config')
def export_config():
    if not _require_admin():
        return redirect(url_for('admin.login'))

    db_path = os.path.join(current_app.instance_path, 'local.db')
    if not os.path.exists(db_path):
        flash('Fichier de configuration introuvable.', 'danger')
        return redirect(url_for('admin.settings'))

    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.write(db_path, arcname='cq_dashboard_backup.db')
    memory_file.seek(0)

    return send_file(
        memory_file,
        mimetype='application/zip',
        as_attachment=True,
        download_name='CQ_Dashboard_Backup.zip'
    )


@admin_bp.route('/import-config', methods=['POST'])
def import_config():
    if not _require_admin():
        return redirect(url_for('admin.login'))

    file = request.files.get('backup_zip')
    if not file or not file.filename.endswith('.zip'):
        flash('Veuillez fournir un fichier .zip valide.', 'danger')
        return redirect(url_for('admin.settings'))

    try:
        with zipfile.ZipFile(file) as zf:
            if 'cq_dashboard_backup.db' in zf.namelist():
                db_path = os.path.join(current_app.instance_path, 'local.db')
                current_app.config['LOCAL_SESSION'].close_all()
                with open(db_path, 'wb') as f:
                    f.write(zf.read('cq_dashboard_backup.db'))
                flash('Restauration reussie.', 'success')
            else:
                flash('ZIP invalide (cq_dashboard_backup.db manquant).', 'danger')
    except Exception as e:
        flash(f'Echec restauration : {e}', 'danger')

    return redirect(url_for('admin.settings'))