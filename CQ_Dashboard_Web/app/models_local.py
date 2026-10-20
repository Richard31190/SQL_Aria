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
    name = db_local.Column(db_local.String(200), nullable=False)
    label = db_local.Column(db_local.String(100), nullable=False)
    display_order = db_local.Column(db_local.Integer, default=0)
    config_json = db_local.Column(db_local.Text, default='{}')

    def _get_config(self):
        try:
            raw = json.loads(self.config_json or '{}')
            if isinstance(raw, list):
                return {"network_paths": [], "file_extensions": ['.dcm', '.pdf'], "default_energy": ""}
            return {
                "network_paths": raw.get("network_paths", []),
                "file_extensions": raw.get("file_extensions", ['.dcm', '.pdf']),
                "default_energy": raw.get("default_energy", "")
            }
        except Exception:
            return {"network_paths": [], "file_extensions": ['.dcm', '.pdf'], "default_energy": ""}

    def get_aria_keywords(self):
        """Retourne la liste des mots-clés ARIA (CSV dans le champ name)."""
        return [k.strip() for k in self.name.split(',') if k.strip()]

    def get_network_paths(self):
        return self._get_config().get("network_paths", [])

    def get_file_extensions(self):
        return self._get_config().get("file_extensions", ['.dcm', '.pdf'])

    def get_default_energy(self):
        return self._get_config().get("default_energy", "")

    def set_config(self, network_paths=None, file_extensions=None, default_energy=""):
        self.config_json = json.dumps({
            "network_paths": network_paths or [],
            "file_extensions": file_extensions or ['.dcm', '.pdf'],
            "default_energy": default_energy or ""
        })

    def to_dict(self):
        c = self._get_config()
        return {
            'id': self.id,
            'name': self.name,
            'label': self.label,
            'display_order': self.display_order,
            'network_paths': c["network_paths"],
            'file_extensions': c["file_extensions"],
            'default_energy': c["default_energy"],
        }


class CQMachine(db_local.Model):
    __tablename__ = 'cq_machines'

    id = db_local.Column(db_local.Integer, primary_key=True)
    name = db_local.Column(db_local.String(100), nullable=False)
    label = db_local.Column(db_local.String(100), nullable=False)
    display_order = db_local.Column(db_local.Integer, default=0)
    duration_rules_json = db_local.Column(db_local.Text, default='{}')

    def get_duration_rules(self):
        try:
            return json.loads(self.duration_rules_json or '{}')
        except Exception:
            return {}

    def get_acquisition(self):
        return self.get_duration_rules().get('acquisition', 15)

    def get_mount_unmount(self):
        return self.get_duration_rules().get('mount_unmount', 0)

    def set_duration_rules(self, rules_dict):
        self.duration_rules_json = json.dumps(rules_dict)

    def to_dict(self):
        rules = self.get_duration_rules()
        return {
            'id': self.id,
            'name': self.name,
            'label': self.label,
            'display_order': self.display_order,
            'duration_rules': rules,
            'acquisition': rules.get('acquisition', 15),
            'mount_unmount': rules.get('mount_unmount', 0),
        }


class ScanCache(db_local.Model):
    __tablename__ = 'scan_cache'

    id = db_local.Column(db_local.Integer, primary_key=True)
    patient_id = db_local.Column(db_local.String(50), index=True)
    file_type = db_local.Column(db_local.String(20))
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
        'LOOKBACK_DAYS': ('14', 'Nombre de jours à remonter dans ARIA'),
        'SCAN_DEPTH': ('3', 'Profondeur max du scan réseau (sous-dossiers)'),
        'SCANDIDOS_SIGNATURE': ('114358', 'Signature des fichiers Scandidos'),
    }
    for key, (val, desc) in default_settings.items():
        if not Setting.query.filter_by(key=key).first():
            db_local.session.add(Setting(key=key, value=val, description=desc))

    # Suppression des anciennes clés obsolètes
    for obsolete_key in ['APP_TITLE', 'DEFAULT_TOMO_ENERGY']:
        old = Setting.query.filter_by(key=obsolete_key).first()
        if old:
            db_local.session.delete(old)

    # 2. Onglets par défaut
    if CQTab.query.count() == 0:
        tabs = [
            CQTab(name='Tomo 2', label='Tomo 2', display_order=1),
            CQTab(name='Tomo 4', label='Tomo 4', display_order=2),
            CQTab(name='Tomo 7', label='Tomo 7', display_order=3),
            CQTab(name='Ruby, Octa4D, Nova3, Nova5', label='Nova', display_order=4),
        ]
        tabs[0].set_config(
            network_paths=[r'\\nasdata1\TOMO\02 - CQ Patients\0_DELTA4\TOMO2'],
            file_extensions=['.dcm', '.pdf'],
            default_energy='6FFF'
        )
        tabs[1].set_config(
            network_paths=[r'\\nasdata1\TOMO\02 - CQ Patients\0_DELTA4\TOMO4'],
            file_extensions=['.dcm', '.pdf'],
            default_energy='6FFF'
        )
        tabs[2].set_config(
            network_paths=[r'\\nasdata1\TOMO\02 - CQ Patients\0_DELTA4\RADI7'],
            file_extensions=['.dcm', '.pdf'],
            default_energy='6FFF'
        )
        tabs[3].set_config(
            network_paths=[
                r'\\srv015\Radiophysique_acquisition\Patients_stereo_EC_IUC',
                r'\\srv015\Radiophysique_acquisition\Patients_stereo_Hyperarc',
                r'\\srv015\Radiophysique_acquisition\Patients_RA'
            ],
            file_extensions=['.dcm', '.pdf'],
            default_energy=''
        )
        db_local.session.add_all(tabs)

    # 3. Techniques par défaut
    if CQMachine.query.count() == 0:
        techniques = [
            CQMachine(name='CQ Patient Tomo', label='5', display_order=1,
                      duration_rules_json=json.dumps({'acquisition': 3, 'mount_unmount': 5})),
            CQMachine(name='CQ Ruby', label='10', display_order=2,
                      duration_rules_json=json.dumps({'acquisition': 12, 'mount_unmount': 10})),
            CQMachine(name='CQ Octa4D', label='8', display_order=3,
                      duration_rules_json=json.dumps({'acquisition': 9, 'mount_unmount': 8})),
            CQMachine(name='CQ Octa4D HyperArc', label='10', display_order=4,
                      duration_rules_json=json.dumps({'acquisition': 15, 'mount_unmount': 10})),
        ]
        db_local.session.add_all(techniques)

    db_local.session.commit()