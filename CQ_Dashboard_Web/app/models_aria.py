# *** app\models_aria.py ***
from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey
from sqlalchemy.orm import declarative_base, relationship

AriaBase = declarative_base()


class Patients(AriaBase):
    __tablename__ = 'patients'

    # Colonnes réellement utilisées dans ton ancien programme
    id = Column(Integer, primary_key=True)
    family_name_official = Column(String(100))
    given = Column(String(100))
    ipp = Column(String(50))


class Careplans(AriaBase):
    __tablename__ = 'careplans'

    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey('patients.id'))
    title = Column(String(200))


class Tasks(AriaBase):
    __tablename__ = 'tasks'

    id = Column(Integer, primary_key=True)
    careplan_id = Column(Integer, ForeignKey('careplans.id'))
    display_focus = Column(String(200))
    status = Column(String(50))
    last_updated = Column(DateTime)
    note = Column(Text)
    recipient = Column(String(100))
    recipient_id = Column(String(100))


class Appointments(AriaBase):
    __tablename__ = 'appointments'

    id = Column(Integer, primary_key=True)
    patient_id = Column(Integer, ForeignKey('patients.id'))
    start_scheduled_period = Column(DateTime)
    end_scheduled_period = Column(DateTime)
    device = Column(String(100))
    service_type = Column(String(100))
    status = Column(String(50))
    comment = Column(Text)

    patient = relationship(
        'Patients',
        foreign_keys=[patient_id],
        lazy='select'
    )


class QueryLog(AriaBase):
    __tablename__ = 'query_log'

    id = Column(Integer, primary_key=True)
    query_text = Column(Text)
    executed_at = Column(DateTime)