# *** app\models_local.py ***
import json
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db_local = SQLAlchemy()

class Setting(db_local.Model):
    __tablename__ = 'settings'

    key = db_local.Column(db_local.String(100), primary_key=True)
    value = db_local.Column(db_local.Text, nullable=True)
    description = db_local.Column(db_local.String(255), nullable=True)

    @classmethod
    def get_val(cls, key, default=''):
        item = cls.query.filter_by(key=key).first()
        return item.value if item and item.value is not None else default

    @classmethod
    def set_val(cls, key, value, description=None):
        item = cls.query.filter_by(key=key).first()
        if not item:
            item = cls(key=key, value=str(value), description=description)
            db_local.session.add(item)
        else:
            item.value = str(value)
            if description:
                item.description = description
        db_local.session.commit()

class CQTab(db_local.Model):
    __tablename__ = 'cq_tabs'

    id = db_local.Column(db_local.Integer, primary_key=True)
    name = db_local.Column(db_local.String(50), nullable=False, unique=True)
    label = db_local.Column(db_local.String(100), nullable=False)
    display_order = db_local.Column(db_local.Integer, default=0)
    machines_json = db_local.Column(db_local.Text, default='{}')

    def _get_data(self):
        try:
            raw = json.loads(self.machines_json or '{}')
            if isinstance(raw, list):
                return {"machines": raw, "network_paths": []}
            return {
                "machines": raw.get("machines", []),
                "network_paths": raw.get("network_paths", [])
            }
        except Exception:
            return {"machines": [], "network_paths": []}

    def get_machines(self):
        return self._get_data().get("machines", [])

    def get_network_paths(self):
        return self._get_data().get("network_paths", [])

    def set_config(self, machines, network_paths):
        self.machines_json = json.dumps({
            "machines": machines or [],
            "network_paths": network_paths or []
        })

    def to_dict(self):
        d = self._get_data()
        return {
            'id': self.id,
            'name': self.name,
            'label': self.label,
            'display_order': self.display_order,
            'machines': d["machines"],
            'network_paths': d["network_paths"],
            'network_paths_csv': ', '.join(d["network_paths"])
        }

class CQMachine(db_local.Model):
    __tablename__ = 'cq_machines'

    id = db_local.Column(db_local.Integer, primary_key=True)
    name = db_local.Column(db_local.String(50), nullable=False, unique=True)
    label = db_local.Column(db_local.String(100), nullable=False)
    display_order = db_local.Column(db_local.Integer, default=0)
    duration_rules_json = db_local.Column(db_local.Text, default='{}')

    def get_duration_rules(self):
        try:
            return json.loads(self.duration_rules_json or '{}')
        except Exception:
            return {}

    def set_duration_rules(self, rules_dict):
        self.duration_rules_json = json.dumps(rules_dict)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'label': self.label,
            'display_order': self.display_order,
            'duration_rules': self.get_duration_rules()
        }

class ScanCache(db_local.Model):
    __tablename__ = 'scan_cache'

    id = db_local.Column(db_local.Integer, primary_key=True)
    patient_id = db_local.Column(db_local.String(50), index=True)
    file_type = db_local.Column(db_local.String(20)) # 'DICOM', 'PDF'
    file_name = db_local.Column(db_local.String(255))
    file_path = db_local.Column(db_local.Text)
    last_modified = db_local.Column(db_local.DateTime)
    extra_data_json = db_local.Column(db_local.Text, default='{}')

def seed_defaults():
    # 1. Paramètres par défaut
    default_settings = {
        'MYSQL_HOST': ('localhost', 'MySQL Host address'),
        'MYSQL_PORT': ('3306', 'MySQL Port'),
        'MYSQL_USER': ('root', 'MySQL Username'),
        'MYSQL_PASSWORD': ('', 'MySQL Password'),
        'MYSQL_DB': ('db_iuct', 'MySQL Database Name'),
        'ADMIN_PASSWORD': ('admin', 'Admin Login Password'),
        'APP_TITLE': ('CQ Dashboard - IUCT Oncopole', 'Application Title'),
        'SCANDIDOS_SIGNATURE': ('114358', 'Signature des fichiers Scandidos'),
        'DEFAULT_TOMO_ENERGY': ('6FFF', 'Default Tomo energy display'),
    }
    for key, (val, desc) in default_settings.items():
        if not Setting.query.filter_by(key=key).first():
            db_local.session.add(Setting(key=key, value=val, description=desc))

    # 2. Onglets avec dossiers réseau par défaut
    if CQTab.query.count() == 0:
        default_tabs = [
            CQTab(name='tomo2', label='Tomo 2', display_order=1),
            CQTab(name='tomo4', label='Tomo 4', display_order=2),
            CQTab(name='tomo7', label='Tomo 7', display_order=3),
            CQTab(name='nova', label='Nova', display_order=4),
        ]
        default_tabs[0].set_config(['Tomo 2'], [r'\\nasdata1\TOMO\02 - CQ Patients\0_DELTA4\TOMO2'])
        default_tabs[1].set_config(['Tomo 4'], [r'\\nasdata1\TOMO\02 - CQ Patients\0_DELTA4\TOMO4'])
        default_tabs[2].set_config(['Tomo 7'], [r'\\nasdata1\TOMO\02 - CQ Patients\0_DELTA4\RADI7'])
        default_tabs[3].set_config(['Nova', 'Ruby'], [
            r'\\srv015\Radiophysique_acquisition\Patients_stereo_EC_IUC',
            r'\\srv015\Radiophysique_acquisition\Patients_stereo_Hyperarc',
            r'\\srv015\Radiophysique_acquisition\Patients_RA'
        ])
        db_local.session.add_all(default_tabs)

    # 3. Machines par défaut
    if CQMachine.query.count() == 0:
        machines = [
            CQMachine(name='Tomo 2', label='Tomotherapy 2', display_order=1, duration_rules_json=json.dumps({'fixed_duration': 3})),
            CQMachine(name='Tomo 4', label='Tomotherapy 4', display_order=2, duration_rules_json=json.dumps({'fixed_duration': 3})),
            CQMachine(name='Tomo 7', label='Tomotherapy 7', display_order=3, duration_rules_json=json.dumps({'fixed_duration': 3})),
            CQMachine(name='Nova', label='Nova', display_order=4, duration_rules_json=json.dumps({'fixed_duration': 12})),
        ]
        db_local.session.add_all(machines)

    db_local.session.commit()