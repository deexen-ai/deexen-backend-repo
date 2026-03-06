import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import engine
from sqlalchemy import text

def diag_db():
    with engine.connect() as conn:
        print("--- Tables in public schema ---")
        res = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"))
        for row in res:
            print(f"Table: {row[0]}")
            
        print("\n--- Columns in 'files' table ---")
        try:
            res = conn.execute(text("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'files' AND table_schema = 'public'"))
            for row in res:
                print(f"Column: {row[0]} ({row[1]})")
        except Exception as e:
            print(f"Error querying 'files' columns: {e}")

        print("\n--- Testing a simple query on 'files' ---")
        try:
            res = conn.execute(text("SELECT * FROM files LIMIT 1"))
            print("Query successful")
        except Exception as e:
            print(f"Query failed: {e}")

if __name__ == "__main__":
    diag_db()
