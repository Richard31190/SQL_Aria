import os
from flask import Flask
from .models_local import init_local_db

def create_app():
    app = Flask(__name__)
    app.secret_key = 'cq-dashboard-secret-key'
    
    engine, SessionLocal = init_local_db(app.instance_path)
    app.config['LOCAL_SESSION'] = SessionLocal
    
    from .routes.main import main_bp
    from .routes.admin import admin_bp
    
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp, url_prefix='/admin')
    
    return app