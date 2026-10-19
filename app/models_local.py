import os
from sqlalchemy import Column, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from werkzeug.security import generate_password_hash

BaseLocal = declarative_base()

class Setting(BaseLocal):
    __tablename__ = 'settings'
    key = Column(String(50), primary_key=True)
    value = Column(String(500), nullable=True)

# Valeurs par defaut initiales
DEFAULT_SETTINGS = {
    # ARIA Database
    'aria_db_host': 'localhost',
    'aria_db_user': 'aria_user',
    'aria_db_password': '',
    'aria_db_name': 'aria_db',
    
    # Chemins NAS CQ Patients
    'nas_tomo2': r'\\nasdata1\TOMO\02 - CQ Patients\0_DELTA4\TOMO2',
    'nas_tomo4': r'\\nasdata1\TOMO\02 - CQ Patients\0_DELTA4\TOMO4',
    'nas_tomo7': r'\\nasdata1\TOMO\02 - CQ Patients\0_DELTA4\RADI7',
    'nas_nova_ec': r'\\srv015\Radiophysique_acquisition\Patients_stereo_EC_IUC',
    'nas_nova_hyperarc': r'\\srv015\Radiophysique_acquisition\Patients_stereo_Hyperarc',
    'nas_nova_ra': r'\\srv015\Radiophysique_acquisition\Patients_RA',
    
    # Exchange / Outlook
    'outlook_email': '',
    'outlook_server': '',
    'outlook_domain': '',
    
    # RadMachine API Token
    'radmachine_token': '',
}

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
        default_hash = generate_password_hash('admin')
        session.add(Setting(key='admin_password', value=default_hash))

    # Initialisation des parametres s'ils n'existent pas
    for k, v in DEFAULT_SETTINGS.items():
        if not session.query(Setting).filter_by(key=k).first():
            session.add(Setting(key=k, value=v))

    session.commit()
    session.close()
    return engine, SessionLocal

def get_all_settings(session):
    """Retourne l'ensemble des reglages sous forme de dictionnaire Python."""
    settings = session.query(Setting).all()
    return {s.key: (s.value or '') for s in settings}