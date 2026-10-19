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
    machines_json = db_local.Column(db_local.Text, default='[]')

    def get_machines(self):
        try:
            return json.loads(self.machines_json or '[]')
        except Exception:
            return []

    def set_machines(self, machine_list):
        self.machines_json = json.dumps(machine_list)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'label': self.label,
            'display_order': self.display_order,
            'machines': self.get_machines()
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

class CQSource(db_local.Model):
    __tablename__ = 'cq_sources'

    id = db_local.Column(db_local.Integer, primary_key=True)
    name = db_local.Column(db_local.String(100), nullable=False)
    source_type = db_local.Column(db_local.String(20), nullable=False) # 'DICOM' or 'PDF'
    path = db_local.Column(db_local.String(500), nullable=False)
    active = db_local.Column(db_local.Boolean, default=True)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'source_type': self.source_type,
            'path': self.path,
            'active': self.active
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
    # 1. Default Settings
    default_settings = {
        'MYSQL_HOST': ('localhost', 'MySQL Host address'),
        'MYSQL_PORT': ('3306', 'MySQL Port'),
        'MYSQL_USER': ('root', 'MySQL Username'),
        'MYSQL_PASSWORD': ('', 'MySQL Password'),
        'MYSQL_DB': ('db_iuct', 'MySQL Database Name'),
        'ADMIN_PASSWORD': ('admin', 'Admin Login Password'),
        'APP_TITLE': ('CQ Dashboard - IUCT Oncopole', 'Application Title'),
        'SCANDIDOS_SIGNATURE': ('Scandidos', 'String signature for Scandidos files'),
        'DEFAULT_TOMO_ENERGY': ('6 MV', 'Default Tomo energy display'),
        'IMPLANT_PATIENT_ID': ('IMPLANT', 'Special patient ID for end of day schedule')
    }
    for key, (val, desc) in default_settings.items():
        if not Setting.query.filter_by(key=key).first():
            db_local.session.add(Setting(key=key, value=val, description=desc))

    # 2. Default Tabs
    if CQTab.query.count() == 0:
        tabs = [
            CQTab(name='tomo2', label='Tomo 2', display_order=1, machines_json=json.dumps(['Tomo 2'])),
            CQTab(name='tomo4', label='Tomo 4', display_order=2, machines_json=json.dumps(['Tomo 4'])),
            CQTab(name='tomo7', label='Tomo 7', display_order=3, machines_json=json.dumps(['Tomo 7'])),
            CQTab(name='nova', label='Nova', display_order=4, machines_json=json.dumps(['Nova', 'Ruby'])),
        ]
        db_local.session.add_all(tabs)

    # 3. Default Machines
    if CQMachine.query.count() == 0:
        machines = [
            CQMachine(name='Tomo 2', label='Tomotherapy 2', display_order=1, duration_rules_json=json.dumps({
                'base_offset': 7, 'per_target_offset': 3, 'use_beam_duration': True
            })),
            CQMachine(name='Tomo 4', label='Tomotherapy 4', display_order=2, duration_rules_json=json.dumps({
                'base_offset': 7, 'per_target_offset': 3, 'use_beam_duration': True
            })),
            CQMachine(name='Tomo 7', label='Tomotherapy 7', display_order=3, duration_rules_json=json.dumps({
                'base_offset': 7, 'per_target_offset': 3, 'use_beam_duration': True
            })),
            CQMachine(name='Nova', label='Halcyon Nova', display_order=4, duration_rules_json=json.dumps({
                'base_offset': 8, 'per_target_offset': 0, 'fixed_duration': 8
            })),
            CQMachine(name='Ruby', label='Ethos Ruby', display_order=5, duration_rules_json=json.dumps({
                'base_offset': 12, 'per_target_offset': 0, 'fixed_duration': 12
            })),
        ]
        db_local.session.add_all(machines)

    # 4. Default Network Sources
    if CQSource.query.count() == 0:
        sources = [
            CQSource(name='NAS DICOM Export', source_type='DICOM', path=r'\\nas_iuct\dicom_export', active=True),
            CQSource(name='NAS PDF Reports', source_type='PDF', path=r'\\nas_iuct\pdf_reports', active=True)
        ]
        db_local.session.add_all(sources)

    db_local.session.commit()