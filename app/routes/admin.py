import os
import zipfile
import io
from flask import Blueprint, render_template, request, redirect, url_for, session, current_app, flash, send_file
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy import create_engine, text
from ..models_local import Setting, get_all_settings

admin_bp = Blueprint('admin', __name__)

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
        else:
            flash('Mot de passe incorrect.', 'danger')
            
    return render_template('admin_login.html', title="Connexion Administration")

@admin_bp.route('/logout')
def logout():
    session.pop('is_admin', None)
    flash('Vous etes deconnecte.', 'info')
    return redirect(url_for('main.index'))

@admin_bp.route('/settings', methods=['GET', 'POST'])
def settings():
    if not session.get('is_admin'):
        return redirect(url_for('admin.login'))
        
    db_session = current_app.config['LOCAL_SESSION']()
    
    if request.method == 'POST':
        action = request.form.get('action')
        
        # 1. Sauvegarde des configurations
        if action == 'save_settings':
            form_fields = [
                'aria_db_host', 'aria_db_user', 'aria_db_password', 'aria_db_name',
                'nas_tomo2', 'nas_tomo4', 'nas_tomo7', 
                'nas_nova_ec', 'nas_nova_hyperarc', 'nas_nova_ra',
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
            flash('Configuration sauvegardee avec succes !', 'success')

        # 2. Changement du mot de passe admin
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
                flash('Mot de passe administrateur modifie avec succes !', 'success')
                
        # 3. Test de connexion BDD ARIA
        elif action == 'test_aria':
            host = request.form.get('aria_db_host')
            user = request.form.get('aria_db_user')
            password = request.form.get('aria_db_password')
            dbname = request.form.get('aria_db_name')
            
            try:
                uri = f"mysql+pymysql://{user}:{password}@{host}/{dbname}"
                test_engine = create_engine(uri, connect_args={'connect_timeout': 5})
                with test_engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                flash('Connexion a la base ARIA reussie !', 'success')
            except Exception as e:
                flash(f'Echec de la connexion ARIA : {str(e)}', 'danger')
    
    cfg = get_all_settings(db_session)
    db_session.close()
    
    return render_template('admin.html', title="Administration", cfg=cfg)

@admin_bp.route('/export-config')
def export_config():
    """Exporte la BDD SQLite local.db dans un fichier ZIP pour sauvegarde."""
    if not session.get('is_admin'):
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
    """Restaure la BDD local.db depuis un fichier ZIP uploade."""
    if not session.get('is_admin'):
        return redirect(url_for('admin.login'))
        
    file = request.files.get('backup_zip')
    if not file or not file.filename.endswith('.zip'):
        flash('Veuillez fournir un fichier .zip valide.', 'danger')
        return redirect(url_for('admin.settings'))
        
    try:
        with zipfile.ZipFile(file) as zf:
            if 'cq_dashboard_backup.db' in zf.namelist():
                db_path = os.path.join(current_app.instance_path, 'local.db')
                db_session = current_app.config['LOCAL_SESSION']
                db_session.close_all()
                
                with open(db_path, 'wb') as f:
                    f.write(zf.read('cq_dashboard_backup.db'))
                    
                flash('Restauration reussie ! La configuration a ete reappliquee.', 'success')
            else:
                flash('Le fichier ZIP ne contient pas de sauvegarde valide.', 'danger')
    except Exception as e:
        flash(f'Echec de la restauration : {str(e)}', 'danger')
        
    return redirect(url_for('admin.settings'))