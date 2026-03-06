import sys
import os

# FORCE path to apps/backend/backend so we get the correct 'app' package
target_path = r"c:\Users\aggam\Documents\deexen\project_1\experimental\apps\backend\backend"
if target_path not in sys.path:
    sys.path.insert(0, target_path)

from app.database import engine, DATABASE_URL
from sqlalchemy import text

def fix_db():
    print(f"Targeting DATABASE_URL: {DATABASE_URL}")
    with engine.connect() as conn:
        print("Fixing schema for Postgres...")
        
        # Files table adjustments
        try:
            # Check if columns exist first
            res = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'files' AND table_schema = 'public'"))
            columns = [row[0] for row in res]
            print(f"Existing columns in files: {columns}")
            
            if 'parent_id' not in columns:
                print("Adding files.parent_id...")
                conn.execute(text("ALTER TABLE files ADD COLUMN parent_id INTEGER"))
            
            if 'content' not in columns:
                print("Adding files.content...")
                conn.execute(text("ALTER TABLE files ADD COLUMN content TEXT"))
            
            if 'is_active' not in columns:
                print("Adding files.is_active...")
                conn.execute(text("ALTER TABLE files ADD COLUMN is_active BOOLEAN DEFAULT TRUE"))
            
            if 'language' not in columns:
                print("Adding files.language...")
                conn.execute(text("ALTER TABLE files ADD COLUMN language VARCHAR(50)"))

            if 'updated_at' not in columns:
                print("Adding files.updated_at...")
                conn.execute(text("ALTER TABLE files ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"))
                
            if 'created_at' not in columns:
                print("Adding files.created_at...")
                conn.execute(text("ALTER TABLE files ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"))
            
            if 'type' not in columns and 'file_type' in columns:
                print("Renaming file_type to type...")
                conn.execute(text("ALTER TABLE files RENAME COLUMN file_type TO type"))
            elif 'type' not in columns:
                 print("Adding files.type...")
                 conn.execute(text("ALTER TABLE files ADD COLUMN type VARCHAR(50)"))

            conn.commit()
            print("Files table fixed.")
        except Exception as e:
            print(f"Error fixing files table: {e}")
            
        # Projects table adjustments
        try:
            res = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'projects' AND table_schema = 'public'"))
            columns = [row[0] for row in res]
            
            if 'is_active' not in columns:
                print("Adding projects.is_active...")
                conn.execute(text("ALTER TABLE projects ADD COLUMN is_active BOOLEAN DEFAULT TRUE"))
            
            if 'updated_at' not in columns:
                print("Adding projects.updated_at...")
                conn.execute(text("ALTER TABLE projects ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"))
            
            conn.commit()
            print("Projects table fixed.")
        except Exception as e:
            print(f"Error fixing projects table: {e}")

if __name__ == "__main__":
    fix_db()
