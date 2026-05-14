from typing import List, Optional

from sqlalchemy import Date, DateTime, ForeignKeyConstraint, Index, Integer, JSON, String, Column
from sqlalchemy.dialects.mysql import TINYINT
#from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.orm import Mapped, mapped_column, relationship, declarative_base
from base import Base
from datetime import datetime


#class Base(DeclarativeBase):
#    pass


class Comments(Base):
    __tablename__ = 'comments'
    __table_args__ = (
        Index('ix_comments_id', 'id'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_updated: Mapped[Optional[datetime]] = mapped_column(DateTime)

    comment_associations: Mapped[List['CommentAssociations']] = relationship('CommentAssociations', back_populates='comment')


class Patients(Base):
    __tablename__ = 'patients'
    __table_args__ = (
        Index('ix_patients_id', 'id'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    family_name_official: Mapped[str] = mapped_column(String(255))
    given: Mapped[str] = mapped_column(String(255))
    birth_date: Mapped[datetime.date] = mapped_column(Date)
    family_name_maiden: Mapped[Optional[str]] = mapped_column(String(255))
    gender: Mapped[Optional[str]] = mapped_column(String(255))
    ipp: Mapped[Optional[str]] = mapped_column(String(255))
    telecom: Mapped[Optional[dict]] = mapped_column(JSON)
    address: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_updated: Mapped[Optional[datetime]] = mapped_column(DateTime)

    appointments: Mapped[List['Appointments']] = relationship('Appointments', back_populates='patient')
    careplans: Mapped[List['Careplans']] = relationship('Careplans', back_populates='patient')
    events: Mapped[List['Events']] = relationship('Events', back_populates='patient')
    prescriptions: Mapped[List['Prescriptions']] = relationship('Prescriptions', back_populates='patient')
    plans: Mapped[List['Plans']] = relationship('Plans', back_populates='patient')


class QueryLog(Base):
    __tablename__ = 'query_log'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_task_request: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_appointment_request: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_updated: Mapped[Optional[datetime]] = mapped_column(DateTime)


class Appointments(Base):
    __tablename__ = 'appointments'
    __table_args__ = (
        ForeignKeyConstraint(['patient_id'], ['patients.id'], name='appointments_ibfk_1'),
        Index('ix_appointments_id', 'id'),
        Index('patient_id', 'patient_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[Optional[str]] = mapped_column(String(255))
    code: Mapped[Optional[str]] = mapped_column(String(255))
    service_type: Mapped[Optional[str]] = mapped_column(String(255))
    service_category: Mapped[Optional[str]] = mapped_column(String(255))
    is_active: Mapped[Optional[int]] = mapped_column(TINYINT(1))
    start_scheduled_period: Mapped[Optional[datetime]] = mapped_column(DateTime)
    end_scheduled_period: Mapped[Optional[datetime]] = mapped_column(DateTime)
    instance: Mapped[Optional[str]] = mapped_column(String(255))
    user_note: Mapped[Optional[str]] = mapped_column(String(255))
    minutes_duration: Mapped[Optional[int]] = mapped_column(Integer)
    comment: Mapped[Optional[str]] = mapped_column(String(255))
    device: Mapped[Optional[str]] = mapped_column(String(255))
    physician_id: Mapped[Optional[int]] = mapped_column(Integer)
    physician: Mapped[Optional[str]] = mapped_column(String(255))
    patient_id: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_updated: Mapped[Optional[datetime]] = mapped_column(DateTime)

    patient: Mapped[Optional['Patients']] = relationship('Patients', back_populates='appointments')


class Careplans(Base):
    __tablename__ = 'careplans'
    __table_args__ = (
        ForeignKeyConstraint(['patient_id'], ['patients.id'], name='careplans_ibfk_1'),
        Index('ix_careplans_id', 'id'),
        Index('patient_id', 'patient_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    note: Mapped[Optional[str]] = mapped_column(String(255))
    patient_id: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_updated: Mapped[Optional[datetime]] = mapped_column(DateTime)

    patient: Mapped[Optional['Patients']] = relationship('Patients', back_populates='careplans')
    tasks: Mapped[List['Tasks']] = relationship('Tasks', back_populates='careplan')


class CommentAssociations(Base):
    __tablename__ = 'comment_associations'
    __table_args__ = (
        ForeignKeyConstraint(['comment_id'], ['comments.id'], name='comment_associations_ibfk_1'),
        Index('comment_id', 'comment_id'),
        Index('ix_comment_associations_id', 'id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    table_name: Mapped[str] = mapped_column(String(255))
    entity_id: Mapped[int] = mapped_column(Integer)
    comment_id: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_updated: Mapped[Optional[datetime]] = mapped_column(DateTime)

    comment: Mapped[Optional['Comments']] = relationship('Comments', back_populates='comment_associations')


class Events(Base):
    __tablename__ = 'events'
    __table_args__ = (
        ForeignKeyConstraint(['patient_id'], ['patients.id'], name='events_ibfk_1'),
        Index('ix_events_event_type', 'event_type'),
        Index('ix_events_id', 'id'),
        Index('patient_id', 'patient_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(255))
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    entity_id: Mapped[Optional[int]] = mapped_column(Integer)
    description: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_updated: Mapped[Optional[datetime]] = mapped_column(DateTime)

    patient: Mapped['Patients'] = relationship('Patients', back_populates='events')


class Prescriptions(Base):
    __tablename__ = 'prescriptions'
    __table_args__ = (
        ForeignKeyConstraint(['patient_id'], ['patients.id'], name='prescriptions_ibfk_1'),
        Index('ix_prescriptions_id', 'id'),
        Index('patient_id', 'patient_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prescription_key: Mapped[int] = mapped_column(Integer)
    patient_id: Mapped[int] = mapped_column(Integer)
    version_number: Mapped[int] = mapped_column(Integer)
    procedure_intent: Mapped[Optional[str]] = mapped_column(String(255))
    prescription_type: Mapped[Optional[str]] = mapped_column(String(255))
    volumes_and_doses: Mapped[Optional[dict]] = mapped_column(JSON)
    modality: Mapped[Optional[str]] = mapped_column(String(255))
    technique: Mapped[Optional[str]] = mapped_column(String(255))
    fractions_prescribed: Mapped[Optional[int]] = mapped_column(Integer)
    prescription_id: Mapped[Optional[str]] = mapped_column(String(255))
    course_name: Mapped[Optional[str]] = mapped_column(String(255))
    course_id: Mapped[Optional[str]] = mapped_column(String(255))
    phaseprescription_id: Mapped[Optional[str]] = mapped_column(String(255))
    basedOn: Mapped[Optional[dict]] = mapped_column(JSON)
    course_basedOn: Mapped[Optional[str]] = mapped_column(String(255))
    replaces: Mapped[Optional[str]] = mapped_column(String(255))
    occurrence: Mapped[Optional[str]] = mapped_column(String(255))
    site: Mapped[Optional[str]] = mapped_column(String(255))
    bodysite: Mapped[Optional[str]] = mapped_column(String(255))
    note: Mapped[Optional[str]] = mapped_column(String(3000))
    is_active: Mapped[Optional[int]] = mapped_column(TINYINT(1))
    status: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_updated: Mapped[Optional[datetime]] = mapped_column(DateTime)
    requester_display: Mapped[Optional[str]] = mapped_column(String(255))
    bolus: Mapped[Optional[str]] = mapped_column(String(255))
    motion_management: Mapped[Optional[str]] = mapped_column(String(255))

    patient: Mapped['Patients'] = relationship('Patients', back_populates='prescriptions')
    plans: Mapped[List['Plans']] = relationship('Plans', back_populates='prescription')


class Plans(Base):
    __tablename__ = 'plans'
    __table_args__ = (
        ForeignKeyConstraint(['patient_id'], ['patients.id'], name='plans_ibfk_1'),
        ForeignKeyConstraint(['prescription_id'], ['prescriptions.id'], name='plans_ibfk_2'),
        Index('ix_plans_id', 'id'),
        Index('patient_id', 'patient_id'),
        Index('prescription_id', 'prescription_id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String(255))
    plan_operator: Mapped[Optional[str]] = mapped_column(String(255))
    treatment_technique: Mapped[Optional[str]] = mapped_column(String(255))
    patient_id: Mapped[Optional[int]] = mapped_column(Integer)
    prescription_id: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_updated: Mapped[Optional[datetime]] = mapped_column(DateTime)

    patient: Mapped[Optional['Patients']] = relationship('Patients', back_populates='plans')
    prescription: Mapped[Optional['Prescriptions']] = relationship('Prescriptions', back_populates='plans')


class Tasks(Base):
    __tablename__ = 'tasks'
    __table_args__ = (
        ForeignKeyConstraint(['careplan_id'], ['careplans.id'], name='tasks_ibfk_1'),
        Index('careplan_id', 'careplan_id'),
        Index('ix_tasks_id', 'id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_focus: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(255))
    code: Mapped[Optional[str]] = mapped_column(String(255))
    minutes_duration: Mapped[Optional[int]] = mapped_column(Integer)
    activitydefinition_id: Mapped[Optional[int]] = mapped_column(Integer)
    basedOn: Mapped[Optional[int]] = mapped_column(Integer)
    restriction_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime)
    executionPeriod: Mapped[Optional[datetime]] = mapped_column(DateTime)
    lastModified: Mapped[Optional[datetime]] = mapped_column(DateTime)
    authoredOn: Mapped[Optional[datetime]] = mapped_column(DateTime)
    category: Mapped[Optional[str]] = mapped_column(String(255))
    note: Mapped[Optional[str]] = mapped_column(String(255))
    recipient: Mapped[Optional[str]] = mapped_column(String(255))
    recipient_id: Mapped[Optional[int]] = mapped_column(Integer)
    careplan_id: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_updated: Mapped[Optional[datetime]] = mapped_column(DateTime)
    device: Mapped[Optional[str]] = mapped_column(String(255))

    careplan: Mapped[Optional['Careplans']] = relationship('Careplans', back_populates='tasks')

