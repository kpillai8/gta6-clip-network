from pathlib import Path
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from app.models import Base


def make_sessionmaker(database_url: str):
    if database_url.startswith('sqlite:///') and ':memory:' not in database_url:
        Path(database_url.removeprefix('sqlite:///')).expanduser().parent.mkdir(parents=True, exist_ok=True)
    kw = {'connect_args': {'check_same_thread': False}} if database_url.startswith('sqlite') else {}
    engine = create_engine(database_url, pool_pre_ping=True, **kw)
    Base.metadata.create_all(engine)
    # One additive SQLite migration for existing v0.1 local databases. Production
    # deployment should use versioned Alembic migrations instead.
    if engine.dialect.name == 'sqlite':
        existing = {c['name'] for c in inspect(engine).get_columns('publications')}
        if 'scheduled_at' not in existing:
            with engine.begin() as conn:
                conn.execute(text('ALTER TABLE publications ADD COLUMN scheduled_at DATETIME'))
    return sessionmaker(bind=engine, expire_on_commit=False)
