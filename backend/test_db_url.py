import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import DATABASE_URL, engine
import os

def debug_env():
    print(f"DATABASE_URL from app.database: {DATABASE_URL}")
    print(f"DATABASE_URL from os.environ: {os.environ.get('DATABASE_URL')}")
    print(f"Engine Dialect: {engine.dialect.name}")
    
    # Try a simple version query
    with engine.connect() as conn:
        try:
            res = conn.execute("SELECT version()") if engine.dialect.name == 'postgresql' else conn.execute("SELECT VERSION()")
            print(f"DB Version: {res.fetchone()[0]}")
        except Exception as e:
            print(f"Version query failed: {e}")

if __name__ == "__main__":
    debug_env()
