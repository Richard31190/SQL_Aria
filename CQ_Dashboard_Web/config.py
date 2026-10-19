import os

class Config:
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    SECRET_KEY = 'cq-dashboard-iuct-secret-key-2025'
    
    # Base SQLite locale pour la configuration
    INSTANCE_PATH = os.path.join(BASE_DIR, 'instance')
    os.makedirs(INSTANCE_PATH, exist_ok=True)
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(INSTANCE_PATH, 'local.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False