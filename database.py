from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# One SQLite file living in the project folder.
DATABASE_URL = "sqlite:///./xc_training.db"

# check_same_thread=False is required for SQLite when used with FastAPI,
# because requests may be handled on different threads.
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

# A factory that hands out database sessions (one per request, later on).
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """Parent class that all ORM models inherit from."""
    pass


def get_db():
    """Yield a database session for one request, then close it.

    Used as a FastAPI dependency so each request gets its own session.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
