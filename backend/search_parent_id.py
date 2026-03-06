import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import engine
from sqlalchemy import text

def search_column():
    with engine.connect() as conn:
        print("Searching for 'parent_id' column...")
        res = conn.execute(text("""
            SELECT table_schema, table_name, column_name 
            FROM information_schema.columns 
            WHERE column_name = 'parent_id'
        """))
        for row in res:
            print(f"Schema: {row[0]}, Table: {row[1]}, Column: {row[2]}")
            
        print("\n--- Listing ALL columns for 'files' table in ALL schemas ---")
        res = conn.execute(text("""
            SELECT table_schema, table_name, column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'files'
            ORDER BY table_schema, column_name
        """))
        for row in res:
            print(f"Schema: {row[0]}, Table: {row[1]}, Column: {row[2]} ({row[3]})")

if __name__ == "__main__":
    search_column()
