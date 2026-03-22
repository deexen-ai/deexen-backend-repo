from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import SQLAlchemyError

import os
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '..', '.env')
load_dotenv(dotenv_path=env_path)

Base = declarative_base()


def _engine_for(url: str):
    url_l = url.lower()
    connect_args = {}

    if url_l.startswith('sqlite'):
        connect_args = {'check_same_thread': False}
    elif url_l.startswith('postgresql') or url_l.startswith('postgres'):
        connect_args = {'connect_timeout': 4}
    elif url_l.startswith('mysql'):
        connect_args = {'connect_timeout': 4}

    return create_engine(
        url,
        connect_args=connect_args,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_timeout=5,
    )


primary_url = os.getenv('DATABASE_URL', 'sqlite:///./deexen_demo.db')
fallback_url = os.getenv('DATABASE_FALLBACK_URL', 'mysql+pymysql://root:882004@localhost:3306/deexendemo')

engine = _engine_for(primary_url)
DATABASE_URL = primary_url

try:
    with engine.connect() as conn:
        conn.exec_driver_sql('SELECT 1')
except SQLAlchemyError:
    if fallback_url and fallback_url != primary_url:
        engine = _engine_for(fallback_url)
        DATABASE_URL = fallback_url

SessionLocal = sessionmaker(bind=engine)
