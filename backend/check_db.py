import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Connect to the exact database URL
DATABASE_URL = "postgresql://postgres.wjdvbodcmsfnpuyuhgxi:SLp6ckivxqEjGCYR@aws-1-ap-northeast-1.pooler.supabase.com:6543/postgres"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
db = SessionLocal()

print("Connected to DB.")
try:
    with engine.connect() as con:
        users = con.execute(text("SELECT id, email, provider_id FROM users")).fetchall()
        print(f"Total users found: {len(users)}")
        for u in users:
            print(u)
except Exception as e:
    print(f"Error accessing DB: {e}")
