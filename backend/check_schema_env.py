import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import engine
from sqlalchemy import text

def check_env():
    with engine.connect() as conn:
        res = conn.execute(text("SHOW search_path"))
        print(f"Current search_path: {res.fetchone()[0]}")
        
        res = conn.execute(text("SELECT current_schema()"))
        print(f"Current schema: {res.fetchone()[0]}")
        
        print("\n--- Listing tables in 'public' schema ---")
        res = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"))
        for row in res:
            print(f"Table: {row[0]}")

if __name__ == "__main__":
    check_env()
