"""
db/database.py
==============
SQLAlchemy engine + session factory for the SQLite persistence layer.

Usage
-----
  from db.database import get_db, init_db

  # In api_server lifespan:
  init_db()

  # In a route (sync):
  with SessionLocal() as db:
      db.add(...)
      db.commit()

  # As FastAPI dependency (sync):
  def my_route(db: Session = Depends(get_db)): ...
"""
from __future__ import annotations

import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

import config

# ── Engine ────────────────────────────────────────────────────────

_DB_PATH = os.environ.get("SQLITE_DB_PATH", getattr(config, "SQLITE_DB_PATH", "bo_trading.db"))
_DB_URL  = f"sqlite:///{_DB_PATH}"

engine = create_engine(
    _DB_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

# Enable WAL journal mode for better concurrency with async writers
@event.listens_for(engine, "connect")
def _set_wal(dbapi_conn, _connection_record):
    dbapi_conn.execute("PRAGMA journal_mode=WAL")
    dbapi_conn.execute("PRAGMA foreign_keys=ON")
    dbapi_conn.execute("PRAGMA synchronous=NORMAL")


SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=engine, autocommit=False, autoflush=False, expire_on_commit=False
)


# ── Helpers ───────────────────────────────────────────────────────

def init_db() -> None:
    """Create all tables if they do not exist. Called once at startup."""
    from db.models import Base
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency — yields a database session and closes it afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
