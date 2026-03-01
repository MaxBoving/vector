from sqlmodel import SQLModel, create_engine, Session, select
from .models import CompanyState, CEOPreferences
import os

sqlite_url = "sqlite:///./agenticmind.db"
engine = create_engine(sqlite_url, echo=False)

def init_db():
    SQLModel.metadata.create_all(engine)

def get_ceo_preferences(ceo_id: str) -> CEOPreferences:
    with Session(engine) as session:
        statement = select(CEOPreferences).where(CEOPreferences.ceo_id == ceo_id)
        return session.exec(statement).first()

def get_company_state(company_name: str) -> CompanyState:
    with Session(engine) as session:
        statement = select(CompanyState).where(CompanyState.company_name == company_name)
        return session.exec(statement).first()

def save_object(obj: SQLModel):
    with Session(engine) as session:
        session.add(obj)
        session.commit()
        session.refresh(obj)
        return obj
