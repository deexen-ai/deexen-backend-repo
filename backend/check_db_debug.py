import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, engine
from app.models.user import User
from app.models.file import File
from sqlalchemy import inspect

def check_db():
    db = SessionLocal()
    inspector = inspect(engine)
    
    print("--- Database Schema ---")
    for table_name in inspector.get_table_names():
        print(f"Table: {table_name}")
        for column in inspector.get_columns(table_name):
            print(f"  Column: {column['name']} ({column['type']})")
    
    print("\n--- Users ---")
    users = db.query(User).all()
    for user in users:
        print(f"ID: {user.id}, Email: {user.email}, Name: {user.name}, HasPassword: {bool(user.password)}")
    
    db.close()

if __name__ == "__main__":
    check_db()
