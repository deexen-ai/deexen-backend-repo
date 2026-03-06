from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

import os
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '..', '.env')
load_dotenv(dotenv_path=env_path)

# Fallback safely without hardcoding Production secrets
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:password@db.wjdvbodcmsfnpuyuhgxi.supabase.co:5432/postgres")

engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()
