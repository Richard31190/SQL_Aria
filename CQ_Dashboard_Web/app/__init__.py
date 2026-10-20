# *** app\__init__.py ***
from flask import Flask
from config import Config
from app.models_local import db_local, seed_defaults


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db_local.init_app(app)

    with app.app_context():
        db_local.create_all()
        seed_defaults()

    from app.routes.main import main_bp
    from app.routes.admin import admin_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp, url_prefix='/admin')

    @app.context_processor
    def inject_global_vars():
        from app.models_local import CQTab, CQMachine
        try:
            tabs = CQTab.query.order_by(CQTab.display_order).all()
            machines = CQMachine.query.order_by(CQMachine.display_order).all()
        except Exception:
            tabs, machines = [], []
        return {
            'global_tabs': tabs,
            'global_machines': machines,
            'app_title': 'CQ Dashboard - IUCT Oncopole'  # Titre fixe
        }

    return app