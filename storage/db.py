# planner/storage/db.py
from sqlmodel import SQLModel, create_engine, Session

_engine = create_engine("sqlite:///app.db", echo=False)

def init_db():
    SQLModel.metadata.create_all(_engine)

def get_engine():
    return _engine

def get_session() -> Session:
    return Session(_engine)
