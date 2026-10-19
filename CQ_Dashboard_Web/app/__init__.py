from flask import Flask
from config import Config
from app.models_local import db_local, seed_defaults

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize SQLite Database
    db_local.init_app(app)

    with app.app_context():
        # Create local SQLite tables if they do not exist
        db_local.create_all()
        # Seed default settings, tabs, machines, and sources into SQLite
        seed_defaults()

    # Register Blueprints
    from app.routes.main import main_bp
    from app.routes.admin import admin_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp, url_prefix='/admin')

    # Context Processors for Templates
    @app.context_processor
    def inject_global_vars():
        from app.models_local import CQTab, CQMachine, Setting
        try:
            tabs = CQTab.query.order_by(CQTab.display_order).all()
            machines = CQMachine.query.order_by(CQMachine.display_order).all()
            title = Setting.get_val('APP_TITLE', 'CQ Dashboard - Radiation Oncology')
        except Exception:
            tabs, machines, title = [], [], 'CQ Dashboard'
        return {
            'global_tabs': tabs,
            'global_machines': machines,
            'app_title': title
        }

    return app