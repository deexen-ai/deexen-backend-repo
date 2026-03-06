import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import engine
from sqlalchemy import text

def fix_db():
    with engine.connect() as conn:
        print("Fixing schema...")
        
        # Files table adjustments
        try:
            # Check if columns exist first to be safe
            columns = [c[0] for c in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'files'"))]
            
            if 'content' not in columns:
                print("Adding files.content...")
                conn.execute(text("ALTER TABLE files ADD COLUMN content TEXT"))
            
            if 'is_active' not in columns:
                print("Adding files.is_active...")
                conn.execute(text("ALTER TABLE files ADD COLUMN is_active BOOLEAN DEFAULT TRUE"))
            
            if 'updated_at' not in columns:
                print("Adding files.updated_at...")
                conn.execute(text("ALTER TABLE files ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"))
            
            conn.commit()
            print("Files table fixed.")
        except Exception as e:
            print(f"Error fixing files table: {e}")
            
        # Projects table adjustments
        try:
            columns = [c[0] for c in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'projects'"))]
            
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
