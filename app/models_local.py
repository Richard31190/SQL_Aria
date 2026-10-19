import os
from sqlalchemy import Column, String, Integer, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from werkzeug.security import generate_password_hash

BaseLocal = declarative_base()


class Setting(BaseLocal):
    __tablename__ = 'settings'
    key = Column(String(50), primary_key=True)
    value = Column(String(500), nullable=True)


class Machine(BaseLocal):
    __tablename__ = 'machines'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)   # ex: TOMO2
    label = Column(String(100), nullable=False)               # ex: Tomo 2
    dicom_path = Column(String(500), nullable=True)           # chemin NAS DICOM
    device_ids = Column(String(300), nullable=True)           # codes ARIA, separes par virgule
    order_index = Column(Integer, default=0)
    is_active = Column(Integer, default=1)                    # 1=active, 0=inactive


DEFAULT_SETTINGS = {
    'aria_db_host': 'localhost',
    'aria_db_user': 'aria_user',
    'aria_db_password': '',
    'aria_db_name': 'aria_db'
}

# Machines proposees au tout premier demarrage (modifiables ensuite)
DEFAULT_MACHINES = [
    {'name': 'TOMO2',    'label': 'Tomo 2',    'dicom_path': r'\\nasdata1\TOMO\02 - CQ Patients\0_DELTA4\TOMO2', 'device_ids': 'TOMO2',   'order_index': 1},
    {'name': 'TOMO4',    'label': 'Tomo 4',    'dicom_path': r'\\nasdata1\TOMO\02 - CQ Patients\0_DELTA4\TOMO4', 'device_ids': '0210462', 'order_index': 2},
    {'name': 'RADI7',    'label': 'Tomo 7',    'dicom_path': r'\\nasdata1\TOMO\02 - CQ Patients\0_DELTA4\RADI7', 'device_ids': 'RADI7',   'order_index': 3},
    {'name': 'NOVA3',    'label': 'Nova 3',    'dicom_path': r'\\srv015\Radiophysique_acquisition\Patients_stereo_EC_IUC', 'device_ids': 'NOVA3', 'order_index': 4},
    {'name': 'NOVA5',    'label': 'Nova 5',    'dicom_path': r'\\srv015\Radiophysique_acquisition\Patients_stereo_Hyperarc', 'device_ids': 'NOVA5', 'order_index': 5},
    {'name': 'HALCYON6', 'label': 'Halcyon 6', 'dicom_path': '', 'device_ids': 'HALCYON6', 'order_index': 6},
    {'name': 'HALCYON8', 'label': 'Halcyon 8', 'dicom_path': '', 'device_ids': 'HALCYON8', 'order_index': 7},
]


def init_local_db(app_instance_path):
    os.makedirs(app_instance_path, exist_ok=True)
    db_path = os.path.join(app_instance_path, 'local.db')
    engine = create_engine(f'sqlite:///{db_path}')
    BaseLocal.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    # Mot de passe admin par defaut
    admin_pwd = session.query(Setting).filter_by(key='admin_password').first()
    if not admin_pwd:
        session.add(Setting(key='admin_password', value=generate_password_hash('admin')))

    # Settings par defaut
    for k, v in DEFAULT_SETTINGS.items():
        if not session.query(Setting).filter_by(key=k).first():
            session.add(Setting(key=k, value=v))

    # Machines par defaut (uniquement si table vide)
    if session.query(Machine).count() == 0:
        for m in DEFAULT_MACHINES:
            session.add(Machine(
                name=m['name'],
                label=m['label'],
                dicom_path=m['dicom_path'],
                device_ids=m['device_ids'],
                order_index=m['order_index'],
                is_active=1
            ))

    session.commit()
    session.close()
    return engine, SessionLocal


def get_all_settings(session):
    settings = session.query(Setting).all()
    return {s.key: (s.value or '') for s in settings}


def get_all_machines(session, active_only=False):
    q = session.query(Machine).order_by(Machine.order_index.asc(), Machine.label.asc())
    if active_only:
        q = q.filter(Machine.is_active == 1)
    return q.all()